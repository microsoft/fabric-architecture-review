# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Architectural demand, endpoint boundary and definition-persistence regressions."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analyzers.far_profile import ACTIVE_RULE_IDS, TENANT_RULES
from collectors import (
    capacity_metrics, far_collection, fuam_sourced, pipeline_definitions, pipelines_notebooks,
    semantic_model_definitions, semantic_models,
)
from collectors.definition_safety import metadata_only, model_summary, notebook_summary, pipeline_summary
from collectors.pipeline_definitions import _normalise

WORKSPACE = "aaaaaaaa-0000-4000-8000-000000000001"
ITEM = "bbbbbbbb-0000-4000-8000-000000000001"
CAPACITY = "cccccccc-0000-4000-8000-000000000001"
ROOT = Path(__file__).resolve().parents[1]
SOURCE_LITERAL = "CUSTOMER_SECRET_LITERAL_NEVER_PERSIST"


def definition(path, body):
    text = body if isinstance(body, str) else json.dumps(body)
    return {"definition": {"parts": [{"path": path, "payloadType": "InlineBase64",
                                     "payload": base64.b64encode(text.encode()).decode()}]}}


def snapshot():
    from orchestration.review_config import local_configuration, digest
    configuration = local_configuration(ROOT / "config")
    return {"configuration": configuration, "sha256": digest(configuration),
            "captured_at_utc": "2026-10-12T12:00:00+00:00"}


@pytest.mark.parametrize(("fuam", "fca"), [(False, False), (True, False), (False, True), (True, True)])
def test_planner_never_enables_optimizers_even_with_upstream_modules(fuam, fca):
    frozen = snapshot()
    frozen["configuration"]["sources"]["fuam"]["enabled"] = fuam
    frozen["configuration"]["sources"]["fca"]["enabled"] = fca
    result = far_collection.plan(frozen, [{"id": WORKSPACE, "capacityId": CAPACITY}])
    assert set(result["active_rule_ids"]) == ACTIVE_RULE_IDS
    # Only what FUAM already holds is read from it; everything else stays on the API.
    expected = {row["collector"]: ("fuam" if fuam and row["collector"] in fuam_sourced.SUPPLIED
                                   else "native_architecture") for row in result["source_resolutions"]}
    assert {row["collector"]: row["resolution"] for row in result["source_resolutions"]} == expected
    assert expected["tenant_settings"] == ("fuam" if fuam else "native_architecture")
    baseline = snapshot()
    assert result["collectors"] == far_collection.plan(baseline, [{"id": WORKSPACE, "capacityId": CAPACITY}])["collectors"]
    assert {"collectors.semantic_models", "collectors.pipelines_notebooks"} <= set(result["collectors"])


def test_disabled_policy_reduces_demand_without_reenabling_removed_rules():
    frozen = snapshot()
    frozen["configuration"]["profiles"]["rule_profiles"] = {"declared": {"rule_overrides": {
        rule: {"applicability": "disabled", "reason": "Explicit review policy."}
        for rule in ACTIVE_RULE_IDS - TENANT_RULES
    }}}
    frozen["configuration"]["profiles"]["default_rule_profile"] = "declared"
    result = far_collection.plan(frozen, [{"id": WORKSPACE}])
    assert set(result["collectors"]) == {"collectors.tenant_settings", "collectors.gateways", "collectors.capacity_metrics"}
    assert set(result["active_rule_ids"]) == TENANT_RULES
    assert not result["empty_scope"]
    frozen["configuration"]["profiles"]["rule_profiles"] = {"off": {"rule_overrides": {
        rule: {"applicability": "disabled", "reason": "Explicit review policy."} for rule in ACTIVE_RULE_IDS
    }}}
    frozen["configuration"]["profiles"]["default_rule_profile"] = "off"
    result = far_collection.plan(frozen, [{"id": WORKSPACE}])
    assert result["collectors"] == []
    assert result["active_rule_ids"] == []


def test_empty_scope_does_not_collect_global_architectural_evidence():
    result = far_collection.plan(snapshot(), [])
    assert result["empty_scope"]
    assert result["collectors"] == []


def test_missing_workspace_identity_cannot_expand_to_tenant_wide_demand():
    assert far_collection.plan(snapshot(), [{"name": "Unknown"}])["collectors"] == []


def test_shared_collection_stamps_are_accepted_by_analysis_and_merge_for_empty_scope(monkeypatch, tmp_path):
    import os
    from analyzers import far_review
    from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256
    from analyzers.merge_findings import merge
    from collectors import scanner_api, workspace_inventory
    from orchestration.review_config import activate, capture, local_configuration, read_snapshot

    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("WORKSPACE_IDS", "")
    configuration = local_configuration(ROOT / "config")
    for source in configuration["sources"].values():
        source["enabled"] = False
    configuration_path = capture(tmp_path, configuration)
    activate(configuration_path)
    frozen = read_snapshot(configuration_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for filename in ("pipeline_definitions.json", "semantic_model_definitions.json", "tenant_settings.json"):
        (raw_dir / filename).write_text('{"historical": "unstamped"}')
    attempted = []

    def bootstrap(filename, payload):
        def collect(output_dir):
            attempted.append(filename)
            target = Path(output_dir) / filename
            target.write_text(json.dumps(payload), encoding="utf-8")
            return target
        return collect

    monkeypatch.setattr(scanner_api, "collect", bootstrap(
        "scanner.json", {"workspaces": [], "collectionComplete": True, "_meta": {"complete": True}},
    ))
    monkeypatch.setattr(workspace_inventory, "collect", bootstrap(
        "workspace_inventory.json",
        {"workspaces": [], "collectionComplete": True, "workspaceListComplete": True},
    ))
    plan_path = far_collection.collect(raw_dir)
    execution_plan = json.loads(plan_path.read_text())
    assert execution_plan["empty_scope"] is True
    assert execution_plan["collectors"] == []
    assert execution_plan["collectionComplete"] is True
    assert attempted == ["scanner.json", "workspace_inventory.json"]
    for filename in ("scanner.json", "workspace_inventory.json"):
        provenance = json.loads((raw_dir / filename).read_text())["_provenance"]
        assert provenance["source"] == "native"
        assert provenance["profile_id"] == PROFILE_ID
        assert provenance["profile_sha256"] == PROFILE_SHA256
        assert provenance["configuration_sha256"] == frozen["sha256"]
        assert provenance["configuration_captured_at_utc"] == frozen["captured_at_utc"]
        assert provenance["workspace_scope"] == ""
        assert provenance["capture_status"] == "collected"
    for filename in ("pipeline_definitions.json", "semantic_model_definitions.json", "tenant_settings.json"):
        assert not (raw_dir / filename).exists()

    findings_path = far_review.analyze(raw_dir, tmp_path, ROOT / "config" / "review-checklist.yaml")
    findings = json.loads(findings_path.read_text())
    assert {row["rule_id"] for row in findings} == ACTIVE_RULE_IDS
    assert len(findings) == len(ACTIVE_RULE_IDS)
    assert not any(row["scored"] for row in findings)
    assert all(row["configuration_sha256"] == frozen["sha256"] for row in findings)
    assert all(row["configuration_captured_at_utc"] == frozen["captured_at_utc"] for row in findings)
    merged = json.loads(merge(tmp_path, run_id="shared-collector-empty-scope").read_text())
    assert {row["rule_id"] for row in merged} == ACTIVE_RULE_IDS
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    assert manifest["profile_id"] == PROFILE_ID
    assert manifest["profile_sha256"] == PROFILE_SHA256
    assert manifest["configuration_sha256"] == frozen["sha256"]


@pytest.mark.parametrize("gap", [None, "collectors.workspace_inventory"])
def test_shared_runtime_scopes_demand_stamps_evidence_and_removes_stale_files(monkeypatch, tmp_path, gap):
    other = "dddddddd-0000-4000-8000-000000000001"
    frozen = snapshot()
    frozen["configuration"]["profiles"]["workspaces"] = [
        {"id": WORKSPACE, "profile": {"environment": "production"}},
        {"id": other, "profile": {"environment": "nonproduction"}},
    ]
    workspaces = [{"id": WORKSPACE}, {"id": other}]
    monkeypatch.setenv("WORKSPACE_IDS", ",".join((WORKSPACE, other)))
    monkeypatch.setenv("FAR_CONFIG_SNAPSHOT", str(tmp_path / "review_config.json"))
    monkeypatch.setattr(far_collection, "read_snapshot", lambda _: frozen)
    monkeypatch.setattr(far_collection, "load_workspace_inventory", lambda _: workspaces)
    for filename in ("earlier_run_evidence.json", "pipeline_definitions.json"):
        (tmp_path / filename).write_text(SOURCE_LITERAL)
    calls = []
    run_scopes = set()

    def module(name):
        def collect(output_dir):
            calls.append((name, __import__("os").environ["WORKSPACE_IDS"]))
            run_scopes.add(__import__("os").environ.get("FAR_RUN_WORKSPACE_IDS"))
            short = name.removeprefix("collectors.")
            filename = far_collection.ARTIFACTS.get(short, {
                "scanner_api": "scanner.json", "workspace_inventory": "workspace_inventory.json",
                "far_evidence": "far_evidence.json",
            }.get(short))
            target = Path(output_dir) / filename
            target.write_text(json.dumps({"collectionComplete": name != gap, "workspaces": workspaces}))
            return target
        return SimpleNamespace(collect=collect)

    monkeypatch.setattr(far_collection.importlib, "import_module", module)
    plan_path = far_collection.collect(tmp_path)
    result = json.loads(plan_path.read_text())
    assert result["status"] == ("incomplete" if gap else "completed")
    assert result["collectionComplete"] is (gap is None)
    assert __import__("os").environ["WORKSPACE_IDS"] == ",".join((WORKSPACE, other))
    # Every collector can read the scope of the whole review, whatever narrower scope it was given; nothing leaks after.
    assert run_scopes == {",".join((WORKSPACE, other))}
    assert "FAR_RUN_WORKSPACE_IDS" not in __import__("os").environ
    tenant_wide = {"collectors.tenant_settings", "collectors.gateways"}
    both = ",".join((WORKSPACE, other))
    # Only declared production workspaces demand the production-only source-control evidence.
    production_only = {"collectors.git_integration", "collectors.workspace_network", "collectors.deployment_pipelines",
                       "collectors.onelake_roles"}
    expected = {name: "" if name in tenant_wide else WORKSPACE if name in production_only else both
                for name in result["collectors"]}
    assert "collectors.git_integration" in expected
    assert {name: scope for name, scope in calls if name in result["collectors"]} == expected
    assert set(result["collectors"]) <= {name for name, _ in calls}
    if gap:
        assert {"collector": gap, "status": "incomplete"} in result["outcomes"]
    assert not (tmp_path / "earlier_run_evidence.json").exists()
    payload = json.loads((tmp_path / "pipeline_definitions.json").read_text())
    assert SOURCE_LITERAL not in json.dumps(payload)
    assert payload["_provenance"]["profile_sha256"] == result["profile_sha256"]
    assert payload["_provenance"]["configuration_sha256"] == frozen["sha256"]
    assert payload["_provenance"]["configuration_captured_at_utc"] == frozen["captured_at_utc"]
    assert payload["_provenance"]["capture_status"] == "collected"
    assert payload["_provenance"]["workspace_scope"] == ",".join((WORKSPACE, other))
    assert payload["_provenance"]["collector_workspace_scope"] == both


def test_model_definition_demand_cannot_read_broader_prerequisite_scope(monkeypatch, tmp_path):
    provider(monkeypatch, semantic_model_definitions)
    other = "dddddddd-0000-4000-8000-000000000001"
    monkeypatch.setenv("WORKSPACE_IDS", WORKSPACE)
    (tmp_path / "semantic_models.json").write_text(json.dumps({
        "collectionComplete": True,
        "datasets": [{"id": ITEM, "workspaceId": WORKSPACE, "targetStorageMode": "DirectLake"},
                     {"id": other, "workspaceId": other}],
    }))
    requests = []

    def fetch(_, workspace, item):
        requests.append((workspace, item))
        return definition("model.bim", {"model": {"tables": [{"partitions": [{
            "mode": "directLake", "source": {"expression": SOURCE_LITERAL},
        }]}]}}), None

    monkeypatch.setattr(semantic_model_definitions, "_get_definition", fetch)
    target = semantic_model_definitions.collect(tmp_path)
    assert requests == [(WORKSPACE, ITEM)]
    raw = json.loads(target.read_text())
    assert raw["models"][0]["storageModes"] == ["DirectLake"]
    assert raw["models"][0]["parts"] == []
    assert SOURCE_LITERAL not in target.read_text()


def test_pipeline_producer_never_persists_source_literals(monkeypatch, tmp_path):
    provider(monkeypatch, pipeline_definitions)
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    (tmp_path / "pipelines_notebooks.json").write_text(json.dumps({
        "pipelines": [{"id": ITEM, "workspaceId": WORKSPACE}],
        "notebooks": [{"id": ITEM, "workspaceId": WORKSPACE}],
        "collectionComplete": True,
    }))
    responses = iter([
        definition("pipeline-content.json", {"activities": [{
            "name": "Run", "type": "TridentNotebook",
            "typeProperties": {"parameters": {"input": {"value": SOURCE_LITERAL}},
                               "script": SOURCE_LITERAL},
        }]}),
        definition("notebook-content.ipynb", {"cells": [{
            "cell_type": "code", "metadata": {"tags": ["parameters"]},
            "source": f'password = "{SOURCE_LITERAL}"', "outputs": [{"text": SOURCE_LITERAL}],
        }]}),
    ])
    monkeypatch.setattr(pipeline_definitions, "_get_definition", lambda *_: (next(responses), None))
    target = pipeline_definitions.collect(tmp_path)
    text = target.read_text()
    assert SOURCE_LITERAL not in text
    assert base64.b64encode(SOURCE_LITERAL.encode()).decode() not in text
    raw = json.loads(text)
    assert raw["notebooks"][0]["parameterNames"] == ["password"]
    assert raw["notebooks"][0]["signals"]["SEC-011"] == 1


def test_pipeline_skeleton_discards_values_code_and_base64():
    raw = definition("pipeline-content.json", {"properties": {
        "parameters": {"job": {"defaultValue": SOURCE_LITERAL}},
        "activities": [{
            "name": "Execute", "type": "TridentNotebook",
            "dependsOn": [{"activity": "Prepare", "dependencyConditions": ["Succeeded"]}],
            "typeProperties": {"notebookId": ITEM, "workspaceId": WORKSPACE,
                               "parameters": {"defaulted": {"value": SOURCE_LITERAL}},
                               "script": SOURCE_LITERAL},
        }],
    }})
    record = _normalise({"id": ITEM, "workspaceId": WORKSPACE}, raw, None)
    serialized = json.dumps(record)
    assert SOURCE_LITERAL not in serialized
    assert "payload" not in serialized
    activity = record["parts"][0]["decoded"]["properties"]["activities"][0]
    assert activity["typeProperties"]["parameters"] == {"defaulted": None}
    assert activity["dependsOn"][0]["activity"] == "Prepare"
    assert activity["typeProperties"]["notebookId"] == ITEM


def test_notebook_parameter_defaults_are_names_only_and_signals_are_counts():
    raw = definition("notebook-content.ipynb", {"cells": [{
        "cell_type": "code", "metadata": {"tags": ["parameters"]},
        "source": [f'password = "{SOURCE_LITERAL}"\n', "optional = 3\n"],
        "outputs": [{"text": SOURCE_LITERAL}], "execution_count": 9,
    }]})
    record = _normalise({"id": ITEM}, raw, None, notebook=True)
    assert record["parameterNames"] == ["optional", "password"]
    assert record["parameterCoverage"] == "complete"
    assert record["signals"]["SEC-011"] == 1
    assert record["parts"] == []
    assert SOURCE_LITERAL not in json.dumps(record)


def test_invalid_notebook_parameters_never_claim_complete_coverage():
    raw = definition("notebook-content.ipynb", {"cells": [{
        "cell_type": "code", "metadata": {"tags": ["parameters"]}, "source": ["%sql\nselect 1"],
    }]})
    assert notebook_summary(raw)["parameterCoverage"] == "partial"
    assert notebook_summary(definition("notebook-content.py", f'x = "{SOURCE_LITERAL}"'))[
        "parameterCoverage"] == "partial"
    assert pipeline_summary(definition("script.py", SOURCE_LITERAL))["parts"] == []


def test_model_definitions_discard_dax_m_connection_literals_and_payload():
    raw = definition("definition/tables/Orders.tmdl", (
        "table Orders\n  measure Revenue = " + SOURCE_LITERAL +
        "\n  partition Orders = m\n    mode: directLake\n"
        f'    source = Sql.Database("server", "{SOURCE_LITERAL}")\n'
        "  directLakeBehavior: Automatic\n"
    ))
    record = model_summary(raw)
    assert record["storageModes"] == ["DirectLake"]
    assert record["directLakeModes"] == ["sql_endpoint"]
    assert record["directLakeBehavior"] == "automatic"
    assert SOURCE_LITERAL not in json.dumps(record)
    assert record["parts"] == []


def test_unexpected_scanner_executable_fields_are_discarded():
    result = metadata_only({"datasets": [{"id": ITEM, "tables": [{
        "name": "Table", "expression": SOURCE_LITERAL, "connectionDetails": {"password": SOURCE_LITERAL},
    }]}], "outputs": [SOURCE_LITERAL], "payload": SOURCE_LITERAL,
        "script": SOURCE_LITERAL, "definition": {"M": SOURCE_LITERAL}})
    assert SOURCE_LITERAL not in json.dumps(result)
    assert result["datasets"][0]["tables"][0]["name"] == "Table"


def provider(monkeypatch, module):
    monkeypatch.setattr(module, "get_default_provider", lambda: SimpleNamespace(headers=lambda **_: {}))


def test_capacity_collect_is_metadata_only_inventory(monkeypatch, tmp_path):
    provider(monkeypatch, capacity_metrics)
    monkeypatch.setattr(capacity_metrics, "_list_capacities",
                        lambda _: [{"id": CAPACITY, "displayName": "Capacity", "sku": "F64"}])
    monkeypatch.setattr(capacity_metrics, "_workspaces_by_capacity", lambda _: {})
    payload = json.loads(capacity_metrics.collect(tmp_path).read_text())
    assert payload["capacities"][0]["metadataOnly"]
    assert not {"refreshables", "workloads"} & payload["capacities"][0].keys()
    assert "deepMetricsAvailable" not in payload


def test_semantic_inventory_is_metadata_only(monkeypatch, tmp_path):
    provider(monkeypatch, semantic_models)
    monkeypatch.setattr(semantic_models, "get_scope_workspace_ids", lambda: set())
    monkeypatch.setattr(semantic_models, "_list_datasets_admin",
                        lambda *_: [{"id": ITEM, "workspaceId": WORKSPACE}])
    monkeypatch.setattr(semantic_models, "filter_review_payload", lambda value, _: value)
    payload = json.loads(semantic_models.collect(tmp_path).read_text())
    assert payload["datasets"][0]["id"] == ITEM
    assert not {"refreshes", "refreshErrors", "executionEvidence"} & payload.keys()


def test_pipeline_inventory_never_requests_job_endpoints(monkeypatch, tmp_path):
    provider(monkeypatch, pipelines_notebooks)
    monkeypatch.setattr(pipelines_notebooks, "_load_workspaces", lambda _: [(WORKSPACE, "Workspace")])
    calls = []

    def inventory(url, _):
        calls.append(url)
        assert "/jobs/" not in url
        return [{"id": ITEM, "displayName": "Item"}]

    monkeypatch.setattr(pipelines_notebooks, "paginate_value", inventory)
    payload = json.loads(pipelines_notebooks.collect(tmp_path).read_text())
    assert len(calls) == 2
    assert not {"jobs", "failedJobs", "executionEvidence"} & payload.keys()


def test_local_and_fabric_entrypoints_share_the_planner_and_single_analyzer():
    for path in ("scripts/bash/01_collect.sh", "scripts/powershell/01_collect.ps1"):
        source = (ROOT / path).read_text(encoding="utf-8-sig")
        assert "collectors.far_collection" in source
    for path in ("scripts/bash/02_analyze.sh", "scripts/powershell/02_analyze.ps1"):
        source = (ROOT / path).read_text(encoding="utf-8-sig")
        assert "analyzers.far_review" in source
        assert "analyzers.merge_findings" in source
        assert "analyzers.performance_review" not in source
    for notebook, module in (("01_collect", "collectors.far_collection"), ("02_analyze", "analyzers.far_review")):
        payload = json.loads((ROOT / "fabric" / "notebooks" / (notebook + ".ipynb")).read_text())
        source = "\n".join("".join(cell["source"]) for cell in payload["cells"])
        assert module in source
        assert "analyzers.performance_review" not in source

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Positive scope metadata gates every resource probe, including explicit IDs."""
import json
from unittest.mock import Mock

import pytest

from analyzers._common import load_raw
from collectors import (
    capacity_metrics, capacity_metrics_app, dataflows, deployment_pipelines,
    git_integration, pipelines_notebooks, realtime_intelligence, scanner_api,
    semantic_model_definitions, semantic_models, workspace_inventory,
)
from collectors._common import filter_workspaces_by_scope, load_workspace_inventory
from collectors._http import HttpError
from collectors.workspace_scope import (
    excluded_capacity_ids, excluded_workspace_ids, filter_review_payload,
    is_excluded_capacity, is_excluded_workspace, resolve_workspace_scope,
)
from reports.gold_layer import build_gold
from reports.execution_history import build_execution_evidence

SHARED, PPU, PP3, DELETED, DEDICATED, UNKNOWN = [
    f"aaaaaaaa-0000-4000-8000-{i:012d}" for i in range(1, 7)
]
ROWS = [
    {"id": SHARED, "type": "PersonalGroup", "isOnDedicatedCapacity": False},
    {"id": PPU, "isOnDedicatedCapacity": True, "isOnPremiumPerUserCapacity": True},
    {"id": PP3, "capacityId": "virtual", "isOnDedicatedCapacity": True},
    {"id": DELETED, "name": "FAR-pre-release-test", "state": "Deleted"},
    {"id": DEDICATED, "capacityId": "real", "isOnDedicatedCapacity": True},
    {"id": UNKNOWN, "name": "PPU Pro deleted", "type": "Workspace"},
]
CAPACITIES = [{"id": "virtual", "sku": "PP3"}, {"id": "real", "sku": "F64"}]


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture(autouse=True)
def discovery(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_, **kw: None)
    for key in ("WORKSPACE_IDS", "METRICS_APP_WORKSPACE_ID", "METRICS_APP_DATASET_ID",
                "CAPACITY_METRICS_APP_INSTALLED"):
        monkeypatch.delenv(key, raising=False)
    catalog = Mock(return_value=CAPACITIES)
    monkeypatch.setattr(capacity_metrics, "_list_capacities", catalog)
    return catalog


@pytest.mark.parametrize("sku", ["F2", "F64", "P1", "P3", "A1", "EM1", None, "Unknown"])
def test_real_and_unknown_capacity_skus_are_not_inferred_excluded(sku):
    assert not is_excluded_capacity({"sku": sku})
    assert not is_excluded_workspace({"name": "Pro", "users": [{"license": "Pro"}]})


def test_scope_resolution_caches_catalog_and_persists_positive_identities(tmp_path, discovery):
    resolved = resolve_workspace_scope(ROWS, {}, tmp_path)
    assert [w["id"] for w in filter_workspaces_by_scope(resolved)] == [DEDICATED, UNKNOWN]
    assert excluded_workspace_ids(tmp_path) == {SHARED, PPU, PP3, DELETED}
    assert excluded_capacity_ids(tmp_path) == {"virtual"}
    resolve_workspace_scope(ROWS, {}, tmp_path)
    discovery.assert_called_once()


@pytest.mark.parametrize("historical", [None, "workspace_inventory.json", "scanner.json"])
def test_pp3_to_known_f64_migration_stays_eligible_on_subsequent_runs(tmp_path, discovery, historical):
    old = {"id": PP3, "capacityId": "virtual", "isOnDedicatedCapacity": True}
    resolved = resolve_workspace_scope([old], {}, tmp_path)
    assert resolved[0]["capacitySku"] == "PP3"
    assert excluded_workspace_ids(tmp_path) == {PP3}
    if historical:
        write(tmp_path / historical, {"excludedWorkspaces": resolved, "collectionComplete": False})
    fresh = {
        "id": PP3, "capacityId": "real", "isOnDedicatedCapacity": True,
        "isOnPremiumPerUserCapacity": False,
    }
    for _ in range(2):
        resolved = resolve_workspace_scope([fresh], {}, tmp_path)
        assert resolved[0]["capacitySku"] == "F64"
        assert not is_excluded_workspace(resolved[0])
        assert PP3 not in excluded_workspace_ids(tmp_path)
        assert filter_review_payload({"datasets": [{"workspaceId": PP3}]}, tmp_path)["datasets"]
        persisted = json.loads((tmp_path / "review_scope.json").read_text())["workspaces"][0]
        assert persisted["capacitySku"] == "F64"
        assert persisted["capacityId"] == "real"
    discovery.assert_called_once()


@pytest.mark.parametrize("fresh", [{"id": PP3}, {"id": PP3, "capacityId": None}])
def test_unknown_assignment_does_not_erase_cached_exclusion(tmp_path, fresh):
    resolve_workspace_scope([{"id": PP3, "capacityId": "virtual"}], {}, tmp_path)
    resolve_workspace_scope([fresh], {}, tmp_path)
    assert PP3 in excluded_workspace_ids(tmp_path)


@pytest.mark.parametrize("old_flag", [
    {"isOnPremiumPerUserCapacity": True}, {"isOnDedicatedCapacity": False},
])
@pytest.mark.parametrize("new_capacity", ["real", "unknown"])
def test_new_capacity_assignment_invalidates_old_assignment_flags(tmp_path, old_flag, new_capacity):
    write(tmp_path / "review_scope.json", {
        "workspaces": [{"id": PP3, "capacityId": "virtual", "capacitySku": "PP3", **old_flag}],
        "capacities": CAPACITIES,
    })
    fresh = {"id": PP3, "capacityId": new_capacity}
    for _ in range(2):
        resolve_workspace_scope([fresh], {}, tmp_path)
        assert PP3 not in excluded_workspace_ids(tmp_path)
        persisted = json.loads((tmp_path / "review_scope.json").read_text())["workspaces"][0]
        assert all(flag not in persisted for flag in old_flag)
        assert persisted.get("capacitySku") == ("F64" if new_capacity == "real" else None)


@pytest.mark.parametrize("explicit", [False, True])
def test_inventory_does_not_query_roles_or_items_for_excluded_workspaces(tmp_path, monkeypatch, explicit):
    if explicit:
        monkeypatch.setenv("WORKSPACE_IDS", ",".join(w["id"] for w in ROWS))
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: (ROWS, True))
    users, items = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(workspace_inventory, "_list_users", users)
    monkeypatch.setattr(workspace_inventory, "_list_items", items)
    payload = json.loads(workspace_inventory.collect(tmp_path).read_text())
    assert payload["collectionComplete"]
    assert [call.args[1] for call in users.call_args_list] == [DEDICATED, UNKNOWN]
    assert [call.args[1] for call in items.call_args_list] == [DEDICATED, UNKNOWN]
    assert len(payload["excludedWorkspaces"]) == 4


def test_discovery_error_is_not_success_and_never_probes(tmp_path, monkeypatch, discovery):
    discovery.side_effect = HttpError("Capacity inventory unavailable", status_code=503)
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: (ROWS, True))
    probe = Mock(side_effect=AssertionError("Must not probe after discovery failure"))
    monkeypatch.setattr(workspace_inventory, "_list_items", probe)
    payload = json.loads(workspace_inventory.collect(tmp_path).read_text())
    assert payload["collectionComplete"] is False
    assert payload["collectionErrors"][0]["statusCode"] == 503
    probe.assert_not_called()


def test_scanner_never_submits_excluded_ids_including_explicit_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_IDS", ",".join(w["id"] for w in ROWS))
    monkeypatch.setattr(scanner_api, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda _: [w["id"] for w in ROWS])
    monkeypatch.setattr(scanner_api._http, "collect_workspace_groups", lambda *_, **kw: ROWS)
    scan = Mock(return_value="scan")
    monkeypatch.setattr(scanner_api, "_start_scan", scan)
    monkeypatch.setattr(scanner_api, "_wait_for_scan", Mock())
    monkeypatch.setattr(scanner_api, "_fetch_scan_result", lambda *_: {"workspaces": ROWS})
    payload = json.loads(scanner_api.collect(tmp_path).read_text())
    assert scan.call_args.args[1] == [DEDICATED, UNKNOWN]
    assert [w["id"] for w in payload["workspaces"]] == [DEDICATED, UNKNOWN]


@pytest.mark.parametrize("explicit", [False, True])
def test_semantic_models_use_eligible_workspace_endpoints_not_global_catalog(tmp_path, monkeypatch, explicit):
    if explicit:
        monkeypatch.setenv("WORKSPACE_IDS", ",".join(w["id"] for w in ROWS))
    monkeypatch.setattr(semantic_models, "get_default_provider", Mock())
    calls = []

    def listing(url, *_, **kwargs):
        calls.append(url)
        assert not url.endswith("/admin/datasets")
        if url.endswith("/groups"):
            return ROWS
        assert any(f"/groups/{wid}/datasets" in url for wid in (DEDICATED, UNKNOWN))
        return [{"id": url.split("/")[-2]}]

    monkeypatch.setattr(semantic_models, "collect_value", listing)
    monkeypatch.setattr(semantic_models, "collect_workspace_groups", listing)
    refresh = Mock(return_value=[])
    monkeypatch.setattr(semantic_models, "_refresh_history", refresh)
    payload = json.loads(semantic_models.collect(tmp_path).read_text())
    assert [d["workspaceId"] for d in payload["datasets"]] == [DEDICATED, UNKNOWN]
    assert [c.args[1] for c in refresh.call_args_list] == [DEDICATED, UNKNOWN]


def test_pp3_capacity_receives_no_refreshable_or_workload_requests(tmp_path, monkeypatch):
    write(tmp_path / "workspace_inventory.json", {"workspaces": ROWS})
    monkeypatch.setattr(capacity_metrics, "get_default_provider", Mock())
    refresh, workload = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(capacity_metrics, "_refreshables", refresh)
    monkeypatch.setattr(capacity_metrics, "_workloads", workload)
    payload = json.loads(capacity_metrics.collect(tmp_path).read_text())
    assert [cap["id"] for cap in payload["capacities"]] == ["real"]
    assert payload["excludedCapacities"] == [CAPACITIES[0]]
    assert refresh.call_args.args[1] == workload.call_args.args[1] == "real"
    assert refresh.call_count == workload.call_count == 1


@pytest.mark.parametrize("override", [False, True])
def test_metrics_source_discovery_never_queries_excluded_models(tmp_path, monkeypatch, override):
    if override:
        monkeypatch.setenv("METRICS_APP_WORKSPACE_ID", PP3)
        monkeypatch.setenv("METRICS_APP_DATASET_ID", "metrics")
    calls = []

    def listing(url, *_, **kwargs):
        calls.append(url)
        assert not url.endswith("/admin/datasets")
        if url.endswith("/groups"):
            return ROWS
        assert f"/groups/{DEDICATED}/datasets" in url
        return [{"id": "metrics", "name": "Fabric Capacity Metrics"}]

    monkeypatch.setattr(capacity_metrics_app, "collect_value", listing)
    monkeypatch.setattr(capacity_metrics_app, "collect_workspace_groups", listing)
    monkeypatch.setattr(capacity_metrics_app, "get_default_provider", Mock())
    execute = Mock(return_value={"ok": True, "rowCount": 0, "rows": []})
    monkeypatch.setattr(capacity_metrics_app, "_execute_dax", execute)
    payload = json.loads(capacity_metrics_app.collect(tmp_path).read_text())
    if override:
        execute.assert_not_called()
        assert not payload["datasetLocated"]
    else:
        assert execute.call_count == len(capacity_metrics_app.PROBES)
        assert all(c.args[1] == DEDICATED for c in execute.call_args_list)


def test_legacy_inputs_and_explicit_dataflow_ids_cannot_restore_exclusions(tmp_path, monkeypatch):
    write(tmp_path / "capacity_metrics.json", {"capacities": CAPACITIES})
    write(tmp_path / "workspace_inventory.json", {"workspaces": ROWS})
    assert [w["id"] for w in load_workspace_inventory(tmp_path)] == [DEDICATED, UNKNOWN]
    for module in (pipelines_notebooks, git_integration):
        assert [w[0] for w in module._load_workspaces(tmp_path)] == [DEDICATED, UNKNOWN]
    assert [w["id"] for w in realtime_intelligence._list_workspace_ids(tmp_path)] == [DEDICATED, UNKNOWN]
    monkeypatch.setenv("WORKSPACE_IDS", ",".join([SHARED, PPU, PP3, DELETED]))
    provider = Mock(side_effect=AssertionError("Excluded workspaces must not authenticate"))
    monkeypatch.setattr(dataflows, "get_default_provider", provider)
    payload = json.loads(dataflows.collect(tmp_path).read_text())
    assert payload["collectionComplete"] is True
    assert not payload["workspaces"]
    provider.assert_not_called()


def test_historical_models_are_not_defined_and_gold_does_not_include_exclusions(tmp_path, monkeypatch):
    write(tmp_path / "capacity_metrics.json", {"capacities": CAPACITIES})
    write(tmp_path / "workspace_inventory.json", {"workspaces": ROWS})
    write(tmp_path / "semantic_models.json", {"datasets": [
        {"id": "model", "workspaceId": PP3},
    ], "refreshes": {"model": [{"status": "Failed"}]}})
    before = (tmp_path / "semantic_models.json").read_bytes()
    assert load_raw(tmp_path / "semantic_models.json")["datasets"] == []
    assert load_raw(tmp_path / "semantic_models.json")["refreshes"] == {}
    probe = Mock(side_effect=AssertionError("Excluded model definition queried"))
    monkeypatch.setattr(semantic_model_definitions, "_get_definition", probe)
    semantic_model_definitions.collect(tmp_path)
    probe.assert_not_called()
    tables = build_gold([], tmp_path, run_id="scope", run_timestamp="2026-09-23T00:00:00Z",
                        check_remote=False)
    for records in tables.values():
        assert all(str(row.get("workspace_id") or "").lower() not in {SHARED, PPU, PP3, DELETED}
                   and row.get("capacity_id") != "virtual" for row in records)
    assert (tmp_path / "semantic_models.json").read_bytes() == before


def test_deployment_artifacts_skip_excluded_stage_and_tenant_records_survive(tmp_path, monkeypatch):
    write(tmp_path / "workspace_inventory.json", {"workspaces": ROWS})
    write(tmp_path / "capacity_metrics.json", {"capacities": CAPACITIES})
    monkeypatch.setattr(deployment_pipelines, "get_default_provider", Mock())
    monkeypatch.setattr(deployment_pipelines, "_list_pipelines", lambda _: [{"id": "pipe"}])
    monkeypatch.setattr(deployment_pipelines, "_users", lambda *_: [])
    monkeypatch.setattr(deployment_pipelines, "_stages", lambda *_: [
        {"workspaceId": PP3, "order": 0}, {"workspaceId": DEDICATED, "order": 1},
    ])
    probe = Mock(return_value=[])
    monkeypatch.setattr(deployment_pipelines, "_stage_artifacts", probe)
    deployment_pipelines.collect(tmp_path)
    assert probe.call_count == 1 and probe.call_args.args[2] == 1
    tenant = {"tenantSettings": [{"settingName": "ExportData", "enabled": True}],
              "events": [{"Operation": "UpdateTenantSetting"}]}
    assert filter_review_payload(tenant, tmp_path) == tenant


def test_malformed_scope_metadata_fails_closed_without_claiming_empty_scope(tmp_path):
    (tmp_path / "review_scope.json").write_text("{", encoding="utf-8")
    with pytest.raises(HttpError, match="Scope metadata"):
        excluded_workspace_ids(tmp_path)


def test_historical_unsupported_metrics_source_cannot_supply_analysis_signals(tmp_path):
    write(tmp_path / "workspace_inventory.json", {"workspaces": ROWS})
    write(tmp_path / "capacity_metrics.json", {"capacities": CAPACITIES})
    payload = {"datasetLocated": True, "dataset": {"workspaceId": PP3, "datasetId": "metrics"},
               "queries": {"usage": {"ok": True, "rows": [{"CU": 123}]}}}
    filtered = filter_review_payload(payload, tmp_path)
    assert filtered["skipped"] and not filtered["datasetLocated"]
    assert filtered["queries"] == {} and payload["queries"]


def test_execution_evidence_scope_failure_is_an_explicit_gap_not_unfiltered_data(tmp_path):
    (tmp_path / "review_scope.json").write_text("{", encoding="utf-8")
    write(tmp_path / "semantic_models.json", {
        "datasets": [{"id": "model", "workspaceId": PP3}],
        "refreshes": {"model": [{"status": "Completed"}]},
    })
    result = build_execution_evidence(tmp_path, "run", "2026-09-23T00:00:00Z")
    assert result["gold_item_executions"] == []
    coverage = next(row for row in result["gold_execution_coverage"] if row["item_type"] == "SemanticModel")
    assert coverage["item_id"] is None
    assert coverage["collection_status"] != "empty"
    assert "source_scope_unavailable" in coverage["notice"]


@pytest.mark.parametrize("module", [semantic_models, capacity_metrics_app, scanner_api])
def test_required_scope_discovery_failure_stops_model_and_scan_requests(tmp_path, monkeypatch, discovery, module):
    discovery.side_effect = HttpError("Capacity lookup failed", status_code=503)
    monkeypatch.setattr(module, "get_default_provider", Mock())
    if module is scanner_api:
        monkeypatch.setattr(module, "_modified_workspaces", lambda _: [w["id"] for w in ROWS])
        monkeypatch.setattr(module._http, "collect_workspace_groups", lambda *_, **kw: ROWS)
        probe_name = "_start_scan"
    else:
        monkeypatch.setattr(module, "collect_value", lambda *_, **kw: ROWS)
        monkeypatch.setattr(module, "collect_workspace_groups", lambda *_, **kw: ROWS)
        probe_name = "_refresh_history" if module is semantic_models else "_execute_dax"
    probe = Mock(side_effect=AssertionError("No resource probe after discovery failure"))
    monkeypatch.setattr(module, probe_name, probe)
    payload = json.loads(module.collect(tmp_path).read_text())
    assert payload["collectionComplete"] is False
    assert payload["collectionErrors"]
    assert {SHARED, PPU, DELETED} <= excluded_workspace_ids(tmp_path)
    probe.assert_not_called()

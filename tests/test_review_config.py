# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import base64
import json
from types import SimpleNamespace
import sys

import pytest

from orchestration.config_deployment import SETTINGS_SCHEMA, ensure_library, library_definition, sql_connection
from orchestration.review_config import (
    ROOT, activate, capture, fabric_configuration, local_configuration, read_snapshot, read_yaml,
    targeting_source, validate_configuration,
)

WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CAPACITY = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("FAR_CONFIG_SNAPSHOT", "")
    for name in ("FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    return local_configuration()


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, 0.5])
def test_invalid_integer_threshold_is_rejected(config, value):
    config["thresholds"]["architecture"]["monolith_max_items"] = value
    with pytest.raises(ValueError):
        validate_configuration(config)


def test_removed_optimization_and_universal_heuristic_thresholds_are_rejected(config):
    config["thresholds"]["architecture"]["layer_naming_min_ratio"] = 1.1
    with pytest.raises(ValueError, match="Unknown or missing threshold"):
        validate_configuration(config)
    del config["thresholds"]["architecture"]["layer_naming_min_ratio"]
    config["thresholds"]["performance"] = {"model_size_warn_mb": 10000}
    with pytest.raises(ValueError, match="Threshold groups"):
        validate_configuration(config)


def test_threshold_environment_variables_are_ignored(config, monkeypatch, tmp_path):
    default = config["thresholds"]["architecture"]["monolith_max_items"]
    monkeypatch.setenv("ARCH_MONOLITH_THRESHOLD", "not-a-number")
    config = local_configuration()
    assert config["thresholds"]["architecture"]["monolith_max_items"] == default
    assert config["threshold_environment"] == {}
    path = capture(tmp_path, config)
    assert read_snapshot(path)["configuration"]["threshold_environment"] == {}


def test_threshold_environment_overrides_in_a_snapshot_are_rejected(config):
    config["threshold_environment"] = {"ARCH_MONOLITH_THRESHOLD": "12"}
    with pytest.raises(ValueError, match="not supported"):
        validate_configuration(config)


def test_snapshot_is_preserved_on_retry_and_checked_for_tampering(config, tmp_path):
    path = capture(tmp_path, config)
    original = path.read_bytes()
    config["thresholds"]["architecture"]["monolith_max_items"] = 80
    assert capture(tmp_path, config) == path
    assert path.read_bytes() == original
    value = json.loads(original)
    value["configuration"]["thresholds"]["architecture"]["monolith_max_items"] = 81
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        read_snapshot(path)


def test_explicit_new_local_review_replaces_snapshot(config, tmp_path):
    path = capture(tmp_path, config)
    config["thresholds"]["architecture"]["monolith_max_items"] = 80
    capture(tmp_path, config, replace=True)
    assert read_snapshot(path)["configuration"]["thresholds"]["architecture"]["monolith_max_items"] == 80


@pytest.mark.parametrize("update", [
    {"id": "invalid"},
    {"id": WORKSPACE, "profile": {"environment": "prod"}},
    {"id": WORKSPACE, "profile": {"thresholds": {}}},
    {"id": WORKSPACE, "rule_overrides": {"BPA-001": {"applicability": "applicable", "reason": "test"}}},
])
def test_unsupported_policy_is_not_silently_ignored(config, update):
    config["profiles"]["workspaces"] = [update]
    with pytest.raises(ValueError):
        validate_configuration(config)


def test_case_insensitive_duplicate_profile_ids_are_rejected(config):
    config["profiles"]["workspaces"] = [{"id": WORKSPACE}, {"id": WORKSPACE.upper()}]
    with pytest.raises(ValueError, match="Duplicate"):
        validate_configuration(config)


@pytest.mark.parametrize("update", [
    {"enabled": "false"},
    {"enabled": True},
    {"max_age_hours": 0},
    {"workspace_id": "not-a-guid"},
    {"password": "not-allowed"},
])
def test_source_validation(config, update):
    config["sources"]["fuam"].update(update)
    with pytest.raises(ValueError):
        validate_configuration(config)


class Library:
    def __init__(self, values):
        self.values = values

    def getVariable(self, name):
        return self.values[name]


def libraries(config):
    policy = read_yaml(ROOT / "config" / "policy.yaml")
    return {
        "thresholds": Library({
            f"{group}__{key}": value
            for group, entries in {**config["thresholds"], **policy}.items() for key, value in entries.items()
        }),
        "connections": Library({
            f"{group}__{key}": value
            for group, entries in config["sources"].items() for key, value in entries.items()
        }),
    }


def test_fabric_libraries_have_local_parity(config):
    values = libraries(config)
    actual = fabric_configuration(values.__getitem__, "thresholds", "connections")
    validate_configuration(actual)
    assert actual == {**config, "origin": "fabric"}
    assert actual["profiles"] == {"workspaces": [], "capacities": []}


def test_targeting_reads_current_connection_library(config):
    config["sources"]["fuam"].update(enabled=True, workspace_id=WORKSPACE, lakehouse_id=CAPACITY)
    values = libraries(config)
    assert targeting_source(values.__getitem__, "connections") == {
        "FUAM_WORKSPACE_ID": WORKSPACE, "FUAM_LAKEHOUSE_ID": CAPACITY,
    }
    values["connections"].values["fuam__enabled"] = False
    with pytest.raises(ValueError, match="requires an enabled"):
        targeting_source(values.__getitem__, "connections")


def test_library_definition_matches_fabric_definition_contract(config):
    definition = library_definition(config["thresholds"])
    assert "format" not in definition
    assert [part["path"] for part in definition["parts"]] == ["variables.json", "settings.json"]
    assert json.loads(base64.b64decode(definition["parts"][0]["payload"]))["$schema"].endswith("/variables/1.0.0/schema.json")
    assert json.loads(base64.b64decode(definition["parts"][1]["payload"]))["valueSetsOrder"] == []
    variables = json.loads(base64.b64decode(definition["parts"][0]["payload"]))["variables"]
    assert next(item for item in variables if item["name"] == "architecture__monolith_max_items") == {
        "name": "architecture__monolith_max_items", "type": "Integer", "value": 50,
    }
    assert len(variables) == 24 == sum(len(group) for group in config["thresholds"].values())
    assert next(item for item in variables if item["name"] == "operations__window_days")["type"] == "Integer"
    assert next(item for item in variables if item["name"] == "architecture__description_coverage_min_ratio") == {
        "name": "architecture__description_coverage_min_ratio", "type": "Number", "value": 0.5,
    }
    assert next(item for item in variables if item["name"] == "governance__naming_pattern") == {
        "name": "governance__naming_pattern", "type": "String", "value": "",
    }
    assert next(item for item in variables if item["name"] == "security__gateway_min_version") == {
        "name": "security__gateway_min_version", "type": "String", "value": "",
    }
    assert json.loads(base64.b64decode(definition["parts"][1]["payload"])) == {
        "$schema": SETTINGS_SCHEMA, "valueSetsOrder": [],
    }


@pytest.mark.parametrize("group, key, value", [
    ("architecture", "description_coverage_min_ratio", float("nan")),
    ("architecture", "description_coverage_min_ratio", 1.1),
    ("governance", "naming_coverage_min_ratio", True),
    ("governance", "naming_pattern", "["),
    ("security", "gateway_min_version", "unsupported"),
])
def test_global_advisory_thresholds_are_validated(config, group, key, value):
    config["thresholds"][group][key] = value
    with pytest.raises(ValueError):
        validate_configuration(config)


def test_restored_thresholds_round_trip_through_fabric_configuration(config):
    config["thresholds"]["architecture"]["description_coverage_min_ratio"] = 0.75
    config["thresholds"]["governance"]["naming_pattern"] = "engineering-.*"
    config["thresholds"]["security"]["gateway_min_version"] = "3000.10.1"
    values = libraries(config)
    actual = fabric_configuration(values.__getitem__, "thresholds", "connections")
    validate_configuration(actual)
    assert actual["thresholds"] == config["thresholds"]


def test_existing_library_is_validated_and_no_customer_value_is_changed(config):
    definition = library_definition(config["thresholds"])
    variables = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    variables["variables"][0]["value"] = 99
    from orchestration.fabric_api import definition_part
    definition["parts"][0] = definition_part("variables.json", variables)
    requests = []
    class Client:
        def list_items(self, workspace, item_type):
            assert item_type == "VariableLibrary"
            return [{"type": "VariableLibrary", "displayName": "Thresholds", "id": CAPACITY}]
        def request(self, method, path, **kwargs):
            requests.append((method, path))
            return {"definition": definition}
        def complete(self, response):
            return response
    assert ensure_library(Client(), WORKSPACE, "Thresholds", config["thresholds"]) == CAPACITY
    assert len(requests) == 1 and requests[0][1].endswith("/getDefinition")
    variables["variables"][0]["type"] = "String"
    definition["parts"][0] = definition_part("variables.json", variables)
    with pytest.raises(ValueError, match="Wrong type: architecture__monolith_max_items"):
        ensure_library(Client(), WORKSPACE, "Thresholds", config["thresholds"])
    variables["variables"][0]["type"] = "Integer"
    variables["variables"].append({"name": "performance__model_size_warn_mb", "type": "Integer", "value": 2048})
    definition["parts"][0] = definition_part("variables.json", variables)
    # A variable this release does not use is ignored, not an error, and the library is left untouched.
    requests.clear()
    assert ensure_library(Client(), WORKSPACE, "Thresholds", config["thresholds"]) == CAPACITY
    assert len(requests) == 1 and requests[0][1].endswith("/getDefinition")


def test_a_variable_added_by_a_newer_version_is_created_with_its_default_and_values_are_kept(config, capsys):
    definition = library_definition(config["thresholds"])
    variables = json.loads(base64.b64decode(definition["parts"][0]["payload"]))
    removed = [v for v in variables["variables"] if v["name"] in {"operations__min_capacity_admins", "operations__workspace_cu_max_ratio"}]
    variables["variables"] = [v for v in variables["variables"] if v not in removed]
    variables["variables"][0]["value"] = 99
    from orchestration.fabric_api import definition_part
    definition["parts"][0] = definition_part("variables.json", variables)
    requests = []

    class Client:
        def list_items(self, workspace, item_type):
            return [{"type": "VariableLibrary", "displayName": "Thresholds", "id": CAPACITY}]

        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs))
            return {"definition": definition}

        def complete(self, response):
            return response
    assert ensure_library(Client(), WORKSPACE, "Thresholds", config["thresholds"]) == CAPACITY
    assert [(m, p.rsplit("/", 1)[-1]) for m, p, _ in requests] == [("POST", "getDefinition"), ("POST", "updateDefinition")]
    sent = requests[1][2]["json_body"]["definition"]["parts"]
    assert [part["path"] for part in sent] == [part["path"] for part in definition["parts"]]
    merged = json.loads(base64.b64decode(sent[0]["payload"]))["variables"]
    by_name = {v["name"]: v for v in merged}
    assert by_name["architecture__monolith_max_items"]["value"] == 99
    assert [by_name[v["name"]] for v in removed] == removed
    assert len(by_name) == len(merged) == len(variables["variables"]) + len(removed)
    assert "operations__min_capacity_admins" in capsys.readouterr().out


def test_a_complete_library_is_not_rewritten(config):
    definition = library_definition(config["thresholds"])
    calls = []

    class Client:
        def list_items(self, workspace, item_type):
            return [{"type": "VariableLibrary", "displayName": "Thresholds", "id": CAPACITY}]

        def request(self, method, path, **kwargs):
            calls.append(path)
            return {"definition": definition}

        def complete(self, response):
            return response
    ensure_library(Client(), WORKSPACE, "Thresholds", config["thresholds"])
    assert not any(path.endswith("updateDefinition") for path in calls)


def test_sql_token_is_packed_and_tls_certificate_validation_is_enabled(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "mssql_python", SimpleNamespace(
        connect=lambda *args, **kwargs: calls.append((args, kwargs)),
    ))
    sql_connection("test.datawarehouse.fabric.microsoft.com", "FAR_Configuration", "token")
    assert "Encrypt=yes;TrustServerCertificate=no" in calls[0][0][0]
    assert calls[0][1]["attrs_before"][1256] == b"\x0a\x00\x00\x00" + "token".encode("utf-16-le")
    with pytest.raises(ValueError, match="hostname"):
        sql_connection("example.org", "FAR_Configuration", "token")
    assert len(calls) == 1


def test_artifact_provisioning_uses_verified_create_payloads(config):
    from orchestration import config_deployment
    calls = []
    class Client:
        def list_items(self, workspace, item_type):
            assert item_type == "VariableLibrary"
            return []
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs["json_body"]))
            return {"id": CAPACITY}
        def complete(self, response):
            return response
    result = config_deployment.provision_configuration(Client(), WORKSPACE, "FAR")
    assert [path for _, path, _ in calls] == [f"/workspaces/{WORKSPACE}/variableLibraries"] * 3
    assert {body["displayName"] for _, _, body in calls} == {
        "FAR_Thresholds", "FAR_Connections", "FAR_Environment",
    }
    assert result == {
        "FAR_THRESHOLDS_LIBRARY": "FAR_Thresholds", "FAR_CONNECTIONS_LIBRARY": "FAR_Connections",
        "FAR_ENVIRONMENT_LIBRARY": "FAR_Environment",
    }
    thresholds = next(body for _, _, body in calls if body["displayName"] == "FAR_Thresholds")
    variables = json.loads(base64.b64decode(thresholds["definition"]["parts"][0]["payload"]))["variables"]
    assert "scope__production_workspace_ids" in {item["name"] for item in variables}


def test_notebook_bindings_and_persistence_precede_exit():
    for name in ("01_collect", "02_analyze", "03_report", "04_gold", "06_targeted_review_setup"):
        path = ROOT / "fabric" / "notebooks" / f"{name}.ipynb"
        notebook = json.loads(path.read_text(encoding="utf-8"))
        parameter_cells = [cell for cell in notebook["cells"] if "parameters" in cell.get("metadata", {}).get("tags", [])]
        assert len(parameter_cells) == 1
        parameters = "".join(parameter_cells[0]["source"])
        assert "FAR_ENABLED" not in parameters
        assert 'FAR_CONNECTIONS_LIBRARY = "FAR_Connections"' in parameters
        if name == "01_collect":
            assert 'FAR_ENVIRONMENT_LIBRARY = "FAR_Environment"' in parameters
    gold = json.loads((ROOT / "fabric" / "notebooks" / "04_gold.ipynb").read_text(encoding="utf-8"))
    code = "".join(gold["cells"][6]["source"])
    assert code.index("persist(spark, Path(OUT_DIR), RUN_ID)") < code.index("notebookutils.notebook.exit")


def test_far_targeting_uses_library_and_removes_source_pipeline_parameters():
    from orchestration.deployment import build_parent_pipeline, runtime_notebook
    child = {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}}
    parent = build_parent_pipeline(
        child, workspace_id=WORKSPACE, pipeline_id=CAPACITY, notebook_id=WORKSPACE,
        notification_notebook_id=CAPACITY, connections_library="FAR_Connections",
    )
    assert not {"FUAM_WORKSPACE_ID", "FUAM_LAKEHOUSE_ID"} & parent["properties"]["parameters"].keys()
    notebook = runtime_notebook(
        "bundle.zip", WORKSPACE, CAPACITY, child_pipeline_id=CAPACITY, connections_library="FAR_Connections",
    )
    parameters = "".join(notebook["cells"][0]["source"])
    assert "FUAM_WORKSPACE_ID" not in parameters and "FUAM_LAKEHOUSE_ID" not in parameters
    code = "".join(notebook["cells"][1]["source"])
    assert "config.update(targeting_source(" in code
    assert code.index("targeting_source(") < code.index("result = run_selection(config)")


def test_gold_uses_same_frozen_workspace_profile(config, monkeypatch, tmp_path):
    for name in ("THRESHOLDS_CONFIG", "FAR_CONFIG_SNAPSHOT"):
        monkeypatch.delenv(name, raising=False)
    config["profiles"] = {
        "workspaces": [{"id": WORKSPACE, "profile": {"environment": "production"}}],
        "capacities": [],
    }
    path = capture(tmp_path, config)
    activate(path)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": WORKSPACE, "name": "dev-bi", "items": []}],
        "workspaceListComplete": True,
    }), encoding="utf-8")
    from reports.gold_layer import build_gold
    tables = build_gold([], raw, run_id="test", run_timestamp="2026-01-20T12:00:00Z", check_remote=False)
    assert tables["gold_workspaces"][0]["environment"] == "production"

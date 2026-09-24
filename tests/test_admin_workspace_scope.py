# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Admin monitoring is an operational source, never a reviewed workspace."""
import json
from unittest.mock import Mock

import pytest

from analyzers._common import load_raw
from collectors import (
    capacity_metrics_app, dataflows, git_integration, lakehouse_warehouse,
    pipelines_notebooks, realtime_intelligence, scanner_api, semantic_models, workspace_inventory,
)
from collectors._common import filter_workspaces_by_scope, load_complete_raw, load_workspace_inventory
from collectors.dax_analysis import build_analysis
from collectors.workspace_scope import excluded_workspace_ids, filter_review_payload
from reports.gold_layer import build_gold

ADMIN = "1e7cd593-5572-472c-8108-96ed8a0adcc8"
NORMAL = "aaaaaaaa-0000-4000-8000-000000000001"
PERSONAL = "aaaaaaaa-0000-4000-8000-000000000002"
ROWS = [
    {"id": ADMIN, "name": "Renamed system workspace", "type": "AdminWorkspace"},
    {"id": NORMAL, "name": "Admin monitoring", "type": "Workspace"},
    {"id": PERSONAL, "name": "My workspace", "type": "PersonalGroup"},
]


@pytest.fixture(autouse=True)
def no_scope(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_shared_policy_is_type_based_and_preserves_personal_policy(monkeypatch):
    assert filter_workspaces_by_scope(ROWS) == ROWS[1:]
    assert filter_workspaces_by_scope([{"id": ADMIN, "name": "Admin monitoring"}])
    assert filter_workspaces_by_scope([{"id": NORMAL, "type": " adminworkspace "}]) == []
    monkeypatch.setenv("WORKSPACE_IDS", ADMIN.upper())
    assert filter_workspaces_by_scope(ROWS) == []


@pytest.mark.parametrize("scoped", [False, True])
def test_inventory_never_probes_admin_items_or_roles(tmp_path, monkeypatch, scoped):
    if scoped:
        monkeypatch.setenv("WORKSPACE_IDS", f"{ADMIN},{NORMAL}")
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: (ROWS, True))
    users, items = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(workspace_inventory, "_list_users", users)
    monkeypatch.setattr(workspace_inventory, "_list_items", items)
    payload = json.loads(workspace_inventory.collect(tmp_path).read_text())
    expected = [NORMAL] if scoped else [NORMAL, PERSONAL]
    assert [w["id"] for w in payload["workspaces"]] == expected
    assert [call.args[1] for call in items.call_args_list] == expected
    assert [call.args[1] for call in users.call_args_list] == expected
    assert payload["collectionComplete"] is True
    assert payload["collectionErrors"] == []
    assert payload["excludedWorkspaces"] == [{"id": ADMIN, "type": "AdminWorkspace"}]
    assert excluded_workspace_ids(tmp_path) == {ADMIN}


@pytest.mark.parametrize("source", ["scanner.json", "workspace_inventory.json"])
def test_all_native_entrypoints_exclude_observed_admin_even_from_legacy_inventory(tmp_path, source):
    write_json(tmp_path / source, {"workspaces": ROWS[:2]})
    assert [w["id"] for w in load_workspace_inventory(tmp_path)] == [NORMAL]
    assert pipelines_notebooks._load_workspaces(tmp_path) == [(NORMAL, "Admin monitoring")]
    assert git_integration._load_workspaces(tmp_path) == [(NORMAL, "Admin monitoring")]
    assert lakehouse_warehouse._load_workspaces(tmp_path) == [(NORMAL, "Admin monitoring")]
    assert [w["id"] for w in realtime_intelligence._list_workspace_ids(tmp_path)] == [NORMAL]


def test_dataflow_explicit_scope_does_not_reintroduce_excluded_id(tmp_path, monkeypatch):
    write_json(tmp_path / "workspace_inventory.json", {"workspaces": ROWS[:2]})
    monkeypatch.setenv("WORKSPACE_IDS", ADMIN)
    provider = Mock(side_effect=AssertionError("Excluded scope must not authenticate"))
    monkeypatch.setattr(dataflows, "get_default_provider", provider)
    result = json.loads(dataflows.collect(tmp_path).read_text())
    assert result["workspaces"] == result["dataflows"] == []
    assert result["collectionComplete"] is True
    provider.assert_not_called()


def test_scanner_resolves_type_before_submitting_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_api, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda _: [ADMIN, NORMAL])
    listing = Mock(return_value=ROWS[:2])
    monkeypatch.setattr(scanner_api._http, "collect_workspace_groups", listing)
    scan = Mock(return_value="scan")
    monkeypatch.setattr(scanner_api, "_start_scan", scan)
    monkeypatch.setattr(scanner_api, "_wait_for_scan", Mock())
    monkeypatch.setattr(scanner_api, "_fetch_scan_result", lambda *_: {"workspaces": [ROWS[1]]})
    result = json.loads(scanner_api.collect(tmp_path).read_text())
    assert scan.call_args.args[1] == [NORMAL]
    assert result["_meta"]["workspaces_eligible"] == 1
    assert result["excludedWorkspaces"] == [{"id": ADMIN, "type": "AdminWorkspace"}]
    assert listing.call_args.args[0].endswith("/admin/groups")


def test_scanner_explicit_admin_at_workspace_5001_is_never_submitted(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_IDS", ADMIN)
    monkeypatch.setattr(scanner_api, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda _: [ADMIN])
    get = Mock(side_effect=[
        {"value": [{"id": f"normal-{i}", "type": "Workspace"} for i in range(5000)]},
        {"value": [ROWS[0]]},
    ])
    monkeypatch.setattr(scanner_api._http, "get_json", get)
    scan = Mock(side_effect=AssertionError("Must not scan AdminWorkspace"))
    monkeypatch.setattr(scanner_api, "_start_scan", scan)
    result = json.loads(scanner_api.collect(tmp_path).read_text())
    assert get.call_args.kwargs["params"] == {"$top": 5000, "$skip": 5000}
    assert result["collectionComplete"] is True
    assert result["_meta"]["workspaces_eligible"] == 0
    assert result["excludedWorkspaces"] == [{"id": ADMIN, "type": "AdminWorkspace"}]
    scan.assert_not_called()


def test_scanner_missing_policy_identity_is_incomplete_not_unknown_type(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_api, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda _: [ADMIN])
    monkeypatch.setattr(scanner_api._http, "get_json", lambda *_, **kw: {"value": []})
    scan = Mock()
    monkeypatch.setattr(scanner_api, "_start_scan", scan)
    result = json.loads(scanner_api.collect(tmp_path).read_text())
    assert result["collectionComplete"] is False
    scan.assert_not_called()


@pytest.mark.parametrize("admin_mode", [False, True])
def test_semantic_catalog_excludes_admin_before_refresh_or_definitions(tmp_path, monkeypatch, admin_mode):
    monkeypatch.setattr(semantic_models, "get_default_provider", Mock())
    calls = []

    def listing(url, headers, **kwargs):
        calls.append(url)
        if url.endswith("/admin/datasets"):
            return [{"id": wid, "workspaceId": wid} for wid in (ADMIN, NORMAL)]
        if url.endswith("/groups"):
            return ROWS[:2]
        assert f"/groups/{NORMAL}/datasets" in url
        return [{"id": NORMAL}]

    monkeypatch.setattr(semantic_models, "collect_value", listing)
    monkeypatch.setattr(semantic_models, "collect_workspace_groups", listing)
    refresh = Mock(return_value=[])
    monkeypatch.setattr(semantic_models, "_refresh_history", refresh)
    if not admin_mode:
        monkeypatch.setenv("WORKSPACE_IDS", f"{ADMIN},{NORMAL}")
    result = json.loads(semantic_models.collect(tmp_path).read_text())
    assert [d["id"] for d in result["datasets"]] == [NORMAL]
    assert refresh.call_count == 1
    assert refresh.call_args.args[1] == NORMAL
    assert all(f"/groups/{ADMIN}/" not in url for url in calls)


def test_review_readers_use_partial_identity_and_leave_raw_unchanged(tmp_path):
    write_json(tmp_path / "scanner.json", {"workspaces": [{"id": ADMIN}]})
    write_json(tmp_path / "workspace_inventory.json", {
        "workspaces": ROWS, "collectionComplete": False, "workspaceListComplete": True,
    })
    target = tmp_path / "semantic_models.json"
    write_json(target, {"datasets": [
        {"id": "system", "workspaceId": ADMIN.upper()}, {"id": "normal", "workspaceId": NORMAL},
    ]})
    before = target.read_bytes()
    assert [w["id"] for w in load_workspace_inventory(tmp_path)] == []
    assert load_complete_raw(target)["datasets"] == [{"id": "normal", "workspaceId": NORMAL}]
    assert load_raw(target)["datasets"] == [{"id": "normal", "workspaceId": NORMAL}]
    assert target.read_bytes() == before


@pytest.mark.parametrize("source", ["scanner.json", "workspace_inventory.json"])
def test_gold_excludes_admin_native_rows_and_coverage_without_name_blacklist(tmp_path, source):
    write_json(tmp_path / source, {"workspaces": ROWS[:2]})
    models = [
        {"id": f"model-{i}", "name": "Shared model", "workspaceId": row["id"],
         "parts": [{"path": "definition/tables/Metrics.tmdl",
                    "text": "table Metrics\n    column Flag = 1\n"}]}
        for i, row in enumerate(ROWS[:2])
    ]
    write_json(tmp_path / "semantic_models.json", {"datasets": models})
    write_json(tmp_path / "semantic_model_definitions.json", {"models": models})
    write_json(tmp_path / "dax_analysis.json", build_analysis({"models": models}))
    write_json(tmp_path / "pipelines_notebooks.json", {
        "pipelines": [], "notebooks": [], "executionEvidence": [
            {"workspaceId": ADMIN, "itemType": "DataPipeline", "collectionStatus": "error",
             "noticeCode": "WorkspaceTypeNotSupported"},
        ],
    })
    write_json(tmp_path / "dataflows.json", {
        "schemaVersion": 1, "collectionComplete": True, "workspaces": ROWS[:2],
        "dataflows": [
            {"id": f"df-{i}", "workspaceId": row["id"], "definitionStatus": "inspected", "queries": []}
            for i, row in enumerate(ROWS[:2])
        ],
    })
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    tables = build_gold([], tmp_path, run_id="run", run_timestamp="2026-09-23T08:00:00Z", check_remote=False)
    for table, records in tables.items():
        assert all(str(row.get("workspace_id", "")).lower() != ADMIN for row in records), table
    assert [row["workspace_name"] for row in tables["gold_workspaces"]] == ["Admin monitoring"]
    assert [row["model_id"] for row in tables["gold_dax_object_coverage"]] == ["model-1"]
    assert all(row["workspace_name"] == "Admin monitoring" for row in tables["gold_dax_objects"])
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_operational_metrics_source_in_admin_workspace_is_still_discoverable(monkeypatch):
    monkeypatch.delenv("METRICS_APP_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("METRICS_APP_DATASET_ID", raising=False)
    monkeypatch.setattr(capacity_metrics_app, "collect_workspace_groups", lambda *_, **kw: ROWS[:1])
    monkeypatch.setattr(capacity_metrics_app, "collect_value", lambda url, *_, **kwargs:
                       ROWS[:1] if url.endswith("/groups") else [
                           {"id": "metrics", "workspaceId": ADMIN, "name": "Fabric Capacity Metrics"},
                       ])
    source = capacity_metrics_app._find_dataset({})
    assert source["workspaceId"] == ADMIN


def test_reference_filter_preserves_tenant_settings_and_operational_source(tmp_path):
    write_json(tmp_path / "workspace_inventory.json", {"workspaces": ROWS[:2]})
    payload = {
        "events": [{"WorkspaceId": ADMIN}, {"Operation": "UpdateTenantSetting"}],
        "stages": [{"workspaceId": ADMIN}, {"workspaceId": NORMAL}],
        "assignedWorkspaceIds": [ADMIN, NORMAL],
        "dataset": {"workspaceId": ADMIN, "datasetId": "operational-source"},
    }
    filtered = filter_review_payload(payload, tmp_path)
    assert filtered["events"] == [{"Operation": "UpdateTenantSetting"}]
    assert filtered["stages"] == [{"workspaceId": NORMAL}]
    assert filtered["assignedWorkspaceIds"] == [NORMAL]
    assert filtered["dataset"] == payload["dataset"]

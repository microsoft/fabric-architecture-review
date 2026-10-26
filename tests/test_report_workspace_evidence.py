# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Report consumers share normalized, scoped workspace evidence (synthetic only)."""
import json

import pytest

from reports.gold_layer import build_gold
from reports.powerbi.schema import GOLD_TABLES_BY_NAME
from reports.render_report import _environment_overview


WA = "aaaaaaaa-0000-4000-8000-000000000001"
WB = "bbbbbbbb-0000-4000-8000-000000000002"
NOW = "2026-09-24T12:00:00Z"


def write(path, filename, payload):
    if filename == "workspace_inventory.json":
        payload.setdefault("workspaceListComplete", True)
    (path / filename).write_text(json.dumps(payload), encoding="utf-8")


def gold(path):
    return build_gold([], path, run_id="synthetic", run_timestamp=NOW, check_remote=False)


@pytest.fixture
def mixed(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    write(tmp_path, "scanner.json", {"workspaces": [{
        "id": WA.upper(), "name": "Shared", "capacityId": "cap",
        "datasets": [{"id": "MODEL", "name": "Detailed model", "tables": [{"name": "T"}]}],
        "reports": [{"id": "REPORT", "name": "Detailed report", "datasetId": "model"}],
        "Notebook": [{"id": "NOTEBOOK", "name": "Detailed notebook"}],
        "notebooks": [{"id": "notebook", "name": "Detailed notebook"}],
        "DataPipeline": [{"id": "PIPELINE", "name": "Detailed pipeline"}],
        "Lakehouse": [{"id": "LAKEHOUSE", "name": "Detailed lakehouse"}],
        "users": [
            {"graphId": "PRINCIPAL", "principalType": "User", "groupUserAccessRight": "Admin"},
            {"graphId": "principal", "principalType": "User", "groupUserAccessRight": "Admin"},
        ],
    }]})
    write(tmp_path, "workspace_inventory.json", {"workspaces": [{
        "id": WA, "name": "Shared", "capacityId": "cap",
        "items": [
            {"id": "model", "displayName": "Basic model", "type": "SemanticModel"},
            {"id": "report", "displayName": "Basic report", "type": "Report"},
            {"id": "notebook", "displayName": "Basic notebook", "type": "Notebook"},
            {"id": "pipeline", "displayName": "Basic pipeline", "type": "DataPipeline"},
            {"id": "lakehouse", "displayName": "Basic lakehouse", "type": "Lakehouse"},
            {"id": "warehouse", "displayName": "Warehouse", "type": "Warehouse"},
        ],
        "itemsCollectionStatus": "collected",
        "users": None, "usersCollectionStatus": "unavailable",
    }, {
        "id": WB, "name": "REST only", "capacityId": "cap",
        "items": [{"id": "rest-notebook", "displayName": "REST notebook", "type": "Notebook"}],
        "users": [], "itemsCollectionStatus": "collected", "usersCollectionStatus": "collected",
    }]})
    return tmp_path


def test_mixed_report_counts_and_raw_stability(mixed):
    before = {file.name: file.read_bytes() for file in mixed.iterdir()}
    overview = _environment_overview(mixed, [])
    assert f"| {WA} | Shared | cap | 6 |" in overview
    assert f"| {WB} | REST only | cap | 1 |" in overview
    assert "Architectural inventory" in overview
    assert "DAX" not in overview
    assert before == {file.name: file.read_bytes() for file in mixed.iterdir()}


def test_gold_merged_counts_graph_and_role_dedup(mixed):
    before = {file.name: file.read_bytes() for file in mixed.iterdir()}
    tables = gold(mixed)
    assert before == {file.name: file.read_bytes() for file in mixed.iterdir()}
    rows = {row["workspace_id"].lower(): row for row in tables["gold_workspaces"]}
    assert rows[WA]["item_count"] == 6 and rows[WA]["admin_count"] == 1
    assert rows[WB]["item_count"] == 1 and rows[WB]["admin_count"] == 0
    risk = next(row for row in tables["gold_workspace_risk"] if row["workspace_id"] == WA)
    assert risk["notebook_count"] == risk["pipeline_count"] == risk["semantic_model_count"] == 1
    nodes = [row for row in tables["gold_graph_nodes"] if row["workspace_id"] and row["node_type"] != "Workspace"]
    assert len(nodes) == 7
    assert {row["node_type"] for row in nodes} == {
        "SemanticModel", "Report", "Notebook", "DataPipeline", "Lakehouse", "Warehouse",
    }
    assert next(row for row in nodes if row["node_type"] == "SemanticModel")["node_name"] == "Detailed model"
    assert len([edge for edge in tables["gold_graph_edges"] if edge["relationship"] == "feeds"]) == 1
    assert len(tables["gold_capacity_items"]) == 7


@pytest.mark.parametrize("missing", ["absent", "unavailable"])
def test_missing_components_are_unknown_not_empty(tmp_path, missing):
    workspace = {"id": WA, "name": "Unknown"}
    if missing == "unavailable":
        workspace.update(items=None, users=None, itemsCollectionStatus="unavailable",
                         usersCollectionStatus="unavailable")
    write(tmp_path, "workspace_inventory.json", {"workspaces": [workspace]})
    tables = gold(tmp_path)
    assert tables["gold_workspaces"][0]["item_count"] is None
    assert tables["gold_workspaces"][0]["admin_count"] is None
    assert tables["gold_workspace_risk"][0]["status"] == "unknown"
    assert tables["gold_workspace_risk"][0]["risk_score"] is None
    overview = _environment_overview(tmp_path, [])
    assert f"| {WA} | Unknown |  | Unknown |" in overview


def test_collected_empty_components_are_zero(tmp_path):
    write(tmp_path, "workspace_inventory.json", {"workspaces": [
        {"id": WA, "name": "Empty", "users": [], "items": []},
    ]})
    overview = _environment_overview(tmp_path, [])
    assert f"| {WA} | Empty |  | 0 |" in overview
    row = gold(tmp_path)["gold_workspaces"][0]
    assert row["item_count"] == row["admin_count"] == 0


@pytest.mark.parametrize("missing", ["items", "users"])
def test_component_completeness_is_independent(tmp_path, missing):
    workspace = {"id": WA, "name": "Partial", "items": [], "users": []}
    workspace[missing] = None
    workspace[f"{missing}CollectionStatus"] = "unavailable"
    write(tmp_path, "workspace_inventory.json", {
        "workspaceListComplete": True, "collectionComplete": False,
        "workspaces": [workspace],
    })
    overview = _environment_overview(tmp_path, [])
    assert f"| {WA} | Partial |  | {'Unknown' if missing == 'items' else '0'} |" in overview
    row = gold(tmp_path)["gold_workspaces"][0]
    assert row["item_count"] == (None if missing == "items" else 0)
    assert row["admin_count"] == (None if missing == "users" else 0)


def test_private_normalization_annotations_never_enter_gold_schemas(tmp_path):
    write(tmp_path, "scanner.json", {"workspaces": [{
        "id": WA, "name": "Annotated",
        "dataflows": [{"id": "flow-one", "name": "Gen1"}],
        "Dataflow2": [{"id": "flow-two", "name": "Gen2"}],
        "Lakehouse": [{"id": "lake", "name": "Lake"}],
        "SQLAnalyticsEndpoint": [{"id": "endpoint", "name": "Endpoint"}],
        "users": [
            {"graphId": "principal", "principalType": "User", "groupUserAccessRight": "Admin"},
            {"graphId": "PRINCIPAL", "principalType": "User", "groupUserAccessRight": "Viewer"},
        ],
    }]})
    tables = gold(tmp_path)
    serialized = json.dumps(tables)
    assert all(annotation not in serialized for annotation in (
        "_scannerMetadata", "_dataflowGeneration", "_roleConflict",
    ))
    for table_name, rows in tables.items():
        columns = {column.name for column in GOLD_TABLES_BY_NAME[table_name].columns}
        assert all(set(row) == columns for row in rows)
    assert tables["gold_workspaces"][0]["item_count"] == 4
    assert tables["gold_workspaces"][0]["admin_count"] is None
    assert "SQLEndpoint" in {row["node_type"] for row in tables["gold_graph_nodes"]}


def test_incomplete_scanner_does_not_invalidate_successful_rest_children(tmp_path):
    write(tmp_path, "scanner.json", {"collectionComplete": False, "workspaces": [{
        "id": WA, "name": "Shared", "Notebook": [{"id": "untrusted"}],
        "users": [{"graphId": "untrusted", "groupUserAccessRight": "Admin"}],
    }, {"id": WB, "name": "Partial identity"}]})
    write(tmp_path, "workspace_inventory.json", {"workspaces": [{
        "id": WA, "name": "Shared", "items": [{"id": "known", "type": "Notebook"}],
        "users": [{"principal": {"id": "known", "type": "User"}, "role": "Admin"}],
    }]})
    rows = {row["workspace_id"]: row for row in gold(tmp_path)["gold_workspaces"]}
    assert rows[WA]["item_count"] == rows[WA]["admin_count"] == 1
    assert rows[WB]["item_count"] is rows[WB]["admin_count"] is None
    assert f"| {WA} | Shared |  | 1 |" in _environment_overview(tmp_path, [])


@pytest.mark.parametrize("users", [
    [{"graphId": "id", "principalType": "User"}],
    [{"graphId": "id", "principalType": "User", "groupUserAccessRight": "Admin"},
     {"graphId": "ID", "principalType": "User", "groupUserAccessRight": "Viewer"}],
])
def test_gold_unknown_or_conflicting_roles_do_not_assert_admin_count(tmp_path, users):
    write(tmp_path, "workspace_inventory.json", {"workspaces": [
        {"id": WA, "name": "Unknown roles", "users": users, "items": []},
    ]})
    tables = gold(tmp_path)
    assert tables["gold_workspaces"][0]["admin_count"] is None
    assert tables["gold_workspace_risk"][0]["owner"] == ""


def test_known_items_do_not_hide_workspace_with_unknown_items(mixed):
    path = mixed / "workspace_inventory.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["workspaces"].append({"id": "unknown-ws", "name": "Unknown items",
                               "items": None, "itemsCollectionStatus": "unavailable"})
    write(mixed, path.name, data)
    assert "| unknown-ws | Unknown items |  | Unknown |" in _environment_overview(mixed, [])


def test_shared_scope_filters_all_report_consumers(mixed):
    write(mixed, "review_scope.json", {"workspaces": [{"id": WB, "type": "AdminWorkspace"}]})
    assert "REST only" not in _environment_overview(mixed, [])
    assert {row["workspace_id"].lower() for row in gold(mixed)["gold_workspaces"]} == {WA}

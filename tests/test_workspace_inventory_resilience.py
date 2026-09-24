# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace HTTP failures must not discard independently collected evidence."""
import ast
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analyzers._common import load_raw
from collectors import workspace_inventory
from collectors._common import load_complete_raw, load_workspace_inventory
from collectors._http import HttpError
from reports.gold_layer import build_gold
from reports.owner.gold import build_owner_gold
from reports.owner.fabric_runtime import materialize_owner_gold


WORKSPACES = [f"aaaaaaaa-0000-4000-8000-{i:012d}" for i in range(1, 4)]
MODEL = "bbbbbbbb-0000-4000-8000-000000000001"
NOW = "2026-09-22T06:00:00Z"


@pytest.fixture
def inventory(monkeypatch):
    rows = [{"id": wid, "name": f"Workspace {i}"} for i, wid in enumerate(WORKSPACES)]
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "get_scope_workspace_ids", lambda: set())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: (rows, True))
    users = Mock(return_value=[{"groupUserAccessRight": "Admin"}])
    items = Mock(return_value=[])
    monkeypatch.setattr(workspace_inventory, "_list_users", users)
    monkeypatch.setattr(workspace_inventory, "_list_items", items)
    return users, items


@pytest.mark.parametrize("component", ["users", "items"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_workspace_failure_retains_identities_and_independent_components(
    tmp_path, inventory, component, status, capsys,
):
    users, items = inventory
    target = users if component == "users" else items
    target.side_effect = [
        target.return_value,
        HttpError("Synthetic workspace failure", status_code=status),
        target.return_value,
    ]
    path = workspace_inventory.collect(tmp_path)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert [w["id"] for w in result["workspaces"]] == WORKSPACES
    assert users.call_count == items.call_count == 3
    assert result["workspaceListComplete"] is True
    assert result["collectionComplete"] is False
    failed = result["workspaces"][1]
    assert failed[component] is None
    assert failed[f"{component}CollectionStatus"] == "unavailable"
    other = "items" if component == "users" else "users"
    assert failed[f"{other}CollectionStatus"] == "collected"
    assert failed[other] is not None
    assert result["collectionErrors"] == [{
        "workspaceId": WORKSPACES[1], "component": component,
        "statusCode": status, "message": "Synthetic workspace failure",
    }]
    assert "incomplete" in capsys.readouterr().out.lower()
    assert load_raw(path) is None
    with pytest.raises(HttpError, match="incomplete"):
        load_complete_raw(path)
    assert [w["id"] for w in load_workspace_inventory(tmp_path)] == WORKSPACES


def test_successful_empty_components_are_not_unknown(tmp_path, inventory):
    users, _ = inventory
    users.return_value = []
    result = json.loads(workspace_inventory.collect(tmp_path).read_text(encoding="utf-8"))
    assert result["collectionComplete"] is True
    assert result["collectionErrors"] == []
    assert all(w["users"] == [] and w["items"] == [] for w in result["workspaces"])


def test_programming_errors_still_fail_the_collector(tmp_path, inventory):
    inventory[0].side_effect = TypeError("Synthetic programming error")
    with pytest.raises(TypeError, match="programming error"):
        workspace_inventory.collect(tmp_path)


def _write(path, name, payload):
    (path / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("native_state", ["unavailable", "partial", "missing"])
def test_gold_preserves_owner_executions_without_dataflows(tmp_path, native_state):
    scanner_workspace = {
        "id": WORKSPACES[0], "name": "Scanned workspace",
        "datasets": [{"id": MODEL, "name": "Model"}],
    }
    _write(tmp_path, "scanner.json", {"workspaces": [scanner_workspace]})
    if native_state != "missing":
        native = {"collectionComplete": False, "collectionErrors": [{"statusCode": 404}]}
        if native_state == "partial":
            native.update(workspaceListComplete=True, workspaces=[{
                "id": WORKSPACES[1], "name": "Native workspace",
                "users": None, "items": None,
                "usersCollectionStatus": "unavailable", "itemsCollectionStatus": "unavailable",
            }])
        _write(tmp_path, "workspace_inventory.json", native)
    _write(tmp_path, "semantic_models.json", {
        "datasets": [{
            "id": MODEL, "name": "Model", "workspaceId": WORKSPACES[0],
            "workspaceName": "Scanned workspace",
        }],
        "executionEvidence": [{
            "workspaceId": WORKSPACES[0], "workspaceName": "Scanned workspace",
            "itemId": MODEL, "itemName": "Model", "itemType": "SemanticModel",
            "collectionStatus": "collected", "noticeCode": "refresh_top_limit",
            "executions": [{
                "requestId": "refresh-1", "status": "Completed",
                "startTime": "2026-09-21T12:00:00Z", "endTime": "2026-09-21T12:00:01Z",
            }],
        }],
    })
    tables = build_gold([], tmp_path, run_id="current-run", run_timestamp=NOW, check_remote=False)
    workspaces = {w["workspace_id"]: w for w in tables["gold_workspaces"]}
    assert WORKSPACES[0] in workspaces
    assert workspaces[WORKSPACES[0]]["admin_count"] is None
    if native_state == "partial":
        assert set(workspaces) == set(WORKSPACES[:2])
        assert workspaces[WORKSPACES[1]]["item_count"] is None
    owner = build_owner_gold(tables)
    assert len(owner["owner_executions"]) == 1
    assert owner["owner_executions"][0]["workspace_id"] == WORKSPACES[0]
    assert owner["owner_executions"][0]["duration_ms"] == 1000
    assert owner["owner_coverage"]
    assert {r["run_id"] for r in owner["owner_reviews"]} == {"current-run"}
    assert all(r["workspace_id"] in workspaces for r in owner["owner_coverage"])


def test_native_only_item_inventory_is_known_without_scanner(tmp_path, inventory):
    inventory[0].side_effect = [
        [], HttpError("Synthetic missing roles", status_code=404), [],
    ]
    workspace_inventory.collect(tmp_path)
    tables = build_gold([], tmp_path, run_id="current-run", run_timestamp=NOW, check_remote=False)
    assert [r["admin_count"] for r in tables["gold_workspaces"]] == [0, None, 0]
    assert [r["item_count"] for r in tables["gold_workspaces"]] == [0, 0, 0]


def test_unknown_item_inventory_is_not_an_empty_workspace(tmp_path, inventory):
    inventory[1].side_effect = HttpError("Synthetic item access denied", status_code=403)
    workspace_inventory.collect(tmp_path)
    tables = build_gold([], tmp_path, run_id="current-run", run_timestamp=NOW, check_remote=False)
    assert all(r["item_count"] is None for r in tables["gold_workspaces"])
    assert all(r["archetype"] == "unknown" for r in tables["gold_workspaces"])
    for row in tables["gold_workspace_risk"]:
        assert row["status"] == "grey"
        assert all(row[key] is None for key in (
            "item_count", "semantic_model_count", "report_count",
            "notebook_count", "pipeline_count", "lakehouse_count",
        ))
    assert build_owner_gold(tables)["owner_coverage"]


def test_inventory_deduplicates_scanner_and_native_by_id_not_name(tmp_path):
    _write(tmp_path, "scanner.json", {"workspaces": [
        {"id": WORKSPACES[0].upper(), "name": "Same name", "datasets": [{"id": MODEL}]},
        {"id": WORKSPACES[1], "name": "Same name", "datasets": []},
    ]})
    _write(tmp_path, "workspace_inventory.json", {"workspaces": [
        {"id": WORKSPACES[0], "name": "Same name", "users": [], "items": []},
    ]})
    tables = build_gold([], tmp_path, run_id="current-run", run_timestamp=NOW, check_remote=False)
    workspaces = tables["gold_workspaces"]
    assert len(workspaces) == 2
    assert {r["workspace_id"].lower() for r in workspaces} == set(WORKSPACES[:2])
    assert next(r for r in workspaces if r["workspace_id"] == WORKSPACES[0])["item_count"] == 1
    assert len(build_owner_gold(tables)["owner_reviews"]) == 2


def test_missing_workspace_id_does_not_certify_a_complete_list(tmp_path, inventory, monkeypatch):
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: ([
        {"name": "No ID"}, {"id": WORKSPACES[0], "name": "Known workspace"},
    ], True))
    result = json.loads(workspace_inventory.collect(tmp_path).read_text(encoding="utf-8"))
    assert result["workspaceListComplete"] is False
    assert result["collectionComplete"] is False
    assert [w["id"] for w in result["workspaces"]] == [WORKSPACES[0]]
    with pytest.raises(HttpError, match="complete"):
        load_workspace_inventory(tmp_path)


def test_listing_failure_replaces_stale_snapshot_but_cannot_claim_complete_identities(
    tmp_path, inventory, monkeypatch,
):
    workspace_inventory.collect(tmp_path)
    monkeypatch.setattr(
        workspace_inventory, "_list_workspaces",
        Mock(side_effect=HttpError("Synthetic listing failure", status_code=500)),
    )
    result = json.loads(workspace_inventory.collect(tmp_path).read_text(encoding="utf-8"))
    assert result["collectionComplete"] is False
    assert "workspaceListComplete" not in result and "workspaces" not in result
    with pytest.raises(HttpError, match="complete"):
        load_workspace_inventory(tmp_path)


def test_scoping_remains_enforced_when_a_component_fails(tmp_path, inventory, monkeypatch):
    monkeypatch.setattr(workspace_inventory, "get_scope_workspace_ids", lambda: {WORKSPACES[1]})
    inventory[0].side_effect = HttpError("Synthetic missing roles", status_code=404)
    result = json.loads(workspace_inventory.collect(tmp_path).read_text(encoding="utf-8"))
    assert [row["id"] for row in result["workspaces"]] == [WORKSPACES[1]]
    assert inventory[0].call_count == inventory[1].call_count == 1


def test_empty_owner_projection_fails_before_any_write():
    spark = Mock()
    spark.catalog.tableExists.return_value = True
    with pytest.raises(RuntimeError, match="no attributable workspace reviews"):
        materialize_owner_gold(spark, {
            "gold_workspaces": [],
            "gold_run_summary": [{"run_id": "current-run", "run_timestamp": NOW}],
        })
    spark.createDataFrame.assert_not_called()
    spark.table.assert_not_called()
    spark.sql.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"collectionComplete": False}, {"failedWorkspaces": ["unavailable"]},
    {"_meta": {"complete": False}},
    {"errors": [{"stage": "vertipaq", "error": "Synthetic unavailable model"}]},
    {"errors": 3},
    {"refreshErrors": {"model": {"statusCode": 403}}},
    {"queries": {"unavailable_probe": {"ok": False}, "available_probe": {"ok": True}}},
    {"available": False, "models": [], "notes": ["Required analysis library unavailable"]},
    RuntimeError("Synthetic collector failure"),
])
def test_collect_notebook_reports_gaps_and_attempts_every_collector(
    tmp_path, monkeypatch, capsys, payload,
):
    notebook_path = Path(__file__).resolve().parents[1] / "fabric" / "notebooks" / "01_collect.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    source = next("".join(c["source"]) for c in notebook["cells"] if c["id"] == "cell11")
    tree = ast.parse(source)
    collector_assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "COLLECTORS"
    )
    collectors = ast.literal_eval(collector_assignment.value)
    collector_count = len(collectors)
    complete_path = tmp_path / "complete.json"
    incomplete_path = tmp_path / "incomplete.json"
    _write(tmp_path, complete_path.name, {})
    failed = isinstance(payload, Exception)
    _write(tmp_path, incomplete_path.name, {} if failed else payload)

    def import_collector(name):
        affected = name == "collectors.workspace_inventory"
        return SimpleNamespace(collect=Mock(
            return_value=incomplete_path if affected else complete_path,
            side_effect=payload if affected and failed else None,
        ))

    import_module = Mock(side_effect=import_collector)
    monkeypatch.setattr(importlib, "import_module", import_module)
    exit_notebook = Mock()
    monkeypatch.setitem(sys.modules, "notebookutils", SimpleNamespace(
        notebook=SimpleNamespace(exit=exit_notebook),
    ))
    if failed:
        with pytest.raises(RuntimeError, match="Collection incomplete"):
            exec(compile(source, "collect-notebook", "exec"), {"RAW_DIR": str(tmp_path)})
        exit_notebook.assert_not_called()
    else:
        exec(compile(source, "collect-notebook", "exec"), {"RAW_DIR": str(tmp_path)})
        assert "incomplete=collectors.workspace_inventory" in exit_notebook.call_args.args[0]
    output = capsys.readouterr().out
    assert f"{collector_count - 1} complete, {int(not failed)} incomplete, {int(failed)} failed" in output
    assert [call.args[0] for call in import_module.call_args_list] == collectors
    assert "collectors succeeded" not in output


def test_explicit_opt_out_is_not_an_unexpected_collection_failure():
    from collectors._common import collection_incomplete

    assert not collection_incomplete({"available": False, "skipped": True}, include_errors=True)
    assert not collection_incomplete({"available": True, "models": []}, include_errors=True)

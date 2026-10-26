# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Scanner and REST evidence must reconcile into one workspace view."""
import json
import copy

from collectors._common import load_workspace_inventory
from collectors.workspace_evidence import merge_workspace_evidence, workspace_items, workspace_users


def write_raw(path, filename, payload):
    (path / filename).write_text(json.dumps(payload), encoding="utf-8")


def workspace(**fields):
    return {"id": "ws-a", "name": "engineering-prod", "type": "Workspace", **fields}


def test_inventory_reconciles_workspace_ids_and_preserves_scanner_metadata(tmp_path):
    write_raw(tmp_path, "scanner.json", {"workspaces": [
        workspace(id="WS-A", datasets=[{"id": "MODEL-A", "name": "Model", "endorsementDetails": {"endorsement": "Certified"}}])
    ]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "model-a", "displayName": "Model", "type": "SemanticModel"}], users=[]),
        workspace(id="ws-b", name="other-prod", items=[{"id": "notebook-b", "type": "Notebook"}]),
    ]})
    rows = load_workspace_inventory(tmp_path)
    assert {w["id"].lower() for w in rows} == {"ws-a", "ws-b"}
    assert len(workspace_items(rows[0])) == 1
    assert rows[0]["users"] == []
    assert rows[0]["datasets"][0]["endorsementDetails"]["endorsement"] == "Certified"


def test_child_collectors_include_rest_only_workspaces(tmp_path):
    write_raw(tmp_path, "scanner.json", {"workspaces": [workspace()]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [workspace(id="ws-b")]})
    assert {w["id"] for w in load_workspace_inventory(tmp_path)} == {"ws-a", "ws-b"}


def test_mixed_item_shapes_are_deduplicated_and_empty_flat_list_does_not_hide_scanner():
    ws = workspace(
        Notebook=[{"id": "ITEM-A"}],
        notebooks=[{"id": "item-a"}],
        items=[{"id": "item-a", "type": "Notebook"}, {"id": "item-b", "type": "Lakehouse"}],
    )
    assert len(workspace_items(ws)) == 2
    ws["items"] = []
    assert len(workspace_items(ws)) == 1


def test_reconciliation_preserves_known_children_and_inputs():
    scanner = [workspace(
        users=[{"identifier": "owner", "role": "Admin"}],
        Notebook=[{"id": "ITEM-A", "name": "Detailed notebook"}],
    )]
    rest = [workspace(users=[], usersCollectionStatus="unavailable",
                      items=None, itemsCollectionStatus="unavailable")]
    before = copy.deepcopy((scanner, rest))
    row = merge_workspace_evidence(scanner, rest)[0]
    assert workspace_users(row) == scanner[0]["users"]
    assert workspace_items(row)[0]["id"] == "ITEM-A"
    assert (scanner, rest) == before


def test_failed_previous_child_is_not_resurrected():
    row = merge_workspace_evidence(
        [workspace(users=[], usersCollectionStatus="unavailable")],
        [workspace(users=None, usersCollectionStatus="unavailable")],
    )[0]
    assert workspace_users(row) is None


def test_collected_empty_membership_overrides_previous_users():
    row = merge_workspace_evidence(
        [workspace(users=[{"role": "Admin", "identifier": "old-owner"}])],
        [workspace(users=[], usersCollectionStatus="collected")],
    )[0]
    assert workspace_users(row) == []


def test_unrelated_metadata_lists_are_not_items_and_future_types_remain_visible():
    row = workspace(
        Users=[{"id": "user-a"}], DatasourceInstances=[{"id": "source-a"}],
        Roles=[{"id": "role-a"}], FutureArtifact=[{"id": "future-a"}],
    )
    assert [item["type"] for item in workspace_items(row)] == ["FutureArtifact"]


def test_service_principal_role_shapes_do_not_double_count():
    row = workspace(users=[
        {"principalType": "App", "graphId": "APP-A", "role": "Admin"},
        {"principal": {"type": "ServicePrincipal", "id": "app-a"}, "role": "Admin"},
    ])
    assert len(workspace_users(row)) == 1


def test_workspace_names_never_join_different_ids():
    rows = merge_workspace_evidence([workspace(id="ws-a")], [workspace(id="ws-b", users=[])])
    assert len(rows) == 2
    assert workspace_users(rows[0]) is None
    assert workspace_users(rows[1]) == []

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The workspace inventory reads FUAM first and asks the APIs only for what FUAM does not cover."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from collectors import fuam_inventory, workspace_inventory
from orchestration.review_config import local_configuration

NOW = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
COVERED = "aaaaaaaa-0000-4000-8000-000000000001"
PARTIAL = "aaaaaaaa-0000-4000-8000-000000000002"
MISSING = "aaaaaaaa-0000-4000-8000-000000000003"
ITEM = "bbbbbbbb-0000-4000-8000-000000000001"
APP_OBJECT = "cccccccc-0000-4000-8000-000000000001"
APP_ID = "dddddddd-0000-4000-8000-000000000001"
SECRET = "SECRET-endpoint-name"


def snapshot(enabled=True):
    configuration = local_configuration()
    configuration["sources"]["fuam"].update(enabled=enabled, workspace_id=COVERED, lakehouse_id=PARTIAL)
    return {"configuration": configuration}


def newest(hours=2):
    return [{"newest": NOW.replace(tzinfo=None) - timedelta(hours=hours)}]


def item_row(wid=COVERED, **extra):
    return {"id": ITEM.upper(), "workspaceId": wid.upper(), "type": "Notebook", "name": "Load", "description": None,
            "folderId": None, **extra}


def user_row(wid=COVERED, **extra):
    return {"WorkspaceId": wid.upper(), "GraphId": "user-1", "PrincipalType": "User", "UserType": "Guest",
            "Identifier": "x#EXT#@contoso.com", "GroupUserAccessRight": "Admin", **extra}


class Fuam:
    """A fake SQL endpoint keyed on substrings of the statement."""

    def __init__(self, newest_rows=None, items=None, users=None, error=None):
        self.statements = []
        self.newest_rows = newest() if newest_rows is None else newest_rows
        self.items = [item_row()] if items is None else items
        self.users = [user_row()] if users is None else users
        self.error = error

    def __call__(self, source, statement):
        assert source == "fuam"
        self.statements.append(statement)
        if self.error:
            raise self.error
        if "MAX(fuam_modified_at)" in statement:
            return list(self.newest_rows)
        if "FROM dbo.active_items" in statement:
            return list(self.items)
        if "FROM dbo.workspaces_scanned_users" in statement:
            return list(self.users)
        raise AssertionError(statement)


def read(fuam, ids=(COVERED, PARTIAL, MISSING)):
    return fuam_inventory.read(snapshot(), fuam, list(ids), NOW)


# --- reading FUAM ------------------------------------------------------------------------------

def test_items_and_members_are_read_per_workspace_in_the_apis_shape():
    result = read(Fuam())
    assert result.usable and result.observed_at == (NOW - timedelta(hours=2)).isoformat()
    assert result.items[COVERED] == [{"id": ITEM, "displayName": "Load", "type": "Notebook", "description": None,
                                      "folderId": None, "workspaceId": COVERED}]
    user = result.users[COVERED][0]
    assert (user["principalType"], user["userType"], user["groupUserAccessRight"]) == ("User", "Guest", "Admin")
    assert user["identifier"] == "x#EXT#@contoso.com" and user["graphId"] == "user-1"


def test_an_application_is_identified_by_its_application_id_like_the_admin_api():
    app = user_row(GraphId=APP_OBJECT, PrincipalType="App", UserType=None, Identifier=APP_ID)
    assert read(Fuam(users=[app])).users[COVERED][0]["graphId"] == APP_ID


def test_statements_are_bounded_to_the_scope_and_exclude_deleted_items():
    fuam = Fuam()
    read(fuam, ids=(COVERED.upper(), "not-a-guid"))
    scoped = [statement for statement in fuam.statements if " IN (" in statement]
    assert scoped and all(f"'{COVERED}'" in statement and "not-a-guid" not in statement for statement in scoped)
    assert any("fuam_deleted = 0" in statement for statement in scoped)


@pytest.mark.parametrize("kwargs, reason", [
    ({"newest_rows": [{"newest": None}]}, "no_transfer_snapshot"),
    ({"newest_rows": newest(hours=24 + 24 + 1)}, "source_data_stale"),
    ({"newest_rows": [{"other": 1}]}, "incompatible_schema"),
    ({"items": [{"id": ITEM}]}, "incompatible_schema"),
    ({"users": [{"WorkspaceId": COVERED}]}, "incompatible_schema"),
])
def test_a_snapshot_that_cannot_be_trusted_is_unusable_with_a_reason(kwargs, reason):
    result = read(Fuam(**kwargs))
    assert (result.usable, result.reason) == (False, reason)
    assert not result.items and not result.users


def test_the_snapshot_is_trusted_up_to_the_limit_plus_a_day_of_grace():
    assert read(Fuam(newest_rows=newest(hours=24 + 24))).usable


def test_a_failing_query_is_unusable_without_leaking_its_message():
    result = read(Fuam(error=RuntimeError(SECRET)))
    assert (result.usable, result.reason) == (False, "query_failed")
    assert SECRET not in repr(result)


def test_fuam_supplies_the_inventory_only_when_it_is_connected():
    assert fuam_inventory.supplies(snapshot(True)) and not fuam_inventory.supplies(snapshot(False))


# --- the inventory collector -------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    rows = [{"id": wid, "name": f"Workspace {index}"} for index, wid in enumerate((COVERED, PARTIAL, MISSING))]
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "get_scope_workspace_ids", lambda: set())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: (rows, True))
    users = Mock(return_value=[{"groupUserAccessRight": "Viewer", "principalType": "User", "graphId": "api-user"}])
    items = Mock(return_value=[{"id": "api-item", "type": "Report", "displayName": "From API"}])
    monkeypatch.setattr(workspace_inventory, "_list_users", users)
    monkeypatch.setattr(workspace_inventory, "_list_items", items)
    return users, items


def collect(tmp_path, fuam):
    reader = (lambda ids: fuam_inventory.read(snapshot(), fuam, ids, NOW)) if fuam is not None else None
    return json.loads(workspace_inventory.collect(tmp_path, fuam_reader=reader).read_text(encoding="utf-8"))


def by_id(result):
    return {row["id"]: row for row in result["workspaces"]}


def test_the_api_is_called_only_for_what_fuam_does_not_hold(tmp_path, api):
    users, items = api
    fuam = Fuam(items=[item_row(COVERED), item_row(PARTIAL)], users=[user_row(COVERED)])
    result = collect(tmp_path, fuam)
    rows = by_id(result)
    assert (rows[COVERED]["itemsDataSource"], rows[COVERED]["usersDataSource"]) == ("fuam", "fuam")
    assert (rows[PARTIAL]["itemsDataSource"], rows[PARTIAL]["usersDataSource"]) == ("fuam", "api")
    assert (rows[MISSING]["itemsDataSource"], rows[MISSING]["usersDataSource"]) == ("api", "api")
    assert [call.args[1] for call in users.call_args_list] == [PARTIAL, MISSING]
    assert [call.args[1] for call in items.call_args_list] == [MISSING]
    assert rows[COVERED]["items"][0]["id"] == ITEM and rows[MISSING]["items"][0]["id"] == "api-item"
    assert result["dataSource"] == "fuam+api" and "reason" not in result
    assert result["inventorySource"] == {"fuamObservedAt": (NOW - timedelta(hours=2)).isoformat(),
                                         "fromFuam": {"items": 2, "users": 1}, "fromApi": {"items": 1, "users": 2}}
    assert result["collectionComplete"] is True


def test_a_workspace_with_no_rows_in_fuam_is_read_from_the_api_not_assumed_empty(tmp_path, api):
    users, items = api
    result = collect(tmp_path, Fuam(items=[], users=[]))
    assert result["dataSource"] == "api" and result["reason"] == "no_workspace_covered"
    assert users.call_count == items.call_count == 3


@pytest.mark.parametrize("fuam, reason", [
    (Fuam(newest_rows=newest(hours=100)), "source_data_stale"),
    (Fuam(newest_rows=[{"newest": None}]), "no_transfer_snapshot"),
    (Fuam(error=RuntimeError(SECRET)), "query_failed"),
])
def test_an_unusable_snapshot_means_the_full_api_call(tmp_path, api, fuam, reason):
    users, items = api
    result = collect(tmp_path, fuam)
    assert (result["dataSource"], result["reason"], result["fallback"]) == ("api", reason, "full_api_call")
    assert users.call_count == items.call_count == 3
    assert all(row["itemsDataSource"] == row["usersDataSource"] == "api" for row in result["workspaces"])
    assert SECRET not in json.dumps(result)


def test_without_fuam_the_inventory_is_unchanged(tmp_path, api):
    users, items = api
    result = collect(tmp_path, None)
    assert users.call_count == items.call_count == 3
    assert not {"dataSource", "inventorySource", "reason"} & set(result)
    assert not any("DataSource" in key for row in result["workspaces"] for key in row)


def test_an_api_failure_for_an_uncovered_part_is_still_reported(tmp_path, api):
    from collectors._http import HttpError
    users, items = api
    items.side_effect = HttpError("Synthetic failure", status_code=500)
    result = collect(tmp_path, Fuam(items=[item_row(COVERED)], users=[user_row(COVERED), user_row(PARTIAL)]))
    assert result["collectionComplete"] is False
    rows = by_id(result)
    assert rows[PARTIAL]["itemsCollectionStatus"] == "unavailable" and rows[COVERED]["itemsCollectionStatus"] == "collected"
    assert {error["workspaceId"] for error in result["collectionErrors"]} == {PARTIAL, MISSING}


def test_fuam_rows_feed_the_principal_rules_like_api_rows(tmp_path, api):
    from analyzers import native_rules
    from analyzers.far_profile import RULE_KINDS
    app = user_row(COVERED, GraphId=APP_OBJECT, PrincipalType="App", UserType=None, Identifier=APP_ID,
                   GroupUserAccessRight="Member")
    workspace = by_id(collect(tmp_path, Fuam(users=[user_row(COVERED), app])))[COVERED]
    assert RULE_KINDS["SEC-017"] == "signal"
    _, guests = native_rules.guest_members(workspace, {}, None)
    _, apps = native_rules.app_roles(workspace, {}, None)
    assert (guests["guest_principal_count"], guests["guest_elevated_role_count"]) == (1, 1)
    assert (apps["app_principal_count"], apps["app_elevated_role_count"]) == (1, 1)

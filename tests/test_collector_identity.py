# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Regression coverage for identity, refresh, and repeated notebook runs."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analyzers import security_review
from collectors import auth, scanner_api, workspace_inventory
from collectors._http import HttpError
from orchestration.notebook_parameters import optional_string


ROOT = Path(__file__).resolve().parent.parent


def test_tokens_refresh_before_expiry_without_repeated_credential_calls(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    credential = Mock()
    credential.get_token.side_effect = [
        SimpleNamespace(token="synthetic-first-token", expires_on=2000),
        SimpleNamespace(token="synthetic-renewed-token", expires_on=3000),
    ]
    provider = object.__new__(auth.TokenProvider)
    provider._credential = credential
    provider._token_cache = {}

    assert provider.get_token() == "synthetic-first-token"
    now[0] = 1100
    assert provider.get_token() == "synthetic-first-token"
    assert credential.get_token.call_count == 1
    now[0] = 1700
    assert provider.get_token() == "synthetic-renewed-token"
    assert credential.get_token.call_count == 2


def test_failed_token_refresh_does_not_reuse_expired_token(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    credential = Mock()
    credential.get_token.side_effect = [
        SimpleNamespace(token="synthetic-expiring-token", expires_on=2000),
        RuntimeError("Synthetic authentication failure"),
    ]
    provider = object.__new__(auth.TokenProvider)
    provider._credential = credential
    provider._token_cache = {}
    provider.get_token()
    now[0] = 2001
    with pytest.raises(RuntimeError, match="Synthetic authentication failure"):
        provider.get_token()


def test_scanner_reacquires_headers_for_each_request(monkeypatch, tmp_path):
    provider = Mock()
    provider.headers.side_effect = lambda **kwargs: {"X-Synthetic-Token": str(provider.headers.call_count)}
    monkeypatch.setattr(scanner_api, "get_default_provider", lambda: provider)
    monkeypatch.setattr(scanner_api, "get_scope_workspace_ids", lambda: set())
    monkeypatch.setattr(scanner_api.time, "sleep", lambda _: None)
    seen = []
    responses = iter([
        [{"id": "synthetic-workspace"}],
        {"value": [{"id": "synthetic-workspace", "type": "Workspace"}]},
        {"id": "synthetic-scan"},
        {"status": "Running"},
        {"status": "Succeeded"},
        {"workspaces": [{"id": "synthetic-workspace"}]},
    ])

    def request(method, url, headers, **kwargs):
        current = headers() if callable(headers) else headers
        seen.append(current["X-Synthetic-Token"])
        return SimpleNamespace(
            status_code=200, content=b"synthetic",
            raise_for_status=lambda: None, json=lambda: next(responses),
        )

    monkeypatch.setattr(scanner_api._http, "request", request)
    scanner_api.collect(tmp_path)
    assert seen == ["1", "2", "3", "4", "5", "6"]


def test_member_role_assignments_are_normalized_for_security_review(monkeypatch, tmp_path):
    assignments = [
        {
            "id": f"synthetic-assignment-{i}",
            "role": "Admin" if i == 0 else "Viewer",
            "principal": {
                "id": f"synthetic-user-{i}",
                "type": "User",
                "displayName": f"Synthetic user {i}",
                "userDetails": {
                    "userPrincipalName": f"guest{i}_example.com#EXT#@example.onmicrosoft.com",
                },
            },
        }
        for i in range(11)
    ]
    monkeypatch.setattr(workspace_inventory, "collect_value", lambda *args, **kwargs: assignments)
    users = workspace_inventory._list_users(Mock(), "synthetic-workspace", False)
    assert users[0]["principalType"] == "User"
    assert users[0]["graphId"] == "synthetic-user-0"
    assert users[0]["groupUserAccessRight"] == "Admin"
    assert users[0]["identifier"] == assignments[0]["principal"]["userDetails"]["userPrincipalName"]
    (tmp_path / "workspace_inventory.json").write_text(
        json.dumps({"workspaces": [{"id": "synthetic-workspace", "name": "Synthetic", "users": users}]}),
        encoding="utf-8",
    )
    results = {f["rule_id"]: f["status"] for f in security_review.analyze(tmp_path)}
    assert {key: results[key] for key in ("SEC-004", "SEC-005", "SEC-007")} == {
        "SEC-004": "fail", "SEC-005": "fail", "SEC-007": "fail",
    }


def test_security_review_handles_historical_nested_role_assignments(tmp_path):
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{
            "id": "synthetic-workspace",
            "users": [{
                "role": "Admin",
                "principal": {
                    "id": "synthetic-user",
                    "type": "User",
                    "userDetails": {"userPrincipalName": "guest_example.com#EXT#@example.onmicrosoft.com"},
                },
            }],
        }],
    }), encoding="utf-8")
    results = {f["rule_id"]: f for f in security_review.analyze(tmp_path)}
    assert results["SEC-004"]["status"] == "fail"
    assert results["SEC-007"]["status"] == "fail"
    assert results["SEC-007"]["evidence"]["examples"][0]["externalUsers"] == [
        "guest_example.com#EXT#@example.onmicrosoft.com",
    ]


@pytest.mark.parametrize("principal_type", ["Group", "ServicePrincipal", "EntireTenant"])
def test_non_user_fabric_principals_are_not_individual_admins(monkeypatch, principal_type):
    monkeypatch.setattr(workspace_inventory, "collect_value", lambda *args, **kwargs: [{
        "role": "Admin", "principal": {"id": "synthetic-principal", "type": principal_type},
    }])
    users = workspace_inventory._list_users(Mock(), "synthetic-workspace", False)
    assert users[0]["principalType"] == principal_type
    assert users[0]["graphId"] == "synthetic-principal"
    assert security_review._principal_type(users[0]) != "user"
    assert not security_review._is_external(users[0])


@pytest.mark.parametrize("status_code", [401, 403])
def test_workspace_listing_falls_back_only_for_permissions(monkeypatch, status_code):
    collect = Mock(side_effect=[
        HttpError("Synthetic admin permission failure", status_code=status_code),
        [{"id": "synthetic-workspace"}],
    ])
    monkeypatch.setattr(workspace_inventory, "collect_workspace_groups", collect)
    workspaces, is_admin = workspace_inventory._list_workspaces(Mock())
    assert workspaces == [{"id": "synthetic-workspace"}]
    assert not is_admin
    assert collect.call_count == 2


def test_workspace_listing_server_failure_is_not_member_scope(monkeypatch):
    collect = Mock(side_effect=HttpError("Synthetic server failure", status_code=500))
    monkeypatch.setattr(workspace_inventory, "collect_workspace_groups", collect)
    with pytest.raises(HttpError, match="Synthetic server failure"):
        workspace_inventory._list_workspaces(Mock())
    collect.assert_called_once()


def test_successful_empty_admin_listing_is_not_a_permission_failure(monkeypatch):
    collect = Mock(return_value=[])
    monkeypatch.setattr(workspace_inventory, "collect_workspace_groups", collect)
    assert workspace_inventory._list_workspaces(Mock()) == ([], True)
    collect.assert_called_once()


def test_failed_membership_collection_replaces_stale_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "get_scope_workspace_ids", lambda: set())
    monkeypatch.setattr(
        workspace_inventory, "_list_workspaces",
        lambda _: ([{"id": "synthetic-workspace", "name": "Synthetic"}], False),
    )
    monkeypatch.setattr(
        workspace_inventory, "_list_users",
        Mock(side_effect=HttpError("Synthetic membership denied", status_code=403)),
    )
    monkeypatch.setattr(workspace_inventory, "_list_items", Mock(return_value=[]))
    (tmp_path / "workspace_inventory.json").write_text(
        json.dumps({"workspaces": [{"id": "synthetic-workspace", "users": []}]}),
        encoding="utf-8",
    )
    (tmp_path / "scanner.json").write_text(
        json.dumps({"workspaces": [{"id": "synthetic-workspace"}]}), encoding="utf-8",
    )
    path = workspace_inventory.collect(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["collectionComplete"] is False
    results = {f["rule_id"]: f for f in security_review.analyze(tmp_path)}
    for rule in ("SEC-004", "SEC-005", "SEC-007"):
        assert results[rule]["status"] == "missing_evidence"


def test_security_review_uses_inventory_members_when_scanner_omits_them(tmp_path):
    (tmp_path / "scanner.json").write_text(
        json.dumps({"workspaces": [{"id": "synthetic-workspace"}]}), encoding="utf-8",
    )
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{
            "id": "synthetic-workspace",
            "users": [{
                "principalType": "User", "groupUserAccessRight": "Admin",
                "identifier": "guest_example.com#EXT#@example.onmicrosoft.com",
            }],
        }],
    }), encoding="utf-8")
    results = {f["rule_id"]: f["status"] for f in security_review.analyze(tmp_path)}
    assert results["SEC-004"] == "fail"
    assert results["SEC-007"] == "fail"


def test_successfully_collected_empty_membership_is_not_missing(tmp_path):
    (tmp_path / "workspace_inventory.json").write_text(
        json.dumps({"workspaces": [{"id": "synthetic-workspace", "users": []}]}),
        encoding="utf-8",
    )
    results = {f["rule_id"]: f["status"] for f in security_review.analyze(tmp_path)}
    for rule in ("SEC-004", "SEC-005", "SEC-007"):
        assert results[rule] == "pass"


def test_guest_check_requires_user_identity_evidence(tmp_path):
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{
            "id": "synthetic-workspace",
            "users": [{"principalType": "User", "graphId": "synthetic-user", "groupUserAccessRight": "Viewer"}],
        }],
    }), encoding="utf-8")
    results = {f["rule_id"]: f for f in security_review.analyze(tmp_path)}
    assert results["SEC-007"]["status"] == "missing_evidence"
    assert results["SEC-007"]["evidence"]["workspacesMissingMembershipEvidence"] == 1


@pytest.mark.parametrize("empty_scope", ["", None])
def test_empty_workspace_parameter_clears_previous_notebook_scope(monkeypatch, empty_scope):
    notebook = json.loads((ROOT / "fabric/notebooks/01_collect.ipynb").read_text(encoding="utf-8"))
    source = next(
        "".join(cell["source"]) for cell in notebook["cells"]
        if 'os.environ["WORKSPACE_IDS"]' in "".join(cell.get("source", []))
    )
    module = ast.parse(source)
    scope_nodes = [
        node for node in module.body
        if "WORKSPACE_IDS" in {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
    ]
    code = compile(ast.Module(body=scope_nodes, type_ignores=[]), "notebook-workspace-scope", "exec")
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    namespace = {"os": os, "optional_string": optional_string, "WORKSPACE_IDS": "synthetic-workspace"}
    exec(code, namespace)
    assert os.environ["WORKSPACE_IDS"] == "synthetic-workspace"
    namespace["WORKSPACE_IDS"] = empty_scope
    exec(code, namespace)
    actual_scope = os.environ.get("WORKSPACE_IDS")
    assert not actual_scope


@pytest.mark.parametrize("is_admin", [True, False])
@pytest.mark.parametrize("restricted", [True, False])
def test_normal_collection_keeps_empty_scope_unfiltered(monkeypatch, tmp_path, is_admin, restricted):
    first = "aaaaaaaa-0000-4000-8000-000000000001"
    second = "bbbbbbbb-0000-4000-8000-000000000002"
    monkeypatch.setenv("WORKSPACE_IDS", first if restricted else "")
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(
        workspace_inventory, "_list_workspaces",
        lambda _: ([{"id": first}, {"id": second}], is_admin),
    )
    monkeypatch.setattr(workspace_inventory, "_list_users", lambda *_: [])
    monkeypatch.setattr(workspace_inventory, "_list_items", lambda *_: [])
    collected = json.loads(workspace_inventory.collect(tmp_path).read_text(encoding="utf-8"))
    assert [row["id"] for row in collected["workspaces"]] == ([first] if restricted else [first, second])
    assert collected["adminMode"] is is_admin

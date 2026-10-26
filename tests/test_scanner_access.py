# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The scanner asks who can access each item and keeps only counts."""
import json

from collectors import scanner_api
from collectors.scanner_api import GET_INFO_PARAMS, reduce_artifact_users

ROLE = "11111111-1111-4111-8111-111111111111"
DIRECT = "22222222-2222-4222-8222-222222222222"
GROUP = "33333333-3333-4333-8333-333333333333"
APP = "44444444-4444-4444-8444-444444444444"
GUEST = "55555555-5555-4555-8555-555555555555"
SECRET_NAME = "Secret Person"
SECRET_MAIL = "secret.person@contoso.com"


def person(graph, right, kind="User", **extra):
    return {"graphId": graph, "identifier": extra.pop("identifier", graph), "principalType": kind,
            "displayName": SECRET_NAME, "emailAddress": SECRET_MAIL, "datasetUserAccessRight": right, **extra}


def workspace():
    return {"id": "w", "users": [{"graphId": ROLE, "identifier": "role@contoso.com", "principalType": "User"}],
            "datasets": [{"id": "d1", "users": [
                person(ROLE.upper(), "ReadWriteReshareExplore"), person(DIRECT, "ReadReshare"), person(GROUP, "Read", "Group"),
                person(APP, "ReadReshare", "App"), person(GUEST, "Read", userType="Guest", identifier="x#EXT#@t.onmicrosoft.com")]}],
            "Lakehouse": [{"id": "l1", "users": []}], "Notebook": [{"id": "n1"}]}


def test_the_scan_asks_for_artifact_users_and_nothing_that_can_hold_code_or_credentials():
    assert GET_INFO_PARAMS["getArtifactUsers"] == "true"
    assert {GET_INFO_PARAMS[k] for k in ("datasourceDetails", "datasetSchema", "datasetExpressions")} == {"false"}


def test_access_lists_become_counts_against_the_workspace_roles():
    reduced = reduce_artifact_users(workspace())
    assert reduced["datasets"][0]["accessSummary"] == {
        "principalCount": 5, "directGrantCount": 4, "directReshareCount": 2,
        "directGroupCount": 1, "directAppCount": 1, "directGuestCount": 1}
    assert reduced["Lakehouse"][0]["accessSummary"]["principalCount"] == 0
    assert "accessSummary" not in reduced["Notebook"][0]


def test_no_principal_survives_in_what_is_written():
    text = json.dumps(reduce_artifact_users(workspace()))
    for leaked in (SECRET_NAME, SECRET_MAIL, DIRECT, GROUP, APP, GUEST, "#EXT#"):
        assert leaked not in text
    assert '"users": [{"graphId": "%s"' % ROLE in text  # the workspace role list itself is unchanged


def test_the_collector_writes_the_reduced_workspaces(tmp_path, monkeypatch):
    from unittest.mock import Mock
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    monkeypatch.setattr(scanner_api, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda headers: ["w"])
    monkeypatch.setattr(scanner_api._http, "collect_workspace_groups", lambda url, headers: [{"id": "w", "type": "Workspace"}])
    monkeypatch.setattr(scanner_api, "resolve_workspace_scope", lambda rows, headers, out: rows)
    monkeypatch.setattr(scanner_api, "_start_scan", lambda headers, batch: "scan")
    monkeypatch.setattr(scanner_api, "_wait_for_scan", lambda headers, scan: None)
    monkeypatch.setattr(scanner_api, "_fetch_scan_result", lambda headers, scan: {"workspaces": [workspace()]})
    written = scanner_api.collect(tmp_path).read_text(encoding="utf-8")
    assert SECRET_MAIL not in written and SECRET_NAME not in written
    assert json.loads(written)["workspaces"][0]["datasets"][0]["accessSummary"]["directGrantCount"] == 4

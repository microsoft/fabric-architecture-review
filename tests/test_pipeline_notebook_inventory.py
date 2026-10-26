# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from unittest.mock import Mock

import pytest
import requests

from collectors import _http, pipelines_notebooks as pipelines


def response(status, value):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(value).encode()
    return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(_http.time, "sleep", lambda _: None)
    monkeypatch.setattr(_http.requests, "request", Mock(side_effect=AssertionError("Unexpected network")))
    monkeypatch.setenv("WORKSPACE_IDS", "")
    monkeypatch.setattr(pipelines, "get_default_provider", lambda: Mock(headers=lambda **_: {}))


def test_pipeline_inventory_failure_still_collects_notebooks(tmp_path, monkeypatch):
    monkeypatch.setattr(pipelines, "_load_workspaces", lambda _: [("ws", "Workspace")])
    monkeypatch.setattr(pipelines, "paginate_value", Mock(side_effect=[
        _http.HttpError("secret https://private.invalid", status_code=403), [{"id": "notebook"}],
    ]))
    raw = json.loads(pipelines.collect(tmp_path).read_text())
    assert raw["notebooks"][0]["id"] == "notebook"
    assert raw["inventoryErrors"][0]["statusCode"] == 403
    assert "secret" not in json.dumps(raw)


@pytest.mark.parametrize("endpoint,inventory_key", [
    ("dataPipelines", "pipelines"), ("notebooks", "notebooks"),
])
def test_inventory_later_page_failure_retains_items(
    tmp_path, monkeypatch, endpoint, inventory_key,
):
    monkeypatch.setattr(pipelines, "_load_workspaces", lambda _: [("ws", "Workspace")])
    listing = f"{pipelines.FAB}/workspaces/ws/{endpoint}"
    next_page = listing + "?continuationToken=next"

    def send(method, url, **kwargs):
        if url == listing:
            return response(200, {"value": [{"id": "retained"}], "continuationUri": next_page})
        if url == next_page:
            return response(403, {"errorCode": "InsufficientPrivileges", "message": "private response"})
        return response(200, {"value": []})

    requests_mock = Mock(side_effect=send)
    monkeypatch.setattr(_http.requests, "request", requests_mock)
    raw = json.loads(pipelines.collect(tmp_path).read_text())
    assert [item["id"] for item in raw[inventory_key]] == ["retained"]
    assert raw["collectionComplete"] is False
    assert raw["inventoryErrors"][0]["collectionStatus"] == "partial"
    assert raw["inventoryErrors"][0]["observedCount"] == 1
    assert "private response" not in json.dumps(raw)


@pytest.mark.parametrize("status,code", [
    (400, "WorkspaceTypeNotSupported"), (401, "Unauthorized"),
    (403, "InsufficientPrivileges"), (404, "EntityNotFound"), (500, "InternalError"),
])
def test_inventory_errors_identify_workspace_and_safe_service_code(
    tmp_path, monkeypatch, capsys, status, code,
):
    monkeypatch.setattr(pipelines, "_load_workspaces", lambda _: [("ws", "Workspace")])

    def send(method, url, **kwargs):
        if url.endswith("/dataPipelines"):
            return response(status, {"errorCode": code, "message": "private response"})
        return response(200, {"value": []})

    monkeypatch.setattr(_http.requests, "request", Mock(side_effect=send))
    raw = json.loads(pipelines.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert raw["failedWorkspaces"] == ["ws"]
    assert raw["inventoryErrors"] == [{
        "workspaceId": "ws", "workspaceName": "Workspace", "itemType": "DataPipeline",
        "statusCode": status, "errorCode": code, "noticeCode": "inventory_request_failed",
        "collectionStatus": "unavailable", "observedCount": 0,
    }]
    output = capsys.readouterr().out
    assert "workspace ws" in output
    assert code in output
    assert "private response" not in output + json.dumps(raw)


def test_pipeline_scope_filters_unscoped_upstream_inventory(tmp_path, monkeypatch):
    wanted, other = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    monkeypatch.setenv("WORKSPACE_IDS", wanted)
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": wanted, "name": "Wanted"}, {"id": other, "name": "Other"}],
    }))
    assert pipelines._load_workspaces(tmp_path) == [(wanted, "Wanted")]

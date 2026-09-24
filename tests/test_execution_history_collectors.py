# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from unittest.mock import Mock

import pytest
import requests

from collectors import _http, pipelines_notebooks as pipelines, semantic_models
from reports.execution_history import build_execution_evidence


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
    provider = Mock(headers=lambda **_: {})
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: provider)
    monkeypatch.setattr(pipelines, "get_default_provider", lambda: provider)


def test_fabric_uses_only_tokens_fixed_endpoint_and_fresh_headers(monkeypatch):
    headers = Mock(side_effect=[{"Authorization": "first"}, {"Authorization": "second"}])
    send = Mock(side_effect=[
        response(200, {"value": [{"id": "a"}], "continuationToken": "opaque",
                       "continuationUri": "https://untrusted.invalid/steal"}),
        response(200, {"value": [{"id": "b"}]}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    result = pipelines._job_history(headers, "ws", "item")
    assert result.status == "collected"
    assert len(result.records) == 2
    assert send.call_args_list[0].kwargs["params"] is None
    assert send.call_args_list[1].kwargs["params"] == {"continuationToken": "opaque"}
    assert send.call_args_list[0].args[1] == send.call_args_list[1].args[1]
    assert [call.kwargs["headers"]["Authorization"] for call in send.call_args_list] == ["first", "second"]


def test_page_bound_is_measured_and_partial(monkeypatch):
    send = Mock(side_effect=[
        response(200, {"value": [{"id": str(index)}], "continuationToken": str(index)})
        for index in range(10)
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    result = pipelines._job_history({}, "ws", "item")
    assert send.call_count == pipelines.JOB_INSTANCE_MAX_PAGES == 5
    assert result.status == "partial"
    assert result.notice == "collection_limit"


def test_record_bound_is_measured_and_partial(monkeypatch):
    send = Mock(return_value=response(200, {"value": [{"id": str(i)} for i in range(1001)]}))
    monkeypatch.setattr(_http.requests, "request", send)
    result = pipelines._job_history({}, "ws", "item")
    assert len(result.records) == pipelines.JOB_INSTANCE_MAX_RECORDS == 1000
    assert result.status == "partial"
    assert send.call_count == 1


@pytest.mark.parametrize("token", [42, {}, "repeat"])
def test_invalid_or_repeated_continuation_cannot_loop(monkeypatch, token):
    send = Mock(return_value=response(200, {"value": [{"id": "a"}], "continuationToken": token}))
    monkeypatch.setattr(_http.requests, "request", send)
    result = pipelines._job_history({}, "ws", "item")
    assert result.status == "partial"
    assert send.call_count <= 2
    assert result.notice == "invalid_continuation"


def test_later_page_denied_keeps_explicit_partial_evidence(monkeypatch):
    send = Mock(side_effect=[
        response(200, {"value": [{"id": "a"}], "continuationToken": "next"}),
        response(403, {"message": "Bearer secret; https://private.invalid"}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    result = pipelines._job_history({}, "ws", "item")
    assert result.status == "partial"
    assert result.status_code == 403
    assert result.records == [{"id": "a"}]
    assert "secret" not in result.notice


def test_job_history_rejects_mismatched_item_but_retains_matching_records(monkeypatch):
    send = Mock(side_effect=[
        response(200, {"value": [{"id": "a", "itemId": "ITEM"},
                                 {"id": "foreign", "itemId": "another-item"}],
                       "continuationToken": "next"}),
        response(200, {"value": [{"id": "b", "itemId": "item"}]}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    result = pipelines._job_history({}, "ws", "item")
    assert [row["id"] for row in result.records] == ["a", "b"]
    assert result.status == "partial"
    assert result.notice == "mismatched_execution_item_id"


def test_item_failure_does_not_skip_other_items_or_notebooks(tmp_path, monkeypatch):
    monkeypatch.setattr(pipelines, "_load_workspaces", lambda _: [("ws", "Workspace")])
    monkeypatch.setattr(pipelines, "paginate_value", Mock(side_effect=[
        [{"id": "denied"}, {"id": "ok"}, {"name": "missing-id"}], [{"id": "notebook"}],
    ]))
    history = Mock(side_effect=[
        pipelines._JobHistory([], "forbidden", "request_failed", 403),
        pipelines._JobHistory([], "empty", "retained_jobs"),
        pipelines._JobHistory([], "empty", "retained_jobs"),
    ])
    monkeypatch.setattr(pipelines, "_job_history", history)
    raw = json.loads(pipelines.collect(tmp_path).read_text())
    assert history.call_count == 3
    assert [entry["collectionStatus"] for entry in raw["executionEvidence"]] == [
        "forbidden", "empty", "not_collected", "empty",
    ]
    assert raw["collectionComplete"] is False
    assert raw["jobs"] == {"ok": [], "notebook": []}
    assert raw["failedJobs"] == {}


def test_pipeline_inventory_failure_still_collects_notebooks(tmp_path, monkeypatch):
    monkeypatch.setattr(pipelines, "_load_workspaces", lambda _: [("ws", "Workspace")])
    monkeypatch.setattr(pipelines, "paginate_value", Mock(side_effect=[
        _http.HttpError("secret https://private.invalid", status_code=403), [{"id": "notebook"}],
    ]))
    monkeypatch.setattr(pipelines, "_job_history", lambda *_: pipelines._JobHistory([], "empty", "retained_jobs"))
    raw = json.loads(pipelines.collect(tmp_path).read_text())
    assert raw["notebooks"][0]["id"] == "notebook"
    assert raw["inventoryErrors"][0]["statusCode"] == 403
    assert "secret" not in json.dumps(raw)


@pytest.mark.parametrize("endpoint,inventory_key", [
    ("dataPipelines", "pipelines"), ("notebooks", "notebooks"),
])
def test_inventory_later_page_failure_retains_items_and_their_jobs(
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
    assert raw["jobs"] == {"retained": []}
    assert raw["executionEvidence"][0]["itemId"] == "retained"
    assert raw["collectionComplete"] is False
    assert raw["inventoryErrors"][0]["collectionStatus"] == "partial"
    assert raw["inventoryErrors"][0]["observedCount"] == 1
    assert any(call.args[1].endswith("/items/retained/jobs/instances")
               for call in requests_mock.call_args_list)
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


def test_refresh_bound_native_request_id_and_redacted_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_models, "_list_datasets_admin", lambda *_: [
        {"id": "one", "workspaceId": "ws"}, {"id": "two", "workspaceId": "ws"}, {"name": "missing-id"},
    ])
    send = Mock(side_effect=[
        response(200, {"value": [{"requestId": "r", "status": "Completed", "startTime": "2026-09-01T00:00:00Z"}]}),
        response(403, {"message": "secret https://private.invalid"}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    raw = json.loads(semantic_models.collect(tmp_path).read_text())
    assert all(call.kwargs["params"] == {"$top": 10} for call in send.call_args_list)
    assert [entry["collectionStatus"] for entry in raw["executionEvidence"]] == [
        "collected", "forbidden", "not_collected",
    ]
    assert raw["refreshes"]["one"][0]["requestId"] == "r"
    assert raw["refreshErrors"]["two"]["statusCode"] == 403
    assert "secret" not in json.dumps(raw["executionEvidence"])


def test_refresh_rejects_undocumented_pagination_without_following_urls(monkeypatch):
    send = Mock(return_value=response(200, {"value": [], "@odata.nextLink": "https://untrusted.invalid"}))
    monkeypatch.setattr(_http.requests, "request", send)
    with pytest.raises(_http.HttpError, match="Invalid refresh history"):
        semantic_models._refresh_history({}, "ws", "item")
    assert send.call_count == 1


def test_failed_jobs_are_filtered_locally_and_raw_legacy_fields_remain(tmp_path, monkeypatch):
    monkeypatch.setattr(pipelines, "_load_workspaces", lambda _: [("ws", "Workspace")])
    monkeypatch.setattr(pipelines, "paginate_value", Mock(side_effect=[[{"id": "pipeline"}], []]))
    record = {"id": "job", "status": "Failed", "startTimeUtc": "2099-01-01T00:00:00Z",
              "failureReason": {"message": "sensitive diagnostics"}}
    records = [
        record,
        {"id": "completed", "status": "Completed", "startTimeUtc": record["startTimeUtc"]},
        {"id": "cancelled", "status": "Cancelled", "startTimeUtc": record["startTimeUtc"]},
        {"id": "old-failure", "status": "Failed", "startTimeUtc": "2000-01-01T00:00:00Z"},
    ]
    monkeypatch.setattr(pipelines, "_job_history", lambda *_: pipelines._JobHistory(records, "collected", "retained_jobs"))
    raw = json.loads(pipelines.collect(tmp_path).read_text())
    assert raw["jobs"]["pipeline"] == records
    assert raw["failedJobs"]["pipeline"] == [record]
    assert raw["failedJobsHistoryScope"] == "filtered_recent_retained_observations"
    assert "failureReason" not in raw["executionEvidence"][0]["executions"][0]
    public = build_execution_evidence(tmp_path, "run", "2026-09-22T06:00:00Z")
    assert "sensitive" not in json.dumps(public)
    assert next(row for row in public["gold_item_executions"]
                if row["execution_id"] == "job")["status"] == "failed"

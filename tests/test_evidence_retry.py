# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Selective retries retain successful evidence and respect access and time boundaries."""
import json
import time
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from collectors import _http, evidence_retry, pipeline_definitions, onelake_roles, pipelines_notebooks
from collectors.access_grant import Settings
from tests.test_access_grant import WS, WS2, M1, M2, make


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    provider = Mock()
    provider.headers.return_value = {"Authorization": "synthetic"}
    monkeypatch.setattr(evidence_retry, "get_default_provider", lambda: provider)
    monkeypatch.setattr(pipeline_definitions, "get_default_provider", lambda: provider)


def write(tmp_path, filename, payload):
    path = tmp_path / filename
    path.write_text(json.dumps(payload))
    return path


def test_96_definitions_retry_only_two_missing_items_not_76_successes_or_18_existing_assignments(tmp_path, monkeypatch):
    grant, holder, _, _ = make(tmp_path, settings=Settings(True, "auto", "required_evidence"))
    factory = grant._api_factory

    def api(provider):
        result = factory(provider)
        listed = result.listed
        result.listed = lambda workspace, principal: workspace == WS2 or listed(workspace, principal)
        return result

    grant._api_factory = api
    items = [{"id": str(i), "workspaceId": WS2 if i < 18 else WS} for i in range(96)]
    write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": items[:25], "notebooks": items[25:], "collectionComplete": True,
    })
    calls = Counter()

    def fetch(headers, ws, item):
        calls[item] += 1
        return None, "http_401" if int(item) < 18 else "not_found" if int(item) >= 94 else None

    monkeypatch.setattr(pipeline_definitions, "_get_definition", fetch)
    monkeypatch.setattr(pipeline_definitions, "get_scope_workspace_ids", lambda: {WS, WS2})
    producer = Mock(side_effect=lambda: pipeline_definitions.collect(tmp_path))
    path = grant.retry_collection(producer, {WS, WS2}, "collectors.pipeline_definitions")
    producer.assert_called_once()
    assert sum(calls.values()) == 102  # 96 initial, two failed items on three attempts.
    assert all(calls[str(i)] == 1 for i in range(94))
    assert calls["94"] == calls["95"] == 4
    assert [call for call in holder["api"].calls if call[0] == "add"] == [("add", WS, "Contributor")]
    assert grant.ledger.entries() == [] and not grant.failures
    result = json.loads(path.read_text())
    assert result["errors"] == 20 and result["collectionComplete"] is False
    assert len(result["pipelines"]) == 25 and len(result["notebooks"]) == 71


@pytest.mark.parametrize("row,kind", [
    ({"error": "http_401"}, "access"), ({"error": "http_403"}, "access"),
    ({"error": "not_found"}, "not_found"), ({"error": "http_429"}, "transient"),
    ({"statusCode": 503}, "transient"), ({"error": "lro_failed"}, "other"),
    ({"statusCode": 403, "errorCode": "UnsupportedOperation"}, "other"),
    ({"statusCode": 401, "errorCode": "InvalidToken"}, "other"),
])
def test_failure_classification(row, kind):
    assert evidence_retry.failure_kind(row) == kind


def test_unsupported_definition_is_not_misclassified_as_permission_failure(monkeypatch):
    response = SimpleNamespace(status_code=403, content=b"json",
                               json=lambda: {"errorCode": "OperationNotSupported", "message": "SECRET"})
    monkeypatch.setattr(pipeline_definitions, "request", lambda *args: response)
    assert pipeline_definitions._get_definition({}, WS, M1) == (None, "unsupported_operation")


def test_selective_definition_retry_preserves_other_scope_and_successes(tmp_path, monkeypatch):
    original = [
        {"workspaceId": WS, "id": M1, "summary": "success"},
        {"workspaceId": WS2, "id": M1, "error": "http_403"},
        {"workspaceId": WS, "id": M2, "error": "http_403"},
        {"workspaceId": WS, "id": "transient", "error": "http_503"},
    ]
    path = write(tmp_path, "pipeline_definitions.json", {
        "pipelines": original, "notebooks": [], "sourceCollectionComplete": False, "errors": 3,
    })
    fetch = Mock(return_value=(None, None))
    monkeypatch.setattr(pipeline_definitions, "_get_definition", fetch)
    evidence_retry.retry_failed(path, {WS}, "collectors.pipeline_definitions", time.monotonic() + 120)
    assert fetch.call_count == 1
    assert fetch.call_args.args[1:] == (WS, M2)
    result = json.loads(path.read_text())
    assert result["pipelines"][:2] == original[:2]
    assert result["pipelines"][3] == original[3]
    assert result["errors"] == 2 and result["collectionComplete"] is False


def test_onelake_retry_is_per_item_and_successes_are_not_repeated(tmp_path, monkeypatch):
    path = write(tmp_path, "onelake_roles.json", {
        "lakehouses": [{"workspaceId": WS, "id": item, "status": "unavailable"} for item in (M1, M2)],
        "collectionErrors": [{"workspaceId": WS, "id": item, "statusCode": 403} for item in (M1, M2)],
        "collectionComplete": False,
    })
    calls = []

    def read(ws, item, headers):
        calls.append(item)
        if item == M2 and calls.count(item) == 1:
            return {"workspaceId": ws, "id": item, "status": "unavailable"}, [
                {"workspaceId": ws, "id": item, "statusCode": 403}]
        return {"workspaceId": ws, "id": item, "status": "collected", "roleCount": 1}, []

    monkeypatch.setattr(onelake_roles, "read_roles", read)
    for _ in range(2):
        evidence_retry.retry_failed(path, {WS}, "collectors.onelake_roles", time.monotonic() + 120)
    assert calls == [M1, M2, M2]
    assert json.loads(path.read_text())["collectionComplete"] is True


def test_inventory_retries_only_failed_item_type_and_keeps_partial_items(tmp_path, monkeypatch):
    path = write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": [{"workspaceId": WS, "id": M1}], "notebooks": [{"workspaceId": WS, "id": M2}],
        "inventoryErrors": [{"workspaceId": WS, "itemType": "DataPipeline", "statusCode": 403}],
        "collectionComplete": False,
    })
    read = Mock(return_value=([], [{"workspaceId": WS, "itemType": "DataPipeline", "statusCode": 503}]))
    monkeypatch.setattr(pipelines_notebooks, "read_inventory", read)
    for _ in range(2):
        evidence_retry.retry_failed(path, {WS}, "collectors.pipelines_notebooks", time.monotonic() + 120)
    assert read.call_count == 1
    assert read.call_args.args[:3] == (WS, "", "DataPipeline")
    result = json.loads(path.read_text())
    assert result["pipelines"] == [{"workspaceId": WS, "id": M1}]
    assert result["notebooks"] == [{"workspaceId": WS, "id": M2}]
    assert result["collectionComplete"] is False


def test_deadline_preserves_partial_success_and_unattempted_items(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(evidence_retry.time, "monotonic", lambda: clock[0])
    path = write(tmp_path, "pipeline_definitions.json", {
        "pipelines": [{"workspaceId": WS, "id": item, "error": "http_403"} for item in (M1, M2)],
        "notebooks": [], "sourceCollectionComplete": True,
    })

    def fetch(*args):
        clock[0] += 2
        return None, None

    monkeypatch.setattr(pipeline_definitions, "_get_definition", fetch)
    with pytest.raises(_http.HttpError, match="time limit"):
        evidence_retry.retry_failed(path, {WS}, "collectors.pipeline_definitions", 1.0)
    result = json.loads(path.read_text())
    assert not result["pipelines"][0].get("error") and result["pipelines"][1]["error"] == "http_403"
    assert result["errors"] == 1 and result["collectionComplete"] is False


def test_http_retry_after_cannot_exceed_budget_and_cleanup_context_is_restored(monkeypatch):
    response = SimpleNamespace(status_code=429, headers={"Retry-After": "600"})
    request = Mock(return_value=response)
    sleep = Mock()
    monkeypatch.setattr(_http.requests, "request", request)
    monkeypatch.setattr(_http.time, "sleep", sleep)
    with pytest.raises(_http.HttpError, match="time limit"):
        with _http.request_deadline(time.monotonic() + 5):
            _http.request("GET", "https://example.invalid", {})
    assert request.call_count == 1
    assert 0 < request.call_args.kwargs["timeout"] <= 5
    sleep.assert_not_called()
    assert _http.remaining_time() is None


def test_budget_exhaustion_removes_grant_and_is_audited(tmp_path):
    grant, holder, _, _ = make(tmp_path, settings=Settings(True, "auto", "required_evidence"))
    path = write(tmp_path, "pipeline_definitions.json", {
        "pipelines": [{"workspaceId": WS, "id": M1, "error": "http_403"}], "notebooks": [],
    })
    retry = Mock(side_effect=_http.HttpError("time limit", error_code="RetryDeadlineExceeded"))
    grant.retry_collection(lambda: path, {WS}, "collectors.pipeline_definitions", retry=retry)
    assert retry.call_count == 1
    assert grant.audit[0]["status"] == "retry_budget_exhausted"
    assert grant.audit[0]["removed"] is True and not holder["api"].present
    assert not grant.ledger.entries()

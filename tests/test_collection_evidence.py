# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Failed and partial collection must never masquerade as successful emptiness."""
from __future__ import annotations

import importlib
import json
from unittest.mock import Mock

import pytest
import requests

from tests._raw import load_raw
from collectors import (
    _http, capacity_metrics, scanner_api, semantic_models, workspace_inventory,
)
from collectors._common import load_complete_raw, load_workspace_inventory, record_collection_failure


def response(status, payload=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload if payload is not None else {}).encode()
    return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_, **kw: None)
    monkeypatch.setattr(_http.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        _http.requests, "request",
        Mock(side_effect=AssertionError("Unexpected network request")),
    )
    monkeypatch.setattr(semantic_models, "get_scope_workspace_ids", lambda: set())


@pytest.mark.parametrize("status,attempts", [(403, 1), (500, _http.MAX_RETRIES)])
def test_get_json_http_failure_raises(monkeypatch, status, attempts):
    send = Mock(return_value=response(status, {"error": {"code": "Unavailable"}}))
    monkeypatch.setattr(_http.requests, "request", send)
    with pytest.raises(_http.HttpError) as raised:
        _http.get_json("https://example.invalid/models", {})
    assert raised.value.status_code == status
    assert send.call_count == attempts


@pytest.mark.parametrize("payload,code", [
    ({"errorCode": "UnsupportedWorkspaceType", "message": "private details"}, "UnsupportedWorkspaceType"),
    ({"error": {"code": "PowerBIEntityNotFound", "message": "private details"}}, "PowerBIEntityNotFound"),
    ({"errorCode": "invalid code: private details"}, None),
])
def test_http_failure_reports_only_safe_service_code(monkeypatch, payload, code):
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(400, payload)))
    with pytest.raises(_http.HttpError) as raised:
        _http.get_json("https://example.invalid/items", {})
    assert raised.value.status_code == 400
    assert raised.value.error_code == code
    assert "private details" not in str(raised.value)
    if code:
        assert code in str(raised.value)


def test_explicit_error_allowlist_does_not_make_error_payload_data(monkeypatch):
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(403)))
    with pytest.raises(_http.HttpError):
        _http.get_json("https://example.invalid/models", {}, allow=(200, 403))


def test_exhausted_transport_error_is_not_empty(monkeypatch):
    send = Mock(side_effect=requests.ConnectionError("unavailable"))
    monkeypatch.setattr(_http.requests, "request", send)
    with pytest.raises(_http.HttpError):
        _http.collect_value("https://example.invalid/models", {})
    assert send.call_count == _http.MAX_RETRIES


@pytest.mark.parametrize("body", [b"", b"<html>not json</html>"])
def test_invalid_success_body_is_not_empty(monkeypatch, body):
    result = response(200)
    result._content = body
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=result))
    with pytest.raises(_http.HttpError):
        _http.get_json("https://example.invalid/models", {})


def test_headers_provider_is_resolved_for_every_retry_and_page(monkeypatch):
    headers = Mock(side_effect=[
        {"Authorization": "first"}, {"Authorization": "retry"}, {"Authorization": "next"},
    ])
    send = Mock(side_effect=[
        response(500),
        response(200, {"value": [{"id": "model"}], "continuationUri": "https://example.invalid/next"}),
        response(200, {"value": []}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    assert _http.collect_value("https://example.invalid/models", headers) == [{"id": "model"}]
    assert [call.kwargs["headers"]["Authorization"] for call in send.call_args_list] == [
        "first", "retry", "next",
    ]


def test_semantic_collection_uses_fresh_headers_for_inventory_pages_only(tmp_path, monkeypatch):
    provider = Mock()
    provider.headers.side_effect = [{"Authorization": str(i)} for i in range(5)]
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: provider)
    send = Mock(side_effect=[
        response(200, {"value": [{"id": "workspace", "type": "Workspace"}]}),
        response(200, {
            "value": [{"id": "first", "workspaceId": "workspace"}],
            "@odata.nextLink": "https://example.invalid/next",
        }),
        response(200, {"value": [{"id": "second", "workspaceId": "workspace"}]}),
        response(200, {"value": []}),
        response(200, {"value": []}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    json.loads(semantic_models.collect(tmp_path).read_text())
    assert [call.kwargs["headers"]["Authorization"] for call in send.call_args_list] == [
        "0", "1", "2",
    ]


@pytest.mark.parametrize("module_name", ["pipeline_definitions", "semantic_model_definitions"])
def test_definition_long_running_requests_refresh_headers_on_retry_and_poll(monkeypatch, module_name):
    module = importlib.import_module(f"collectors.{module_name}")
    pending = response(202)
    pending.headers["Location"] = "https://example.invalid/operation"
    headers = Mock(side_effect=[{"Authorization": str(i)} for i in range(4)])
    send = Mock(side_effect=[
        pending, response(500), response(200, {"status": "Succeeded"}),
        response(200, {"definition": {"parts": []}}),
    ])
    monkeypatch.setattr(_http.requests, "request", send)
    definition, error = module._get_definition(headers, "workspace", "item")
    assert error is None
    assert definition == {"definition": {"parts": []}}
    assert [call.kwargs["headers"]["Authorization"] for call in send.call_args_list] == [
        "0", "1", "2", "3",
    ]


@pytest.mark.parametrize("status", [403, 500])
@pytest.mark.parametrize("link", ["@odata.nextLink", "continuationUri", "nextLink"])
def test_later_page_failure_is_not_a_complete_list(monkeypatch, status, link):
    first = response(200, {"value": [{"id": "first"}], link: "https://example.invalid/page2"})
    send = Mock(side_effect=[first] + [response(status)] * _http.MAX_RETRIES)
    monkeypatch.setattr(_http.requests, "request", send)
    with pytest.raises(_http.HttpError):
        _http.collect_value("https://example.invalid/page1", {}, params={"$top": 1})
    assert send.call_args.kwargs["params"] is None


def test_successful_empty_list_is_complete(monkeypatch):
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(200, {"value": []})))
    assert _http.collect_value("https://example.invalid/models", {}) == []


@pytest.mark.parametrize("payload", [{}, {"error": {}}, {"value": None}, {"value": [None]}])
def test_malformed_list_is_not_empty_evidence(monkeypatch, payload):
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(200, payload)))
    with pytest.raises(_http.HttpError):
        _http.collect_value("https://example.invalid/models", {})


@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("count", [0, 1, 4999, 5000, 5001, 10000])
def test_workspace_groups_exhaust_top_skip_without_continuation(monkeypatch, admin, count):
    rows = [{"id": f"workspace-{i}"} for i in range(count)]
    get = Mock(side_effect=lambda url, headers, *, params: {
        "value": rows[params["$skip"]:params["$skip"] + params["$top"]],
    })
    monkeypatch.setattr(_http, "get_json", get)
    artifacts = Mock(return_value=[])
    monkeypatch.setattr(_http, "collect_value", artifacts)
    url = "https://api.powerbi.com/v1.0/myorg/" + ("admin/" if admin else "") + "groups"
    headers = lambda: {}
    assert _http.collect_workspace_groups(url, headers) == rows
    assert [call.kwargs["params"] for call in get.call_args_list] == [
        {"$top": 5000, "$skip": skip, **({"$expand": "datasets,reports"} if admin else {})}
        for skip in range(0, count + 1, 5000)
    ]
    assert all(call.args == (url, headers) for call in get.call_args_list)
    assert artifacts.call_count == (0 if admin else count * 2)


@pytest.mark.parametrize("page", [
    {}, {"error": {}, "value": []}, {"value": None}, {"value": [None]}, {"value": [{}]}, {"value": [{"id": " "}]},
    {"value": [{"id": 42}]}, {"value": [{"id": "a"}, {"id": "A"}]},
    {"value": [{"id": f"workspace-{i}"} for i in range(5001)]},
    *[{"value": [], key: "unexpected"} for key in (
        "@odata.nextLink", "nextLink", "continuationUri", "continuationToken",
    )],
])
def test_workspace_groups_reject_invalid_pages(monkeypatch, page):
    monkeypatch.setattr(_http, "get_json", Mock(return_value=page))
    with pytest.raises(_http.HttpError):
        _http.collect_workspace_groups("https://example.invalid/admin/groups", {})


def test_workspace_groups_repeated_page_fails_instead_of_looping(monkeypatch):
    get = Mock(return_value={"value": [{"id": str(i)} for i in range(5000)]})
    monkeypatch.setattr(_http, "get_json", get)
    with pytest.raises(_http.HttpError, match="repeated"):
        _http.collect_workspace_groups("https://example.invalid/admin/groups", {})
    assert get.call_count == 2


@pytest.mark.parametrize("module", [semantic_models, scanner_api, workspace_inventory])
@pytest.mark.parametrize("status", [403, 503])
def test_late_workspace_page_failure_stops_probes_without_member_fallback(
    tmp_path, monkeypatch, module, status,
):
    rows = [{"id": str(i)} for i in range(5000)]
    get = Mock(side_effect=[
        {"value": rows}, _http.HttpError("Page failed", status_code=status),
    ])
    monkeypatch.setattr(_http, "get_json", get)
    monkeypatch.setattr(module, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda _: ["0"])
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    probe_names = {
        semantic_models: "_datasets_for_groups", scanner_api: "_start_scan",
        workspace_inventory: "_list_items",
    }
    probe = Mock(side_effect=AssertionError("No probe after incomplete discovery"))
    monkeypatch.setattr(module, probe_names[module], probe)
    raw = json.loads(module.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert raw["collectionErrors"][0]["statusCode"] == status
    assert get.call_count == 2
    assert get.call_args.args[0].endswith("/admin/groups")
    assert not (tmp_path / "review_scope.json").exists()
    probe.assert_not_called()


@pytest.mark.parametrize("module", [semantic_models, workspace_inventory])
def test_workspace_5001_is_discovered_before_child_probes(tmp_path, monkeypatch, module):
    first = [{"id": str(i), "isOnDedicatedCapacity": False} for i in range(5000)]
    last = {"id": "workspace-5001", "type": "Workspace"}
    get = Mock(side_effect=[{"value": first}, {"value": [last]}])
    monkeypatch.setattr(_http, "get_json", get)
    monkeypatch.setattr(module, "get_default_provider", Mock())
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    model = {"id": "last-model", "name": "Model"}
    if module is workspace_inventory:
        users, items = Mock(return_value=[]), Mock(return_value=[])
        monkeypatch.setattr(module, "_list_users", users)
        monkeypatch.setattr(module, "_list_items", items)
    else:
        datasets = Mock(return_value=[model])
        monkeypatch.setattr(module, "collect_value", datasets)
    raw = json.loads(module.collect(tmp_path).read_text())
    assert raw.get("collectionComplete", True)
    assert get.call_count == 2
    assert get.call_args.kwargs["params"] == {"$top": 5000, "$skip": 5000, "$expand": "datasets,reports"}
    if module is workspace_inventory:
        assert [row["id"] for row in raw["workspaces"]] == [last["id"]]
        assert items.call_count == users.call_count == 1
        assert items.call_args.args[1] == last["id"]
    else:
        assert datasets.call_count == 1
        assert datasets.call_args.args[0].endswith(f"/admin/groups/{last['id']}/datasets")
        assert raw["datasets"] == [{**model, "workspaceId": last["id"], "workspaceName": None}]


def setup_model_collector(monkeypatch):
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(semantic_models, "_list_datasets_admin", lambda *_: [
        {"id": "model", "workspaceId": "workspace", "name": "Model", "isRefreshable": True},
    ])


def test_historical_refresh_rows_are_replaced_by_architecture_inventory(tmp_path, monkeypatch):
    raw = {
        "datasets": [{"id": "known"}, {"id": "unavailable", "isRefreshable": True}],
        "refreshes": {"known": [{"status": "Failed"}, {"status": "Failed"}]},
    }
    (tmp_path / "semantic_models.json").write_text(json.dumps(raw))
    setup_model_collector(monkeypatch)
    fresh = json.loads(semantic_models.collect(tmp_path).read_text())
    assert [d["id"] for d in fresh["datasets"]] == ["model"]
    assert not {"refreshes", "refreshErrors", "executionEvidence"} & fresh.keys()


def test_capacity_permission_fallback_but_not_server_failure(monkeypatch):
    send = Mock(side_effect=[response(403), response(200, {"value": []})])
    monkeypatch.setattr(_http.requests, "request", send)
    assert capacity_metrics._list_capacities({}) == []
    assert send.call_args_list[0].args[1].endswith("/admin/capacities")
    assert send.call_args_list[1].args[1].endswith("/capacities")
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(500)))
    with pytest.raises(_http.HttpError):
        capacity_metrics._list_capacities({})


@pytest.mark.parametrize("scoped", [False, True])
def test_semantic_admin_permission_fallback(tmp_path, monkeypatch, scoped):
    if scoped:
        monkeypatch.setenv("WORKSPACE_IDS", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    send = Mock(side_effect=[response(403), response(200, {"value": []})])
    monkeypatch.setattr(_http.requests, "request", send)
    raw = json.loads(semantic_models.collect(tmp_path).read_text())
    assert raw["adminMode"] is False
    assert raw["datasets"] == []
    assert send.call_args.args[1].endswith("/groups")


def test_successful_empty_admin_inventory_needs_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    send = Mock(return_value=response(200, {"value": []}))
    monkeypatch.setattr(_http.requests, "request", send)
    raw = json.loads(semantic_models.collect(tmp_path).read_text())
    assert raw["adminMode"] is True
    assert send.call_count == 1


def test_capacity_mapping_cannot_infer_empty_capacity_from_failed_workspace_inventory(tmp_path):
    (tmp_path / "workspace_inventory.json").write_text('{"collectionComplete": false}')
    with pytest.raises(_http.HttpError):
        capacity_metrics._workspaces_by_capacity(tmp_path)


def test_capacity_workspace_mapping_failure_preserves_capacity_inventory(tmp_path, monkeypatch):
    capacities = [{"id": cid, "displayName": cid, "sku": "F64", "state": "Active"}
                  for cid in ("first", "second", "third")]
    monkeypatch.setattr(capacity_metrics, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(capacity_metrics, "_list_capacities", lambda _: capacities)
    monkeypatch.setattr(capacity_metrics, "get_run_scope_workspace_ids", lambda: set())
    mapping = {c["id"]: [{"id": f"workspace-{c['id']}", "name": "Workspace"}] for c in capacities}
    monkeypatch.setattr(capacity_metrics, "_workspaces_by_capacity", Mock(
        side_effect=_http.HttpError("Synthetic mapping failure", status_code=403), return_value=mapping,
    ))

    raw = json.loads(capacity_metrics.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert raw["capacityListComplete"] is True
    assert [c["id"] for c in raw["capacities"]] == ["first", "second", "third"]
    assert all(c["assignedWorkspaceCount"] is None for c in raw["capacities"])
    assert raw["summary"]["emptyCapacities"] == []
    assert raw["collectionErrors"][0]["component"] == "workspace_mapping"


def test_the_capacity_list_follows_the_review_scope_not_the_collectors_narrower_one(tmp_path, monkeypatch):
    """The planner hands each collector only the workspaces that need its evidence. That is not a limited review."""
    inside, outside = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    capacities = [{"id": "busy", "displayName": "busy", "sku": "F8", "state": "Active"},
                  {"id": "idle", "displayName": "idle", "sku": "F4", "state": "Active"}]
    monkeypatch.setattr(capacity_metrics, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(capacity_metrics, "_list_capacities", lambda _: capacities)
    monkeypatch.setattr(capacity_metrics, "_workspaces_by_capacity", lambda _: {"busy": [{"id": inside, "name": "w"}]})
    monkeypatch.setenv("WORKSPACE_IDS", inside)  # what the planner gives this collector

    monkeypatch.setenv(capacity_metrics_run_scope(), "")  # the review itself was not limited
    whole = json.loads(capacity_metrics.collect(tmp_path).read_text())
    assert whole["workspaceScopeLimited"] is False
    assert [c["id"] for c in whole["capacities"]] == ["busy", "idle"]
    assert whole["summary"]["emptyCapacities"] == ["idle"]

    monkeypatch.setenv(capacity_metrics_run_scope(), f"{inside},{outside}")  # the review was limited
    limited = json.loads(capacity_metrics.collect(tmp_path).read_text())
    assert limited["workspaceScopeLimited"] is True
    assert [c["id"] for c in limited["capacities"]] == ["busy"]


def test_without_a_planner_the_collector_scope_is_the_review_scope(tmp_path, monkeypatch):
    from collectors._common import get_run_scope_workspace_ids
    monkeypatch.delenv(capacity_metrics_run_scope(), raising=False)
    monkeypatch.setenv("WORKSPACE_IDS", "11111111-1111-4111-8111-111111111111")
    assert get_run_scope_workspace_ids() == {"11111111-1111-4111-8111-111111111111"}
    monkeypatch.delenv("WORKSPACE_IDS")
    assert get_run_scope_workspace_ids() == set()


def capacity_metrics_run_scope():
    from collectors._common import RUN_SCOPE_ENV
    return RUN_SCOPE_ENV


def test_failed_inventory_replaces_stale_evidence(tmp_path, monkeypatch, capsys):
    (tmp_path / "semantic_models.json").write_text('{"datasets": [], "refreshes": {}}')
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(500)))
    target = semantic_models.collect(tmp_path)
    assert json.loads(target.read_text())["collectionComplete"] is False
    assert load_raw(target) is None
    assert "Collection incomplete:" in capsys.readouterr().out


def test_failure_boundary_does_not_hide_programming_errors(tmp_path):
    @record_collection_failure("not-written.json")
    def broken(output_dir):
        raise TypeError("programming error")

    with pytest.raises(TypeError):
        broken(tmp_path)
    assert not (tmp_path / "not-written.json").exists()


def test_legacy_partial_workspace_inventory_is_unavailable(tmp_path):
    target = tmp_path / "pipelines_notebooks.json"
    target.write_text(json.dumps({"pipelines": [], "notebooks": [], "failedWorkspaces": ["workspace"]}))
    assert load_raw(target) is None


def test_legacy_scanner_incompleteness_is_not_accepted_as_complete_inventory(tmp_path):
    scanner = tmp_path / "scanner.json"
    scanner.write_text(json.dumps({
        "_meta": {"complete": False}, "workspaces": [{"id": "partial"}],
    }))
    with pytest.raises(_http.HttpError, match="incomplete"):
        load_complete_raw(scanner)
    with pytest.raises(_http.HttpError, match="complete scanner"):
        load_workspace_inventory(tmp_path)


def test_legacy_partial_scanner_can_fall_back_to_complete_workspace_inventory(tmp_path):
    (tmp_path / "scanner.json").write_text(json.dumps({
        "_meta": {"complete": False}, "workspaces": [{"id": "partial"}],
    }))
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": "complete"}],
    }))
    assert load_workspace_inventory(tmp_path) == [{"id": "complete"}]


@pytest.mark.parametrize("marker", [
    {"collectionComplete": False, "_meta": {"complete": False, "batches_failed": 1}},
    {"_meta": {"complete": False, "batches_failed": 1}},
    {"collectionComplete": False, "collectionErrors": [{"statusCode": 403}]},
    {"collectionComplete": False, "_meta": {"complete": True}},
])
def test_incomplete_metadata_is_hidden_unless_inspection_is_explicit(tmp_path, marker):
    payload = {**marker, "workspaces": [{"id": "partial", "name": "partial"}]}
    scanner = tmp_path / "scanner.json"
    scanner.write_text(json.dumps(payload))
    assert load_raw(scanner) is None
    assert load_raw(scanner, allow_incomplete=True) == payload


def test_meta_without_complete_flag_is_not_treated_as_incomplete(tmp_path):
    scanner = tmp_path / "scanner.json"
    scanner.write_text('{"_meta": {}, "workspaces": []}')
    assert load_raw(scanner) == {"_meta": {}, "workspaces": []}


@pytest.mark.parametrize("name", ["gateways", "pipelines_notebooks"])
def test_collectors_persist_permission_failure_not_empty_inventory(tmp_path, monkeypatch, name):
    module = importlib.import_module(f"collectors.{name}")
    monkeypatch.setattr(module, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=response(403)))
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": "workspace", "name": "Workspace"}],
    }))
    target = module.collect(tmp_path)
    assert json.loads(target.read_text())["collectionComplete"] is False
    assert load_raw(target) is None


@pytest.mark.parametrize("name,source", [
    ("semantic_model_definitions", "semantic_models.json"),
    ("pipeline_definitions", "pipelines_notebooks.json"),
    ("pipelines_notebooks", "workspace_inventory.json"),
    ("git_integration", "workspace_inventory.json"),
])
def test_dependent_collectors_cannot_relabel_unavailable_inventory_as_empty(tmp_path, name, source):
    (tmp_path / source).write_text(json.dumps({"collectionComplete": False}))
    module = importlib.import_module(f"collectors.{name}")
    target = module.collect(tmp_path)
    assert json.loads(target.read_text())["collectionComplete"] is False
    assert load_raw(target) is None


def test_optional_gateway_members_failure_is_not_assumed_single_member(tmp_path, monkeypatch):
    from collectors import gateways

    monkeypatch.setattr(gateways, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(_http.requests, "request", Mock(side_effect=[
        response(200, {"value": [{"id": "gateway"}]}),
        response(200, {"value": []}),
        response(403),
    ]))
    raw = json.loads(gateways.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert "gateways" not in raw

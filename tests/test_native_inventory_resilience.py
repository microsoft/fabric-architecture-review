# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Native inventory failures retain observations without claiming full coverage."""
import json
from unittest.mock import Mock

import pytest
import requests

from analyzers._common import load_raw
from analyzers.architecture_review import analyze
from collectors import _http, lakehouse_warehouse as lh, realtime_intelligence as rti


WORKSPACES = [f"aaaaaaaa-0000-4000-8000-{i:012d}" for i in range(1, 4)]
LAKEHOUSES = [f"bbbbbbbb-0000-4000-8000-{i:012d}" for i in range(1, 5)]
WAREHOUSES = [f"cccccccc-0000-4000-8000-{i:012d}" for i in range(1, 4)]
ITEM = "dddddddd-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": wid, "name": f"Workspace {i}"} for i, wid in enumerate(WORKSPACES)],
    }), encoding="utf-8")
    for module in (lh, rti):
        monkeypatch.setattr(module, "get_default_provider", lambda: Mock(headers=lambda **_: {}))
    monkeypatch.setattr(_http.time, "sleep", lambda _: None)
    monkeypatch.setattr(_http.requests, "request", Mock(side_effect=AssertionError("Unexpected network")))


def respond(status, payload):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


def install_routes(monkeypatch, routes):
    def request(method, url, **kwargs):
        assert method == "GET"
        assert url in routes, f"Unexpected request: {url}"
        return respond(*routes[url])

    send = Mock(side_effect=request)
    monkeypatch.setattr(_http.requests, "request", send)
    return send


def lakehouse_routes():
    routes = {}
    for wid, lhid, whid in zip(WORKSPACES, LAKEHOUSES, WAREHOUSES):
        routes[f"{lh.FAB}/workspaces/{wid}/lakehouses"] = (200, {"value": [{"id": lhid}]})
        routes[f"{lh.FAB}/workspaces/{wid}/warehouses"] = (200, {"value": [{"id": whid}]})
        routes[f"{lh.FAB}/workspaces/{wid}/lakehouses/{lhid}/tables"] = (
            200, {"data": [{"name": f"Table_{lhid[-1]}", "format": "Delta"}]},
        )
    return routes


def rti_routes():
    return {
        f"{rti.FABRIC}/workspaces/{wid}/{kind}": (200, {"value": [{"id": ITEM}]})
        for wid in WORKSPACES for kind in rti.ITEM_KINDS
    }


def read_result(module, tmp_path):
    return json.loads(module.collect(tmp_path).read_text(encoding="utf-8"))


def coverage(raw, wid, component, item=None):
    return next(row for row in raw["collectionCoverage"] if (
        row["workspaceId"] == wid and row["component"] == component
        and (item is None or row.get("itemId") == item)
    ))


@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_table_failure_retains_all_items_and_continues_other_lakehouses(tmp_path, monkeypatch, capsys, status):
    routes = lakehouse_routes()
    wid, failed = WORKSPACES[1], LAKEHOUSES[1]
    routes[f"{lh.FAB}/workspaces/{wid}/lakehouses"][1]["value"].append({"id": LAKEHOUSES[3]})
    routes[f"{lh.FAB}/workspaces/{wid}/lakehouses/{LAKEHOUSES[3]}/tables"] = (200, {"data": []})
    code = "UnknownError"
    routes[f"{lh.FAB}/workspaces/{wid}/lakehouses/{failed}/tables"] = (
        status, {"errorCode": code, "message": "Do not retain service response bodies"},
    )
    install_routes(monkeypatch, routes)
    raw = read_result(lh, tmp_path)
    assert [row["id"] for row in raw["lakehouses"]] == [
        LAKEHOUSES[0], LAKEHOUSES[1], LAKEHOUSES[3], LAKEHOUSES[2],
    ]
    assert [row["id"] for row in raw["warehouses"]] == WAREHOUSES
    assert failed not in raw["tables"]
    assert raw["tables"][LAKEHOUSES[0]][0]["name"] == "Table_1"
    assert raw["tables"][LAKEHOUSES[2]][0]["name"] == "Table_3"
    assert raw["tables"][LAKEHOUSES[3]] == []
    assert coverage(raw, wid, "tables", failed)["collectionStatus"] == "unavailable"
    assert coverage(raw, wid, "tables", LAKEHOUSES[3])["collectionStatus"] == "collected"
    assert raw["collectionComplete"] is False
    assert len(raw["collectionErrors"]) == 1
    assert raw["collectionErrors"][0]["errorCode"] == code
    assert raw["collectionErrors"][0]["itemId"] == failed
    assert raw["collectionErrors"][0]["statusCode"] == status
    assert "Do not retain" not in json.dumps(raw)
    assert "incomplete" in capsys.readouterr().out
    assert load_raw(tmp_path / "lakehouse_warehouse.json") is None
    assert load_raw(tmp_path / "lakehouse_warehouse.json", allow_incomplete=True)["lakehouses"]


@pytest.mark.parametrize("component", ["lakehouses", "warehouses"])
def test_lakehouse_component_failure_does_not_block_other_component_or_workspaces(tmp_path, monkeypatch, component):
    routes = lakehouse_routes()
    routes[f"{lh.FAB}/workspaces/{WORKSPACES[1]}/{component}"] = (400, {"errorCode": "UnknownError"})
    send = install_routes(monkeypatch, routes)
    raw = read_result(lh, tmp_path)
    other = "warehouses" if component == "lakehouses" else "lakehouses"
    assert [item["workspaceId"] for item in raw[component]] == [WORKSPACES[0], WORKSPACES[2]]
    assert [item["workspaceId"] for item in raw[other]] == WORKSPACES
    assert coverage(raw, WORKSPACES[1], component)["collectionStatus"] == "unavailable"
    assert raw["collectionComplete"] is False
    requested = {call.args[1] for call in send.call_args_list}
    assert all(f"{lh.FAB}/workspaces/{wid}/{kind}" in requested
               for wid in WORKSPACES for kind in ("lakehouses", "warehouses"))


@pytest.mark.parametrize("kind", rti.ITEM_KINDS)
@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_rti_failure_isolates_only_one_workspace_kind(tmp_path, monkeypatch, capsys, kind, status):
    routes = rti_routes()
    routes[f"{rti.FABRIC}/workspaces/{WORKSPACES[1]}/{kind}"] = (status, {"errorCode": "UnknownError"})
    send = install_routes(monkeypatch, routes)
    raw = read_result(rti, tmp_path)
    assert raw["collectionComplete"] is False
    assert len(raw["collectionErrors"]) == 1
    assert coverage(raw, WORKSPACES[1], kind)["collectionStatus"] == "unavailable"
    for current in rti.ITEM_KINDS:
        expected = [WORKSPACES[0], WORKSPACES[2]] if current == kind else WORKSPACES
        assert [item["workspaceId"] for item in raw[current]] == expected
        assert raw["summary"][current] == len(expected)
    assert len({call.args[1] for call in send.call_args_list}) == len(WORKSPACES) * len(rti.ITEM_KINDS)
    assert "incomplete" in capsys.readouterr().out


@pytest.mark.parametrize("module,component", [(lh, "lakehouses"), (lh, "warehouses"), (rti, "eventhouses")])
def test_item_later_page_failure_retains_first_page(tmp_path, monkeypatch, module, component):
    routes = lakehouse_routes() if module is lh else rti_routes()
    url = f"{lh.FAB}/workspaces/{WORKSPACES[0]}/{component}"
    next_url = url + "?continuationToken=synthetic"
    routes[url][1]["continuationUri"] = next_url
    routes[next_url] = (400, {"errorCode": "UnknownError"})
    install_routes(monkeypatch, routes)
    raw = read_result(module, tmp_path)
    assert [item["workspaceId"] for item in raw[component]] == WORKSPACES
    assert coverage(raw, WORKSPACES[0], component)["collectionStatus"] == "partial"
    assert coverage(raw, WORKSPACES[0], component)["observedCount"] == 1
    assert raw["collectionComplete"] is False
    if module is lh:
        assert LAKEHOUSES[0] in raw["tables"]


def test_table_later_page_failure_retains_first_page(tmp_path, monkeypatch):
    routes = lakehouse_routes()
    url = f"{lh.FAB}/workspaces/{WORKSPACES[0]}/lakehouses/{LAKEHOUSES[0]}/tables"
    next_url = url + "?continuationToken=synthetic"
    routes[url][1]["continuationUri"] = next_url
    routes[next_url] = (400, {"errorCode": "UnknownError"})
    install_routes(monkeypatch, routes)
    raw = read_result(lh, tmp_path)
    assert len(raw["tables"]) == 3
    assert raw["tables"][LAKEHOUSES[0]][0]["name"] == "Table_1"
    assert coverage(raw, WORKSPACES[0], "tables")["collectionStatus"] == "partial"
    assert raw["collectionComplete"] is False


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": [None]}, {"data": [], "continuationUri": 123}])
def test_malformed_tables_are_unknown_not_empty(tmp_path, monkeypatch, payload):
    routes = lakehouse_routes()
    routes[f"{lh.FAB}/workspaces/{WORKSPACES[0]}/lakehouses/{LAKEHOUSES[0]}/tables"] = (200, payload)
    install_routes(monkeypatch, routes)
    raw = read_result(lh, tmp_path)
    assert LAKEHOUSES[0] not in raw["tables"]
    assert len(raw["lakehouses"]) == len(raw["warehouses"]) == 3
    assert raw["collectionComplete"] is False


@pytest.mark.parametrize("link", ["continuationUri", "continuationToken"])
def test_table_pagination_uses_documented_parameters(monkeypatch, link):
    first = {"data": [{"name": "First"}], link: (
        f"{lh.FAB}/workspaces/{WORKSPACES[0]}/lakehouses/{LAKEHOUSES[0]}/tables?continuationToken=next"
        if link == "continuationUri" else "next"
    )}
    send = Mock(side_effect=[respond(200, first), respond(200, {"data": [{"name": "Last"}]})])
    monkeypatch.setattr(_http.requests, "request", send)
    assert list(lh._tables({}, WORKSPACES[0], LAKEHOUSES[0])) == [{"name": "First"}, {"name": "Last"}]
    assert send.call_args_list[0].kwargs["params"] == {"maxResults": 100}
    assert send.call_args_list[1].kwargs["params"] == (
        None if link == "continuationUri" else {"maxResults": 100, "continuationToken": "next"}
    )


def test_repeated_table_continuation_is_an_explicit_gap(monkeypatch):
    monkeypatch.setattr(_http.requests, "request", Mock(return_value=respond(200, {
        "data": [], "continuationToken": "same",
    })))
    with pytest.raises(_http.HttpError, match="repeats a continuation"):
        list(lh._tables({}, WORKSPACES[0], LAKEHOUSES[0]))


@pytest.mark.parametrize("module", [lh, rti])
def test_successful_empty_inventory_is_known_complete(tmp_path, monkeypatch, module):
    routes = lakehouse_routes() if module is lh else rti_routes()
    install_routes(monkeypatch, {url: (200, {"value": []}) for url in routes})
    raw = read_result(module, tmp_path)
    assert raw["collectionComplete"] is True
    assert raw["collectionErrors"] == []
    assert all(row["collectionStatus"] == "collected" for row in raw["collectionCoverage"])
    assert all(row["observedCount"] == 0 for row in raw["collectionCoverage"])


def test_personal_workspace_is_not_applicable_but_http_400_is_unknown(tmp_path, monkeypatch):
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({"workspaces": [
        {"id": WORKSPACES[0], "type": "PersonalGroup"}, {"id": WORKSPACES[1], "type": "Workspace"},
    ]}), encoding="utf-8")
    routes = rti_routes()
    routes[f"{rti.FABRIC}/workspaces/{WORKSPACES[1]}/eventhouses"] = (400, {})
    send = install_routes(monkeypatch, routes)
    raw = read_result(rti, tmp_path)
    assert all(WORKSPACES[0] not in call.args[1] for call in send.call_args_list)
    assert coverage(raw, WORKSPACES[0], "eventhouses")["collectionStatus"] == "not_applicable"
    assert coverage(raw, WORKSPACES[1], "eventhouses")["collectionStatus"] == "unavailable"
    assert raw["collectionComplete"] is False


@pytest.mark.parametrize("module", [lh, rti])
def test_missing_workspace_source_is_unavailable_not_empty(tmp_path, module):
    (tmp_path / "workspace_inventory.json").unlink()
    raw = read_result(module, tmp_path)
    assert raw["collectionComplete"] is False
    assert raw["collectionErrors"]


@pytest.mark.parametrize("module", [lh, rti])
def test_complete_empty_workspace_source_is_distinct_from_missing(tmp_path, monkeypatch, module):
    (tmp_path / "workspace_inventory.json").write_text('{"workspaces": []}', encoding="utf-8")
    raw = read_result(module, tmp_path)
    assert raw["collectionComplete"] is True
    assert raw["collectionErrors"] == raw["collectionCoverage"] == []


@pytest.mark.parametrize("module", [lh, rti])
def test_all_requests_failed_replaces_stale_success_without_claiming_absence(tmp_path, monkeypatch, module):
    filename = "lakehouse_warehouse.json" if module is lh else "realtime_intelligence.json"
    (tmp_path / filename).write_text('{"collectionComplete": true, "stale": true}', encoding="utf-8")
    routes = lakehouse_routes() if module is lh else rti_routes()
    send = install_routes(monkeypatch, {url: (400, {}) for url in routes})
    raw = read_result(module, tmp_path)
    expected = 6 if module is lh else 15
    assert send.call_count == expected
    assert len(raw["collectionErrors"]) == expected
    assert raw["collectionComplete"] is False
    assert "stale" not in raw
    assert all(row["collectionStatus"] == "unavailable" for row in raw["collectionCoverage"])


@pytest.mark.parametrize("module", [lh, rti])
def test_programming_error_is_not_suppressed(tmp_path, monkeypatch, module):
    monkeypatch.setattr(_http.requests, "request", Mock(side_effect=TypeError("Synthetic programming bug")))
    with pytest.raises(TypeError, match="programming bug"):
        module.collect(tmp_path)


@pytest.mark.parametrize("duplicate", [True, False])
def test_shortcut_analysis_uses_observed_tables_without_passing_incomplete_coverage(tmp_path, monkeypatch, duplicate):
    routes = lakehouse_routes()
    routes[f"{lh.FAB}/workspaces/{WORKSPACES[1]}/lakehouses/{LAKEHOUSES[1]}/tables"] = (400, {})
    if duplicate:
        for index in (0, 2):
            routes[f"{lh.FAB}/workspaces/{WORKSPACES[index]}/lakehouses/{LAKEHOUSES[index]}/tables"] = (
                200, {"data": [{"name": "SharedTable", "schema": "bronze"}]},
            )
    install_routes(monkeypatch, routes)
    read_result(lh, tmp_path)
    finding = next(row for row in analyze(tmp_path) if row["rule_id"] == "ARCH-003")
    assert finding["status"] == ("fail" if duplicate else "unknown")
    assert finding["evidence"]["tableCount"] == 2
    assert finding["evidence"]["collectionComplete"] is False
    if duplicate:
        assert all(
            row["schema"] == "bronze"
            for item in finding["evidence"]["duplicateTableGroups"]
            for row in item["locations"]
        )


def test_rti_analysis_does_not_infer_absent_alerts_from_a_failed_reflex_listing(tmp_path, monkeypatch):
    routes = rti_routes()
    routes[f"{rti.FABRIC}/workspaces/{WORKSPACES[0]}/reflexes"] = (400, {})
    routes[f"{rti.FABRIC}/workspaces/{WORKSPACES[1]}/reflexes"] = (200, {"value": []})
    install_routes(monkeypatch, routes)
    read_result(rti, tmp_path)
    finding = next(row for row in analyze(tmp_path) if row["rule_id"] == "ARCH-010")
    assert finding["status"] == "unknown"
    assert finding["evidence"]["counts"]["eventhouses"] == 3
    assert finding["evidence"]["workspacesWithEventhouseButNoReflex"] == [WORKSPACES[1]]
    assert finding["evidence"]["collectionComplete"] is False

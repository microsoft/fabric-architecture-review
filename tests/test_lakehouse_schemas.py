# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Schema-enabled Lakehouse discovery uses read-only OneLake metadata."""
import json
from unittest.mock import Mock

import pytest
import requests

from collectors import _http, lakehouse_warehouse as lh
from collectors.auth import FABRIC_SCOPE, STORAGE_SCOPE


WS = "aaaaaaaa-0000-4000-8000-000000000001"
LAKE = "bbbbbbbb-0000-4000-8000-000000000001"
BASE = f"{lh.ONELAKE}/{WS}/{LAKE}/api/2.1/unity-catalog"
UNSUPPORTED = "UnsupportedOperationForSchemasEnabledLakehouse"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": WS, "name": "Workspace"}],
    }), encoding="utf-8")
    provider = Mock()
    provider.headers.side_effect = lambda scope: {"X-Test-Audience": scope}
    monkeypatch.setattr(lh, "get_default_provider", lambda: provider)
    monkeypatch.setattr(_http.time, "sleep", lambda _: None)
    routes = {
        (f"{lh.FAB}/workspaces/{WS}/lakehouses", ()): (200, {"value": [
            {"id": LAKE, "displayName": "Lake", "properties": {"defaultSchema": "dbo"}},
        ]}),
        (f"{lh.FAB}/workspaces/{WS}/warehouses", ()): (200, {"value": []}),
        (BASE + "/schemas", (("catalog_name", LAKE),)): (200, {
            "schemas": [{"name": "dbo"}], "next_page_token": None,
        }),
        (BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "dbo"))): (200, {
            "tables": [{"name": "Sales", "schema_name": "dbo", "data_source_format": "DELTA",
                        "storage_location": "https://onelake.dfs.fabric.microsoft.com/metadata-only"}],
        }),
    }

    def send(method, url, **kwargs):
        assert method == "GET"
        assert kwargs["headers"]["X-Test-Audience"] == (STORAGE_SCOPE if url.startswith(lh.ONELAKE) else FABRIC_SCOPE)
        key = (url, tuple(sorted((kwargs.get("params") or {}).items())))
        assert key in routes, f"Unexpected request: {key}"
        status, payload = routes[key]
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response

    request = Mock(side_effect=send)
    monkeypatch.setattr(_http.requests, "request", request)
    return routes, request


def collect(tmp_path):
    return json.loads(lh.collect(tmp_path).read_text())


def test_known_schema_lakehouse_never_calls_unsupported_legacy_endpoint(tmp_path, setup):
    _, request = setup
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is True
    assert raw["collectionErrors"] == []
    assert raw["tables"][LAKE] == [{
        "name": "Sales", "schema": "dbo", "format": "DELTA",
        "location": "https://onelake.dfs.fabric.microsoft.com/metadata-only",
    }]
    assert not any("/lakehouses/" in call.args[1] for call in request.call_args_list)
    assert len(request.call_args_list) == 4


def test_missing_schema_metadata_falls_back_only_for_specific_400(tmp_path, setup):
    routes, _ = setup
    routes[(f"{lh.FAB}/workspaces/{WS}/lakehouses", ())][1]["value"][0].pop("properties")
    routes[(f"{lh.FAB}/workspaces/{WS}/lakehouses/{LAKE}/tables", (("maxResults", 100),))] = (
        400, {"errorCode": UNSUPPORTED},
    )
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is True
    assert raw["tables"][LAKE][0]["schema"] == "dbo"
    assert raw["collectionErrors"] == []


@pytest.mark.parametrize("status,code", [(401, UNSUPPORTED), (403, UNSUPPORTED), (404, UNSUPPORTED), (400, "UnknownError")])
def test_unrelated_legacy_errors_never_trigger_onelake_requests(tmp_path, setup, status, code):
    routes, request = setup
    routes[(f"{lh.FAB}/workspaces/{WS}/lakehouses", ())][1]["value"][0].pop("properties")
    routes[(f"{lh.FAB}/workspaces/{WS}/lakehouses/{LAKE}/tables", (("maxResults", 100),))] = (
        status, {"errorCode": code},
    )
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert raw["collectionErrors"][0]["statusCode"] == status
    assert not any(call.args[1].startswith(lh.ONELAKE) for call in request.call_args_list)


def test_schemas_and_tables_pagination_keep_same_names_in_different_schemas(tmp_path, setup):
    routes, _ = setup
    routes[(BASE + "/schemas", (("catalog_name", LAKE),))][1]["next_page_token"] = "schemas-next"
    routes[(BASE + "/schemas", (("catalog_name", LAKE), ("page_token", "schemas-next")))] = (
        200, {"schemas": [{"name": "sales & west"}]},
    )
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "dbo")))][1]["next_page_token"] = "tables-next"
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("page_token", "tables-next"), ("schema_name", "dbo")))] = (
        200, {"tables": [{"name": "Products", "schema_name": "dbo"}]},
    )
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "sales & west")))] = (
        200, {"tables": [{"name": "Sales", "schema_name": "sales & west"}]},
    )
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is True
    assert [(t["schema"], t["name"]) for t in raw["tables"][LAKE]] == [
        ("dbo", "Sales"), ("dbo", "Products"), ("sales & west", "Sales"),
    ]


def test_schema_failure_preserves_other_schemas_and_marks_partial(tmp_path, setup):
    routes, _ = setup
    routes[(BASE + "/schemas", (("catalog_name", LAKE),))][1]["schemas"].insert(0, {"name": "restricted"})
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "restricted")))] = (
        403, {"errorCode": "Forbidden"},
    )
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert raw["tables"][LAKE][0]["name"] == "Sales"
    row = next(row for row in raw["collectionCoverage"] if row["component"] == "tables")
    assert row["collectionStatus"] == "partial" and row["observedCount"] == 1
    assert raw["collectionErrors"][0]["statusCode"] == 403


@pytest.mark.parametrize("payload", [
    {}, {"tables": None}, {"tables": [None]}, {"tables": [{"name": ""}]},
    {"tables": [{"name": "Sales", "schema_name": "different"}]},
    {"tables": [], "next_page_token": 12},
])
def test_malformed_table_responses_are_not_complete_empty_inventory(tmp_path, setup, payload):
    routes, _ = setup
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "dbo")))] = (200, payload)
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert LAKE not in raw["tables"]
    assert raw["collectionErrors"]


def test_later_page_failure_retains_previous_tables(tmp_path, setup):
    routes, _ = setup
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "dbo")))][1]["next_page_token"] = "next"
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("page_token", "next"), ("schema_name", "dbo")))] = (
        403, {"errorCode": "Forbidden"},
    )
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert len(raw["tables"][LAKE]) == 1


@pytest.mark.parametrize("collection", ["schemas", "tables"])
def test_repeated_page_tokens_stop_without_claiming_complete_coverage(tmp_path, setup, collection):
    routes, request = setup
    params = (("catalog_name", LAKE),) if collection == "schemas" else (("catalog_name", LAKE), ("schema_name", "dbo"))
    routes[(BASE + "/" + collection, params)][1]["next_page_token"] = "again"
    next_params = tuple(sorted((*params, ("page_token", "again"))))
    routes[(BASE + "/" + collection, next_params)] = (200, {collection: [], "next_page_token": "again"})
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert len(request.call_args_list) == 5
    assert len(raw["tables"][LAKE]) == 1


def test_empty_schemas_are_a_successful_empty_inventory(tmp_path, setup):
    routes, _ = setup
    routes[(BASE + "/schemas", (("catalog_name", LAKE),))] = (200, {"schemas": []})
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is True
    assert raw["tables"][LAKE] == []


def test_empty_tables_are_a_successful_empty_inventory(tmp_path, setup):
    routes, _ = setup
    routes[(BASE + "/tables", (("catalog_name", LAKE), ("schema_name", "dbo")))] = (200, {"tables": []})
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is True
    assert raw["tables"][LAKE] == []


@pytest.mark.parametrize("collection", ["schemas", "tables"])
def test_duplicate_identities_keep_observations_but_mark_incomplete(tmp_path, setup, collection):
    routes, _ = setup
    params = (("catalog_name", LAKE),) if collection == "schemas" else (("catalog_name", LAKE), ("schema_name", "dbo"))
    rows = routes[(BASE + "/" + collection, params)][1][collection]
    rows.append(dict(rows[0]))
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert len(raw["tables"][LAKE]) == 1
    assert raw["collectionErrors"]


def test_schema_listing_failure_after_first_page_keeps_tables(tmp_path, setup):
    routes, _ = setup
    routes[(BASE + "/schemas", (("catalog_name", LAKE),))][1]["next_page_token"] = "next"
    routes[(BASE + "/schemas", (("catalog_name", LAKE), ("page_token", "next")))] = (
        403, {"errorCode": "Forbidden"},
    )
    raw = collect(tmp_path)
    assert raw["collectionComplete"] is False
    assert len(raw["tables"][LAKE]) == 1
    assert raw["collectionErrors"][0]["statusCode"] == 403

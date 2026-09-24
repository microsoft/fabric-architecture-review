# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Synthetic metadata only. HTTP tests never acquire a token or contact Fabric."""
import base64
import json

import pytest
import requests
import yaml

from analyzers.dataflow_review import analyze
from collectors import _http, dataflows, pipeline_definitions
from reports.dataflow_evidence import build_dataflow_evidence, inspect_definition

WS = "11111111-1111-1111-1111-111111111111"
OTHER_WS = "22222222-2222-2222-2222-222222222222"
FLOW = "33333333-3333-3333-3333-333333333333"
SECRET = "DO_NOT_PUBLISH_SENTINEL"


def part(path, content):
    text = content if isinstance(content, str) else json.dumps(content)
    return {"path": path, "payloadType": "InlineBase64",
            "payload": base64.b64encode(text.encode()).decode()}


def definition(source="section Section1; shared Sales = Table.Buffer(Source);", variant="split"):
    if variant == "split":
        parts = [part("queryMetadata.json", {"formatVersion": "202502", "name": "Example"}),
                 part("mashup.pq", source)]
    else:
        parts = [part("dataflow-content.json", {"editingSessionMashup": {"mashupDocument": source}})]
    return {"definition": {"parts": parts}}


def record(payload=None, **overrides):
    return {"id": FLOW, "displayName": "Example", "workspaceId": WS, "workspaceName": "Workspace",
            **inspect_definition(payload or definition()), **overrides}


def write_raw(tmp_path, records=None, complete=True, workspaces=None):
    payload = {"schemaVersion": 1, "metadataOnly": True, "collectionComplete": complete,
               "workspaces": workspaces if workspaces is not None else [
                   {"id": WS, "name": "Workspace", "inventoryStatus": "available"}],
               "dataflows": records if records is not None else [record()]}
    (tmp_path / "dataflows.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_case_variant_identifiers_are_one_ambiguous_item_not_duplicate_gold_keys(tmp_path):
    write_raw(tmp_path, [
        record(id="ABCDEFAB-1234-1234-1234-ABCDEFABCDEF", workspaceId="ABCDEFAB-1111-1111-1111-ABCDEFABCDEF"),
        record(id="abcdefab-1234-1234-1234-abcdefabcdef", workspaceId="abcdefab-1111-1111-1111-abcdefabcdef"),
    ])
    result = build_dataflow_evidence(tmp_path, "run", "timestamp")
    assert len(result["gold_dataflows"]) == 1
    assert result["gold_dataflows"][0]["definition_status"] == "partial"
    assert result["gold_dataflows"][0]["dataflow_id"] == "abcdefab-1234-1234-1234-abcdefabcdef"
    assert result["gold_dataflow_queries"] == []


@pytest.fixture
def checklist(tmp_path):
    path = tmp_path / "dataflow-checklist.yaml"
    path.write_text(yaml.safe_dump({"rules": [
        {"id": "DFLOW-001", "severity": "low"},
        {"id": "DFLOW-002", "severity": "medium"},
    ]}), encoding="utf-8")
    return path


@pytest.mark.parametrize("variant", ["split", "content"])
def test_documented_variants_have_same_safe_shapes(tmp_path, variant):
    source = '''[StagingDefinition = [Kind = "FastCopy"]]
section Section1;
shared Sales = let Source = Table.Buffer(Input) in Table.StopFolding(Source);
shared #"Native Query" = Value.NativeQuery(Source, "select DO_NOT_PUBLISH_SENTINEL");
shared Parameter = "https://private.example/password=DO_NOT_PUBLISH_SENTINEL";
'''
    write_raw(tmp_path, [record(definition(source, variant))])
    tables = build_dataflow_evidence(tmp_path, "run", "timestamp")
    flow = tables["gold_dataflows"][0]
    assert flow["definition_status"] == "inspected"
    assert flow["query_count"] == 3
    assert flow["flagged_query_count"] == 2
    assert {key for key in flow} == {
        "run_id", "run_timestamp", "workspace_id", "workspace_name", "dataflow_id",
        "dataflow_name", "definition_status", "query_count", "flagged_query_count", "notice",
    }
    query = tables["gold_dataflow_queries"][0]
    assert set(query) == {
        "run_id", "run_timestamp", "workspace_id", "workspace_name", "dataflow_id", "dataflow_name",
        "query_name", "signal_codes", "signal_count", "recommendation", "notice",
    }
    assert query["signal_codes"] == "DFLOW_STOP_FOLDING;DFLOW_TABLE_BUFFER"
    assert type(query["signal_count"]) is int
    assert type(flow["query_count"]) is int
    assert type(flow["flagged_query_count"]) is int
    assert SECRET not in json.dumps(tables)
    assert "private.example" not in json.dumps(tables)
    assert "select " not in json.dumps(tables)


@pytest.mark.parametrize("source", [
    'section S; shared Q = "Table.Buffer(Source)";',
    'section S; shared Q = Source; // Table.StopFolding(Source)',
    'section S; /* Value.NativeQuery(X, "secret") */ shared Q = Source;',
    'section S; shared Q = let #"Table.Buffer" = (x) => x in #"Table.Buffer"(Source);',
    'section S; shared Q = "escaped "" Table.Buffer(X) "" text";',
    'section S; shared Q = Record.Field(X, "Table.Buffer")(Source);',
    'section S; shared Q = table.buffer(Source);',
    'section S; shared Q = X[Table.Buffer](Source);',
    'section S; shared Q = OtherSection!Table.Buffer(Source);',
])
def test_comments_strings_identifiers_and_field_access_are_not_library_calls(source):
    result = inspect_definition(definition(source))
    assert all(query["signalCodes"] == [] for query in result["queries"])


@pytest.mark.parametrize("source", [
    "section S; shared Q = Table.Buffer(Source)",
    "section S; shared Q = Table.Buffer(Source];",
    'section S; shared Q = "unterminated;',
    "section S; /* unterminated",
    "not a section",
    "section S; shared Q = ;",
    'section S; shared #"escaped#(tab)name" = Source;',
    'section S; shared Q = Expression.Evaluate("Table.Buffer(Source)");',
])
def test_parse_and_dynamic_analysis_gaps_are_not_clean(source):
    result = inspect_definition(definition(source))
    assert result["definitionStatus"] in ("partial", "parse_error")
    assert "PARSE_GAP" in result["noticeCodes"]


@pytest.mark.parametrize("payload", [
    {}, {"definition": {}}, {"definition": {"parts": []}},
    {"definition": {"parts": [part("unrecognized.json", {"source": SECRET})]}},
    {"definition": {"parts": [part("mashup.pq", "section S;")]}},
    {"definition": {"parts": [part("queryMetadata.json", {"formatVersion": "future"}),
                              part("mashup.pq", "section S;")]}},
])
def test_unsupported_definition_variants_remain_visible(tmp_path, payload):
    item = record()
    item.update(inspect_definition(payload))
    write_raw(tmp_path, [item])
    tables = build_dataflow_evidence(tmp_path, "run", "now")
    assert tables["gold_dataflows"][0]["definition_status"] == "unsupported"
    assert tables["gold_dataflow_queries"] == []


@pytest.mark.parametrize("bad_part", [
    {"path": "mashup.pq", "payloadType": "InlineBase64", "payload": "%%%"},
    {"path": "mashup.pq", "payloadType": "InlineBase64", "payload": "/w=="},
    {"path": "mashup.pq", "payloadType": "External", "payload": "https://private.example"},
])
def test_bad_encoding_is_explicit(bad_part):
    payload = definition()
    payload["definition"]["parts"][1] = bad_part
    assert inspect_definition(payload)["definitionStatus"] == "parse_error"


def test_mdf_and_unknown_parts_are_analysis_gaps():
    payload = definition()
    payload["definition"]["parts"].append(part("External transform.mdf", {"scriptLines": [SECRET]}))
    result = inspect_definition(payload)
    assert result["definitionStatus"] == "partial"
    assert result["queries"][0]["signalCodes"] == ["DFLOW_TABLE_BUFFER"]
    assert "EXTRA_PARTS" in result["noticeCodes"]
    assert SECRET not in json.dumps(result)


def test_duplicate_parts_and_queries_are_gaps():
    payload = definition()
    payload["definition"]["parts"].append(payload["definition"]["parts"][1])
    assert inspect_definition(payload)["definitionStatus"] == "parse_error"
    result = inspect_definition(definition("section S; shared Q = Source; shared Q = Source;"))
    assert result["definitionStatus"] == "partial"
    assert len(result["queries"]) == 1


def test_query_metadata_reconciliation_keeps_missing_query_visible():
    payload = definition("section S; shared Present = Source;")
    payload["definition"]["parts"][0] = part("queryMetadata.json", {
        "formatVersion": "202502",
        "queriesMetadata": {
            "Present": {"queryId": "same", "queryName": "Present"},
            "Missing": {"queryId": "same", "queryName": "Missing"},
        },
    })
    result = inspect_definition(payload)
    assert result["definitionStatus"] == "partial"
    assert "QUERY_METADATA_GAP" in result["noticeCodes"]
    assert {q["name"] for q in result["queries"]} == {"Present", "Missing"}


def test_duplicate_names_across_ids_and_workspaces_do_not_merge(tmp_path):
    write_raw(tmp_path, [record(), record(id="second"), record(workspaceId=OTHER_WS)])
    tables = build_dataflow_evidence(tmp_path, "run", "now")
    assert len(tables["gold_dataflows"]) == 3
    assert len(tables["gold_dataflow_queries"]) == 3
    write_raw(tmp_path, [record(), record()])
    tables = build_dataflow_evidence(tmp_path, "run", "now")
    assert len(tables["gold_dataflows"]) == 1
    assert tables["gold_dataflows"][0]["definition_status"] == "partial"
    assert tables["gold_dataflow_queries"] == []


def test_absent_corrupt_collection_and_legacy_scanner_do_not_become_clean(tmp_path, checklist):
    (tmp_path / "scanner.json").write_text(json.dumps({
        "workspaces": [{"id": WS, "dataflows": [{"objectId": FLOW, "name": "Legacy"}]}],
    }), encoding="utf-8")
    for text in (None, "{", "[]", "{}"):
        if text is not None:
            (tmp_path / "dataflows.json").write_text(text, encoding="utf-8")
        tables = build_dataflow_evidence(tmp_path, "r", "t")
        assert tables["gold_dataflows"][0]["definition_status"] == "inventory_unavailable"
        assert tables["gold_dataflows"][0]["dataflow_id"] == ""
        assert {f["status"] for f in analyze(tmp_path, checklist)} == {"missing_evidence"}


def test_projection_allowlists_every_free_text_field(tmp_path, checklist):
    malicious = record(
        displayName=f"https://{SECRET}.example",
        workspaceName=f"password={SECRET}",
        noticeCodes=[SECRET],
        error={"message": SECRET}, parts=[part("mashup.pq", SECRET)],
        queries=[{"name": f"Bearer {SECRET}", "signalCodes": ["DFLOW_TABLE_BUFFER", SECRET],
                  "noticeCodes": [SECRET], "recommendation": SECRET, "source": SECRET}],
    )
    write_raw(tmp_path, [malicious])
    output = json.dumps(build_dataflow_evidence(tmp_path, "run", "now")) + json.dumps(analyze(tmp_path, checklist))
    assert SECRET not in output
    assert "https://" not in output
    assert "DFLOW_TABLE_BUFFER" in output
    assert "redacted-" in output


def test_analyzer_statuses_and_disabled_rules(tmp_path, checklist):
    write_raw(tmp_path)
    findings = analyze(tmp_path, checklist)
    assert [f["status"] for f in findings] == ["info", "pass"]
    assert findings[0]["evidence"]["queries"][0]["dataflow_id"] == FLOW
    write_raw(tmp_path, [record(definition("section S; shared Q = Source;"))])
    assert [f["status"] for f in analyze(tmp_path, checklist)] == ["pass", "pass"]
    write_raw(tmp_path, [record(), record(id="unavailable", definitionStatus="forbidden", queries=[])])
    assert [f["status"] for f in analyze(tmp_path, checklist)] == ["info", "unknown"]
    write_raw(tmp_path, [])
    assert [f["status"] for f in analyze(tmp_path, checklist)] == ["not_applicable", "not_applicable"]
    checklist.write_text("rules: []", encoding="utf-8")
    assert analyze(tmp_path, checklist) == []


def response(code, payload=None, headers=None):
    result = requests.Response()
    result.status_code = code
    result.headers.update(headers or {})
    result._content = json.dumps(payload).encode() if payload is not None else b""
    return result


def setup_http(tmp_path, monkeypatch, replies, workspace_items=None):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": WS, "name": "Workspace", "items": workspace_items or []}],
    }), encoding="utf-8")
    calls = []
    auth_calls = []

    class Provider:
        def headers(self, scope):
            auth_calls.append(scope)
            return {"Authorization": "Bearer fake"}

    monkeypatch.setattr(dataflows, "get_default_provider", Provider)
    monkeypatch.setattr(pipeline_definitions.time, "sleep", lambda _: None)

    def request(method, url, **kwargs):
        calls.append((method, url))
        assert kwargs["headers"] == {"Authorization": "Bearer fake"}
        assert kwargs["json"] is None
        assert replies, (method, url)
        return replies.pop(0)

    monkeypatch.setattr(_http.requests, "request", request)
    return calls, auth_calls


def test_collector_paging_lro_and_redaction(tmp_path, monkeypatch):
    next_page = f"{dataflows.FAB}/workspaces/{WS}/dataflows?continuationToken=next"
    operation = f"{dataflows.FAB}/operations/op"
    replies = [
        response(200, {"value": [{"id": FLOW, "displayName": "One", "description": SECRET}],
                       "continuationUri": next_page}),
        response(200, {"value": [{"id": "second", "displayName": "One"}]}),
        response(202, headers={"Location": operation, "Retry-After": "0"}),
        response(200, {"status": "Running"}, {"Retry-After": "0"}),
        response(200, {"status": "Succeeded"}),
        response(200, definition(f'section S; shared Q = Table.Buffer("{SECRET}");')),
        response(403, {"message": SECRET}),
    ]
    calls, auth = setup_http(tmp_path, monkeypatch, replies)
    target = dataflows.collect(tmp_path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["collectionComplete"]
    assert [item["definitionStatus"] for item in raw["dataflows"]] == ["inspected", "forbidden"]
    assert SECRET not in target.read_text(encoding="utf-8")
    assert calls[1] == ("GET", next_page)
    assert ("GET", operation + "/result") in calls
    assert len(auth) == len(calls) == 7
    assert not replies
    tables = build_dataflow_evidence(tmp_path, "r", "t")
    assert len(tables["gold_dataflows"]) == 2
    assert all("/jobs/" not in url for _, url in calls)


def test_collector_canonicalizes_native_and_paged_ids_before_definition_requests(tmp_path, monkeypatch):
    workspace_id = "abcdefab-1111-1111-1111-abcdefabcdef"
    item_id = "abcdefab-1234-1234-1234-abcdefabcdef"
    next_page = f"{dataflows.FAB}/workspaces/{workspace_id}/dataflows?continuationToken=next"
    replies = [
        response(200, {"value": [{"id": item_id.upper(), "workspaceId": workspace_id.upper()}],
                       "continuationUri": next_page}),
        response(200, {"value": [{"id": item_id, "workspaceId": workspace_id}]}),
        response(200, definition()),
    ]
    calls, _ = setup_http(tmp_path, monkeypatch, replies)
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({"workspaces": [
        {"id": workspace_id.upper(), "name": "Workspace",
         "items": [{"id": item_id.upper(), "type": "Dataflow"}]},
        {"id": workspace_id, "name": "Workspace"},
    ]}), encoding="utf-8")
    raw = json.loads(dataflows.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is True
    assert len(raw["workspaces"]) == len(raw["dataflows"]) == 1
    assert raw["dataflows"][0]["id"] == item_id
    assert raw["dataflows"][0]["workspaceId"] == workspace_id
    assert len(calls) == 3
    assert not replies


def test_partial_paging_keeps_observed_items_and_gap(tmp_path, monkeypatch, checklist):
    next_page = f"{dataflows.FAB}/next"
    replies = [response(200, {"value": [{"id": FLOW, "displayName": "One"}], "continuationUri": next_page}),
               response(403, {"message": SECRET}), response(200, definition())]
    setup_http(tmp_path, monkeypatch, replies)
    dataflows.collect(tmp_path)
    tables = build_dataflow_evidence(tmp_path, "r", "t")
    assert {row["definition_status"] for row in tables["gold_dataflows"]} == {"inspected", "inventory_unavailable"}
    assert analyze(tmp_path, checklist)[1]["status"] == "unknown"


@pytest.mark.parametrize("reply", [
    response(400, {"errorCode": "OperationNotSupportedForItem", "message": SECRET}),
    response(404, {"message": SECRET}),
    response(202),
])
def test_unavailable_definitions_are_retained(tmp_path, monkeypatch, reply):
    replies = [response(200, {"value": [{"id": FLOW, "displayName": "One"}]}), reply]
    setup_http(tmp_path, monkeypatch, replies)
    target = dataflows.collect(tmp_path)
    assert SECRET not in target.read_text(encoding="utf-8")
    flow = build_dataflow_evidence(tmp_path, "r", "t")["gold_dataflows"][0]
    assert flow["definition_status"] == "unavailable"
    assert flow["query_count"] == 0


def test_native_fallback_survives_list_permission_failure(tmp_path, monkeypatch):
    replies = [response(403, {"message": SECRET}), response(200, definition())]
    calls, _ = setup_http(tmp_path, monkeypatch, replies, [
        {"id": FLOW, "type": "Dataflow", "displayName": "Native"},
        {"id": "legacy", "type": "PowerBIDataflow", "displayName": "Legacy"},
    ])
    dataflows.collect(tmp_path)
    tables = build_dataflow_evidence(tmp_path, "r", "t")
    assert {row["dataflow_id"] for row in tables["gold_dataflows"]} == {FLOW, ""}
    assert len(calls) == 2


def test_lro_failure_and_timeout_are_safe(tmp_path, monkeypatch):
    operation = f"{dataflows.FAB}/operations/op"
    replies = [
        response(200, {"value": [{"id": FLOW, "displayName": "One"}]}),
        response(202, headers={"Location": operation}),
        response(200, {"status": "Failed", "error": {"errorCode": SECRET}}),
    ]
    setup_http(tmp_path, monkeypatch, replies)
    target = dataflows.collect(tmp_path)
    assert SECRET not in target.read_text(encoding="utf-8")
    assert json.loads(target.read_text())["dataflows"][0]["definitionStatus"] == "unavailable"
    replies.extend([
        response(200, {"value": [{"id": FLOW, "displayName": "One"}]}),
        response(202, headers={"Location": operation}),
        response(200, {"status": "Running"}),
    ])
    monkeypatch.setattr(pipeline_definitions, "LRO_MAX_POLLS", 1)
    dataflows.collect(tmp_path)
    assert json.loads(target.read_text())["dataflows"][0]["definitionStatus"] == "unavailable"


def test_scope_and_no_auth_on_missing_inventory(tmp_path, monkeypatch):
    calls, _ = setup_http(tmp_path, monkeypatch, [response(200, {"value": []})])
    payload = json.loads((tmp_path / "workspace_inventory.json").read_text())
    payload["workspaces"].append({"id": OTHER_WS, "name": "Out of scope"})
    (tmp_path / "workspace_inventory.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_IDS", WS.upper())
    dataflows.collect(tmp_path)
    assert len(calls) == 1 and WS in calls[0][1]
    (tmp_path / "workspace_inventory.json").unlink()
    monkeypatch.setattr(dataflows, "get_default_provider", lambda: pytest.fail("No auth for missing inventory"))
    dataflows.collect(tmp_path)
    assert not json.loads((tmp_path / "dataflows.json").read_text())["collectionComplete"]


def test_collector_deduplicates_paged_item_ids(tmp_path, monkeypatch):
    replies = [response(200, {"value": [{"id": FLOW}, {"id": FLOW}]}), response(200, definition())]
    calls, _ = setup_http(tmp_path, monkeypatch, replies)
    dataflows.collect(tmp_path)
    assert len(calls) == 2
    assert len(build_dataflow_evidence(tmp_path, "r", "t")["gold_dataflows"]) == 1


@pytest.mark.parametrize("source", [
    "section S; shared Q = let x = in x;",
    "section S; shared Q = let x = Source;",
    "section S; shared Q = Source in Source;",
    "section S; shared Q = Table.Buffer;",
])
def test_observable_grammar_gaps_and_bare_function_references(source):
    result = inspect_definition(definition(source))
    if "let" in source or " in " in source:
        assert result["definitionStatus"] == "partial"
    assert all(not query["signalCodes"] for query in result["queries"])


@pytest.mark.parametrize("name", ["[", "]", "{", "}", "(", ")", ";"])
def test_quoted_delimiters_do_not_alter_query_structure(name):
    source = f'section S; shared #"{name}" = Table.Buffer(Source); shared Other = Source;'
    result = inspect_definition(definition(source))
    assert result["definitionStatus"] == "inspected"
    assert len(result["queries"]) == 2
    assert result["queries"][0]["signalCodes"] == ["DFLOW_TABLE_BUFFER"]


def test_section_function_shadowing_is_not_a_builtin_call():
    result = inspect_definition(definition(
        "section S; shared Table.Buffer = (x) => x; shared Q = Table.Buffer(Source);"
    ))
    assert result["definitionStatus"] == "partial"
    assert all(query["signalCodes"] == [] for query in result["queries"])


def test_duplicate_json_keys_are_not_silently_accepted():
    payload = definition()
    payload["definition"]["parts"][0] = part(
        "queryMetadata.json", '{"formatVersion": "202502", "queriesMetadata": {}, "queriesMetadata": {}}'
    )
    assert inspect_definition(payload)["definitionStatus"] == "parse_error"


def test_raw_projection_corruption_is_not_clean(tmp_path):
    write_raw(tmp_path, [record(queries=[{"name": "Q", "signalCodes": ["new_unknown_code"]}])])
    tables = build_dataflow_evidence(tmp_path, "r", "t")
    assert tables["gold_dataflows"][0]["definition_status"] == "partial"
    assert "Complete" in tables["gold_dataflow_queries"][0]["recommendation"]


@pytest.mark.parametrize("native_type", ["Dataflow", "Dataflow2", "DataflowGen2"])
@pytest.mark.parametrize("membership_failed", [False, True])
def test_scanner_selection_does_not_drop_explicit_native_inventory(tmp_path, monkeypatch, native_type, membership_failed):
    replies = [response(403), response(403)]
    setup_http(tmp_path, monkeypatch, replies, [{"id": FLOW, "type": native_type, "displayName": "Native"}])
    if membership_failed:
        inventory_path = tmp_path / "workspace_inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory.update(workspaceListComplete=True, collectionComplete=False)
        inventory["workspaces"][0].update(users=None, usersCollectionStatus="unavailable", itemsCollectionStatus="collected")
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    (tmp_path / "scanner.json").write_text(json.dumps({
        "workspaces": [{"id": WS, "name": "Workspace",
                        "dataflows": [{"objectId": "legacy-id", "name": "Legacy"}]}],
    }), encoding="utf-8")
    target = dataflows.collect(tmp_path)
    raw = json.loads(target.read_text())
    assert len(raw["dataflows"]) == 1
    assert raw["dataflows"][0]["id"] == FLOW
    assert raw["dataflows"][0]["definitionStatus"] == "forbidden"
    assert "legacy-id" not in json.dumps(raw)


def test_explicit_scope_not_in_upstream_inventory_is_not_clean_empty(tmp_path, monkeypatch):
    calls, _ = setup_http(tmp_path, monkeypatch, [response(403)])
    monkeypatch.setenv("WORKSPACE_IDS", OTHER_WS)
    dataflows.collect(tmp_path)
    assert len(calls) == 1 and OTHER_WS in calls[0][1]
    assert build_dataflow_evidence(tmp_path, "r", "t")["gold_dataflows"][0]["definition_status"] == "inventory_unavailable"


@pytest.mark.parametrize("item", [
    {"displayName": "No ID"},
    {"id": "https://private.example/" + SECRET},
    {"id": FLOW, "workspaceId": OTHER_WS},
])
def test_malformed_inventory_items_are_explicit_gaps(tmp_path, monkeypatch, item):
    calls, _ = setup_http(tmp_path, monkeypatch, [response(200, {"value": [item]})])
    target = dataflows.collect(tmp_path)
    assert len(calls) == 1
    assert SECRET not in target.read_text()
    assert build_dataflow_evidence(tmp_path, "r", "t")["gold_dataflows"][0]["definition_status"] == "inventory_unavailable"


def test_invalid_json_definition_response_is_unavailable(tmp_path, monkeypatch):
    invalid = response(200)
    invalid._content = b"invalid json DO_NOT_PUBLISH_SENTINEL"
    replies = [response(200, {"value": [{"id": FLOW}]}), invalid]
    setup_http(tmp_path, monkeypatch, replies)
    target = dataflows.collect(tmp_path)
    assert SECRET not in target.read_text()
    assert build_dataflow_evidence(tmp_path, "r", "t")["gold_dataflows"][0]["definition_status"] == "unavailable"


def test_http_throttle_retry_uses_shared_helper(tmp_path, monkeypatch):
    replies = [response(429, {"message": SECRET}, {"Retry-After": "0"}),
               response(200, {"value": []})]
    calls, _ = setup_http(tmp_path, monkeypatch, replies)
    target = dataflows.collect(tmp_path)
    assert len(calls) == 2
    assert json.loads(target.read_text())["collectionComplete"]


def test_large_parts_are_coverage_gaps(monkeypatch):
    from reports import dataflow_evidence
    monkeypatch.setattr(dataflow_evidence, "MAX_PART_BYTES", 4)
    assert inspect_definition(definition())["definitionStatus"] == "parse_error"


def test_external_transform_annotation_without_part_is_not_clean():
    result = inspect_definition(definition(
        'section S; [ItemType = "MDF"] shared Transform = "___ExternalResource-Placeholder___";'
    ))
    assert result["definitionStatus"] == "partial"
    assert result["queries"][0]["signalCodes"] == []
    assert "PARSE_GAP" in result["queries"][0]["noticeCodes"]

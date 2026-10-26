# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Known artifact definitions remain usable when inventory coverage is partial."""
import base64
import json
from unittest.mock import Mock

import pytest

from analyzers.structural_contracts import _pipeline_json_from_parts, _dependency_graph
from collectors import pipeline_definitions


def write_catalog(tmp_path, pipelines=None, notebooks=None, **metadata):
    catalog = {"pipelines": pipelines or [], "notebooks": notebooks or [], **metadata}
    (tmp_path / "pipelines_notebooks.json").write_text(json.dumps(catalog), encoding="utf-8")
    return catalog


def item(item_id):
    return {"id": item_id, "workspaceId": "visible", "displayName": item_id}


def definition(path, content):
    return {"definition": {"parts": [{
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(json.dumps(content).encode()).decode(),
    }]}}


@pytest.fixture
def fetch(monkeypatch):
    monkeypatch.setattr(pipeline_definitions, "get_default_provider", Mock())
    result = Mock(return_value=(definition("pipeline-content.json", {"activities": []}), None))
    monkeypatch.setattr(pipeline_definitions, "_get_definition", result)
    return result


def test_all_23_pipelines_and_58_notebooks_fetched_despite_inventory_gap(tmp_path, fetch):
    catalog = write_catalog(
        tmp_path, [item(f"p-{i}") for i in range(23)],
        [item(f"n-{i}") for i in range(58)],
        collectionComplete=False, failedWorkspaces=["unavailable"],
        inventoryErrors=[{"workspaceId": "unavailable", "statusCode": 400}],
    )
    target = pipeline_definitions.collect(tmp_path)
    raw = json.loads(target.read_text())
    assert fetch.call_count == 81
    assert sorted(call.args[2] for call in fetch.call_args_list) == sorted(
        row["id"] for row in catalog["pipelines"] + catalog["notebooks"]
    )
    assert [row["id"] for row in raw["pipelines"]] == [row["id"] for row in catalog["pipelines"]]
    assert [row["id"] for row in raw["notebooks"]] == [row["id"] for row in catalog["notebooks"]]
    assert len(raw["pipelines"]) == 23
    assert len(raw["notebooks"]) == 58
    assert raw["collectionComplete"] is False
    assert raw["sourceCollectionComplete"] is False
    assert raw["inventoryErrors"] == catalog["inventoryErrors"]


@pytest.mark.parametrize("complete", [True, False])
def test_empty_catalog_preserves_coverage_without_requests(tmp_path, fetch, complete):
    write_catalog(tmp_path, collectionComplete=complete)
    raw = json.loads(pipeline_definitions.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is complete
    assert raw["pipelines"] == raw["notebooks"] == []
    fetch.assert_not_called()


def test_definition_and_identity_failures_do_not_skip_remaining_items(tmp_path, fetch):
    write_catalog(
        tmp_path, [item("denied"), {"id": "missing-workspace"}, item("good")], [item("notebook")],
    )
    good = definition("pipeline-content.json", {"activities": []})
    fetch.side_effect = [(None, "http_403"), (good, None), (good, None)]
    raw = json.loads(pipeline_definitions.collect(tmp_path).read_text())
    assert fetch.call_count == 3
    assert raw["sourceCollectionComplete"] is True
    assert raw["collectionComplete"] is False
    assert raw["errors"] == 2
    assert raw["pipelines"][0]["error"] == "http_403"
    assert raw["pipelines"][1]["error"] == "missing_item_identity"
    assert raw["pipelines"][2]["parts"]
    assert raw["notebooks"][0]["parts"] == []
    assert raw["notebooks"][0]["parameterCoverage"] == "unknown"


@pytest.mark.parametrize("catalog", [
    {"pipelines": {}, "notebooks": []},
    {"pipelines": [None], "notebooks": []},
])
def test_unavailable_or_invalid_catalog_is_not_relabelled_empty(tmp_path, fetch, catalog):
    (tmp_path / "pipelines_notebooks.json").write_text(json.dumps(catalog), encoding="utf-8")
    raw = json.loads(pipeline_definitions.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert raw["collectionErrors"]
    fetch.assert_not_called()


@pytest.mark.parametrize(("partial", "mismatch"), [(True, True), (True, False), (False, False)])
def test_catalog_to_safe_structural_evidence_preserves_coverage(tmp_path, fetch, partial, mismatch):
    write_catalog(
        tmp_path, [item("pipeline")], [item("notebook")], collectionComplete=not partial,
    )
    fetch.side_effect = [
        (definition("pipeline-content.json", {"activities": [{
            "name": "Run", "type": "TridentNotebook",
            "dependsOn": [{"activity": "Run", "dependencyConditions": ["Succeeded"]}] if mismatch else [],
            "typeProperties": {"notebookId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "parameters": {
                "wrong" if mismatch else "expected": {"value": 1},
            }},
        }]}), None),
        (definition("notebook-content.ipynb", {"cells": [
            {"cell_type": "code", "source": "expected = 1",
             "metadata": {"tags": ["parameters"]}},
            {"cell_type": "code", "source": "df.collect()" if mismatch else "value = 1",
             "outputs": [{"text": "output-must-not-be-retained"}], "execution_count": 1},
        ]}), None),
    ]
    target = pipeline_definitions.collect(tmp_path)
    raw = json.loads(target.read_text())
    assert fetch.call_count == 2
    assert raw["collectionComplete"] is not partial
    body = _pipeline_json_from_parts(raw["pipelines"][0]["parts"])
    assert body is not None
    graph = _dependency_graph(body)
    assert ("self_dependency" in graph["signal_codes"]) is mismatch
    notebook = raw["notebooks"][0]
    assert notebook["parameterNames"] == ["expected"]
    assert notebook["parameterCoverage"] == "complete"
    assert notebook["signalCoverage"] == "complete"
    assert notebook["parts"] == []
    assert "output-must-not-be-retained" not in target.read_text()


def test_error_only_definitions_remain_missing_evidence(tmp_path, fetch):
    write_catalog(tmp_path, notebooks=[item("denied")])
    fetch.return_value = (None, "http_403")
    raw = json.loads(pipeline_definitions.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert raw["notebooks"][0]["parameterCoverage"] == "unknown"
    assert raw["notebooks"][0]["signalCoverage"] == "unknown"


def test_legacy_job_catalog_is_not_copied_to_architecture_definitions(tmp_path, fetch):
    write_catalog(
        tmp_path, [item("pipeline")], [item("notebook")], collectionComplete=False,
        jobs={"pipeline": [{"status": "Failed"}, {"status": "Failed"}],
              "notebook": [{"status": "Completed"}]},
    )
    raw = json.loads(pipeline_definitions.collect(tmp_path).read_text())
    assert raw["collectionComplete"] is False
    assert "jobs" not in raw

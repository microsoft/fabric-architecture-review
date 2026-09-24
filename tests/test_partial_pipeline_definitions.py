# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Known artifact definitions remain usable when inventory coverage is partial."""
import base64
import json
from unittest.mock import Mock

import pytest

from analyzers import architecture_review, notebook_code_review, performance_review
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
    assert [call.args[2] for call in fetch.call_args_list] == [
        row["id"] for row in catalog["pipelines"] + catalog["notebooks"]
    ]
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
    assert raw["notebooks"][0]["parts"]


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
def test_catalog_to_definitions_to_findings_preserves_coverage(tmp_path, fetch, partial, mismatch):
    write_catalog(
        tmp_path, [item("pipeline")], [item("notebook")], collectionComplete=not partial,
    )
    fetch.side_effect = [
        (definition("pipeline-content.json", {"activities": [{
            "name": "Run", "type": "TridentNotebook",
            "dependsOn": [{"activity": "Run", "dependencyConditions": ["Succeeded"]}] if mismatch else [],
            "typeProperties": {"notebookId": "notebook", "parameters": {
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
    findings = {row["rule_id"]: row for row in (
        architecture_review.analyze(tmp_path) + notebook_code_review.analyze(tmp_path)
    )}
    assert fetch.call_count == 2
    for rid in ("ARCH-012", "ARCH-016", "NBCODE-003", "NBCODE-004"):
        expected = "fail" if mismatch and rid != "NBCODE-004" else "unknown" if partial else "pass"
        assert findings[rid]["status"] == expected
        assert findings[rid]["evidence"]["coverage_status"] == ("partial" if partial else "complete")
    assert findings["ARCH-012"]["evidence"]["mismatchCount"] == int(mismatch)
    assert findings["NBCODE-003"]["evidence"]["notebooksScanned"] == 1
    if mismatch:
        assert findings["NBCODE-003"]["evidence"]["examples"][0]["notebook_id"] == "notebook"
    assert "output-must-not-be-retained" not in target.read_text()


def test_error_only_definitions_remain_missing_evidence(tmp_path):
    (tmp_path / "pipeline_definitions.json").write_text('{"collectionComplete": false}')
    findings = notebook_code_review.analyze(tmp_path)
    assert findings
    assert all(row["status"] == "missing_evidence" for row in findings)


def test_partial_job_catalog_keeps_observed_failures_without_claiming_all_clear(tmp_path):
    write_catalog(
        tmp_path, [item("pipeline")], [item("notebook")], collectionComplete=False,
        jobs={"pipeline": [{"status": "Failed"}, {"status": "Failed"}],
              "notebook": [{"status": "Completed"}]},
    )
    findings = {row["rule_id"]: row for row in performance_review._analyze_jobs(
        tmp_path, {rid: {"id": rid} for rid in ("PERF-008", "PERF-009")},
    )}
    assert findings["PERF-008"]["status"] == "fail"
    assert findings["PERF-008"]["evidence"]["pipelinesWithRuns"] == 1
    assert findings["PERF-009"]["status"] == "unknown"
    assert findings["PERF-009"]["evidence"]["notebooksWithRuns"] == 1
    assert all(row["evidence"]["coverage_status"] == "partial" for row in findings.values())

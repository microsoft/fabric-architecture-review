# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from reports.powerbi.report import PAGE_H, PAGE_W, _home_tiles_page, build_parts
from reports.powerbi.schema import EVIDENCE_RELATIONSHIPS, GOLD_TABLES_BY_NAME
from reports.powerbi.semantic_model import build_bim


NATIVE_PAGES = {"ExecutionHistory", "DataflowsGen2", "DaxObjects"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def report_parts():
    return {part["path"]: json.loads(part["text"]) for part in build_parts("model-test") if part.get("text")}


def test_native_model_has_unambiguous_coverage_filter_paths():
    model = build_bim("test", "endpoint", "database")["model"]
    relationships = model["relationships"]
    for detail, coverage in EVIDENCE_RELATIONSHIPS.items():
        parents = [relation for relation in relationships if relation["fromTable"] == detail]
        assert len(parents) == 1
        assert parents[0]["toTable"] == coverage
        assert parents[0]["fromColumn"] == parents[0]["toColumn"] == "review_item_key"
        assert parents[0]["crossFilteringBehavior"] == "oneDirection"
        assert any(relation["fromTable"] == coverage and relation["toTable"] == "gold_run_summary"
                   for relation in relationships)
    for table_name in {*EVIDENCE_RELATIONSHIPS, *EVIDENCE_RELATIONSHIPS.values()}:
        assert "review_item_key" in {column.name for column in GOLD_TABLES_BY_NAME[table_name].columns}
    duration = next(column for column in GOLD_TABLES_BY_NAME["gold_item_executions"].columns if column.name == "duration_ms")
    assert duration.kind == "int64"
    measures = {measure["name"]: measure for table in model["tables"] for measure in table.get("measures", [])}
    assert "DISTINCTCOUNT" in measures["Observed Execution IDs"]["expression"]
    assert "not latest-observation status totals" in measures["Observed Execution IDs"]["description"]
    assert "HASONEVALUE(gold_run_summary[run_id])" in measures["Failed Executions in Review"]["expression"]
    assert "1000" in measures["Max Observed Execution Seconds"]["expression"]


def test_report_bindings_resolve_to_real_model_fields(report_parts):
    model = build_bim("test", "endpoint", "database")["model"]
    columns = {table["name"]: {column["name"] for column in table["columns"]} for table in model["tables"]}
    measures = {table["name"]: {measure["name"] for measure in table.get("measures", [])} for table in model["tables"]}

    def check(value):
        if isinstance(value, list):
            for child in value:
                check(child)
        elif isinstance(value, dict):
            for kind, lookup in (("Column", columns), ("Measure", measures)):
                if kind in value and "Expression" in value[kind]:
                    field = value[kind]
                    entity = field["Expression"].get("SourceRef", {}).get("Entity")
                    if entity:
                        assert field["Property"] in lookup[entity], (entity, field["Property"])
            for child in value.values():
                check(child)

    for path, document in report_parts.items():
        if "/visuals/" in path:
            check(document)


@pytest.mark.parametrize("page,table,identity", [
    ("ExecutionHistory", "gold_execution_coverage", {"run_id", "workspace_id", "item_id", "item_type"}),
    ("ExecutionHistory", "gold_item_executions", {"run_id", "workspace_id", "item_id", "execution_key"}),
    ("DataflowsGen2", "gold_dataflows", {"run_id", "workspace_id", "dataflow_id"}),
    ("DataflowsGen2", "gold_dataflow_queries", {"run_id", "workspace_id", "dataflow_id", "query_name"}),
    ("DaxObjects", "gold_dax_object_coverage", {"run_id", "workspace_id", "model_id"}),
    ("DaxObjects", "gold_dax_objects",
     {"run_id", "workspace_id", "model_id", "table_name", "object_type", "object_name"}),
])
def test_native_grids_preserve_identity_when_names_and_timestamps_match(report_parts, page, table, identity):
    grids = [document for path, document in report_parts.items()
             if path.startswith(f"definition/pages/{page}/visuals/")
             and document["visual"]["visualType"] == "tableEx"]
    columns = next({
        projection["field"]["Column"]["Property"]
        for projection in grid["visual"]["query"]["queryState"]["Values"]["projections"]
    } for grid in grids if
        grid["visual"]["query"]["queryState"]["Values"]["projections"][0]["field"]["Column"]
        ["Expression"]["SourceRef"]["Entity"] == table)
    assert identity <= columns
    # Table visuals group by projected values: equal labels must not collapse
    # different artifacts or observations with the same display timestamp.
    for field in identity:
        first = dict.fromkeys(columns, "same")
        second = {**first, field: "different"}
        assert len({tuple(row[column] for column in sorted(columns)) for row in (first, second)}) == 2


def test_native_pages_fit_canvas_and_keep_coverage_navigation(report_parts):
    for page in NATIVE_PAGES:
        document = report_parts[f"definition/pages/{page}/page.json"]
        visuals = [value for path, value in report_parts.items() if path.startswith(f"definition/pages/{page}/visuals/")]
        names = {visual["name"] for visual in visuals}
        for visual in visuals:
            box = visual["position"]
            assert 0 <= box["x"] < box["x"] + box["width"] <= PAGE_W, (page, box)
            assert 0 <= box["y"] < box["y"] + box["height"] <= PAGE_H, (page, box)
        interactions = document["visualInteractions"]
        assert any(interaction["type"] == "DataFilter" for interaction in interactions)
        for interaction in interactions:
            assert interaction["source"] in names
            assert interaction["target"] in names
        assert ("filterConfig" not in document) if page == "ExecutionHistory" else ("filterConfig" in document)
        query_visuals = [visual for visual in visuals if "query" in visual["visual"]]
        for index, first in enumerate(query_visuals):
            a = first["position"]
            for second in query_visuals[index + 1:]:
                b = second["position"]
                assert (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                        or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"]), page


def test_navigation_reaches_native_pages_and_fallback_fits(report_parts):
    combined = json.dumps(report_parts)
    for page in NATIVE_PAGES:
        assert f"'{page}'" in combined
    home = json.dumps({path: part for path, part in report_parts.items() if "/Home/" in path})
    assert "'ExecutionHistory'" in home
    fallback = _home_tiles_page()
    for visual in fallback["visuals"]:
        box = visual["position"]
        assert box["y"] + box["height"] <= PAGE_H


def test_all_runners_include_native_dataflow_evidence():
    for relative in ("scripts/powershell/01_collect.ps1", "scripts/bash/01_collect.sh", "fabric/notebooks/01_collect.ipynb"):
        assert "collectors.dataflows" in (ROOT / relative).read_text(encoding="utf-8-sig")
    for relative in ("scripts/powershell/02_analyze.ps1", "scripts/bash/02_analyze.sh", "fabric/notebooks/02_analyze.ipynb"):
        text = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert "analyzers.dataflow_review" in text
        assert "findings_dataflows.json" in text


def _stub_evidence(monkeypatch, coverage, executions):
    from reports import gold_layer

    monkeypatch.setattr(gold_layer, "build_execution_evidence", lambda *_: {
        "gold_execution_coverage": coverage, "gold_item_executions": executions,
    })
    monkeypatch.setattr(gold_layer, "build_dataflow_evidence", lambda *_: {})
    monkeypatch.setattr(gold_layer, "build_dax_object_evidence", lambda *_: {})
    return gold_layer


def test_gold_keys_preserve_workspace_identity_and_observation_grain(monkeypatch, tmp_path):
    coverage = [{"workspace_id": workspace, "item_id": "same-id", "item_type": "SemanticModel"}
                for workspace in ("workspace-a", "workspace-b")]
    executions = [{**item, "execution_key": item["workspace_id"] + "-execution", "duration_ms": 1234} for item in coverage]
    gold = _stub_evidence(monkeypatch, coverage, executions)
    first = gold.build_gold([], tmp_path, run_id="run-1", run_timestamp="2026-09-22T10:00:00Z", check_remote=False)
    second = gold.build_gold([], tmp_path, run_id="run-2", run_timestamp="2026-09-23T10:00:00Z", check_remote=False)
    assert len({row["review_item_key"] for row in first["gold_execution_coverage"]}) == 2
    for table in ("gold_execution_coverage", "gold_item_executions"):
        assert first[table][0]["review_item_key"] != second[table][0]["review_item_key"]
    assert first["gold_item_executions"][0]["execution_key"] == second["gold_item_executions"][0]["execution_key"]
    assert first["gold_item_executions"][0]["duration_ms"] == 1234
    assert first["gold_dax_measures"] == []


def test_gold_duration_nullability_does_not_change_other_numeric_defaults():
    from reports.gold_layer import _coerce_row

    for row in ({}, {"duration_ms": None}):
        assert _coerce_row("gold_item_executions", row)["duration_ms"] is None
    for value in (0, "0", 1234, "1234"):
        assert _coerce_row("gold_item_executions", {"duration_ms": value})["duration_ms"] == int(value)
    for row in ({}, {"table_count": None, "total_size": None}):
        legacy = _coerce_row("gold_semantic_models", row)
        assert legacy["table_count"] == 0
        assert legacy["total_size"] == 0.0
    assert _coerce_row("gold_execution_coverage", {})["observed_execution_count"] == 0


def test_unavailable_execution_inventories_keep_distinct_type_coverage(monkeypatch, tmp_path):
    coverage = [{"item_type": item_type, "collection_status": "not_collected"}
                for item_type in ("SemanticModel", "DataPipeline", "Notebook")]
    gold = _stub_evidence(monkeypatch, coverage, [])
    tables = gold.build_gold([], tmp_path, run_id="run", run_timestamp="2026-09-22T10:00:00Z", check_remote=False)
    assert len({row["review_item_key"] for row in tables["gold_execution_coverage"]}) == 3
    assert tables["gold_item_executions"] == []


@pytest.mark.parametrize("coverage,executions,message", [
    ([{"workspace_id": "ws", "item_id": "item"}] * 2, [], "Duplicate item coverage"),
    ([], [{"workspace_id": "ws", "item_id": "item"}], "missing its gold_execution_coverage"),
])
def test_gold_rejects_ambiguous_or_uncovered_evidence(monkeypatch, tmp_path, coverage, executions, message):
    gold = _stub_evidence(monkeypatch, coverage, executions)
    with pytest.raises(ValueError, match=message):
        gold.build_gold([], tmp_path, run_id="run", run_timestamp="2026-09-22T10:00:00Z", check_remote=False)


def test_pipeline_dependency_targets_use_ids_not_duplicate_workspace_names(monkeypatch, tmp_path):
    gold = _stub_evidence(monkeypatch, [], [])
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": "ws-a", "name": "Same name"}, {"id": "ws-b", "name": "Same name"}],
    }), encoding="utf-8")
    finding = {
        "rule_id": "ARCH-016", "dimension": "architecture", "severity": "high",
        "status": "fail", "title": "Pipeline dependency integrity",
        "evidence": {"items": [
            {"workspace_id": "ws-a", "workspace_name": "Same name", "item_id": "pipeline-a",
             "item_type": "DataPipeline", "signal_codes": ["dependency_cycle"]},
            {"workspace_id": "ws-b", "workspace_name": "Same name", "item_id": "pipeline-b",
             "item_type": "DataPipeline", "signal_codes": [], "coverage_status": "unavailable"},
        ]},
    }
    tables = gold.build_gold([finding], tmp_path, run_id="run", run_timestamp="2026-09-22T10:00:00Z", check_remote=False)
    assert [row["workspace_id"] for row in tables["gold_finding_targets"]] == ["ws-a"]

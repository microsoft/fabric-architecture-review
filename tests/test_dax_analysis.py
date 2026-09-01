# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

from collectors.dax_analysis import analyze_expression, build_analysis, collect, extract_bim_measures, extract_measures
from reports.render_report import _dax_section
from tests._analyzers import FIXTURE_RAW


TMDL = """table Sales
\tmeasure 'Net Sales' = SUM(Sales[Amount])
\t\tformatString: #,0
\tmeasure 'Expensive Margin' =
\t\tSUMX(
\t\t\tFILTER(Sales, Sales[Amount] > 0),
\t\t\tSUMX(CROSSJOIN(Products, Stores), Sales[Amount])
\t\t)
\t\tdisplayFolder: Finance
"""


def test_extracts_multiline_tmdl_measures_without_properties():
    measures = extract_measures(TMDL)

    assert [measure["measure_name"] for measure in measures] == ["Net Sales", "Expensive Margin"]
    assert measures[0]["expression"] == "SUM(Sales[Amount])"
    assert "displayFolder" not in measures[1]["expression"]


def test_static_signals_are_explainable_and_deterministic():
    signals = analyze_expression(extract_measures(TMDL)[1]["expression"])

    assert {signal["code"] for signal in signals} >= {"nested_iterators", "crossjoin", "whole_table_filter"}
    assert all(signal["points"] > 0 and signal["message"] for signal in signals)


def test_extracts_model_bim_measure_expressions():
    measures = extract_bim_measures(json.dumps({"model": {"tables": [{
        "name": "Measures",
        "measures": [{"name": "Net Sales", "expression": ["SUMX(", "Sales, Sales[Amount])"]}],
    }]}}))

    assert measures == [{"table_name": "Measures", "measure_name": "Net Sales", "expression": "SUMX(\nSales, Sales[Amount])"}]


def test_consultant_report_has_dedicated_metadata_only_dax_page():
    section = _dax_section(FIXTURE_RAW)

    assert section.startswith("# DAX Analyzer - Metadata-only static risk")
    assert "not measured duration" in section
    assert "Potentially Expensive Sales" in section
    assert "nested_iterators, crossjoin, whole_table_filter" in section


def test_build_analysis_preserves_model_context_and_metadata_boundary():
    payload = build_analysis({"models": [{
        "id": "model-1",
        "name": "Sales Model",
        "workspaceId": "workspace-1",
        "workspaceName": "Finance",
        "parts": [{"path": "definition/tables/Sales.tmdl", "text": TMDL}],
    }]})

    assert payload["metadata_only"] is True
    assert payload["models_scanned"] == 1
    assert payload["measures"][1]["model_id"] == "model-1"
    assert payload["measures"][1]["table_name"] == "Sales"
    assert payload["measures"][1]["risk_level"] == "high"


def test_collect_degrades_when_definitions_are_missing(tmp_path):
    target = collect(tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert payload["available"] is False
    assert payload["measures"] == []
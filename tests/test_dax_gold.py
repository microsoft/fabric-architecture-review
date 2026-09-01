# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

from reports.gold_layer import build_gold
from reports.ontology.ontology import ENTITIES_BY_NAME, RELATIONSHIPS
from reports.powerbi.report import _home_page, _home_tiles_page
from reports.powerbi.semantic_model import _column_description


def test_builds_filterable_dax_gold_tables(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    payloads = {
        "capacity_metrics.json": {"capacities": [{"id": "cap-1", "displayName": "Finance F64"}]},
        "workspace_inventory.json": {"workspaces": [{"id": "ws-1", "name": "Finance", "capacityId": "cap-1"}]},
        "scanner.json": {"workspaces": []},
        "semantic_models.json": {"datasets": [{"id": "model-1", "name": "Sales", "workspaceId": "ws-1"}]},
        "semantic_model_definitions.json": {"models": [{"id": "model-1", "parts": []}]},
        "dax_analysis.json": {"measures": [{
            "model_id": "model-1",
            "measure_name": "Net Sales",
            "table_name": "Measures",
            "expression": "SUMX(CROSSJOIN(Products, Stores), Sales[Amount])",
            "risk_level": "high",
            "risk_score": 60,
            "expression_length": 51,
            "signals": [{"code": "crossjoin", "points": 35, "message": "Potential expansion"}],
        }]},
    }
    for name, payload in payloads.items():
        (raw / name).write_text(json.dumps(payload), encoding="utf-8")

    tables = build_gold([], raw, run_id="run-1", run_timestamp="2026-09-01T00:00:00Z", check_remote=False)

    assert tables["gold_dax_models"][0]["capacity_name"] == "Finance F64"
    assert tables["gold_dax_models"][0]["high_risk_count"] == 1
    measure = tables["gold_dax_measures"][0]
    assert (measure["capacity_name"], measure["model_name"]) == ("Finance F64", "Sales")
    assert measure["signal_codes"] == "crossjoin"
    assert "SUMX" in measure["expression_preview"]


def test_dax_contract_reaches_semantic_metadata_ontology_and_home_navigation():
    assert "not measured duration" in _column_description("gold_dax_measures", "risk_score")
    assert ENTITIES_BY_NAME["DaxMeasure"].table == "gold_dax_measures"
    relationship = next(item for item in RELATIONSHIPS if item.name == "MeasureBelongsToModel")
    assert (relationship.source, relationship.target) == ("DaxMeasure", "SemanticModel")
    assert "DaxAnalyzer" in json.dumps(_home_page())
    assert "DaxAnalyzer" in json.dumps(_home_tiles_page())
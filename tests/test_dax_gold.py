# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import pytest

from collectors.dax_analysis import build_analysis
from reports.gold_layer import build_gold
from reports.ontology.ontology import ENTITIES_BY_NAME, RELATIONSHIPS
from reports.powerbi import home_map
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


def test_dax_contract_reaches_semantic_metadata_ontology_and_home_navigation(monkeypatch):
    assert "not measured duration" in _column_description("gold_dax_measures", "risk_score")
    assert ENTITIES_BY_NAME["DaxMeasure"].table == "gold_dax_measures"
    relationship = next(item for item in RELATIONSHIPS if item.name == "MeasureBelongsToModel")
    assert (relationship.source, relationship.target) == ("DaxMeasure", "SemanticModel")
    monkeypatch.setattr(home_map, "render", lambda *_: b"home-image")
    home = json.dumps(_home_page())
    tiles = json.dumps(_home_tiles_page())
    assert "DaxAnalyzer" in home
    assert "DaxAnalyzer" in tiles
    assert "\"'DAX'\"" in home
    assert '"value": "DAX"' in tiles
    assert "DAX analyzer" not in home + tiles


@pytest.mark.parametrize("workspace_source", ["workspace_inventory.json", "scanner.json"])
def test_non_measure_dax_workspace_names_resolve_from_same_run_inventory(tmp_path, workspace_source):
    models = [
        {
            "id": model_id, "name": "Shared model name", "workspaceId": workspace_id,
            "parts": [{"path": "definition/tables/Metrics.tmdl",
                       "text": "table Metrics\n    column Flag = 1\n"}],
        }
        for model_id, workspace_id in (
            ("finance-model", "WS-1"), ("sales-model", "ws-2"), ("unknown-model", "unknown"),
        )
    ]
    models.append({**models[0], "id": "named-model", "workspaceName": "Already collected"})
    models.append({**models[0], "id": "personal-model", "workspaceId": "PERSONAL"})
    payloads = {
        workspace_source: {
            "workspaces": [
                {"id": "ws-1", "name": "Finance"}, {"id": "ws-2", "name": "Sales"},
                {"id": "personal", "name": "My workspace", "type": "PersonalGroup"},
            ],
            "collectionComplete": False, "workspaceListComplete": True,
        },
        "semantic_models.json": {"datasets": [
            {key: model[key] for key in ("id", "name", "workspaceId")} for model in models
        ]},
        "semantic_model_definitions.json": {"models": models},
        "dax_analysis.json": build_analysis({"models": models}),
    }
    for name, payload in payloads.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    tables = build_gold([], tmp_path, run_id="run", run_timestamp="2026-09-23T07:30:00Z", check_remote=False)

    for table in ("gold_dax_objects", "gold_dax_object_coverage"):
        assert {row["model_id"]: row["workspace_name"] for row in tables[table]} == {
            "finance-model": "Finance", "sales-model": "Sales", "unknown-model": "",
            "named-model": "Already collected",
        }
    parents = {row["review_item_key"]: row for row in tables["gold_dax_object_coverage"]}
    assert all(row["workspace_id"].lower() != "personal"
               for row in tables["gold_execution_coverage"])
    for row in tables["gold_dax_objects"]:
        assert row["object_name"] == "Flag"
        assert row["risk_score"] == 0
        assert row["workspace_name"] == parents[row["review_item_key"]]["workspace_name"]
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
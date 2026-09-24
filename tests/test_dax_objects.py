# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import pytest

from collectors.dax_analysis import build_analysis
from reports.dax_objects import build_dax_object_evidence


def _write(raw, name, payload):
    (raw / name).write_text(json.dumps(payload), encoding="utf-8")


def _definitions():
    return {"models": [{
        "id": "m", "name": "Model", "workspaceId": "w", "workspaceName": "Workspace",
        "parts": [{"path": "definition/tables/Table.tmdl", "text": """table Shared
    measure Shared = SUMX(CROSSJOIN(A,B),1)
    column Shared = SUMX(CROSSJOIN(A,B),1) + LEN("PRIVATE_LITERAL")
    calculationGroup
        calculationItem Shared = SELECTEDMEASURE()
    partition Partition = calculated
        source = CROSSJOIN(A,B)
"""}],
    }]}


def test_exact_gold_schema_types_counts_and_no_expression_leak(tmp_path):
    _write(tmp_path, "dax_analysis.json", build_analysis(_definitions()))
    tables = build_dax_object_evidence(tmp_path, "run", "timestamp")
    assert set(tables) == {"gold_dax_objects", "gold_dax_object_coverage"}
    rows = tables["gold_dax_objects"]
    assert len(rows) == 3
    expected = {
        "run_id", "run_timestamp", "workspace_id", "workspace_name", "model_id", "model_name",
        "table_name", "object_type", "object_name", "risk_level", "risk_rank", "risk_score",
        "expression_length", "signal_codes", "signal_details_json",
    }
    integer_fields = {"risk_rank", "risk_score", "expression_length"}
    for row in rows:
        assert set(row) == expected
        assert all(type(row[key]) is (int if key in integer_fields else str) for key in expected)
        assert (row["run_id"], row["model_name"], row["object_name"]) == ("run", "Model", "Shared")
    assert len({(row["workspace_id"], row["model_id"], row["table_name"],
                 row["object_type"], row["object_name"]) for row in rows}) == 3
    serialized = json.dumps(tables)
    assert "PRIVATE_LITERAL" not in serialized
    assert "SUMX(CROSSJOIN" not in serialized
    assert "expression_preview" not in serialized
    coverage = tables["gold_dax_object_coverage"][0]
    assert set(coverage) == {
        "run_id", "run_timestamp", "workspace_id", "workspace_name", "model_id", "model_name",
        "definition_status", "object_count", "flagged_object_count", "notice",
    }
    assert coverage["definition_status"] == "complete"
    assert coverage["object_count"] == 3
    assert coverage["flagged_object_count"] == 2
    assert type(coverage["object_count"]) is type(coverage["flagged_object_count"]) is int
    assert "Visual calculations are not collected" in coverage["notice"]
    assert "Format-string expressions" in coverage["notice"]


def test_legacy_payload_can_use_collected_definitions_without_writes(tmp_path):
    _write(tmp_path, "dax_analysis.json", {"measures": []})
    _write(tmp_path, "semantic_model_definitions.json", _definitions())
    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    result = build_dax_object_evidence(tmp_path, "r", "t")
    assert len(result["gold_dax_objects"]) == 3
    assert {file.name: file.read_bytes() for file in tmp_path.iterdir()} == before


@pytest.mark.parametrize("has_objects", [False, True])
def test_standard_tmdl_metadata_preserves_gold_coverage_and_object_types(tmp_path, has_objects):
    from reports.gold_layer import build_gold

    definitions = _definitions()
    if not has_objects:
        definitions["models"][0]["parts"][0]["text"] = (
            "table Shared\n    measure Shared = 1\n"
            "    column Source\n        sourceColumn: Source"
        )
    definitions["models"][0]["parts"].extend([
        {"path": "definition/model.tmdl",
         "text": "model Model\n    dataAccessOptions\n        legacyRedirects\n        returnErrorValuesAsNull"},
        {"path": "definition/relationships.tmdl",
         "text": "relationship Relationship\n    relyOnReferentialIntegrity"},
        {"path": "definition/cultures/en-US.tmdl",
         "text": 'cultureInfo en-US\n    linguisticMetadata = {"Version":"1.0.0"}\n        contentType: json'},
    ])
    _write(tmp_path, "semantic_model_definitions.json", definitions)
    _write(tmp_path, "dax_analysis.json", build_analysis(definitions))
    tables = build_gold([], tmp_path, run_id="run", run_timestamp="2026-09-23T00:00:00Z", check_remote=False)
    coverage = tables["gold_dax_object_coverage"][0]
    objects = tables["gold_dax_objects"]
    assert coverage["definition_status"] == "complete"
    assert coverage["object_count"] == len(objects) == (3 if has_objects else 0)
    assert {row["object_type"] for row in objects} == (
        {"calculated_column", "calculated_table", "calculation_item"} if has_objects else set()
    )
    assert all(row["review_item_key"] == coverage["review_item_key"] for row in objects)


def test_inventory_models_without_definition_are_unavailable_not_clean(tmp_path):
    _write(tmp_path, "semantic_models.json", {"datasets": [{
        "id": "m", "name": "Model", "workspaceId": "w",
    }]})
    tables = build_dax_object_evidence(tmp_path, "r", "t")
    assert tables["gold_dax_objects"] == []
    coverage = tables["gold_dax_object_coverage"][0]
    assert coverage["definition_status"] == "unavailable"
    assert coverage["object_count"] == 0
    assert "not collected" in coverage["notice"]


def test_invalid_file_surfaces_coverage_limitation_without_echoing_source(tmp_path):
    _write(tmp_path, "semantic_models.json", {"datasets": [{"id": "m"}]})
    (tmp_path / "semantic_model_definitions.json").write_text("{ PRIVATE_SOURCE", encoding="utf-8")
    tables = build_dax_object_evidence(tmp_path, "r", "t")
    coverage = tables["gold_dax_object_coverage"][0]
    assert coverage["definition_status"] == "unavailable"
    assert "definitions: invalid_json" in coverage["notice"]
    assert "PRIVATE_SOURCE" not in json.dumps(tables)


def test_invalid_file_without_model_identity_raises_explicitly(tmp_path):
    (tmp_path / "dax_analysis.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="coverage cannot be determined"):
        build_dax_object_evidence(tmp_path, "r", "t")


def test_invalid_definition_shape_surfaces_in_coverage(tmp_path):
    _write(tmp_path, "semantic_model_definitions.json", {"models": {"m": {}}})
    _write(tmp_path, "semantic_models.json", {"datasets": [{"id": "m"}]})
    coverage = build_dax_object_evidence(tmp_path, "r", "t")["gold_dax_object_coverage"][0]
    assert coverage["definition_status"] == "unavailable"
    assert "definitions: invalid_shape" in coverage["notice"]


def test_empty_directory_has_no_invented_models(tmp_path):
    assert build_dax_object_evidence(tmp_path, "r", "t") == {
        "gold_dax_objects": [], "gold_dax_object_coverage": [],
    }


def test_output_is_deterministic_under_model_and_object_reordering(tmp_path):
    definitions = _definitions()
    other = {**definitions["models"][0], "workspaceId": "different-workspace"}
    definitions["models"].append(other)
    payload = build_analysis(definitions)
    _write(tmp_path, "dax_analysis.json", payload)
    before = build_dax_object_evidence(tmp_path, "r", "t")
    payload["objects"].reverse()
    payload["object_coverage"].reverse()
    _write(tmp_path, "dax_analysis.json", payload)
    assert before == build_dax_object_evidence(tmp_path, "r", "t")
    assert len(before["gold_dax_object_coverage"]) == 2


@pytest.mark.parametrize("value", [-1, True, "3", 2**63])
def test_invalid_int64_values_are_rejected(tmp_path, value):
    payload = build_analysis(_definitions())
    payload["objects"][0]["expression_length"] = value
    _write(tmp_path, "dax_analysis.json", payload)
    with pytest.raises(ValueError, match="int64"):
        build_dax_object_evidence(tmp_path, "r", "t")


def test_definition_inventory_union_resolves_only_unambiguous_workspace_context(tmp_path):
    _write(tmp_path, "semantic_models.json", {"datasets": [
        {"id": "M", "name": "Inventory name", "workspaceId": "w", "workspaceName": "Workspace"},
    ]})
    _write(tmp_path, "semantic_model_definitions.json", {"models": [
        {"id": "m", "parts": []},
        {"id": "definition-only", "workspaceId": "w", "parts": []},
    ]})
    tables = build_dax_object_evidence(tmp_path, "r", "t")
    coverage = tables["gold_dax_object_coverage"]
    assert len(coverage) == 2
    assert {row["workspace_id"] for row in coverage} == {"w"}
    assert {row["definition_status"] for row in coverage} == {"unavailable"}
    assert next(row for row in coverage if row["model_id"] == "m")["model_name"] == "Inventory name"


@pytest.mark.parametrize("collection", ["objects", "object_coverage"])
def test_ambiguous_raw_identities_fail_explicitly(tmp_path, collection):
    payload = build_analysis(_definitions())
    payload[collection].append(payload[collection][0])
    _write(tmp_path, "dax_analysis.json", payload)
    with pytest.raises(ValueError, match="Duplicate DAX"):
        build_dax_object_evidence(tmp_path, "r", "t")


def test_model_absent_from_new_analysis_still_gets_unavailable_coverage(tmp_path):
    _write(tmp_path, "dax_analysis.json", {"objects": [], "object_coverage": []})
    _write(tmp_path, "semantic_model_definitions.json", _definitions())
    coverage = build_dax_object_evidence(tmp_path, "r", "t")["gold_dax_object_coverage"]
    assert len(coverage) == 1
    assert coverage[0]["definition_status"] == "unavailable"

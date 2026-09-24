# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import pytest

from collectors.dax_analysis import (
    analyze_expression, build_analysis, build_object_analysis, collect,
    extract_bim_measures, extract_bim_objects, extract_measures, extract_tmdl_objects,
)
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
    assert payload["objects"] == []
    assert payload["object_coverage"] == []


def _definitions(text, path="definition/tables/NotTheTableName.tmdl", **extra):
    return {"models": [{
        "id": "model-1", "name": "Sales Model",
        "workspaceId": "workspace-1", "workspaceName": "Finance",
        "parts": [{"path": path, "text": text}], **extra,
    }]}


def test_non_measure_objects_do_not_change_legacy_measure_rows():
    base = build_analysis(_definitions(TMDL))
    broader = build_analysis(_definitions(TMDL + """
\tcolumn 'Risky column' = SUMX(CROSSJOIN(Products, Stores), 1)
\tpartition Sales = calculated
\t\tmode: import
\t\tsource = CROSSJOIN(Products, Stores)
\tcalculationGroup
\t\tcalculationItem 'Risky item' = SUMX(CROSSJOIN(Products, Stores), 1)
"""))
    assert broader["measures"] == base["measures"]
    assert broader["models_scanned"] == base["models_scanned"] == 1
    assert broader["definition_errors"] == base["definition_errors"] == 0
    assert {obj["object_type"] for obj in broader["objects"]} == {
        "calculated_column", "calculated_table", "calculation_item",
    }
    assert broader["object_coverage"][0]["object_count"] == 3
    assert broader["object_coverage"][0]["flagged_object_count"] == 3


@pytest.mark.parametrize("indent", ["\t", "    "])
def test_tmdl_quoted_names_multiline_and_metadata_boundaries(indent):
    text = """table 'Sales = O''Brien'
    column 'Margin: O''Brien' =
            VAR Factor = 2
            RETURN SUMX(
                FILTER('Sales = O''Brien', [Amount] > 0),
                [Amount] * Factor)
        dataType: double
        isHidden
        formatString: "CROSSJOIN(A,B)"
        annotation Description = SUMX(CROSSJOIN(A,B),1)
    calculationGroup
        precedence: 1
        calculationItem 'Margin: O''Brien' =
                SELECTEDMEASURE()
            ordinal: 0
            formatStringDefinition = "CROSSJOIN(A,B)"
    partition 'Different partition name' = calculated
        mode: import
        source =
            CROSSJOIN(
                Products,
                Stores)
""".replace("    ", indent)
    rows, issues = extract_tmdl_objects(text)
    assert issues == set()
    indexed = {row["object_type"]: row for row in rows}
    assert {row["table_name"] for row in rows} == {"Sales = O'Brien"}
    assert indexed["calculated_column"]["object_name"] == "Margin: O'Brien"
    assert indexed["calculated_table"]["object_name"] == "Sales = O'Brien"
    assert "CROSSJOIN" not in indexed["calculated_column"]["expression"]
    assert {signal["code"] for signal in indexed["calculated_column"]["signals"]} == {
        "iterator", "whole_table_filter",
    }
    assert indexed["calculation_item"]["expression"] == "SELECTEDMEASURE()"
    assert indexed["calculation_item"]["risk_score"] == 0
    assert indexed["calculated_table"]["risk_score"] == 35


def test_tmdl_fenced_expression_and_other_language_bodies_are_not_declarations():
    text = '''table T
    column Literal = ```
"PRIVATE_LITERAL CROSSJOIN(A,B)"
        ```
    partition T = m
        source = ```
table Fake
    column NotDax = CROSSJOIN(A,B)
        ```
    annotation Note = ```
table Fake2
    column NotDax = CROSSJOIN(A,B)
        ```
    column Real = 1
'''
    rows, issues = extract_tmdl_objects(text)
    assert issues == set()
    assert [row["object_name"] for row in rows] == ["Literal", "Real"]
    assert rows[0]["risk_score"] == 0


def test_tmdl_multiple_tables_partial_declarations_and_explicit_expression():
    text = """table 'First table'
    column Shared
        expression = 1
        dataType: int64
table Second
    column Shared = 2
    column Imported
        sourceColumn: Shared
"""
    rows, issues = extract_tmdl_objects(text)
    assert issues == set()
    assert [(row["table_name"], row["object_name"], row["expression"]) for row in rows] == [
        ("First table", "Shared", "1"), ("Second", "Shared", "2"),
    ]


@pytest.mark.parametrize("wrapper", [
    lambda model: {"model": model},
    lambda model: model,
    lambda model: {"createOrReplace": {"object": {"database": "db"}, "database": {"model": model}}},
])
def test_bim_tmsl_requires_true_expressions_and_calculated_partition_sources(wrapper):
    model = {"tables": [{
        "name": "Shared",
        "measures": [{"name": "Shared", "expression": "CROSSJOIN(A,B)"}],
        "columns": [
            {"name": "Shared", "type": "calculated", "expression": ["SUMX(", "A, [Amount])"]},
            {"name": "Output", "type": "calculatedTableColumn", "sourceColumn": "[Output]"},
            {"name": "Imported", "sourceColumn": "CROSSJOIN(A,B)"},
        ],
        "partitions": [{"name": "Partition", "source": {"type": "calculated", "expression": "CROSSJOIN(A,B)"}}],
        "calculationGroup": {"calculationItems": [{
            "name": "Shared", "expression": "SELECTEDMEASURE()",
            "formatStringDefinition": {"expression": "SUMX(CROSSJOIN(A,B),1)"},
        }]},
        "description": "SUMX(CROSSJOIN(A,B),1)",
        "annotations": [{"name": "NotDax", "value": "SUMX(CROSSJOIN(A,B),1)"}],
    }, {
        "name": "Imported",
        "partitions": [{"name": "P", "source": {"type": "m", "expression": "CROSSJOIN(A,B)"}}],
    }]}
    rows, issues = extract_bim_objects(json.dumps(wrapper(model)))
    assert issues == set()
    assert len(rows) == 3
    assert {(row["object_type"], row["object_name"]) for row in rows} == {
        ("calculated_column", "Shared"), ("calculated_table", "Shared"), ("calculation_item", "Shared"),
    }
    assert rows[0]["expression"] == "SUMX(\nA, [Amount])"
    assert rows[2]["risk_score"] == 0


@pytest.mark.parametrize("expression", [
    '1 // SUMX(CROSSJOIN(A,B),1)\n-- EARLIER([C])\n/* FILTER(T, TRUE()) */',
    '"SUMX(""CROSSJOIN("", EARLIER([C]))"',
    "'SUMX(''CROSSJOIN(''[Table]'[GENERATE(]]EARLIER(]",
])
def test_comments_strings_and_identifiers_do_not_create_risk_signals(expression):
    assert analyze_expression(expression) == []


def test_real_functions_survive_masking_and_existing_weights_are_preserved():
    signals = analyze_expression("SUMX(/* CROSSJOIN(A,B) */ FILTER('Table name', [X] > 0), [X])")
    assert [(signal["code"], signal["points"]) for signal in signals] == [
        ("iterator", 8), ("whole_table_filter", 20),
    ]
    assert analyze_expression("REMOVEFILTERS('Quoted table')")[0]["points"] == 12
    assert analyze_expression("1" + " " * 1499)[0]["code"] == "very_long_expression"


@pytest.mark.parametrize(("text", "path", "reason"), [
    ("{invalid", "model.bim", "invalid_json_definition"),
    ('{"createOrReplace":{"table":{"name":"T"}}}', "command.tmsl", "unsupported_json_definition"),
    ('{"model":{"tables":"invalid"}}', "model.bim", "invalid_object_collection"),
    ('{"model":{"tables":null}}', "model.bim", "missing_or_invalid_tables_collection"),
    ('{"model":{"tables":[{"name":"T","columns":[{"type":"calculated","name":"C"}]}]}}',
     "model.bim", "missing_or_invalid_object_expression"),
    ("table T\n    column C = ```\n1", "table.tmdl", "unterminated_tmdl_expression"),
    ("table T\n    column C =", "table.tmdl", "missing_or_invalid_object_expression"),
    ("table T\n    partition P = unknown", "table.tmdl", "unsupported_partition_source"),
    ("table T\n    calculatedColumn C = 1", "table.tmdl", "unsupported_tmdl_construct"),
])
def test_unsupported_or_incomplete_definitions_are_explicit_partial_coverage(text, path, reason):
    payload = build_object_analysis(_definitions(text, path))
    coverage = payload["object_coverage"][0]
    assert coverage["definition_status"] == "partial"
    assert reason in coverage["notice"]
    assert "Visual calculations are not collected" in coverage["notice"]


@pytest.mark.parametrize("metadata", [
    "model Model\n    dataAccessOptions\n        legacyRedirects",
    "model Model\n    dataAccessOptions\n        returnErrorValuesAsNull",
    "relationship Relationship\n    relyOnReferentialIntegrity",
    'cultureInfo en-US\n    linguisticMetadata = {"Version":"1.0.0"}\n        contentType: json',
])
@pytest.mark.parametrize("has_object", [False, True])
def test_standard_tmdl_metadata_does_not_make_object_coverage_partial(metadata, has_object):
    definitions = _definitions(
        "table Table\n    column Calculated = 1" if has_object
        else "table Table\n    column Source\n        sourceColumn: Source",
    )
    definitions["models"][0]["parts"].append({"path": "metadata.tmdl", "text": metadata})
    payload = build_object_analysis(definitions)
    assert payload["object_coverage"][0]["definition_status"] == "complete"
    assert payload["object_coverage"][0]["object_count"] == int(has_object)
    assert [row["object_type"] for row in payload["objects"]] == (
        ["calculated_column"] if has_object else []
    )


@pytest.mark.parametrize("text", [
    'table T\n    column C = "unterminated',
    'table T\n    column C = /* unterminated',
    'table T\n    column C = "escaped end""',
])
def test_unterminated_dax_does_not_emit_clean_object(text):
    payload = build_object_analysis(_definitions(text))
    assert payload["objects"] == []
    assert payload["object_coverage"][0]["definition_status"] == "partial"


@pytest.mark.parametrize("expression", ["SUMX(", ")(1", "{1", "1}"])
def test_unbalanced_dax_is_partial_not_a_clean_object(expression):
    payload = build_object_analysis(_definitions(f"table T\n    column C = {expression}"))
    assert payload["objects"] == []
    assert payload["object_coverage"][0]["definition_status"] == "partial"


def test_collect_persists_independent_objects_and_coverage(tmp_path):
    definitions = _definitions("table T\n    column C = 1")
    (tmp_path / "semantic_model_definitions.json").write_text(json.dumps(definitions), encoding="utf-8")
    target = collect(tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == build_analysis(definitions)
    assert payload["measures"] == []
    assert payload["objects"][0]["object_type"] == "calculated_column"
    assert payload["object_coverage"][0]["object_count"] == 1


def test_empty_and_failed_definition_collection_are_unavailable():
    payload = build_object_analysis({"models": [
        {"id": "missing", "parts": []},
        {"id": "failed", "error": "forbidden"},
    ]})
    assert payload["objects"] == []
    assert {row["definition_status"] for row in payload["object_coverage"]} == {"unavailable"}


def test_undecoded_definition_is_not_clean():
    payload = build_object_analysis({"models": [{
        "id": "undecoded", "parts": [{"path": "model.bim", "payload": "encoded"}],
    }]})
    assert payload["object_coverage"][0]["definition_status"] == "partial"
    assert "definition_text_unavailable" in payload["object_coverage"][0]["notice"]


def test_conflicting_duplicate_objects_are_omitted_and_order_is_deterministic():
    definitions = _definitions("table T\n    column C = 1")
    parts = definitions["models"][0]["parts"]
    parts.extend([
        {"path": "other.tmdl", "text": "table T\n    column C = 2"},
        {"path": "group.tmdl", "text": "table T\n    calculationGroup\n        calculationItem C = 3"},
        {"path": "column.tmdl", "text": "table U\n    column C = 4"},
    ])
    before = build_object_analysis(definitions)
    parts.reverse()
    after = build_object_analysis(definitions)
    assert before == after
    assert [(row["table_name"], row["object_type"]) for row in before["objects"]] == [
        ("T", "calculation_item"), ("U", "calculated_column"),
    ]
    assert "duplicate_object_definition" in before["object_coverage"][0]["notice"]


@pytest.mark.parametrize("path,text", [
    ("table.tmdl", """table T
    partition A = calculated
        source = {1}
    partition B = calculated
        source = {2}
"""),
    ("model.bim", json.dumps({"model": {"tables": [{
        "name": "T", "partitions": [
            {"name": name, "source": {"type": "calculated", "expression": "{1}"}} for name in ("A", "B")
        ],
    }]}})),
])
def test_ambiguous_multi_partition_tables_are_not_collapsed(path, text):
    payload = build_object_analysis(_definitions(text, path))
    assert payload["objects"] == []
    assert "multiple_calculated_partitions" in payload["object_coverage"][0]["notice"]


def test_tmdl_referenced_but_missing_table_is_not_complete():
    definitions = _definitions("model Model\n    ref table 'Missing table'", "model.tmdl")
    coverage = build_object_analysis(definitions)["object_coverage"][0]
    assert coverage["definition_status"] == "partial"
    assert "referenced_tables_not_collected" in coverage["notice"]
    definitions["models"][0]["parts"].append({
        "path": "tables/Missing.tmdl", "text": "table 'Missing table'\n    column C = 1",
    })
    assert build_object_analysis(definitions)["object_coverage"][0]["definition_status"] == "complete"


def test_arbitrary_tmsl_filename_is_supported_but_layout_metadata_is_ignored():
    definitions = _definitions(json.dumps({"createOrReplace": {"database": {"model": {
        "tables": [{"name": "T", "columns": [{"name": "C", "type": "calculated", "expression": "1"}]}],
    }}}}), "create-model.json")
    definitions["models"][0]["parts"].append({
        "path": "diagramLayout.json", "text": '{"tables":[{"name":"not metadata"}],"version":"1"}',
    })
    payload = build_object_analysis(definitions)
    assert payload["object_coverage"][0]["definition_status"] == "complete"
    assert len(payload["objects"]) == 1


def test_mixed_calculated_and_imported_partitions_are_explicitly_unsupported():
    text = """table T
    partition A = calculated
        source = {1}
    partition B = m
        source = #table({}, {})
"""
    rows, issues = extract_tmdl_objects(text)
    assert rows == []
    assert "mixed_partition_sources" in issues
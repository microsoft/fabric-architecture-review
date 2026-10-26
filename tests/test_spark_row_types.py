# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Gold and owner rows must carry the exact Python types Spark accepts for each column."""
import json
from datetime import datetime

import pytest

from reports.gold_layer import build_gold
from reports.owner.gold import build_owner_gold
from reports.owner.schema import OWNER_TABLES_BY_NAME
from reports.powerbi.schema import GOLD_TABLES_BY_NAME, coerce_value
from tests._far_surfaces import finding, raw

# PySpark verifies `type(value) in accepted[type]`, so int is not acceptable for a double column.
SPARK_ACCEPTS = {
    "string": (str,), "int64": (int,), "double": (float,),
    "boolean": (bool,), "dateTime": (str, datetime),
}


def mismatches(tables, schema):
    found = []
    for name, rows in tables.items():
        for row in rows:
            for column in schema[name].columns:
                value = row.get(column.name)
                if value is not None and type(value) not in SPARK_ACCEPTS[column.kind]:
                    found.append((name, column.name, column.kind, type(value).__name__))
    return sorted(set(found))


@pytest.fixture
def gold(tmp_path, monkeypatch):
    raw(tmp_path, monkeypatch)
    findings = [finding("ARCH-001", status="fail"), finding("ARCH-005", status="pass"),
                finding("ARCH-008", status="info")]
    (tmp_path / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
    return build_gold(findings, tmp_path, run_id="types", run_timestamp="2026-10-13T08:00:00Z", check_remote=False)


def test_gold_rows_match_spark_column_types(gold):
    assert gold["gold_workspace_risk"] and gold["gold_graph_nodes"] and gold["gold_severity_matrix"]
    assert mismatches(gold, GOLD_TABLES_BY_NAME) == []


def test_whole_number_scores_are_floats_in_double_columns(gold):
    risk = gold["gold_workspace_risk"][0]["risk_score"]
    assert type(risk) is float and risk > 0
    assert all(type(row["importance"]) is float for row in gold["gold_graph_nodes"])
    assert all(type(row["weighted_risk"]) is float for row in gold["gold_severity_matrix"])


def test_owner_rows_match_spark_column_types(gold):
    owner = build_owner_gold(gold)
    assert owner["owner_reviews"]
    assert mismatches(owner, OWNER_TABLES_BY_NAME) == []


@pytest.mark.parametrize("kind, value, expected", [
    ("double", 90, 90.0), ("double", 0.5, 0.5), ("double", None, None),
    ("int64", 3, 3), ("int64", 3.0, 3), ("int64", True, 1),
    ("boolean", True, True), ("boolean", 1, True), ("boolean", 0, False),
    ("string", "x", "x"), ("string", 7, "7"), ("string", {"b": 1, "a": 2}, '{"a": 2, "b": 1}'),
    ("dateTime", "2026-10-13T08:00:00Z", "2026-10-13T08:00:00Z"),
])
def test_coerce_value_returns_the_type_spark_accepts(kind, value, expected):
    result = coerce_value(kind, value)
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize("kind, value", [
    ("double", "90"), ("double", True), ("int64", 3.5), ("int64", "3"), ("boolean", "yes"), ("boolean", 2),
])
def test_coerce_value_rejects_values_that_would_silently_change_meaning(kind, value):
    with pytest.raises(TypeError, match="demo.column"):
        coerce_value(kind, value, "demo.column")

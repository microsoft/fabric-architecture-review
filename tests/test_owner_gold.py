# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Owner boundary tests use synthetic metadata only, never customer evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reports.owner import OWNER_TABLES, OWNER_TABLES_BY_NAME, build_owner_gold
from reports.owner import gold
from reports.powerbi.schema import Column, Table


WA = "aaaaaaaa-0000-4000-8000-000000000001"
WB = "bbbbbbbb-0000-4000-8000-000000000002"
MA = "aaaaaaaa-0000-4000-8000-000000000011"
MB = "bbbbbbbb-0000-4000-8000-000000000012"
NA = "aaaaaaaa-0000-4000-8000-000000000021"
NB = "bbbbbbbb-0000-4000-8000-000000000022"
RUN = {"run_id": "run-1", "run_timestamp": "2026-09-01T00:00:00Z"}
SECRET = "SYNTHETIC-SECRET-MUST-NEVER-LEAVE-EVIDENCE"
FOREIGN = "FOREIGN-METADATA-MUST-NOT-BE-COPIED"


def scoped(wid, **fields):
    return {**RUN, "workspace_id": wid, **fields}


@pytest.fixture
def mixed():
    return {
        "gold_workspaces": [
            scoped(WA.upper(), workspace_name="Workspace A", description=SECRET,
                   capacity_id=FOREIGN, item_type_counts_json=SECRET),
            scoped(WB, workspace_name="Workspace B"),
        ],
        "gold_run_summary": [{**RUN, "score": 99, "fail_count": 900, "client_name": SECRET}],
        "gold_findings": [{
            **RUN, "rule_id": "DAX-001", "status": "fail", "title": SECRET,
            "affected": FOREIGN, "recommendation": SECRET,
            "evidence_json": json.dumps({"workspaces": [WA, WB], "expression": SECRET}),
        }],
        "gold_finding_targets": [scoped(WA, title=SECRET, workspace_name=FOREIGN)],
        "gold_semantic_models": [
            scoped(WA, model_id=MA, model_name="Model A", storage_mode="Import",
                   total_size=987654321, table_count=999, column_count=888),
            scoped(WB, model_id=MB, model_name="Model B", storage_mode="DirectLake"),
        ],
        "gold_dax_models": [
            scoped(WA, model_id=MA, model_name=FOREIGN, workspace_name=FOREIGN,
                   definition_status="available", measure_count=999,
                   max_risk_score=99, capacity_name=SECRET),
            scoped(WB, model_id=MB, model_name="Model B", definition_status="available"),
        ],
        "gold_dax_measures": [
            scoped(WA, model_id=MA, model_name=FOREIGN, workspace_name=FOREIGN,
                   table_name="Measures A", measure_name="Measure A",
                   signal_codes=f"crossjoin, {SECRET}, crossjoin",
                   signal_details_json=json.dumps([{"message": SECRET}]),
                   expression_preview=SECRET, expression=SECRET,
                   risk_score=100, risk_level="high", capacity_name=FOREIGN),
            scoped(WB, model_id=MB, model_name="Model B",
                   table_name="Measures B", measure_name="Measure B",
                   signal_codes="iterator", expression_preview=SECRET),
        ],
        "gold_notebooks": [
            scoped(WA, notebook_id=NA, notebook_name="Notebook A", risk_score=100),
            scoped(WB, notebook_id=NB, notebook_name="Notebook B"),
        ],
        "gold_notebook_smells": [
            scoped(WA, notebook_id=NA, notebook_name=FOREIGN, workspace_name=FOREIGN,
                   rule_id="NBCODE-001", cells="2, 1, 2", rule_description=SECRET,
                   notebook_url="https://example.invalid/" + SECRET, source=SECRET,
                   recommendation=SECRET, severity=SECRET),
            # The current builder emits name-only rows; even URLs cannot establish attribution.
            {**RUN, "workspace_name": "Workspace A", "notebook_name": "Notebook A",
             "rule_id": "NBCODE-003", "cells": "9",
             "notebook_url": f"https://app.fabric.microsoft.com/groups/{WA}/synapsenotebooks/{NA}"},
        ],
        "gold_model_tables": [
            scoped(WA, model_id=MA, model_name=FOREIGN, workspace_name=FOREIGN,
                   table_name="Table A", row_count=123, total_size=456, pct_db=99.9,
                   source=SECRET),
            {**RUN, "model_id": MB, "model_name": "Model B", "workspace_name": "Workspace B",
             "table_name": SECRET, "total_size": 7654321},
        ],
        "gold_capacities": [{**RUN, "capacity_name": SECRET, "observed_item_count": 99999}],
        "gold_workspace_risk": [scoped(WA, risk_score=100, issue_count=900)],
        "owner_access": [{"workspace_id": WA, "principal_object_id": SECRET}],
    }


def test_mixed_projection_reconstructs_only_scoped_safe_content(mixed):
    result = build_owner_gold(mixed)
    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert FOREIGN not in serialized
    assert "987654321" not in serialized
    assert "7654321" not in serialized
    assert "expression_preview" not in serialized
    assert "capacity_name" not in serialized
    assert "owner_access" not in result
    assert result["owner_workspaces"] == [
        {"workspace_id": WA, "workspace_name": "Workspace A"},
        {"workspace_id": WB, "workspace_name": "Workspace B"},
    ]
    reviews = {row["workspace_id"]: row for row in result["owner_reviews"]}
    assert reviews[WA]["owner_fail_count"] == 2
    assert reviews[WB]["owner_fail_count"] == 0
    assert all(row["is_latest"] and row["assessment_status"] == "incomplete" for row in reviews.values())
    findings = result["owner_findings"]
    assert {row["rule_id"] for row in findings} == {"DAX-001", "NBCODE-001"}
    dax = next(row for row in findings if row["rule_id"] == "DAX-001")
    assert dax["severity"] == "medium"  # Recomputed from approved codes, not raw score.
    assert dax["signal_codes"] == "crossjoin"
    assert dax["item_name"] == "Model A"
    assert dax["title"] == "DAX pattern review: Measures A / Measure A"
    assert "row counts on both sides of CROSSJOIN" in dax["recommendation"]
    assert "verify result correctness and timings" in dax["recommendation"]
    assert "not measured duration or CU" in dax["recommendation"]
    notebook = next(row for row in findings if row["rule_id"] == "NBCODE-001")
    assert notebook["affected_count"] == 2
    assert notebook["item_name"] == "Notebook A"
    stats = [row for row in result["owner_details"] if row["detail_type"] == "model_table_stat"]
    assert {(row["metric_name"], row["metric_value"]) for row in stats} == {
        ("row_count", 123), ("total_size", 456),
    }
    for name in ("owner_findings", "owner_details", "owner_reviews"):
        for row in result[name]:
            assert row["review_key"] == f"run-1:{row['workspace_id']}"
            assert row["run_timestamp"] == RUN["run_timestamp"]
    own_a = json.dumps([row for name, rows in result.items()
                        for row in rows if row["workspace_id"] == WA])
    for value in ("Workspace B", "Model B", "Measures B", "Measure B", WB, MB, NB):
        assert value not in own_a


def test_owner_dax_guidance_covers_only_approved_patterns_and_names_each_measure(mixed):
    assert gold._DAX_GUIDANCE.keys() == gold._DAX_POINTS.keys()
    second = deepcopy(mixed["gold_dax_measures"][0])
    second["measure_name"] = "Another measure"
    mixed["gold_dax_measures"].append(second)
    result = build_owner_gold(mixed)
    findings = [row for row in result["owner_findings"] if row["rule_id"] == "DAX-001"]
    assert {row["title"] for row in findings} == {
        "DAX pattern review: Measures A / Measure A",
        "DAX pattern review: Measures A / Another measure",
    }
    assert all(row["severity"] == "medium" and row["affected_count"] == 1 for row in findings)
    assert len({row["finding_key"] for row in findings}) == 2
    assert SECRET not in json.dumps(result)


def test_input_is_not_mutated_and_output_does_not_alias_input(mixed):
    before = deepcopy(mixed)
    result = build_owner_gold(mixed)
    assert mixed == before
    result["owner_workspaces"][0]["workspace_name"] = "changed"
    assert mixed == before


def test_exact_schema_contract_and_access_expiry(mixed):
    result = build_owner_gold(mixed)
    assert set(result) == {
        "owner_workspaces", "owner_reviews", "owner_findings", "owner_details",
        "owner_executions", "owner_coverage",
    }
    assert all(isinstance(table, Table) for table in OWNER_TABLES)
    assert all(isinstance(column, Column) for table in OWNER_TABLES for column in table.columns)
    assert {column.name: column.kind for column in OWNER_TABLES_BY_NAME["owner_access"].columns} == {
        "workspace_id": "string", "principal_object_id": "string",
        "refreshed_at": "dateTime", "expires_at": "dateTime",
    }
    for table_name, rows in result.items():
        columns = OWNER_TABLES_BY_NAME[table_name].columns
        for row in rows:
            assert set(row) == {column.name for column in columns}
            for column in columns:
                value = row[column.name]
                if value is not None:
                    expected = {"string": str, "int64": int, "boolean": bool, "dateTime": str}[column.kind]
                    assert type(value) is expected


def test_one_workspace_with_no_technical_data_is_not_a_pass():
    result = build_owner_gold({"gold_workspaces": [scoped(WA, workspace_name="Only workspace")]})
    assert len(result["owner_workspaces"]) == len(result["owner_reviews"]) == 1
    assert result["owner_findings"] == result["owner_details"] == []
    review = result["owner_reviews"][0]
    assert review["owner_fail_count"] == 0
    assert review["technical_evidence_status"] == "missing"
    assert review["technical_detail_count"] == 0
    assert "No safely attributable technical evidence" in review["notice"]
    assert "not a full FAR score" in review["notice"]


def test_inventory_or_definition_status_alone_is_not_technical_assessment(mixed):
    result = build_owner_gold({
        key: mixed[key] for key in ("gold_workspaces", "gold_semantic_models", "gold_dax_models")
    })
    assert all(row["technical_evidence_status"] == "missing" for row in result["owner_reviews"])
    assert result["owner_findings"] == []
    assert all(row["metric_value"] is None for row in result["owner_details"])


def test_unscoped_and_uninventoried_facts_are_not_name_guessed(mixed):
    mixed["gold_dax_measures"][0]["workspace_id"] = None
    mixed["gold_dax_measures"][1]["workspace_id"] = ""
    mixed["gold_model_tables"][0]["model_id"] = "cccccccc-0000-4000-8000-000000000031"
    mixed["gold_notebook_smells"][0].pop("notebook_id")
    result = build_owner_gold(mixed)
    assert result["owner_findings"] == []
    assert all(row["technical_evidence_status"] == "missing" for row in result["owner_reviews"])


def test_same_display_names_do_not_merge_workspaces_or_models(mixed):
    for row in mixed["gold_workspaces"]:
        row["workspace_name"] = "Same name"
    for row in mixed["gold_semantic_models"]:
        row["model_name"] = "Same model"
    result = build_owner_gold(mixed)
    assert len(result["owner_workspaces"]) == 2
    assert len({row["review_key"] for row in result["owner_reviews"]}) == 2
    assert all(row["workspace_id"] == WA for row in result["owner_findings"])


def test_identical_approved_duplicates_collapse_and_order_is_deterministic(mixed):
    expected = build_owner_gold(mixed)
    for table in ("gold_workspaces", "gold_semantic_models", "gold_dax_models",
                  "gold_dax_measures", "gold_notebooks", "gold_notebook_smells", "gold_model_tables"):
        mixed[table] = list(reversed(mixed[table] + deepcopy(mixed[table])))
    assert build_owner_gold(mixed) == expected


@pytest.mark.parametrize("table,id_field,new_id", [
    ("gold_workspaces", "workspace_id", None),
    ("gold_workspaces", "workspace_id", "workspace-name"),
    ("gold_workspaces", "workspace_id", "00000000-0000-0000-0000-000000000000"),
    ("gold_semantic_models", "model_id", "not-an-id"),
    ("gold_dax_measures", "workspace_id", ["not", "a", "GUID"]),
])
def test_malformed_identity_is_rejected(mixed, table, id_field, new_id):
    mixed[table][0][id_field] = new_id
    with pytest.raises(ValueError, match="GUID"):
        build_owner_gold(mixed)


def test_ambiguous_normalized_workspace_id_is_rejected(mixed):
    mixed["gold_workspaces"].append(scoped(WA, workspace_name="Different workspace"))
    with pytest.raises(ValueError, match="Ambiguous workspace"):
        build_owner_gold(mixed)


def test_item_id_in_multiple_workspaces_is_rejected(mixed):
    mixed["gold_semantic_models"][1]["model_id"] = MA.upper()
    with pytest.raises(ValueError, match="Ambiguous item"):
        build_owner_gold(mixed)


@pytest.mark.parametrize("table", ["gold_dax_models", "gold_dax_measures", "gold_model_tables"])
def test_foreign_model_id_on_local_row_is_rejected(mixed, table):
    mixed[table][0]["model_id"] = MB
    with pytest.raises(ValueError, match="conflicts"):
        build_owner_gold(mixed)


def test_foreign_notebook_id_on_local_row_is_rejected(mixed):
    mixed["gold_notebook_smells"][0]["notebook_id"] = NB
    with pytest.raises(ValueError, match="conflicts"):
        build_owner_gold(mixed)


def test_conflicting_duplicate_measure_is_rejected(mixed):
    other = deepcopy(mixed["gold_dax_measures"][0])
    other["signal_codes"] = "generate"
    mixed["gold_dax_measures"].append(other)
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        build_owner_gold(mixed)


@pytest.mark.parametrize("value", [True, -1, "123", 1.5, float("nan"), 2**63])
def test_invalid_counter_is_rejected(mixed, value):
    mixed["gold_model_tables"][0]["total_size"] = value
    with pytest.raises(ValueError, match="int64"):
        build_owner_gold(mixed)


@pytest.mark.parametrize("value", ["cell " + SECRET, "1, -2", "", None])
def test_notebook_cell_text_never_passes_through(mixed, value):
    mixed["gold_notebook_smells"][0]["cells"] = value
    with pytest.raises(ValueError, match="numeric cell"):
        build_owner_gold(mixed)


def test_unknown_rules_and_signal_codes_cannot_inject_text(mixed):
    mixed["gold_notebook_smells"][0]["rule_id"] = SECRET
    mixed["gold_dax_measures"][0]["signal_codes"] = SECRET
    result = build_owner_gold(mixed)
    assert result["owner_findings"] == []
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("field,value", [
    ("run_id", "different-run"),
    ("run_timestamp", "2026-09-02T00:00:00Z"),
])
def test_cross_run_scoped_evidence_is_rejected(mixed, field, value):
    mixed["gold_dax_measures"][0][field] = value
    with pytest.raises(ValueError, match="different review run"):
        build_owner_gold(mixed)


def test_multiple_input_runs_are_rejected(mixed):
    mixed["gold_workspaces"][1]["run_id"] = "run-2"
    with pytest.raises(ValueError, match="one consistent"):
        build_owner_gold(mixed)


def test_timestamp_normalization_and_guid_normalization():
    row = scoped("{" + WA.upper() + "}", workspace_name="Workspace")
    row["run_timestamp"] = datetime(2026, 9, 1, tzinfo=timezone.utc)
    result = build_owner_gold({"gold_workspaces": [row]})
    assert result["owner_reviews"][0]["review_key"] == "run-1:" + WA
    assert result["owner_reviews"][0]["run_timestamp"] == RUN["run_timestamp"]


@pytest.mark.parametrize("value", ["bad-date", "2026-09-01T00:00:00", None, 123])
def test_invalid_or_naive_timestamp_is_rejected(value):
    row = scoped(WA, workspace_name="Workspace")
    row["run_timestamp"] = value
    with pytest.raises(ValueError, match="timezone-aware"):
        build_owner_gold({"gold_workspaces": [row]})


def test_foreign_workspace_not_in_inventory_is_excluded_without_copying(mixed):
    mixed["gold_workspaces"] = mixed["gold_workspaces"][:1]
    result = build_owner_gold(mixed)
    assert all(row["workspace_id"] == WA for rows in result.values() for row in rows)
    assert "Workspace B" not in json.dumps(result)


def test_empty_run_does_not_create_grants_or_invent_a_workspace():
    assert build_owner_gold({}) == {
        "owner_workspaces": [], "owner_reviews": [], "owner_findings": [], "owner_details": [],
        "owner_executions": [], "owner_coverage": [],
    }
    assert build_owner_gold({"gold_run_summary": [RUN]}) == build_owner_gold({})


def test_ignored_global_tables_are_never_parsed():
    result = build_owner_gold({
        "gold_workspaces": [scoped(WA, workspace_name="Workspace")],
        "gold_findings": SECRET,
        "gold_finding_targets": {"untrusted": SECRET},
        "gold_model_columns": {"foreign": SECRET},
    })
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("payload", [None, [], {"gold_workspaces": {}}, {"gold_workspaces": [None]}])
def test_malformed_projection_input_rejected(payload):
    with pytest.raises(ValueError):
        build_owner_gold(payload)


@pytest.mark.parametrize("provenance", [
    "direct", "legacy", "foreign_workspace", "name_fallback", "duplicate_model",
])
def test_actual_collectors_analyzers_and_gold_preserve_only_direct_provenance(monkeypatch, tmp_path, provenance):
    """Run the producer chain with synthetic evidence and mocked live XMLA calls."""
    from analyzers import dax_review, notebook_code_review
    from collectors import vertipaq_stats
    from reports import gold_layer
    from tests import build_fixture

    raw = build_fixture._corpus()
    frames = {
        build_fixture.DS_A: {
            "model": [{"total_size": 456}],
            "tables": [{"table_name": "CollectedTableA", "total_size": 456, "row_count": 123}],
        },
        build_fixture.DS_B: {
            "model": [{"total_size": 7654321}],
            "tables": [{"table_name": "CollectedTableB", "total_size": 7654321, "row_count": 987}],
        },
    }
    for filename in ("semantic_models.json", "scanner.json", "workspace_inventory.json"):
        (tmp_path / filename).write_text(json.dumps(raw[filename]), encoding="utf-8")
    target = tmp_path / "vertipaq_stats.json"
    analyze_model = MagicMock(side_effect=lambda model, workspace, read_stats: deepcopy(frames.get(model, {})))
    with monkeypatch.context() as collector_patch:
        collector_patch.delenv("VERTIPAQ_STATS_SKIP", raising=False)
        collector_patch.delenv("VERTIPAQ_STATS_READ_DATA", raising=False)
        collector_patch.setattr(vertipaq_stats, "_ensure_sempy_labs", lambda: None)
        collector_patch.setattr(vertipaq_stats, "_analyze_model", analyze_model)
        assert vertipaq_stats.collect(str(tmp_path)) == target
    raw["vertipaq_stats.json"] = json.loads(target.read_text(encoding="utf-8"))
    assert [(model["model_id"], model["workspace_id"]) for model in raw["vertipaq_stats.json"]["models"]] == [
        (build_fixture.DS_A, build_fixture.WS1),
        (build_fixture.DS_B, build_fixture.WS2),
        (build_fixture.DS_C, build_fixture.WS2),
    ]
    assert [call.args for call in analyze_model.call_args_list] == [
        (build_fixture.DS_A, build_fixture.WS1, False),
        (build_fixture.DS_B, build_fixture.WS2, False),
        (build_fixture.DS_C, build_fixture.WS2, False),
    ]
    # Keep scanner/catalog notebook IDs consistent; the corpus helper's second
    # notebook otherwise repeats WS1 despite its WS2 display label.
    notebooks = raw["pipeline_definitions.json"]["notebooks"]
    notebooks[1]["workspaceId"] = build_fixture.WS2
    for workspace in raw["scanner.json"]["workspaces"]:
        workspace["Notebook"] = [
            {"id": nb["id"], "name": nb["displayName"]}
            for nb in notebooks if nb["workspaceId"] == workspace["id"]
        ]
    direct = provenance == "direct"
    if not direct:
        for nb in notebooks:
            nb.pop("id")
            nb.pop("workspaceId")
        vp_models = raw["vertipaq_stats.json"]["models"]
        if provenance == "legacy":
            for model in vp_models:
                model.pop("workspace_id")
        elif provenance == "foreign_workspace":
            for model in vp_models:
                model["workspace_id"] = WA
        elif provenance == "name_fallback":
            for model in vp_models:
                model["model_id"] = None
        elif provenance == "duplicate_model":
            vp_models.extend(deepcopy(vp_models))

    def read_raw(path, *, allow_incomplete=False):
        return deepcopy(raw.get(Path(path).name))

    monkeypatch.setattr(gold_layer, "_load", lambda directory, filename: read_raw(filename))
    monkeypatch.setattr(dax_review, "load_raw", read_raw)
    monkeypatch.setattr(notebook_code_review, "load_raw", read_raw)
    root = Path(__file__).resolve().parents[1]
    checklist = root / "config" / "review-checklist.yaml"
    findings = (
        dax_review.analyze(root, checklist)
        + notebook_code_review.analyze(root, checklist)
    )
    assert any(row["rule_id"] == "NBCODE-001" and row["status"] == "fail" for row in findings)
    governance = gold_layer.build_gold(
        findings, root, **RUN, check_remote=False,
    )
    before = deepcopy(governance)
    owner = build_owner_gold(governance)
    assert governance == before

    assert {row["notebook_id"] for row in governance["gold_notebooks"]} == {
        build_fixture.NB1, build_fixture.NB2,
    }
    assert len(governance["gold_notebook_smells"]) == 6
    assert all(row["notebook_url"] for row in governance["gold_notebook_smells"])
    assert all(bool(row["workspace_id"]) == direct and bool(row["notebook_id"]) == direct
               for row in governance["gold_notebook_smells"])
    assert len(governance["gold_model_tables"]) == 2
    assert all(bool(row["workspace_id"]) == direct for row in governance["gold_model_tables"])
    assert governance["gold_semantic_models"][0]["total_size"] == 456

    assert owner["owner_workspaces"] == [
        {"workspace_id": build_fixture.WS1, "workspace_name": "data-bronze-dev"},
        {"workspace_id": build_fixture.WS2, "workspace_name": "data-gold-prod"},
    ]
    assert [(row["workspace_id"], row["item_id"], row["rule_id"], row["severity"])
            for row in owner["owner_findings"] if row["rule_id"] == "DAX-001"] == [
        (build_fixture.WS1, build_fixture.DS_A, "DAX-001", "high"),
    ]
    details = owner["owner_details"]
    assert len(details) == (25 if direct else 7)
    assert {row["detail_type"] for row in details} == {
        "model_inventory", "dax_coverage", "dax_measure",
    } | ({"notebook_signal", "model_table_stat"} if direct else set())
    assert len(owner["owner_findings"]) == (7 if direct else 1)
    if direct:
        notebook_signals = [row for row in details if row["detail_type"] == "notebook_signal"]
        assert len(notebook_signals) == 6
        assert {row["signal_codes"] for row in notebook_signals} == {
            f"NBCODE-{index:03d}" for index in range(1, 7)
        }
        assert all(row["workspace_id"] == build_fixture.WS1
                   and row["item_id"] == build_fixture.NB1
                   and row["metric_value"] == 1 for row in notebook_signals)
        statistics = [row for row in details if row["detail_type"] == "model_table_stat"]
        assert len(statistics) == 12
        assert {(row["workspace_id"], row["item_id"], row["table_name"], row["metric_value"])
                for row in statistics if row["metric_name"] == "total_size"} == {
            (build_fixture.WS1, build_fixture.DS_A, "CollectedTableA", 456),
            (build_fixture.WS2, build_fixture.DS_B, "CollectedTableB", 7654321),
        }
    measures = [row for row in details if row["detail_type"] == "dax_measure"]
    assert len(measures) == 1
    measure = measures[0]
    assert (measure["item_name"], measure["table_name"], measure["measure_name"]) == (
        "SalesModel", "SalesFact", "Potentially Expensive Sales",
    )
    assert (measure["metric_name"], measure["metric_value"], measure["signal_codes"]) == (
        "static_pattern_points", 80, "crossjoin, nested_iterators, whole_table_filter",
    )
    assert [(row["item_name"], row["detail"]) for row in details
            if row["detail_type"] == "model_inventory" and row["item_id"] == build_fixture.DS_A] == [
        ("SalesModel", "Storage mode: Import"),
    ]
    reviews = {row["workspace_id"]: row for row in owner["owner_reviews"]}
    assert (reviews[build_fixture.WS1]["owner_fail_count"],
            reviews[build_fixture.WS1]["technical_detail_count"],
            reviews[build_fixture.WS1]["technical_evidence_status"]) == (
                (7, 13, "partial") if direct else (1, 1, "partial")
            )
    assert (reviews[build_fixture.WS2]["owner_fail_count"],
            reviews[build_fixture.WS2]["technical_detail_count"],
            reviews[build_fixture.WS2]["technical_evidence_status"]) == (
                (0, 6, "partial") if direct else (0, 0, "missing")
            )
    if not direct:
        assert "No safely attributable technical evidence" in reviews[build_fixture.WS2]["notice"]
    for row in reviews.values():
        assert row["assessment_status"] == "incomplete"
        assert "name-attributed notebook signals" in row["notice"]
        assert "unscoped model statistics" in row["notice"]
    serialized = json.dumps(owner)
    excluded_values = ["PLACEHOLDER-NOT-A-REAL-SECRET", raw["dax_analysis.json"]["measures"][0]["expression"]]
    if not direct:
        excluded_values.extend(["CollectedTableA", "CollectedTableB", "7654321"])
    for excluded in excluded_values:
        assert excluded not in serialized


def test_notebook_analyzer_keeps_same_named_items_in_separate_id_scopes(monkeypatch):
    from analyzers import notebook_code_review
    from reports import gold_layer

    raw = {"notebooks": [
        {"id": nid, "workspaceId": wid, "displayName": "Same notebook", "workspaceName": "Same workspace",
         "parts": [{"path": "notebook.ipynb", "decoded": {
             "cells": [{"cell_type": "code", "source": ["df.collect()"]}],
         }}]}
        for wid, nid in ((WA, NA), (WB, NB))
    ]}
    monkeypatch.setattr(notebook_code_review, "load_raw", lambda _, **kwargs: deepcopy(raw))
    root = Path(__file__).resolve().parents[1]
    findings = notebook_code_review.analyze(root, root / "config" / "review-checklist.yaml")
    finding = next(row for row in findings if row["rule_id"] == "NBCODE-003")
    assert finding["evidence"]["matchedNotebookCount"] == 2
    assert {(row["workspace_id"], row["notebook_id"]) for row in finding["evidence"]["examples"]} == {
        (WA, NA), (WB, NB),
    }
    assert all(row["cellIndexes"] == [0] for row in finding["evidence"]["examples"])
    workspaces = [
        {"id": wid, "name": "Same workspace",
         "Notebook": [{"id": nid, "name": "Same notebook"}]}
        for wid, nid in ((WA, NA), (WB, NB))
    ]
    payloads = {
        "workspace_inventory.json": {"workspaces": workspaces},
        "scanner.json": {"workspaces": workspaces},
    }
    monkeypatch.setattr(gold_layer, "_load", lambda directory, filename: deepcopy(payloads.get(filename)))
    governance = gold_layer.build_gold(findings, root, **RUN, check_remote=False)
    owner = build_owner_gold(governance)
    assert {(row["workspace_id"], row["item_id"], row["rule_id"]) for row in owner["owner_findings"]} == {
        (WA, NA, "NBCODE-003"), (WB, NB, "NBCODE-003"),
    }
    assert {(row["workspace_id"], row["item_id"]) for row in owner["owner_details"]} == {
        (WA, NA), (WB, NB),
    }

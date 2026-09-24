# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Synthetic owner evidence and additive schema tests; no live Fabric access."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import ModuleType, SimpleNamespace

import pytest

from reports.owner import gold, model
from reports.owner.schema import (
    DEFAULT_OWNER_MODEL_NAME, DEFAULT_OWNER_REPORT_NAME, OWNER_CONTRACT_VERSION, OWNER_TABLES_BY_NAME,
)
from tests.test_owner_gold import WA, WB, MA, MB, NA, RUN, SECRET, scoped

PIPE = "aaaaaaaa-0000-4000-8000-000000000031"
FLOW = "aaaaaaaa-0000-4000-8000-000000000041"


def test_public_setup_names_are_unversioned_with_an_internal_contract_marker():
    assert OWNER_CONTRACT_VERSION == 2
    assert DEFAULT_OWNER_MODEL_NAME == "Fabric Arch Review - Workspace Owner Model"
    assert DEFAULT_OWNER_REPORT_NAME == "Fabric Arch Review - Workspace Owner"


@pytest.fixture
def evidence():
    return {
        "gold_workspaces": [scoped(WA, workspace_name="Same name"), scoped(WB, workspace_name="Same name")],
        "gold_run_summary": [dict(RUN)],
        "gold_semantic_models": [
            scoped(WA, model_id=MA, model_name="Same model"),
            scoped(WB, model_id=MB, model_name="Same model"),
        ],
        "gold_notebooks": [scoped(WA, notebook_id=NA, notebook_name="Notebook")],
        "gold_pipelines": [scoped(WA, pipeline_id=PIPE, pipeline_name="Pipeline")],
        "gold_dax_models": [scoped(WA, model_id=MA, model_name="Same model", definition_status="available")],
        "gold_dax_object_coverage": [
            scoped(WA, model_id=MA, definition_status="available", object_count=3, flagged_object_count=3, notice=SECRET),
        ],
        "gold_dax_objects": [
            scoped(WA, model_id=MA, table_name="Table", object_type=kind, object_name="Same object",
                   signal_codes="crossjoin", risk_score=100, expression=SECRET,
                   signal_details_json=SECRET)
            for kind in ("calculated_column", "calculated_table", "calculation_item")
        ],
        "gold_dataflows": [
            scoped(WA, dataflow_id=FLOW, dataflow_name="Flow", definition_status="partial",
                   query_count=1, flagged_query_count=1, notice=SECRET),
        ],
        "gold_dataflow_queries": [
            scoped(WA, dataflow_id=FLOW, query_name="Query", signal_codes=f"DFLOW_TABLE_BUFFER;{SECRET}",
                   recommendation=SECRET, notice=SECRET),
        ],
        "gold_item_executions": [
            scoped(WA, item_id=iid, item_type=kind, execution_id="run-native-1", item_name=SECRET,
                   execution_type=SECRET, source=SECRET, status="Failed",
                   start_time="2026-09-01T00:00:00Z", end_time="2026-09-01T00:00:00.125Z",
                   duration_ms=125, execution_key=SECRET, error=SECRET)
            for iid, kind in ((MA, "SemanticModel"), (PIPE, "DataPipeline"), (NA, "Notebook"))
        ],
        "gold_execution_coverage": [
            scoped(WA, item_id=MA, item_type="SemanticModel", collection_status="partial",
                   observed_execution_count=1, history_scope=SECRET, notice=SECRET, source=SECRET,
                   oldest_start_time=None, newest_start_time="2026-09-01T00:00:00Z"),
        ],
    }


def test_evidence_projection_is_safe_typed_and_measure_separate(evidence):
    before = deepcopy(evidence)
    result = gold.build_owner_gold(evidence)
    assert evidence == before
    assert OWNER_CONTRACT_VERSION == 2
    assert SECRET not in json.dumps(result)
    assert {row["duration_ms"] for row in result["owner_executions"]} == {125}
    assert len({row["execution_key"] for row in result["owner_executions"]}) == 3
    objects = [row for row in result["owner_details"] if row["detail_type"].startswith("dax_cal")]
    assert len(objects) == 3
    assert all(row["measure_name"] is None and row["metric_value"] == 35 for row in objects)
    assert len({row["detail_key"] for row in objects}) == 3
    assert {row["rule_id"] for row in result["owner_findings"]} == {"DAX-OBJECT-001", "DFLOW-001", "EXEC-001"}
    assert all(row["workspace_id"] == WA for row in result["owner_findings"])
    assert not any(row["detail_type"] == "dax_measure" for row in result["owner_details"])
    for name, rows in result.items():
        assert all(set(row) == {c.name for c in OWNER_TABLES_BY_NAME[name].columns} for row in rows)


def test_native_key_is_stable_across_observation_runs(evidence):
    first = gold.build_owner_gold(evidence)["owner_executions"]
    for rows in evidence.values():
        for row in rows:
            row.update(run_id="later", run_timestamp="2026-09-02T00:00:00Z")
    second = gold.build_owner_gold(evidence)["owner_executions"]
    assert [r["execution_key"] for r in first] == [r["execution_key"] for r in second]
    assert {r["review_key"] for r in first}.isdisjoint(r["review_key"] for r in second)


@pytest.mark.parametrize("central_key", ["f" * 64, SECRET])
def test_central_review_item_key_is_not_owner_attribution_or_passthrough(evidence, central_key):
    expected = gold.build_owner_gold(evidence)
    for table in (
        "gold_item_executions", "gold_execution_coverage", "gold_dataflows",
        "gold_dataflow_queries", "gold_dax_objects", "gold_dax_object_coverage",
    ):
        for row in evidence[table]:
            row["review_item_key"] = central_key
    before = deepcopy(evidence)
    assert gold.build_owner_gold(evidence) == expected
    assert evidence == before


@pytest.mark.parametrize("source,id_field", [
    ("gold_item_executions", "item_id"), ("gold_execution_coverage", "item_id"),
    ("gold_dax_objects", "model_id"), ("gold_dax_object_coverage", "model_id"),
])
def test_cross_workspace_evidence_fails_closed(evidence, source, id_field):
    evidence[source][0][id_field] = MB
    with pytest.raises(ValueError, match="conflicts"):
        gold.build_owner_gold(evidence)


@pytest.mark.parametrize("source", [
    "gold_item_executions", "gold_execution_coverage", "gold_dax_objects",
    "gold_dax_object_coverage", "gold_dataflow_queries",
])
def test_uninventoried_workspace_evidence_is_not_name_matched(evidence, source):
    evidence[source][0]["workspace_id"] = "cccccccc-0000-4000-8000-000000000001"
    evidence[source][0]["workspace_name"] = "Same name"
    assert all(row["workspace_id"] in {WA, WB} for rows in gold.build_owner_gold(evidence).values() for row in rows)


@pytest.mark.parametrize("source,field,value", [
    ("gold_item_executions", "duration_ms", 0.125),
    ("gold_item_executions", "duration_ms", True),
    ("gold_item_executions", "duration_ms", -1),
    ("gold_item_executions", "duration_ms", 2**63),
    ("gold_item_executions", "start_time", "2026-09-01T00:00:00"),
    ("gold_item_executions", "end_time", "2026-08-01T00:00:00Z"),
    ("gold_item_executions", "execution_id", "raw literal\nvalue"),
    ("gold_execution_coverage", "observed_execution_count", -1),
    ("gold_dax_object_coverage", "flagged_object_count", 4),
    ("gold_dax_objects", "object_type", "measure"),
])
def test_invalid_native_values_do_not_enter_owner_tables(evidence, source, field, value):
    evidence[source][0][field] = value
    with pytest.raises(ValueError):
        gold.build_owner_gold(evidence)


def test_null_native_values_stay_null_and_unknown_status_is_explicit(evidence):
    row = evidence["gold_item_executions"][0]
    row.update(start_time=None, end_time=None, duration_ms=None, status=SECRET)
    result = gold.build_owner_gold(evidence)
    projected = next(r for r in result["owner_executions"] if r["item_id"] == MA)
    assert projected["status"] == "Unknown"
    assert all(projected[field] is None for field in ("start_time", "end_time", "duration_ms"))
    assert not any(r["rule_id"] == "EXEC-001" and r["item_id"] == MA for r in result["owner_findings"])


@pytest.mark.parametrize("start,end,expected", [
    ("2026-09-01T00:00:00Z", None, None),
    (None, "2026-09-01T00:00:00Z", None),
    (None, None, None),
    ("2026-09-01T00:00:00Z", "invalid", None),
    ("invalid", "2026-09-01T00:00:00Z", None),
    ("invalid", "invalid", None),
    ("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", 0),
    ("2026-09-01T00:00:00Z", "2026-09-01T00:00:00.125Z", 125),
])
def test_native_duration_survives_gold_and_owner_publication(tmp_path, spark, start, end, expected):
    from reports.execution_history import build_execution_evidence
    from reports.gold_layer import build_gold
    from reports.owner.fabric_runtime import _frame

    (tmp_path / "workspace_inventory.json").write_text(json.dumps({
        "workspaces": [{"id": WA, "name": "Workspace A"}],
    }), encoding="utf-8")
    (tmp_path / "semantic_models.json").write_text(json.dumps({
        "datasets": [{"id": MA, "name": "Model A", "workspaceId": WA}],
        "executionEvidence": [{
            "workspaceId": WA, "itemId": MA, "itemType": "SemanticModel",
            "collectionStatus": "collected",
            "executions": [{
                "requestId": "native-execution", "status": "Unknown",
                "startTime": start, "endTime": end,
            }],
        }],
    }), encoding="utf-8")
    raw = build_execution_evidence(tmp_path, RUN["run_id"], RUN["run_timestamp"])
    central = build_gold([], tmp_path, **RUN, check_remote=False)
    owner = gold.build_owner_gold(central)
    published = _frame(spark, OWNER_TABLES_BY_NAME["owner_executions"], owner["owner_executions"])

    for rows in (raw["gold_item_executions"], central["gold_item_executions"],
                 owner["owner_executions"], published.rows):
        assert len(rows) == 1
        assert rows[0]["duration_ms"] == expected
        assert sum(row["duration_ms"] is not None for row in rows) == int(expected is not None)
        if expected is not None:
            assert type(rows[0]["duration_ms"]) is int
    duration = next(field for field in published.schema.fields if field.name == "duration_ms")
    assert duration.nullable and duration.dataType == "LongType"


def test_omitted_and_empty_sources_preserve_inventory_and_missing_coverage(evidence):
    keys = ("gold_item_executions", "gold_execution_coverage", "gold_dataflow_queries",
            "gold_dax_objects", "gold_dax_object_coverage", "gold_dataflows")
    omitted = {key: value for key, value in evidence.items() if key not in keys}
    empty = {**omitted, **{key: [] for key in keys}}
    result = gold.build_owner_gold(omitted)
    assert result == gold.build_owner_gold(empty)
    assert result["owner_executions"] == []
    assert result["owner_findings"] == []
    assert all(r["technical_evidence_status"] == "missing" for r in result["owner_reviews"])
    assert any(r["evidence_type"] == "Dataflow inventory" and r["collection_status"] == "missing"
               for r in result["owner_coverage"])


def test_duplicate_approved_new_rows_collapse_but_conflicting_facts_raise(evidence):
    expected = gold.build_owner_gold(evidence)
    for table in ("gold_item_executions", "gold_dataflow_queries", "gold_dax_objects",
                  "gold_dax_object_coverage", "gold_execution_coverage"):
        evidence[table] += deepcopy(evidence[table])
    assert gold.build_owner_gold(evidence) == expected
    evidence["gold_item_executions"][-1]["duration_ms"] = 126
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        gold.build_owner_gold(evidence)


@pytest.fixture
def architecture_evidence(evidence):
    evidence["gold_findings"] = [{
        **RUN, "rule_id": "ARCH-016", "status": "fail",
        "evidence_json": json.dumps({"items": [{
            "workspace_id": WA, "item_id": PIPE, "item_type": "DataPipeline",
            "signal_codes": ["dependency_cycle"], "affected_count": 2,
            "activity_count": 3, "coverage_status": "partial",
            "reason_codes": ["definition_collection_partial"],
        }]}),
    }]
    return evidence


@pytest.mark.parametrize("changes", [
    {"item_type": None}, {"item_type": "SemanticModel"},
    {"workspace_id": None}, {"item_id": None},
    {"reason_codes": ["identity_unresolved"]},
])
def test_architecture_rejects_unresolved_or_incorrectly_typed_attribution(architecture_evidence, changes):
    finding = architecture_evidence["gold_findings"][0]
    payload = json.loads(finding["evidence_json"])
    payload["items"][0].update(changes)
    finding["evidence_json"] = json.dumps(payload)
    result = gold.build_owner_gold(architecture_evidence)
    assert not any(row["rule_id"] == "ARCH-016" for row in result["owner_findings"])
    assert not any(row["detail_type"] == "pipeline_structure" for row in result["owner_details"])


@pytest.mark.parametrize("value", [0, None, -1, True, 1.5, "2", 2**63])
def test_architecture_defects_require_positive_integer_counts(architecture_evidence, value):
    finding = architecture_evidence["gold_findings"][0]
    payload = json.loads(finding["evidence_json"])
    payload["items"][0]["affected_count"] = value
    finding["evidence_json"] = json.dumps(payload)
    with pytest.raises(ValueError):
        gold.build_owner_gold(architecture_evidence)


@pytest.mark.parametrize("state", ["complete", "partial", "unknown", "missing_evidence"])
def test_signal_free_architecture_rows_never_inherit_aggregate_failure(architecture_evidence, state):
    finding = architecture_evidence["gold_findings"][0]
    payload = json.loads(finding["evidence_json"])
    payload["items"][0].update(signal_codes=[], affected_count=0, coverage_status=state)
    finding["evidence_json"] = json.dumps(payload)
    result = gold.build_owner_gold(architecture_evidence)
    assert not any(row["rule_id"] == "ARCH-016" for row in result["owner_findings"])
    assert not any(row["detail_type"] == "pipeline_structure" for row in result["owner_details"])
    assert any(row["evidence_type"] == "Pipeline structure" for row in result["owner_coverage"])


def test_partial_architecture_signals_remain_observed_static_defects(architecture_evidence):
    result = gold.build_owner_gold(architecture_evidence)
    finding = next(row for row in result["owner_findings"] if row["rule_id"] == "ARCH-016")
    assert finding["affected_count"] == 2
    assert finding["item_id"] == PIPE
    assert "static graph checks do not prove execution reliability" in finding["recommendation"]
    coverage = next(row for row in result["owner_coverage"] if row["evidence_type"] == "Pipeline structure")
    assert coverage["collection_status"] == "partial"


def test_architecture_untyped_reason_codes_fail_closed(architecture_evidence):
    finding = architecture_evidence["gold_findings"][0]
    payload = json.loads(finding["evidence_json"])
    payload["items"][0]["reason_codes"] = "identity_unresolved"
    finding["evidence_json"] = json.dumps(payload)
    with pytest.raises(ValueError, match="Architecture reasons"):
        gold.build_owner_gold(architecture_evidence)


def test_explicit_pipeline_structure_never_uses_name_targets_or_freeform_text(evidence):
    evidence["gold_findings"] = [{
        **RUN, "rule_id": "ARCH-016", "status": "fail", "recommendation": SECRET,
        "evidence_json": json.dumps({"items": [{
            "workspace_id": WA, "item_id": PIPE, "item_type": "DataPipeline",
            "item_name": SECRET, "signal_codes": ["dependency_cycle", SECRET],
            "activity_count": 2, "affected_count": 2, "coverage_status": "complete",
        }, {
            "workspace_name": "Same name", "item_id": PIPE,
            "signal_codes": ["dependency_cycle"], "affected_count": 99,
        }]}),
    }]
    result = gold.build_owner_gold(evidence)
    structural = [r for r in result["owner_findings"] if r["rule_id"] == "ARCH-016"]
    assert len(structural) == 1
    assert structural[0]["affected_count"] == 2
    assert structural[0]["item_name"] == "Pipeline"
    assert SECRET not in json.dumps(result)


def test_model_duration_unknown_is_blank_and_static_findings_use_rule_identity():
    bim = model.build_bim("Owner", "host", "database")
    measures = {m["name"]: m["expression"] for t in bim["model"]["tables"] for m in t.get("measures", [])}
    assert "rule_id" in measures["Scoped DAX findings"]
    assert "item_type" not in measures["Scoped DAX findings"]
    assert "EXEC-001" not in measures["Scoped DAX findings"]
    assert 'detail_type] = "dax_measure"' in measures["Scoped DAX rows"]
    assert "COALESCE" not in measures["Scoped maximum duration ms"]
    assert "MAX(owner_executions[duration_ms])" in measures["Scoped maximum duration ms"]
    assert "COUNT(owner_executions[duration_ms])" in measures["Scoped timed executions"]
    assert {a["value"] for a in bim["model"]["annotations"] if a["name"] == "OwnerContractVersion"} == {"2"}


def test_source_adapter_enums_and_keys_match_owner_allowlists(evidence):
    from reports.dataflow_evidence import SIGNALS, STATUSES
    from reports.execution_history import COLLECTION_STATUSES, STATUS_MAP, _execution_rows

    assert set(SIGNALS) == set(gold._DATAFLOW_GUIDANCE)
    assert STATUSES <= gold._DEFINITION_STATES
    assert COLLECTION_STATUSES == gold._COLLECTION_STATES
    assert set(STATUS_MAP.values()) <= set(gold._EXECUTION_STATES)
    rows, _ = _execution_rows([{
        "requestId": "native-123", "status": "Completed",
        "startTime": "2026-09-01T00:00:00Z", "endTime": "2026-09-01T00:00:00.125Z",
    }], {
        "workspace_id": WA, "workspace_name": "Same name", "item_id": MA,
        "item_name": "Same model", "item_type": "SemanticModel",
    }, "powerbi_refresh_history")
    evidence["gold_item_executions"] = [{**RUN, **row} for row in rows]
    projected = gold.build_owner_gold(evidence)["owner_executions"]
    assert projected[0]["execution_key"] == rows[0]["execution_key"]
    assert projected[0]["duration_ms"] == 125
    assert projected[0]["status"] == "Completed"


def test_explicit_dataflow_inventory_gap_is_not_a_discovered_item(evidence):
    evidence["gold_dataflows"].append(scoped(
        WA, dataflow_id="", dataflow_name=SECRET, definition_status="inventory_unavailable",
        notice=SECRET, query_count=0, flagged_query_count=0,
    ))
    result = gold.build_owner_gold(evidence)
    gaps = [r for r in result["owner_coverage"] if r["evidence_type"] == "Dataflow inventory" and r["workspace_id"] == WA]
    assert len(gaps) == 1
    assert gaps[0]["item_type"] == "Workspace"
    assert gaps[0]["collection_status"] == "missing"
    assert gaps[0]["observed_count"] is None
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("kind", ["SemanticModel", "DataPipeline", "Notebook"])
def test_native_inventory_gap_survives_alongside_known_item_evidence(evidence, kind):
    baseline = gold.build_owner_gold(evidence)
    evidence["gold_execution_coverage"].append(scoped(
        WA, item_id=None, item_type=kind, item_name=SECRET, collection_status="forbidden",
        observed_execution_count=0, history_scope=SECRET, notice=SECRET, source=SECRET,
    ))
    result = gold.build_owner_gold(evidence)
    gaps = [r for r in result["owner_coverage"] if r["evidence_type"] == f"{kind} execution inventory"]
    assert len(gaps) == 1
    assert gaps[0]["workspace_id"] == WA and gaps[0]["item_id"] == WA
    assert gaps[0]["item_type"] == "Workspace" and gaps[0]["item_name"] == "Same name"
    assert gaps[0]["collection_status"] == "forbidden"
    assert gaps[0]["observed_count"] is None
    assert result["owner_executions"] == baseline["owner_executions"]
    assert result["owner_findings"] == baseline["owner_findings"]
    assert result["owner_reviews"] == baseline["owner_reviews"]
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("state", ["empty", "collected", "unexpected"])
def test_native_inventory_gap_cannot_claim_successful_empty_collection(evidence, state):
    evidence["gold_execution_coverage"].append(scoped(
        WA, item_id=None, item_type="Notebook", collection_status=state,
        observed_execution_count=0,
    ))
    result = gold.build_owner_gold(evidence)
    gap = next(r for r in result["owner_coverage"] if r["evidence_type"] == "Notebook execution inventory")
    assert gap["collection_status"] == "unknown"
    assert gap["observed_count"] is None


@pytest.mark.parametrize("workspace", [None, "cccccccc-0000-4000-8000-000000000001"])
def test_native_inventory_gap_requires_an_inventoried_workspace(evidence, workspace):
    evidence["gold_execution_coverage"].append(scoped(
        workspace, item_id=None, item_type="Notebook", collection_status="forbidden",
    ))
    result = gold.build_owner_gold(evidence)
    assert not any(r["evidence_type"] == "Notebook execution inventory" for r in result["owner_coverage"])


def test_dataflow_informational_signal_does_not_increment_failed_findings(evidence):
    result = gold.build_owner_gold(evidence)
    finding = next(r for r in result["owner_findings"] if r["rule_id"] == "DFLOW-001")
    assert finding["status"] == "info" and finding["is_fail"] == 0
    review = next(r for r in result["owner_reviews"] if r["workspace_id"] == WA)
    assert review["owner_fail_count"] == review["owner_finding_count"] - 1


def test_coverage_cannot_claim_complete_when_observations_are_omitted(evidence):
    evidence["gold_item_executions"] = []
    evidence["gold_execution_coverage"][0]["collection_status"] = "collected"
    result = gold.build_owner_gold(evidence)
    coverage = next(r for r in result["owner_coverage"]
                    if r["item_id"] == MA and r["evidence_type"] == "Native executions")
    assert coverage["collection_status"] == "partial"
    assert coverage["observed_count"] == 1
    assert "0 records were safely projected" in coverage["notice"]


class Frame:
    def __init__(self, spark, rows, schema):
        self.spark, self.rows, self.schema = spark, rows, schema

    def limit(self, count):
        return Frame(self.spark, self.rows[:count], self.schema)

    @property
    def write(self):
        return Writer(self)

    def where(self, predicate):
        return Frame(self.spark, [r for r in self.rows if predicate(r)], self.schema)

    def count(self):
        return len(self.rows)


class Writer:
    def __init__(self, frame):
        self.frame, self.predicate, self.mode_name = frame, None, None

    def format(self, value):
        assert value == "delta"
        return self

    def mode(self, value):
        self.mode_name = value
        return self

    def option(self, name, value):
        assert name == "replaceWhere"
        self.predicate = value.split("'")[1]
        return self

    def saveAsTable(self, name):
        old = self.frame.spark.tables.get(name)
        if self.mode_name == "ignore" and old is not None:
            return
        retained = [r for r in old.rows if r["run_id"] != self.predicate] if old and self.predicate else []
        self.frame.spark.tables[name] = Frame(
            self.frame.spark, retained + deepcopy(self.frame.rows), self.frame.schema,
        )
        self.frame.spark.writes.append(name)

    def save(self, path):
        self.saveAsTable(path.rsplit("/", 1)[-1])


@pytest.fixture
def spark(monkeypatch):
    types = ModuleType("pyspark.sql.types")
    for name in ("BooleanType", "DoubleType", "LongType", "StringType", "TimestampType"):
        setattr(types, name, lambda name=name: name)
    types.StructField = lambda name, dataType, nullable: SimpleNamespace(name=name, dataType=dataType, nullable=nullable)
    types.StructType = lambda fields: SimpleNamespace(fields=fields)
    sql = ModuleType("pyspark.sql")

    class Col:
        def __init__(self, name):
            self.name = name

        def __eq__(self, value):
            return lambda row: row[self.name] == value

    sql.functions = SimpleNamespace(col=Col, lit=lambda value: value)
    monkeypatch.setitem(__import__("sys").modules, "pyspark", ModuleType("pyspark"))
    monkeypatch.setitem(__import__("sys").modules, "pyspark.sql", sql)
    monkeypatch.setitem(__import__("sys").modules, "pyspark.sql.types", types)
    store = SimpleNamespace(tables={}, writes=[])
    store.catalog = SimpleNamespace(tableExists=lambda name: name in store.tables)
    store.table = lambda name: store.tables[name]
    store.createDataFrame = lambda rows, schema: Frame(
        store, [dict(zip([f.name for f in schema.fields], row)) for row in rows], schema,
    )
    return store


@pytest.mark.parametrize("bootstrap_first", [True, False])
def test_runtime_additive_bootstrap_and_materialization_keep_legacy_history_without_grants(
    spark, evidence, monkeypatch, bootstrap_first, capsys,
):
    from reports.owner import fabric_runtime as runtime

    for name, table in OWNER_TABLES_BY_NAME.items():
        if name in {"owner_executions", "owner_coverage"}:
            continue
        spark.tables[name] = runtime._frame(spark, table, [] if name == "owner_access" else [
            {"run_id": "old-run", "workspace_id": WB, "review_key": f"old-run:{WB}"},
        ])
    original = deepcopy({name: frame.rows for name, frame in spark.tables.items()})
    if bootstrap_first:
        runtime.bootstrap_owner_tables(spark, "Tables")
        runtime.bootstrap_owner_tables(spark, "Tables")
    assert all(spark.tables[name].rows == rows for name, rows in original.items())
    publications = []
    monkeypatch.setattr(runtime, "_publish_owner_reviews", lambda *args: publications.append(args[2]))
    assert runtime.materialize_owner_gold(spark, evidence)
    assert runtime.materialize_owner_gold(spark, evidence)
    assert publications == ["run-1", "run-1"]
    messages = capsys.readouterr().out
    assert "owner_executions: replaced run run-1 with 3 row(s)." in messages
    assert "owner_coverage: replaced run run-1 with" in messages
    assert "owner_reviews: published run run-1 for 2 workspace(s)." in messages
    assert spark.tables["owner_access"].rows == []
    assert spark.tables["owner_findings"].rows[0]["run_id"] == "old-run"
    assert spark.tables["owner_details"].rows[0]["run_id"] == "old-run"
    assert len(spark.tables["owner_executions"].rows) == 3
    assert {r["duration_ms"] for r in spark.tables["owner_executions"].rows} == {125}
    assert all(isinstance(r["start_time"], datetime) and r["start_time"].tzinfo == timezone.utc
               for r in spark.tables["owner_executions"].rows)
    assert spark.tables["owner_executions"].schema.fields[-3].dataType == "LongType"
    spark.tables["owner_coverage"].schema.fields[0].dataType = "WrongType"
    before = list(spark.writes)
    with pytest.raises(ValueError, match="Incompatible owner table schema"):
        runtime.materialize_owner_gold(spark, evidence)
    assert spark.writes == before


def test_five_legacy_physical_schemas_are_unchanged():
    common = "review_key workspace_id run_id run_timestamp "
    expected = {
        "owner_workspaces": "workspace_id workspace_name",
        "owner_reviews": common + "workspace_name is_latest owner_fail_count owner_finding_count "
                        "owner_detail_count technical_detail_count assessment_status technical_evidence_status notice",
        "owner_findings": common + "finding_key rule_id dimension severity status is_fail item_id item_name "
                         "item_type title recommendation signal_codes affected_count notice",
        "owner_details": common + "detail_key detail_type item_id item_name item_type table_name measure_name "
                        "signal_codes metric_name metric_value detail notice",
        "owner_access": "workspace_id principal_object_id refreshed_at expires_at",
    }
    numbers = {"owner_fail_count", "owner_finding_count", "owner_detail_count",
               "technical_detail_count", "is_fail", "affected_count", "metric_value"}
    dates = {"run_timestamp", "refreshed_at", "expires_at"}
    for name, columns in expected.items():
        assert [(c.name, c.kind) for c in OWNER_TABLES_BY_NAME[name].columns] == [
            (column, "int64" if column in numbers else "dateTime" if column in dates
             else "boolean" if column == "is_latest" else "string")
            for column in columns.split()
        ]

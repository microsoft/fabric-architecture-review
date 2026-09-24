# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline contracts for central agent grounding; no Fabric/LLM calls."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import sys
from types import ModuleType

import pytest

from reports.agent.data_agent import (
    DEFAULT_AI_INSTRUCTIONS,
    DEFAULT_LAKEHOUSE_FEWSHOTS,
    LATEST_EXECUTION_OBSERVATION_SQL,
    LATEST_WORKSPACE_REVIEW_SQL,
    agent_publish_description,
    compose_instructions,
)
from reports.agent.evaluate import EVAL_CASES
from reports.agent.sdk_deploy import (
    APPROVED_GOLD_TABLES,
    DEFAULT_SKIP_TABLES,
    _select_tables,
    agent_tables,
    deploy_agent,
)
from reports.powerbi.schema import GOLD_TABLES


NATIVE_TABLES = {
    "gold_item_executions", "gold_execution_coverage", "gold_dataflows",
    "gold_dataflow_queries", "gold_dax_objects", "gold_dax_object_coverage",
}


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for table in GOLD_TABLES:
        kinds = {"int64": "INTEGER", "boolean": "INTEGER", "double": "REAL"}
        columns = ", ".join(
            f'"{column.name}" {kinds.get(column.kind, "TEXT")}'
            for column in table.columns
        )
        db.execute(f'CREATE TABLE "{table.name}" ({columns})')
    return db


def _query_for(text: str) -> str:
    return next(item["query"] for item in DEFAULT_LAKEHOUSE_FEWSHOTS if text in item["question"])


def _sqlite(query: str) -> str:
    top = re.search(r"\bTOP (\d+)\s+", query)
    if not top:
        return query
    return re.sub(r"\bTOP \d+\s+", "", query).rstrip(";") + f" LIMIT {top[1]};"


def _insert(db: sqlite3.Connection, table: str, **row: object) -> None:
    names = ", ".join(row)
    params = ", ".join("?" for _ in row)
    db.execute(f"INSERT INTO {table} ({names}) VALUES ({params})", tuple(row.values()))


def test_all_fewshots_use_real_gold_fields_and_explicitly_approved_tables() -> None:
    db = _database()
    try:
        for example in DEFAULT_LAKEHOUSE_FEWSHOTS:
            referenced = set(re.findall(r"\bgold_[a-z_]+\b", example["query"]))
            assert referenced <= APPROVED_GOLD_TABLES, example["question"]
            db.execute(_sqlite(example["query"]))
        assert NATIVE_TABLES <= set(agent_tables())
        assert set(agent_tables([])) == APPROVED_GOLD_TABLES
        assert set(agent_tables()) == APPROVED_GOLD_TABLES - DEFAULT_SKIP_TABLES
        assert APPROVED_GOLD_TABLES <= {table.name for table in GOLD_TABLES}
        assert "owner_access" not in agent_tables([])
    finally:
        db.close()


def test_latest_workspace_sql_retains_untouched_workspace_and_no_stale_definitions() -> None:
    db = _database()
    try:
        for run_id, timestamp, workspace, definition in (
            ("old", "2026-09-14T00:00:00Z", "a", "available"),
            ("old", "2026-09-14T00:00:00Z", "b", "unsupported"),
            ("new", "2026-09-21T00:00:00Z", "a", "missing"),
        ):
            common = dict(run_id=run_id, run_timestamp=timestamp, workspace_id=workspace)
            _insert(db, "gold_workspaces", **common)
            dataflow_status = {"available": "inspected", "missing": "unavailable"}.get(definition, definition)
            _insert(db, "gold_dataflows", **common, dataflow_id="flow", definition_status=dataflow_status)
            dax_status = {"available": "complete", "missing": "unavailable", "unsupported": "partial"}[definition]
            _insert(db, "gold_dax_object_coverage", **common, model_id="model", definition_status=dax_status)
        for text in ("Which Dataflow Gen2 definitions", "Show non-measure DAX definition coverage"):
            query = _query_for(text)
            cursor = db.execute(query)
            results = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor]
            assert {(row["workspace_id"], row["definition_status"]) for row in results} == {
                ("a", "unavailable"), ("b", "unsupported" if "Dataflow" in text else "partial"),
            }
        assert "MAX(run_timestamp)" not in LATEST_WORKSPACE_REVIEW_SQL
    finally:
        db.close()


def test_history_sql_deduplicates_before_window_status_and_duration_aggregation() -> None:
    db = _database()
    try:
        for key, run, status, duration, start in (
            ("changed", "old", "failed", 99000, "2026-09-15T00:00:00Z"),
            ("changed", "new", "completed", 1250, "2026-09-15T00:00:00Z"),
            ("cancelled", "new", "cancelled", 2000, "2026-09-14T00:00:00Z"),
            ("active", "new", "in_progress", None, "2026-09-18T00:00:00Z"),
            ("pending", "new", "not_started", None, "2026-09-18T00:00:00Z"),
            ("deduped", "new", "deduped", None, "2026-09-18T00:00:00Z"),
            ("disabled", "new", "disabled", None, "2026-09-18T00:00:00Z"),
            ("unknown", "new", "unknown", None, "2026-09-19T00:00:00Z"),
            ("failed", "new", "failed", 3000, "2026-09-20T00:00:00Z"),
            ("end-bound", "new", "failed", 9000, "2026-09-21T00:00:00Z"),
            ("moved-out", "old", "failed", 8000, "2026-09-16T00:00:00Z"),
            ("moved-out", "new", "completed", 8000, "2026-09-22T00:00:00Z"),
        ):
            _insert(
                db, "gold_item_executions", execution_key=key, run_id=run,
                run_timestamp="2026-09-15T00:00:00Z" if run == "old" else "2026-09-22T00:00:00Z",
                status=status, duration_ms=duration, start_time=start,
                workspace_id="a", workspace_name="A",
            )
        cursor = db.execute(_query_for("Across saved FAR snapshots"))
        rows = {row[2]: row for row in cursor}
        assert {status: row[3] for status, row in rows.items()} == {
            "completed": 1, "cancelled": 1, "in_progress": 1, "unknown": 1, "failed": 1,
            "not_started": 1, "deduped": 1, "disabled": 1,
        }
        assert rows["completed"][5] == 1.25
        assert rows["in_progress"][4:6] == (0, None)
        assert rows["failed"][5] == 3.0
        assert "WHERE" not in LATEST_EXECUTION_OBSERVATION_SQL
    finally:
        db.close()


def test_dataflow_sql_retains_inventory_notices_without_counting_them_as_artifacts() -> None:
    db = _database()
    try:
        common = dict(run_id="new", run_timestamp="2026-09-21T00:00:00Z", workspace_id="a")
        _insert(db, "gold_workspaces", **common)
        for dataflow_id, status, notice in (
            ("inspected-flow", "inspected", "Static only; not runtime clean."),
            ("partial-flow", "partial", "Some definition parts not inspected."),
            ("", "inventory_unavailable", "Undiscovered dataflows may exist."),
        ):
            _insert(db, "gold_dataflows", **common, dataflow_id=dataflow_id,
                    definition_status=status, notice=notice)
        count = db.execute(_query_for("Count incomplete static inspections")).fetchone()[0]
        assert count == 1
        cursor = db.execute(_query_for("Show Dataflow Gen2 inventory coverage gaps"))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert len(rows) == 1
        assert rows[0]["definition_status"] == "inventory_unavailable"
        assert rows[0]["notice"] == "Undiscovered dataflows may exist."
    finally:
        db.close()


def test_non_measure_dax_sql_filters_incomplete_extraction_not_measure_availability() -> None:
    db = _database()
    try:
        common = dict(run_id="new", run_timestamp="2026-09-21T00:00:00Z", workspace_id="a")
        _insert(db, "gold_workspaces", **common)
        for status in ("complete", "partial", "unavailable"):
            _insert(db, "gold_dax_object_coverage", **common, model_id=status, definition_status=status)
        cursor = db.execute(_query_for("Which non-measure DAX definitions"))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert {row["definition_status"] for row in rows} == {"partial", "unavailable"}
    finally:
        db.close()


def test_arch016_workspace_targets_do_not_join_duplicate_display_names() -> None:
    db = _database()
    try:
        for run, workspace in (("old", "a"), ("new", "a"), ("new", "b")):
            common = dict(
                run_id=run, workspace_id=workspace, workspace_name="Same name",
                run_timestamp=f"2026-09-{'14' if run == 'old' else '21'}T00:00:00Z",
            )
            _insert(db, "gold_workspaces", **common)
            if (run, workspace) != ("new", "a"):
                _insert(db, "gold_finding_targets", **common, rule_id="ARCH-016",
                        severity="medium", status="fail")
        rows = db.execute(_query_for("Which workspaces have ARCH-016")).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "b"
    finally:
        db.close()


@pytest.mark.parametrize(
    ("question", "coverage_table", "detail_table", "item_id", "identity", "coverage_field", "detail_fields"),
    [
        ("Which observed native refreshes", "gold_execution_coverage", "gold_item_executions",
         "item_id", "execution_key", "notice", {"duration_ms": 1250}),
        ("What static Power Query M signals", "gold_dataflows", "gold_dataflow_queries",
         "dataflow_id", "query_name", "definition_status", {"signal_count": 1}),
        ("Rank calculated columns", "gold_dax_object_coverage", "gold_dax_objects",
         "model_id", "object_name", "definition_status", {"object_type": "calculation_item"}),
    ],
)
def test_detail_fewshots_join_gold_review_key_without_cross_review_or_workspace_matches(
    question: str, coverage_table: str, detail_table: str, item_id: str,
    identity: str, coverage_field: str, detail_fields: dict[str, object],
) -> None:
    db = _database()
    try:
        for run, workspace, key, status in (
            ("old", "a", "a" * 64, "old-coverage"),
            ("new", "a", "b" * 64, "current-coverage"),
            ("new", "b", "c" * 64, "other-workspace"),
        ):
            common = dict(
                run_id=run, run_timestamp=f"2026-09-{'14' if run == 'old' else '21'}T00:00:00Z",
                workspace_id=workspace,
            )
            _insert(db, "gold_workspaces", **common)
            _insert(db, coverage_table, **common, review_item_key=key,
                    **{item_id: "same-item-id", coverage_field: status})
        for name, key in (("linked", "b" * 64), ("uncovered", None)):
            _insert(
                db, detail_table, run_id="new", run_timestamp="2026-09-21T00:00:00Z",
                workspace_id="a", review_item_key=key,
                **{item_id: "same-item-id", identity: name, **detail_fields},
            )
        query = _query_for(question)
        assert re.search(r"\b\w+\.review_item_key = \w+\.review_item_key\b", query)
        cursor = db.execute(_sqlite(query))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert {row[identity]: row[coverage_field] for row in rows} == {
            "linked": "current-coverage", "uncovered": None,
        }
        assert len(rows) == 2
    finally:
        db.close()


def test_instruction_contract_has_boundary_and_evidence_limits() -> None:
    for phrase in (
        "CENTRAL", "NOT authorization", "Workspace Owner", "owner_access", "customer business rows",
        "not complete weekly history", "active/cancelled", "unknown is not success",
        "nullable int64 milliseconds", "NULL is unknown", "EACH workspace",
        "execution_key", "review_item_key", "calculated_column", "calculated_table", "calculation_item",
        "Report visual calculations are unsupported", "NON-MEASURE", "ARCH-016",
        "NOT runtime performance", "folding outcomes or savings", "dangling",
        "dependsOn", "self-dependencies", "cycles within each container scope",
        "inspected means selected lexical checks only", "Empty dataflow_id",
        "parse_error", "inventory_unavailable", "semicolon-delimited",
        "DFLOW_TABLE_BUFFER", "DFLOW_STOP_FOLDING", "DFLOW_NATIVE_QUERY",
        "DFLOW-001 syntax signals are info, never fail", "DFLOW-002",
        "complete means supported-type extraction", "Format-string expressions are excluded",
        "DAX-001/002 scoring remains measure-only", "DAX-002.evidence.non_measure_coverage",
        "duplicate_activity", "missing_dependency", "self_dependency", "dependency_cycle",
    ):
        assert phrase in DEFAULT_AI_INSTRUCTIONS
    assert len(compose_instructions("0.0.0-dev.20260922")) < 15000
    public_text = "\n".join([
        compose_instructions("2026.09.2"), agent_publish_description("2026.09.2"),
        *(example["question"] for example in DEFAULT_LAKEHOUSE_FEWSHOTS),
        *(case.question for case in EVAL_CASES),
    ])
    assert not re.search(r"owner v2|no FUAM dependency|rely on FUAM", public_text, re.I)


class FakeSource:
    def __init__(self, name: str = "Gold", kind: str = "lakehouse_tables") -> None:
        self.name = name
        self.kind = kind
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.fewshots: dict[str, str] = {}
        self.ignore_unselect = False
        self.fail_select = False
        self.tables = [
            dict(name=name, type=f"{kind}.table", is_selected=True, children=[])
            for name in ("gold_workspaces", "gold_item_executions", "gold_execution_coverage",
                         "gold_agent_eval", "owner_access", "raw_sales", "gold_future_unreviewed")
        ]
        self.elements = (
            [dict(name="dbo", type=f"{kind}.schema", children=self.tables),
             dict(name="raw", type=f"{kind}.schema", children=[
                 dict(name="gold_workspaces", type=f"{kind}.table", is_selected=True, children=[]),
             ])]
            if kind == "lakehouse_tables" else self.tables
        )

    def get_configuration(self) -> dict:
        return copy.deepcopy(dict(display_name=self.name, type=self.kind, elements=self.elements))

    def _element(self, path: tuple[str, ...]) -> dict:
        elements = self.elements
        for index, name in enumerate(path):
            element = next(element for element in elements
                           if (element.get("name") or element.get("display_name")) == name)
            if index == len(path) - 1:
                return element
            elements = element["children"]
        raise AssertionError("Blanket select/unselect is forbidden")

    def select(self, *path: str) -> None:
        assert path, "Never select all"
        self.calls.append(("select", path))
        if self.fail_select:
            raise RuntimeError("SDK selection failed")
        self._element(path)["is_selected"] = True

    def unselect(self, *path: str) -> None:
        assert path, "Use exact SDK paths"
        self.calls.append(("unselect", path))
        if not self.ignore_unselect:
            self._element(path)["is_selected"] = False

    def add_fewshots(self, examples: dict[str, str]) -> None:
        self.fewshots.update(examples)


def _grouped_schema(source: FakeSource) -> None:
    for schema in source.elements:
        schema["children"] = [
            dict(display_name="Tables", type="table_grouping", children=schema["children"]),
            dict(display_name="Views", type="view_grouping", children=[
                dict(display_name="gold_findings", type="lakehouse_tables.view",
                     is_selected=True, children=[]),
            ]),
            dict(display_name="Functions", type="function_grouping", children=[
                dict(display_name="gold_findings", type="lakehouse_tables.function",
                     is_selected=True, children=[]),
            ]),
        ]
    source.elements = [
        dict(display_name="Schemas", type="schema_grouping", children=source.elements),
        dict(display_name="Files", type="lakehouse_files", is_selected=False, children=[]),
    ]


def test_grouped_schema_preserves_full_sdk_paths_and_dbo_table_allowlist() -> None:
    source = FakeSource()
    _grouped_schema(source)
    assert _select_tables(source, DEFAULT_SKIP_TABLES) == {
        "gold_workspaces", "gold_item_executions", "gold_execution_coverage",
    }
    selections = {path for operation, path in source.calls if operation == "select"}
    assert selections == {
        ("Schemas", "dbo", "Tables", table)
        for table in ("gold_workspaces", "gold_item_executions", "gold_execution_coverage")
    }
    assert not any("Views" in path or "raw" in path for path in selections)
    assert _select_tables(source, DEFAULT_SKIP_TABLES) == {path[-1] for path in selections}


@pytest.mark.parametrize("selected_files", [False, True])
def test_grouped_schema_still_blocks_unverifiable_selection_before_publish(monkeypatch, selected_files):
    source = FakeSource()
    _grouped_schema(source)
    if selected_files:
        source.elements[1]["children"] = [
            dict(display_name="raw", type="lakehouse_files.directory",
                 is_selected=True, children=[]),
        ]
    else:
        source.ignore_unselect = True
    agent = FakeAgent([FakeSource("Governance", "semantic_model"), source])
    _install_fake_sdk(monkeypatch, agent)
    with pytest.raises(ValueError):
        deploy_agent(agent_name="Central", model_name="Governance", lakehouse_name="Gold")
    assert not agent.published


@pytest.mark.parametrize("mutation", ["unknown", "missing_children", "duplicate_group", "duplicate_table"])
def test_malformed_grouped_schema_stops_before_selection(mutation):
    source = FakeSource()
    _grouped_schema(source)
    grouping = source.elements[0]
    if mutation == "unknown":
        grouping["type"] = "unknown_grouping"
    elif mutation == "missing_children":
        del grouping["children"]
    elif mutation == "duplicate_group":
        source.elements.append(copy.deepcopy(grouping))
    else:
        duplicate = copy.deepcopy(grouping)
        duplicate["display_name"] = "Other schemas"
        source.elements.append(duplicate)
    with pytest.raises(ValueError):
        _select_tables(source, DEFAULT_SKIP_TABLES)
    assert not source.calls


@pytest.mark.parametrize("kind", ["lakehouse_tables", "semantic_model"])
def test_selection_is_an_exact_allowlist_and_reconciles_previously_selected_tables(kind: str) -> None:
    source = FakeSource(kind=kind)
    selected = _select_tables(source, DEFAULT_SKIP_TABLES)
    assert selected == {"gold_workspaces", "gold_item_executions", "gold_execution_coverage"}
    for operation, path in source.calls:
        if operation == "select":
            assert path[-1] in APPROVED_GOLD_TABLES
            assert path[0] != "raw"
    assert not next(table for table in source.tables if table["name"] == "owner_access")["is_selected"]
    source.calls.clear()
    assert _select_tables(source, DEFAULT_SKIP_TABLES) == selected


def test_selection_failures_are_not_silently_accepted() -> None:
    source = FakeSource()
    source.ignore_unselect = True
    with pytest.raises(ValueError, match="verification failed"):
        _select_tables(source, DEFAULT_SKIP_TABLES)
    source.ignore_unselect = False
    source.fail_select = True
    with pytest.raises(RuntimeError, match="SDK selection failed"):
        _select_tables(source, DEFAULT_SKIP_TABLES)
    source.elements = []
    with pytest.raises(ValueError, match="No approved Gold"):
        _select_tables(source, DEFAULT_SKIP_TABLES)
    source.elements = None
    with pytest.raises(ValueError, match="must be a list"):
        _select_tables(source, DEFAULT_SKIP_TABLES)


@pytest.mark.parametrize("mutation", ["unknown_type", "missing_selection", "duplicate_path"])
def test_unrecognized_schema_cannot_fall_back_to_select_all(mutation: str) -> None:
    source = FakeSource()
    if mutation == "unknown_type":
        source.tables[0]["type"] = "unexpected"
    elif mutation == "missing_selection":
        del source.tables[0]["is_selected"]
    else:
        source.tables.append(copy.deepcopy(source.tables[0]))
    with pytest.raises(ValueError):
        _select_tables(source, DEFAULT_SKIP_TABLES)
    assert not source.calls


def test_explicitly_enabling_optional_gold_does_not_enable_access_or_raw_tables() -> None:
    source = FakeSource()
    assert "gold_agent_eval" in _select_tables(source, set())
    assert all(path[-1] in APPROVED_GOLD_TABLES for operation, path in source.calls if operation == "select")


class FakeAgent:
    def __init__(self, sources: list[FakeSource]) -> None:
        self.sources = sources
        self.added: list[tuple[str, str]] = []
        self.published = False

    def update_configuration(self, **kwargs: object) -> None:
        pass

    def add_datasource(self, name: str, *, type: str) -> None:
        self.added.append((name, type))

    def get_datasources(self) -> list[FakeSource]:
        return self.sources

    def publish(self, **kwargs: object) -> None:
        self.published = True


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, agent: FakeAgent) -> None:
    client = ModuleType("fabric.dataagent.client")
    client.create_data_agent = lambda name: agent
    monkeypatch.setitem(sys.modules, "fabric", ModuleType("fabric"))
    monkeypatch.setitem(sys.modules, "fabric.dataagent", ModuleType("fabric.dataagent"))
    monkeypatch.setitem(sys.modules, "fabric.dataagent.client", client)


@pytest.mark.parametrize("grouped", [False, True])
def test_deploy_keeps_two_central_sources_and_only_grounded_lakehouse_fewshots(monkeypatch, grouped) -> None:
    model = FakeSource("Governance", "semantic_model")
    lakehouse = FakeSource()
    if grouped:
        _grouped_schema(lakehouse)
    agent = FakeAgent([model, lakehouse])
    _install_fake_sdk(monkeypatch, agent)
    result = deploy_agent(agent_name="Central", model_name="Governance", lakehouse_name="Gold")
    assert result is agent and agent.published
    assert agent.added == [("Governance", "semanticmodel"), ("Gold", "lakehouse")]
    assert not model.fewshots
    assert lakehouse.fewshots
    for query in lakehouse.fewshots.values():
        assert set(re.findall(r"\bgold_[a-z_]+\b", query)) <= {
            "gold_workspaces", "gold_item_executions", "gold_execution_coverage",
        }


@pytest.mark.parametrize("stage", ["update_configuration", "publish"])
def test_deploy_propagates_required_sdk_failures(monkeypatch, stage: str) -> None:
    agent = FakeAgent([FakeSource("Governance", "semantic_model"), FakeSource()])
    error = RuntimeError("Required SDK operation failed")

    def fail(**kwargs: object) -> None:
        raise error

    monkeypatch.setattr(agent, stage, fail)
    _install_fake_sdk(monkeypatch, agent)
    with pytest.raises(RuntimeError) as raised:
        deploy_agent(agent_name="Central", model_name="Governance", lakehouse_name="Gold")
    assert raised.value is error
    assert not agent.published
    if stage == "update_configuration":
        assert agent.added == []


def test_explicit_draft_deployment_does_not_call_publish(monkeypatch) -> None:
    agent = FakeAgent([FakeSource("Governance", "semantic_model"), FakeSource()])

    def fail(**kwargs: object) -> None:
        raise AssertionError("Draft deployment must not publish")

    monkeypatch.setattr(agent, "publish", fail)
    _install_fake_sdk(monkeypatch, agent)
    assert deploy_agent(agent_name="Central", model_name="Governance", lakehouse_name="Gold",
                        publish=False) is agent
    assert not agent.published


@pytest.mark.parametrize("failure", ["unexpected_source", "unselect", "select", "wrong_name"])
def test_deploy_does_not_publish_unverified_sources(monkeypatch, failure: str) -> None:
    lakehouse = FakeSource()
    agent = FakeAgent([FakeSource("Governance", "semantic_model"), lakehouse])
    if failure == "unexpected_source":
        agent.sources.append(FakeSource("Other"))
    elif failure == "wrong_name":
        lakehouse.name = "Workspace Owner"
    elif failure == "unselect":
        lakehouse.ignore_unselect = True
    else:
        lakehouse.fail_select = True
    _install_fake_sdk(monkeypatch, agent)
    with pytest.raises((ValueError, RuntimeError)):
        deploy_agent(agent_name="Central", model_name="Governance", lakehouse_name="Gold")
    assert not agent.published


@pytest.mark.parametrize("status", [
    "empty", "collected", "partial", "forbidden", "not_found", "error", "not_collected", "invalid_data",
])
def test_execution_collection_sql_preserves_zero_only_for_success(status: str) -> None:
    db = _database()
    try:
        common = dict(run_id="new", run_timestamp="2026-09-21T00:00:00Z", workspace_id="a")
        _insert(db, "gold_workspaces", **common)
        _insert(db, "gold_execution_coverage", **common, item_id="job", item_type="DataPipeline",
                collection_status=status, observed_execution_count=0, notice="Retained snapshot only.")
        for kind in ("SemanticModel", "DataPipeline", "Notebook"):
            _insert(db, "gold_execution_coverage", **common, item_id="", item_type=kind,
                    collection_status="not_collected", observed_execution_count=0,
                    notice="Inventory unavailable.")
        cursor = db.execute(_query_for("Which items had successful empty"))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        item = next(row for row in rows if row["item_id"])
        assert item["successfully_collected_count"] == (0 if status in ("empty", "collected") else None)
        assert len(rows) == 4
        assert all(row["successfully_collected_count"] is None for row in rows if not row["item_id"])
        assert all(row["notice"] for row in rows)
        coverage = db.execute(_query_for("What native execution history")).fetchall()
        snapshots = db.execute(_query_for("Show collection windows")).fetchall()
        assert len(coverage) == len(snapshots) == 4
    finally:
        db.close()


@pytest.mark.parametrize("state", ["absent", "inventory_gap", "inspected_empty", "partial_empty"])
def test_dataflow_incomplete_count_sql_does_not_manufacture_zero_when_unavailable(state: str) -> None:
    db = _database()
    try:
        common = dict(run_id="new", run_timestamp="2026-09-21T00:00:00Z", workspace_id="a")
        _insert(db, "gold_workspaces", **common)
        if state != "absent":
            _insert(
                db, "gold_dataflows", **common, dataflow_id="" if state == "inventory_gap" else "flow",
                definition_status={"inventory_gap": "inventory_unavailable",
                                   "inspected_empty": "inspected", "partial_empty": "partial"}[state],
                query_count=0, flagged_query_count=0,
            )
        value = db.execute(_query_for("Count incomplete static inspections")).fetchone()[0]
        assert value == {"absent": None, "inventory_gap": None, "inspected_empty": 0, "partial_empty": 1}[state]
    finally:
        db.close()


def test_dax_population_sql_keeps_counts_separate_without_detail_fanout() -> None:
    db = _database()
    try:
        for run, workspace, measures, objects in (
            ("old", "a", 99, 99), ("old", "b", 5, 0), ("new", "a", 2, 3),
        ):
            common = dict(run_id=run, run_timestamp=f"2026-09-{'14' if run == 'old' else '21'}T00:00:00Z",
                          workspace_id=workspace, workspace_name="Same")
            _insert(db, "gold_workspaces", **common)
            _insert(db, "gold_dax_models", **common, model_id="m", definition_status="available",
                    measure_count=measures, flagged_measure_count=1)
            _insert(db, "gold_dax_object_coverage", **common, model_id="m", definition_status="complete",
                    object_count=objects, flagged_object_count=objects)
            for i in range(2):
                _insert(db, "gold_dax_measures", **common, model_id="m", measure_name=f"M{i}")
            for kind in ("calculated_column", "calculated_table", "calculation_item"):
                _insert(db, "gold_dax_objects", **common, model_id="m", object_name=kind, object_type=kind)
        cursor = db.execute(_query_for("Show separate observed measure"))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert {(row["workspace_id"], row["object_family"]): row["observed_object_count"] for row in rows} == {
            ("a", "measure"): 2, ("a", "non_measure"): 3, ("b", "measure"): 5, ("b", "non_measure"): 0,
        }
        assert len(rows) == 4
        assert all(row["definition_status"] == ("available" if row["object_family"] == "measure" else "complete")
                   for row in rows)
    finally:
        db.close()


def test_arch016_concrete_evidence_query_preserves_workspace_review_and_defect_codes() -> None:
    db = _database()
    try:
        for run, workspace in (("old", "a"), ("old", "b"), ("new", "a")):
            _insert(db, "gold_workspaces", run_id=run, workspace_id=workspace,
                    run_timestamp=f"2026-09-{'14' if run == 'old' else '21'}T00:00:00Z")
        for run, code in (("old", "missing_dependency"), ("new", "dependency_cycle")):
            evidence = {"coverage_status": "partial", "items": [
                {"workspace_id": "a", "item_id": "p", "signal_codes": [code],
                 "coverage_status": "complete", "reason_codes": []},
                {"workspace_id": "b", "item_id": "p", "signal_codes": [],
                 "coverage_status": "missing_evidence", "reason_codes": ["definition_missing"]},
            ]}
            _insert(db, "gold_findings", run_id=run, rule_id="ARCH-016", status="fail", severity="medium",
                    evidence_json=json.dumps(evidence))
        cursor = db.execute(_query_for("Show concrete ARCH-016"))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert len(rows) == 2
        codes = {}
        for row in rows:
            items = [item for item in json.loads(row["evidence_json"])["items"]
                     if item["workspace_id"] == row["workspace_id"]]
            codes[row["workspace_id"]] = items[0]["signal_codes"]
            assert row["severity"] == "medium"
        assert codes == {"a": ["dependency_cycle"], "b": []}
        _insert(db, "gold_run_summary", run_id="new", is_latest=1)
        run_rows = db.execute(_query_for("What does ARCH-016")).fetchall()
        assert len(run_rows) == 1 and run_rows[0][1:3] == ("fail", "medium")
    finally:
        db.close()


def test_deploy_selects_all_native_tables_and_attaches_every_supported_feature_example(monkeypatch) -> None:
    sources = [FakeSource("Governance", "semantic_model"), FakeSource()]
    for source in sources:
        known = {table["name"] for table in source.tables}
        source.tables.extend(
            dict(name=table.name, type=f"{source.kind}.table", is_selected=False, children=[])
            for table in GOLD_TABLES if table.name not in known
        )
    agent = FakeAgent(sources)
    _install_fake_sdk(monkeypatch, agent)
    deploy_agent(agent_name="Central", model_name="Governance", lakehouse_name="Gold",
                 version="2026.09.2")
    assert agent.published
    for source in sources:
        selected = {table["name"] for table in source.tables if table["is_selected"]}
        assert selected == set(agent_tables())
        assert NATIVE_TABLES <= selected
    for example in DEFAULT_LAKEHOUSE_FEWSHOTS:
        referenced = set(re.findall(r"\bgold_[a-z_]+\b", example["query"]))
        assert (example["question"] in sources[1].fewshots) == (referenced <= set(agent_tables()))
    assert not sources[0].fewshots


@pytest.mark.parametrize(("table", "question", "score", "identity", "extra"), [
    ("gold_item_executions", "Which observed native refreshes", "duration_ms", "execution_key", {}),
    ("gold_dax_objects", "Rank calculated columns", "risk_score", "object_name",
     {"object_type": "calculation_item"}),
    ("gold_dax_measures", "Show the 25 highest static-risk DAX measures", "risk_score", "measure_name", {}),
])
def test_ranked_native_queries_limit_to_25_current_objects_not_stale_snapshots(
    table: str, question: str, score: str, identity: str, extra: dict,
) -> None:
    db = _database()
    try:
        old = dict(run_id="old", run_timestamp="2026-09-14T00:00:00Z")
        new = dict(run_id="new", run_timestamp="2026-09-21T00:00:00Z")
        for review, workspace in ((old, "a"), (old, "b"), (new, "a")):
            _insert(db, "gold_workspaces", **review, workspace_id=workspace, workspace_name="Same")
        _insert(db, table, **old, workspace_id="a", **{identity: "stale", score: 9999, **extra})
        _insert(db, table, **old, workspace_id="b", **{identity: "retained", score: 1000, **extra})
        for i in range(30):
            _insert(db, table, **new, workspace_id="a", **{identity: f"current-{i}", score: i, **extra})
        cursor = db.execute(_sqlite(_query_for(question)))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert len(rows) == 25
        assert [row[identity] for row in rows] == ["retained", *(f"current-{i}" for i in range(29, 5, -1))]
        assert {row["workspace_id"] for row in rows} == {"a", "b"}
    finally:
        db.close()


def test_dataflow_signal_query_returns_selected_codes_not_runtime_failures() -> None:
    db = _database()
    try:
        review = dict(run_id="new", run_timestamp="2026-09-21T00:00:00Z", workspace_id="a")
        _insert(db, "gold_workspaces", **review)
        _insert(db, "gold_dataflows", **review, review_item_key="k", dataflow_id="flow",
                definition_status="inspected", query_count=2, flagged_query_count=1)
        for name, count in (("flagged", 3), ("unflagged", 0)):
            _insert(db, "gold_dataflow_queries", **review, review_item_key="k", dataflow_id="flow",
                    query_name=name, signal_count=count,
                    signal_codes="DFLOW_TABLE_BUFFER;DFLOW_STOP_FOLDING;DFLOW_NATIVE_QUERY" if count else "",
                    notice="Lexical checks only.", recommendation="Validate with measurements.")
        cursor = db.execute(_query_for("What static Power Query M signals"))
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor]
        assert len(rows) == 1
        assert rows[0]["signal_codes"].split(";") == [
            "DFLOW_TABLE_BUFFER", "DFLOW_STOP_FOLDING", "DFLOW_NATIVE_QUERY",
        ]
        assert rows[0]["query_name"] == "flagged"
        assert rows[0]["definition_status"] == "inspected"
        assert rows[0]["notice"] == "Lexical checks only."
        assert "duration_ms" not in rows[0]
    finally:
        db.close()

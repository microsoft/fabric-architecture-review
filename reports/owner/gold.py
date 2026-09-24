# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fail-closed, offline projection of one governance run into owner Gold.

Only explicit GUIDs establish attribution. Names, links, finding targets, and
global findings are never used to discover a workspace. The input is trusted
technical collection output, not an authentication boundary: access control
must still be enforced by the owner semantic model and expiring owner_access.

Notebook/workspace IDs flow from collection through ID-keyed analyzer hits and
Gold smell rows. VertiPaq table rows carry a workspace ID only after an exact,
unambiguous collector workspace/model-ID match. Legacy name-only notebook
evidence and name-fallback VertiPaq rows are omitted. Both technical categories
need matching same-run item inventory; names and links never supply provenance.
gold_semantic_models statistics are deliberately not copied. Missing technical
evidence never implies pass. Explicit workspace-scoped native inventory gaps
remain coverage records, never invented item or execution evidence.

All output text is static or an approved same-workspace metadata label. No
expressions, notebook source, arbitrary JSON, signal messages, URLs, global
scores, capacity totals, tenant rules, or raw finding text are carried over.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID

from reports.owner.schema import OWNER_TABLES_BY_NAME

PARTIAL_NOTICE = (
    "Incomplete owner assessment: only explicitly workspace-scoped technical facts "
    "are included. This is not a full FAR score. Tenant and capacity rules, mixed "
    "findings, name-attributed notebook signals and unscoped model statistics are "
    "excluded. Zero owner failures does not establish compliance or complete coverage."
)
STATIC_NOTICE = (
    "Static metadata heuristic only; review each signal and validate runtime "
    "behavior separately. Source text and potential secret values are withheld."
)
MISSING_NOTICE = (
    "No safely attributable technical evidence is available for this workspace. "
    "Collect ID-keyed definitions, native execution history, model statistics or notebook signals."
)

_DAX_POINTS = {
    "iterator": 8, "nested_iterators": 25, "crossjoin": 35, "generate": 30,
    "addcolumns": 15, "whole_table_filter": 20, "broad_context_removal": 12,
    "earlier": 20, "very_long_expression": 20, "long_expression": 10,
}
_DAX_GUIDANCE = {
    "iterator": "Review the rows evaluated by the iterator; reduce them only where the expression semantics allow.",
    "nested_iterators": "Check repeated work inside nested iterators; consider variables for expressions that are invariant in the inner context.",
    "crossjoin": "Check the row counts on both sides of CROSSJOIN; filter the inputs where this preserves the intended result.",
    "generate": "Check how many inner-table rows GENERATE produces for each outer row before changing its filters.",
    "addcolumns": "Review calculated columns added to intermediate tables; avoid computing columns that the result does not use.",
    "whole_table_filter": "Review whole-table FILTER operations; consider narrower column filters only if filter semantics remain equivalent.",
    "broad_context_removal": "Confirm that removing broad filter context is intentional; narrow the scope only when totals remain correct.",
    "earlier": "Review EARLIER row-context dependencies; consider an equivalent variable-based expression and verify the results.",
    "very_long_expression": "Break down the long expression and inspect repeated calculations; use variables where their evaluation context is equivalent.",
    "long_expression": "Inspect repeated calculations and simplify the expression without changing its filter or row context.",
}
_NOTEBOOK_RULES = {
    "NBCODE-001": ("security", "high", "Potential hard-coded credential pattern",
                   "Review the indicated notebook cells, remove embedded credentials, "
                   "and rotate any exposed credentials using the approved secret store."),
    "NBCODE-002": ("operational_excellence", "medium", "Inline package installation pattern",
                   "Manage pinned notebook dependencies in a Fabric environment."),
    "NBCODE-003": ("performance", "high", "Unbounded driver collection pattern",
                   "Bound or aggregate results before collecting them to the driver."),
    "NBCODE-004": ("architecture", "medium", "Platform-specific notebook API pattern",
                   "Review the flagged APIs and migrate to supported Fabric equivalents."),
    "NBCODE-005": ("architecture", "medium", "Hard-coded storage reference pattern",
                   "Parameterize storage references for environment promotion."),
    "NBCODE-006": ("performance", "medium", "Non-Delta write pattern",
                   "Review whether Delta format is appropriate for the target workload."),
}
_MODEL_METRICS = (
    "row_count", "total_size", "data_size", "dictionary_size",
    "hierarchy_size", "column_count",
)
RUNTIME_NOTICE = (
    "Observed native execution history only; bounded API retention and collection "
    "gaps apply. Duration is milliseconds, not DAX query latency or CU consumption."
)
COVERAGE_NOTICE = (
    "Collection coverage only, not an assessment pass. Missing, empty, partial "
    "or unknown evidence must be investigated before drawing conclusions."
)
_DEFINITION_STATES = {
    "available", "partial", "missing", "error", "unsupported", "skipped", "empty",
    "inspected", "complete", "parse_error", "unavailable", "forbidden", "inventory_unavailable",
}
_COLLECTION_STATES = {
    "collected", "empty", "partial", "forbidden", "not_found", "error",
    "not_collected", "invalid_data",
}
_EXECUTION_STATES = {
    "completed": "Completed", "succeeded": "Completed", "success": "Completed",
    "failed": "Failed", "cancelled": "Cancelled", "canceled": "Cancelled",
    "inprogress": "InProgress", "running": "InProgress", "notstarted": "NotStarted",
    "queued": "NotStarted", "unknown": "Unknown", "disabled": "Disabled",
    "in_progress": "InProgress", "not_started": "NotStarted", "deduped": "Deduped",
}
_EXECUTION_KINDS = {
    "SemanticModel": ("Refresh", "powerbi_refresh_history"),
    "DataPipeline": ("Pipeline run", "fabric_job_instances"),
    "Notebook": ("Notebook run", "fabric_job_instances"),
}
_DATAFLOW_GUIDANCE = {
    "DFLOW_NATIVE_QUERY": "Review the native query boundary and validate folding with supported source diagnostics.",
    "DFLOW_TABLE_BUFFER": "Review buffering and memory use; confirm whether buffering prevents useful query folding.",
    "DFLOW_STOP_FOLDING": "Review the explicit folding boundary and confirm its intended placement.",
}
_PIPELINE_GUIDANCE = {
    "duplicate_activity": "Give activities unique names within each dependency scope.",
    "missing_dependency": "Correct dependencies that reference activities absent from the same scope.",
    "self_dependency": "Remove self-dependencies and verify the intended activity order.",
    "dependency_cycle": "Break dependency cycles and validate the intended activity order.",
}


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise ValueError("Owner execution timestamps require timezone-aware dateTime values.") from None


def _codes(row: dict, allowed: dict) -> list[str]:
    value = row.get("signal_codes") or ""
    if not isinstance(value, str):
        raise ValueError("Owner signal_codes must be comma-separated codes.")
    return sorted({code.strip() for code in re.split("[,;]", value)} & allowed.keys())


def _state(value: Any, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _guid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Owner attribution requires a nonempty GUID.")
    try:
        result = UUID(value.strip())
    except ValueError:
        raise ValueError("Owner attribution contains an invalid GUID.") from None
    if not result.int:
        raise ValueError("Owner attribution cannot use the empty GUID.")
    return str(result)


def _label(value: Any, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if (not isinstance(value, str) or len(value) > 512
            or any(ord(char) < 32 for char in value)
            or (required and not value.strip())):
        raise ValueError("Owner metadata requires a valid single-line label.")
    return value


def _run(row: dict) -> tuple[str, str]:
    run_id = row.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", run_id):
        raise ValueError("Owner metadata requires a valid run_id.")
    value = row.get("run_timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        timestamp = stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise ValueError("Owner metadata requires a timezone-aware run_timestamp.") from None
    return run_id, timestamp


def _count(row: dict, field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("Owner counters must be nonnegative int64 values.")
    return value


def _key(*parts: Any) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def build_owner_gold(governance_tables: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Return schema-aligned review tables, never owner_access; do not mutate input.

    Requires one run, established by gold_workspaces or gold_run_summary.
    Identical approved duplicates collapse; ambiguous identities, conflicting
    duplicate facts, malformed scoped values, and cross-run rows raise ValueError.
    Missing/unknown workspace IDs are excluded without inferring attribution.
    """
    if not isinstance(governance_tables, dict):
        raise ValueError("Owner projection requires a table dictionary.")
    output = {name: [] for name in OWNER_TABLES_BY_NAME if name != "owner_access"}

    def rows(name: str) -> list[dict]:
        result = governance_tables.get(name, [])
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ValueError(f"{name} must be a list of row dictionaries.")
        return result

    workspace_rows = rows("gold_workspaces")
    runs = {_run(row) for row in workspace_rows + rows("gold_run_summary")}
    if len(runs) > 1:
        raise ValueError("Owner projection requires exactly one consistent review run.")
    if not runs:
        return output
    run_id, timestamp = runs.pop()
    workspace_names: dict[str, str] = {}
    for row in workspace_rows:
        wid = _guid(row.get("workspace_id"))
        name = _label(row.get("workspace_name"), required=True)
        if wid in workspace_names and workspace_names[wid] != name:
            raise ValueError("Ambiguous workspace identity in owner inventory.")
        workspace_names[wid] = name

    def scope(row: dict) -> str | None:
        value = row.get("workspace_id")
        if value is None or value == "":
            return None
        wid = _guid(value)
        if wid not in workspace_names:
            return None
        if _run(row) != (run_id, timestamp):
            raise ValueError("Scoped owner evidence belongs to a different review run.")
        return wid

    def meta(wid: str) -> dict:
        return {"review_key": f"{run_id}:{wid}", "workspace_id": wid,
                "run_id": run_id, "run_timestamp": timestamp}

    identities: dict[str, tuple[str, str, str]] = {}

    def register(row: dict, wid: str, id_field: str, name_field: str, kind: str) -> str:
        iid = _guid(row.get(id_field))
        name = _label(row.get(name_field)) or iid
        identity = (wid, kind, name)
        old = identities.get(iid)
        if old and old != identity:
            raise ValueError("Ambiguous item identity in owner inventory.")
        identities[iid] = identity
        return iid

    models: dict[tuple[str, str], dict] = {}
    for row in rows("gold_semantic_models"):
        wid = scope(row)
        if wid is None:
            continue
        iid = register(row, wid, "model_id", "model_name", "SemanticModel")
        storage = row.get("storage_mode")
        storage = storage if storage in ("Import", "Abf", "DirectQuery", "DirectLake", "Composite") else "Unknown"
        if storage == "Abf":
            storage = "Import"
        approved = {"storage_mode": storage}
        old = models.get((wid, iid))
        if old is not None and old != approved:
            raise ValueError("Conflicting semantic-model metadata.")
        models[(wid, iid)] = approved
    for row in rows("gold_notebooks"):
        wid = scope(row)
        if wid is not None:
            register(row, wid, "notebook_id", "notebook_name", "Notebook")
    for table, id_field, name_field, kind in (
        ("gold_pipelines", "pipeline_id", "pipeline_name", "DataPipeline"),
        ("gold_dataflows", "dataflow_id", "dataflow_name", "Dataflow"),
    ):
        for row in rows(table):
            wid = scope(row)
            if wid is not None:
                if table == "gold_dataflows" and not row.get(id_field) and row.get("definition_status") == "inventory_unavailable":
                    continue
                register(row, wid, id_field, name_field, kind)
    for row in rows("gold_dax_models"):
        wid = scope(row)
        if wid is None:
            continue
        iid = _guid(row.get("model_id"))
        if iid not in identities:
            register(row, wid, "model_id", "model_name", "SemanticModel")
        elif identities[iid][:2] != (wid, "SemanticModel"):
            raise ValueError("DAX model identity conflicts with owner inventory.")

    def item(row: dict, wid: str, id_field: str, kind: str) -> tuple[str, str] | None:
        if not row.get(id_field):
            return None
        iid = _guid(row[id_field])
        identity = identities.get(iid)
        if identity is None:
            return None
        if identity[:2] != (wid, kind):
            raise ValueError("Technical evidence conflicts with its item's workspace.")
        return iid, identity[2]

    emitted: dict[tuple[str, str], dict] = {}
    technical = {wid: 0 for wid in workspace_names}

    def emit(table: str, row: dict, key: str, *, is_technical: bool = False) -> None:
        record = {column.name: row.get(column.name) for column in OWNER_TABLES_BY_NAME[table].columns}
        identity = (table, key)
        if identity in emitted:
            if emitted[identity] != record:
                raise ValueError("Conflicting duplicate owner technical facts.")
            return
        emitted[identity] = record
        output[table].append(record)
        if is_technical:
            technical[row["workspace_id"]] += 1

    def detail(wid: str, iid: str, name: str, kind: str, category: str,
               discriminator: tuple, *, is_technical: bool = True, **fields: Any) -> None:
        key = _key(run_id, wid, iid, category, *discriminator)
        emit("owner_details", {
            **meta(wid), "detail_key": key, "detail_type": category,
            "item_id": iid, "item_name": name, "item_type": kind,
            "notice": STATIC_NOTICE, **fields,
        }, key, is_technical=is_technical)

    def finding(wid: str, iid: str, name: str, kind: str, rule: str,
                discriminator: tuple, **fields: Any) -> None:
        key = _key(run_id, wid, iid, rule, *discriminator)
        emit("owner_findings", {
            **meta(wid), "finding_key": key, "rule_id": rule,
            "item_id": iid, "item_name": name, "item_type": kind,
            "status": "fail", "is_fail": 1, "notice": STATIC_NOTICE, **fields,
        }, key)

    for (wid, iid), model in sorted(models.items()):
        detail(wid, iid, identities[iid][2], "SemanticModel", "model_inventory", (),
               is_technical=False, detail="Storage mode: " + model["storage_mode"])

    for row in rows("gold_dax_models"):
        wid = scope(row)
        if wid is None:
            continue
        resolved = item(row, wid, "model_id", "SemanticModel")
        if resolved is None:
            continue
        iid, name = resolved
        state = row.get("definition_status")
        state = state if state in ("available", "missing", "error") else "unknown"
        detail(wid, iid, name, "SemanticModel", "dax_coverage", (),
               is_technical=False, detail="Definition collection status: " + state)

    for row in rows("gold_dax_measures"):
        wid = scope(row)
        if wid is None:
            continue
        resolved = item(row, wid, "model_id", "SemanticModel")
        if resolved is None:
            continue
        iid, name = resolved
        table_name = _label(row.get("table_name"))
        measure = _label(row.get("measure_name"), required=True)
        raw_codes = row.get("signal_codes") or ""
        if not isinstance(raw_codes, str):
            raise ValueError("DAX signal_codes must be comma-separated codes.")
        codes = sorted({code.strip() for code in raw_codes.split(",")} & _DAX_POINTS.keys())
        signal_codes = ", ".join(codes)
        points = min(100, sum(_DAX_POINTS[code] for code in codes))
        detail(wid, iid, name, "SemanticModel", "dax_measure", (table_name, measure),
               table_name=table_name, measure_name=measure, signal_codes=signal_codes,
               metric_name="static_pattern_points", metric_value=points,
               detail=" ".join(_DAX_GUIDANCE[code] for code in codes)
               or "No approved static pattern was detected; this does not establish good runtime performance.")
        if points >= 20:
            finding(wid, iid, name, "SemanticModel", "DAX-001", (table_name, measure),
                    dimension="performance", severity="high" if points >= 40 else "medium",
                    title=f"DAX pattern review: {table_name} / {measure}",
                    recommendation=" ".join(_DAX_GUIDANCE[code] for code in codes)
                    + " Capture a representative query baseline with approved runtime tooling; "
                    "verify result correctness and timings after any change. Static pattern points are not measured duration or CU.",
                    signal_codes=signal_codes, affected_count=1)

    for row in rows("gold_model_tables"):
        wid = scope(row)
        if wid is None:
            continue
        resolved = item(row, wid, "model_id", "SemanticModel")
        if resolved is None:
            continue
        iid, name = resolved
        table_name = _label(row.get("table_name"), required=True)
        for metric in _MODEL_METRICS:
            value = _count(row, metric)
            if value is not None:
                detail(wid, iid, name, "SemanticModel", "model_table_stat",
                       (table_name, metric), table_name=table_name,
                       metric_name=metric, metric_value=value,
                       detail="Explicitly ID-scoped model table statistic.")

    for row in rows("gold_notebook_smells"):
        wid = scope(row)
        if wid is None:
            continue
        resolved = item(row, wid, "notebook_id", "Notebook")
        if resolved is None:
            continue
        rule = row.get("rule_id")
        if rule not in _NOTEBOOK_RULES:
            continue
        iid, name = resolved
        cells = row.get("cells")
        if not isinstance(cells, str) or not re.fullmatch(r"\s*\d+(?:\s*,\s*\d+)*\s*", cells):
            raise ValueError("Notebook signals require numeric cell indexes, never source text.")
        indexes = sorted({int(cell.strip()) for cell in cells.split(",")})
        if any(index > 2**31 - 1 for index in indexes):
            raise ValueError("Notebook cell index is out of range.")
        dimension, severity, title, recommendation = _NOTEBOOK_RULES[rule]
        detail(wid, iid, name, "Notebook", "notebook_signal", (rule,),
               signal_codes=rule, metric_name="matched_cell_count", metric_value=len(indexes),
               detail="Matched cell indexes: " + ", ".join(map(str, indexes)))
        finding(wid, iid, name, "Notebook", rule, (), dimension=dimension,
                severity=severity, title=title, recommendation=recommendation,
                signal_codes=rule, affected_count=len(indexes))

    covered: set[tuple[str, str, str]] = set()

    def coverage(wid: str, iid: str, name: str, kind: str, evidence: str,
                 state: str, **fields: Any) -> None:
        key = _key(run_id, wid, iid, evidence)
        covered.add((wid, iid, evidence))
        emit("owner_coverage", {
            **meta(wid), "coverage_key": key, "item_id": iid, "item_name": name,
            "item_type": kind, "evidence_type": evidence,
            "collection_status": state, "notice": COVERAGE_NOTICE, **fields,
        }, key)

    # Only ARCH-016 carries ID-keyed per-item structural evidence. Legacy
    # name-derived finding targets and every other mixed finding stay excluded.
    central_findings = governance_tables.get("gold_findings", [])
    for source in central_findings if isinstance(central_findings, list) else []:
        if not isinstance(source, dict) or source.get("rule_id") != "ARCH-016":
            continue
        if _run(source) != (run_id, timestamp):
            raise ValueError("Scoped architecture evidence belongs to a different review run.")
        try:
            evidence = json.loads(source.get("evidence_json") or "{}")
        except (TypeError, ValueError):
            raise ValueError("Malformed ID-keyed architecture evidence.") from None
        if not isinstance(evidence, dict) or not isinstance(evidence.get("items", []), list):
            raise ValueError("Architecture evidence requires per-item records.")
        for entry in evidence.get("items", []):
            if not isinstance(entry, dict):
                raise ValueError("Architecture evidence requires per-item records.")
            if entry.get("item_type") != "DataPipeline":
                continue
            row = {**entry, "run_id": run_id, "run_timestamp": timestamp}
            wid = scope(row)
            if wid is None:
                continue
            reasons = row.get("reason_codes", [])
            if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
                raise ValueError("Architecture reasons must be a list of codes.")
            if "identity_unresolved" in reasons:
                continue
            resolved = item(row, wid, "item_id", "DataPipeline")
            if resolved is None:
                continue
            iid, name = resolved
            raw_codes = row.get("signal_codes", [])
            if not isinstance(raw_codes, list) or any(not isinstance(code, str) for code in raw_codes):
                raise ValueError("Architecture signals must be a list of codes.")
            codes = sorted(set(raw_codes) & _PIPELINE_GUIDANCE.keys())
            state = _state(row.get("coverage_status"), {"complete", "partial", "unknown", "missing_evidence"})
            coverage(wid, iid, name, "DataPipeline", "Pipeline structure",
                     "available" if state == "complete" else "missing" if state == "missing_evidence" else state,
                     observed_count=_count(row, "activity_count"),
                     history_scope="Static same-scope activity graph", source="Static definition")
            if codes:
                affected = _count(row, "affected_count")
                if affected is None or affected == 0:
                    raise ValueError("Architecture defects require a positive affected_count.")
                guidance = " ".join(_PIPELINE_GUIDANCE[code] for code in codes)
                detail(wid, iid, name, "DataPipeline", "pipeline_structure", (),
                       signal_codes=", ".join(codes), metric_name="affected_activity_count",
                       metric_value=affected, detail=guidance)
                finding(wid, iid, name, "DataPipeline", "ARCH-016", (),
                        dimension="architecture", severity="medium",
                        title="Static pipeline dependency structure",
                        recommendation=guidance + " Validate the definition and rerun; static graph checks do not prove execution reliability.",
                        signal_codes=", ".join(codes), affected_count=affected)

    for table, id_field, kind, evidence, count, flagged in (
        ("gold_dax_models", "model_id", "SemanticModel", "DAX measures", "measure_count", "flagged_measure_count"),
        ("gold_dax_object_coverage", "model_id", "SemanticModel", "DAX objects", "object_count", "flagged_object_count"),
        ("gold_dataflows", "dataflow_id", "Dataflow", "Dataflow queries", "query_count", "flagged_query_count"),
    ):
        for row in rows(table):
            wid = scope(row)
            if wid is None:
                continue
            if table == "gold_dataflows" and not row.get(id_field) and row.get("definition_status") == "inventory_unavailable":
                coverage(wid, wid, workspace_names[wid], "Workspace", "Dataflow inventory", "missing")
                continue
            resolved = item(row, wid, id_field, kind)
            if resolved is None:
                continue
            iid, name = resolved
            observed, flags = _count(row, count), _count(row, flagged)
            if observed is not None and flags is not None and flags > observed:
                raise ValueError("Flagged owner object count exceeds observed count.")
            coverage(wid, iid, name, kind, evidence,
                     _state(row.get("definition_status"), _DEFINITION_STATES),
                     observed_count=observed, flagged_count=flags,
                     history_scope="Current collected definition", source="Static definition")

    for row in rows("gold_dax_objects"):
        wid = scope(row)
        if wid is None:
            continue
        resolved = item(row, wid, "model_id", "SemanticModel")
        if resolved is None:
            continue
        object_type = row.get("object_type")
        if object_type not in ("calculated_column", "calculated_table", "calculation_item"):
            raise ValueError("Owner DAX objects must be explicitly typed non-measures.")
        iid, name = resolved
        table_name = _label(row.get("table_name"))
        object_name = _label(row.get("object_name"), required=True)
        codes = _codes(row, _DAX_POINTS)
        points = min(100, sum(_DAX_POINTS[code] for code in codes))
        # Measure guidance is not reused: column/table refresh and calculation-item
        # query contexts require different validation.
        guidance = (
            "Review the indicated static DAX patterns for this "
            + object_type.replace("_", " ")
            + ". Verify row/filter context and result correctness. "
            + ("Baseline representative queries with the calculation item applied."
               if object_type == "calculation_item"
               else "Baseline refresh processing, model size and downstream queries.")
            + " Static points are not measured duration or CU."
        )
        detail(wid, iid, name, "SemanticModel", "dax_" + object_type,
               (table_name, object_name), table_name=table_name,
               signal_codes=", ".join(codes), metric_name="static_pattern_points",
               metric_value=points, detail=f"{object_type}: {object_name}. {guidance}")
        if points >= 20:
            finding(wid, iid, name, "SemanticModel", "DAX-OBJECT-001",
                    (object_type, table_name, object_name), dimension="performance",
                    severity="high" if points >= 40 else "medium",
                    title=f"DAX {object_type.replace('_', ' ')}: {table_name} / {object_name}",
                    recommendation=guidance, signal_codes=", ".join(codes), affected_count=1)

    for row in rows("gold_dataflow_queries"):
        wid = scope(row)
        if wid is None:
            continue
        resolved = item(row, wid, "dataflow_id", "Dataflow")
        if resolved is None:
            continue
        iid, name = resolved
        query = _label(row.get("query_name"), required=True)
        codes = _codes(row, _DATAFLOW_GUIDANCE)
        guidance = " ".join(_DATAFLOW_GUIDANCE[code] for code in codes)
        detail(wid, iid, name, "Dataflow", "dataflow_query", (query,),
               signal_codes=", ".join(codes), metric_name="approved_signal_count",
               metric_value=len(codes), detail=f"Query: {query}. " + (
                   guidance or "No approved static pattern; folding and runtime are unverified."))
        if codes:
            finding(wid, iid, name, "Dataflow", "DFLOW-001", (query,),
                    dimension="performance", severity="medium",
                    title=f"Dataflow query review: {query}", recommendation=guidance,
                    signal_codes=", ".join(codes), affected_count=1, status="info", is_fail=0)

    for row in rows("gold_execution_coverage"):
        wid = scope(row)
        if wid is None:
            continue
        kind = row.get("item_type")
        if kind not in _EXECUTION_KINDS:
            raise ValueError("Unsupported owner execution item type.")
        if row.get("item_id") in (None, ""):
            coverage(
                wid, wid, workspace_names[wid], "Workspace", f"{kind} execution inventory",
                _state(row.get("collection_status"), _COLLECTION_STATES - {"collected", "empty"}),
                history_scope="Incomplete native execution item inventory",
                source=_EXECUTION_KINDS[kind][1],
                notice="Native execution inventory is incomplete; undiscovered items may be absent. "
                "No execution or item totals can be inferred from this gap. " + COVERAGE_NOTICE,
            )
            continue
        resolved = item(row, wid, "item_id", kind)
        if resolved is None:
            continue
        iid, name = resolved
        oldest, newest = _timestamp(row.get("oldest_start_time")), _timestamp(row.get("newest_start_time"))
        if oldest is not None and newest is not None and datetime.fromisoformat(oldest) > datetime.fromisoformat(newest):
            raise ValueError("Owner execution coverage has reversed timestamps.")
        coverage(wid, iid, name, kind, "Native executions",
                 _state(row.get("collection_status"), _COLLECTION_STATES),
                 observed_count=_count(row, "observed_execution_count"),
                 oldest_start_time=oldest, newest_start_time=newest,
                 history_scope="Bounded native API history", source=_EXECUTION_KINDS[kind][1])

    for row in rows("gold_item_executions"):
        wid = scope(row)
        if wid is None:
            continue
        kind = row.get("item_type")
        if kind not in _EXECUTION_KINDS:
            raise ValueError("Unsupported owner execution item type.")
        resolved = item(row, wid, "item_id", kind)
        if resolved is None:
            continue
        iid, name = resolved
        execution_id = row.get("execution_id")
        if not isinstance(execution_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", execution_id):
            raise ValueError("Owner execution IDs require a safe opaque identifier.")
        start, end = _timestamp(row.get("start_time")), _timestamp(row.get("end_time"))
        if start is not None and end is not None and datetime.fromisoformat(start) > datetime.fromisoformat(end):
            raise ValueError("Owner execution has reversed timestamps.")
        duration = _count(row, "duration_ms")
        raw_status = row.get("status")
        status = _EXECUTION_STATES.get(raw_status.lower(), "Unknown") if isinstance(raw_status, str) else "Unknown"
        execution_type, source = _EXECUTION_KINDS[kind]
        key = _key(wid, iid, kind, source, execution_id)
        emit("owner_executions", {
            **meta(wid), "execution_key": key, "execution_id": execution_id,
            "item_id": iid, "item_name": name, "item_type": kind,
            "execution_type": execution_type, "status": status,
            "start_time": start, "end_time": end, "duration_ms": duration,
            "source": source, "notice": RUNTIME_NOTICE,
        }, key, is_technical=True)
        if status == "Failed":
            finding(wid, iid, name, kind, "EXEC-001", (key,),
                    dimension="operational_excellence", severity="high",
                    title="Observed failed " + execution_type.lower(),
                    recommendation="Inspect the failed run in the source item's native history using its execution ID. "
                    "Review protected diagnostics there, correct the cause and verify a subsequent run.",
                    affected_count=1, notice=RUNTIME_NOTICE)

    for iid, (wid, kind, name) in sorted(identities.items()):
        categories = (["Native executions"] if kind in _EXECUTION_KINDS else [])
        categories += {
            "SemanticModel": ["DAX measures", "DAX objects"],
            "Dataflow": ["Dataflow queries"], "DataPipeline": ["Pipeline structure"],
        }.get(kind, [])
        for evidence in categories:
            if (wid, iid, evidence) not in covered:
                coverage(wid, iid, name, kind, evidence, "missing")
    for wid in workspace_names:
        if ((wid, wid, "Dataflow inventory") not in covered
                and not any(identity[:2] == (wid, "Dataflow") for identity in identities.values())):
            coverage(wid, wid, workspace_names[wid], "Workspace", "Dataflow inventory", "missing")

    detail_categories = {
        "DAX measures": {"dax_measure"},
        "DAX objects": {"dax_calculated_column", "dax_calculated_table", "dax_calculation_item"},
        "Dataflow queries": {"dataflow_query"},
    }
    category_by_type = {kind: category for category, kinds in detail_categories.items() for kind in kinds}
    projected_counts = Counter(
        (fact["workspace_id"], fact["item_id"], "Native executions")
        for fact in output["owner_executions"]
    )
    projected_counts.update(
        (fact["workspace_id"], fact["item_id"], category_by_type[fact["detail_type"]])
        for fact in output["owner_details"] if fact["detail_type"] in category_by_type
    )
    for row in output["owner_coverage"]:
        evidence = row["evidence_type"]
        if evidence != "Native executions" and evidence not in detail_categories:
            continue
        projected = projected_counts[(row["workspace_id"], row["item_id"], evidence)]
        observed = row["observed_count"]
        if observed is not None and observed != projected:
            row["collection_status"] = "partial"
            row["notice"] = (
                f"Collector reported {observed} records; {projected} records were safely projected. "
                "Evidence is incomplete or inconsistent; recollect before drawing conclusions."
            )

    for wid, name in sorted(workspace_names.items()):
        output["owner_workspaces"].append({"workspace_id": wid, "workspace_name": name})
        own_findings = [row for row in output["owner_findings"] if row["workspace_id"] == wid]
        own_details = [row for row in output["owner_details"] if row["workspace_id"] == wid]
        output["owner_reviews"].append({
            **meta(wid), "workspace_name": name, "is_latest": True,
            "owner_fail_count": sum(row["is_fail"] for row in own_findings),
            "owner_finding_count": len(own_findings),
            "owner_detail_count": len(own_details), "technical_detail_count": technical[wid],
            "assessment_status": "incomplete",
            "technical_evidence_status": "partial" if technical[wid] else "missing",
            "notice": PARTIAL_NOTICE + (" " + MISSING_NOTICE if not technical[wid] else ""),
        })
    for name in ("owner_findings", "owner_details"):
        key = "finding_key" if name == "owner_findings" else "detail_key"
        output[name].sort(key=lambda row: (row["workspace_id"], row[key]))
    for name, key in (("owner_executions", "execution_key"), ("owner_coverage", "coverage_key")):
        output[name].sort(key=lambda row: (row["workspace_id"], row[key]))
    return output

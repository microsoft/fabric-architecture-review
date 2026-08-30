# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate the Direct Lake governance semantic model as TMSL (``model.bim``).

We emit TMSL (one JSON object) rather than multi-file TMDL because a single
``model.bim`` part is much easier to generate deterministically and validate,
and the Fabric *Update Semantic Model Definition* API accepts it directly as a
``model.bim`` part (no ``format`` field needed).

The model binds to the Lakehouse SQL analytics endpoint in **Direct Lake**
mode, so it reads the gold Delta tables live with no import/refresh. The
table + column list is generated from :mod:`reports.powerbi.schema`, the same
source of truth the gold-layer builder uses, so they can never drift.

DATA SAFETY: builds metadata only.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from reports.powerbi.schema import GOLD_TABLES, tmdl_type

_NS = uuid.UUID("6f3b1c2a-0d4e-4a5b-9c7d-1e2f3a4b5c6d")

# Fact table that carries the explicit measures + the run dimension key.
FACT_TABLE = "gold_findings"
RUN_TABLE = "gold_run_summary"
# Table that carries the VertiPaq model-burden measures.
MODEL_TABLE = "gold_semantic_models"
# Tables that carry the estate-graph / workspace-risk measures.
RISK_TABLE = "gold_workspace_risk"
GRAPH_NODE_TABLE = "gold_graph_nodes"
GRAPH_EDGE_TABLE = "gold_graph_edges"
# Table that carries the notebook code-smell count measure.
NOTEBOOK_TABLE = "gold_notebook_smells"
PARTITION_TABLE = "gold_model_partitions"
RELATIONSHIP_TABLE = "gold_model_relationships"
HIERARCHY_TABLE = "gold_model_hierarchies"
# Table that carries the Best Practice Analyzer violation count measure.
BPA_TABLE = "gold_bpa_violations"
# Table that carries the severity heatmap data + its colour measure.
SEVERITY_MATRIX_TABLE = "gold_severity_matrix"
# Table that carries the deployed-version banner measures (Home page footer).
RELEASE_TABLE = "gold_release"
# Table that carries the deterministic Data Agent evaluation results.
AGENT_EVAL_TABLE = "gold_agent_eval"
# Columns that hold http(s) links; flagged as Web URLs so report tables render
# them as clickable hyperlinks.
_WEB_URL_COLUMNS = {"microsoft_learn_url", "notebook_url"}

# Ontology layer: plain-language business meaning of the columns, surfaced as
# TMSL column ``description`` so both report authors and the Fabric data agent
# (natural-language -> DAX) can reason over the model. Descriptions that name
# the allowed values ("one of: ...") are deliberate - they steer NL2DAX toward
# correct filters. Keyed by bare column name; the same column carries the same
# meaning across the run-partitioned gold tables. Per-table overrides (where a
# name is reused with a different meaning) live in ``_COLUMN_DESCRIPTIONS_BY_TABLE``.
_COLUMN_DESCRIPTIONS: Dict[str, str] = {
    "run_id": "Unique id of the review run this row belongs to; the join key to gold_run_summary (the run dimension).",
    "run_timestamp": "UTC timestamp when the review run executed. Use for trend-over-time analysis.",
    "client_name": "Name of the reviewed customer / tenant for this engagement.",
    "engagement_name": "Name of the architecture-review engagement.",
    "reviewer_name": "Person who ran the review.",
    "is_latest": "True on the single most recent review run; filter on this to show current-state answers.",
    "rule_id": "Checklist rule identifier, e.g. ARCH-009, PERF-004, SEC-008. The prefix is the dimension (ARCH/PERF/COST/GOV/SEC/TENANT/NBCODE).",
    "dimension": "Assessment area. One of: architecture, performance, cost, governance, security, tenant_settings, notebook_code.",
    "severity": "Finding severity, worst first. One of: critical, high, medium, low, info.",
    "severity_rank": "Numeric severity ordering (higher number = more severe); use to sort.",
    "status": "Check outcome: pass, fail, info, not_applicable, unknown, or missing_evidence.",
    "is_fail": "1 when status = fail, otherwise 0. Sum this to count failing rules.",
    "is_scored": "1 when status is pass or fail, otherwise 0.",
    "title": "Short human-readable finding title.",
    "recommendation": "Recommended remediation action for the finding.",
    "rule_description": "Plain-language description of what the checklist rule verifies.",
    "microsoft_learn_url": "Microsoft Learn documentation link for the rule.",
    "affected": "Short summary of the workspaces / items the finding affects.",
    "evidence_json": "Raw evidence for the finding as a JSON string (detailed drill-down).",
    "total_findings": "Total checklist rules evaluated in the run.",
    "total": "Total checklist rules evaluated for this dimension in the run.",
    "pass_count": "Number of rules that passed.",
    "fail_count": "Number of rules that failed.",
    "info_count": "Number of informational (neither pass nor fail) findings.",
    "not_applicable_count": "Number of controls excluded from scoring by applicability.",
    "unknown_count": "Number of controls whose applicability could not be established.",
    "missing_evidence_count": "Number of controls that lacked required collected evidence.",
    "assessment_coverage": "Percentage of findings with a conclusive applicability/evidence outcome.",
    "critical_fail": "Number of failing rules with critical severity.",
    "high_fail": "Number of failing rules with high severity.",
    "medium_fail": "Number of failing rules with medium severity.",
    "low_fail": "Number of failing rules with low severity.",
    "score": "Best-practice score 0-100 (pass rate: pass / (pass + fail) x 100). 80+ healthy, 50-79 needs review, under 50 poor.",
    "worst_severity": "Most severe outcome seen for this dimension in the run.",
    "capacity_id": "Fabric/Power BI capacity identifier.",
    "capacity_name": "Fabric/Power BI capacity display name.",
    "sku": "Capacity SKU size, e.g. F2, F64, P1, PP3.",
    "kind": "Capacity class: Fabric, Premium, Premium Per User, Embedded, or Trial.",
    "is_dedicated": "True for a dedicated capacity; false for the per-user PPU reservation.",
    "state": "Capacity state at scan time, e.g. Active, Paused.",
    "region": "Azure region the capacity is provisioned in.",
    "workspace_id": "Fabric workspace identifier.",
    "workspace_name": "Fabric workspace display name.",
    "on_capacity": "True when the workspace is assigned to a dedicated capacity.",
    "item_count": "Number of Fabric items (models, reports, notebooks, pipelines, lakehouses) in the workspace.",
    "admin_count": "Number of workspace admins. 1 = single owner / orphan risk (governance rule GOV-001).",
    "last_activity": "Most recent activity-log event for the workspace; blank if there was none in the review window.",
    "is_inactive": "True when the workspace had no activity in the review window - an archival / close candidate (GOV-006).",
    "model_id": "Semantic model identifier.",
    "model_name": "Semantic model display name.",
    "storage_mode": "Semantic model storage mode: Import/Abf, DirectQuery, DirectLake, or Fabric.Warehouse.",
    "is_refreshable": "True when the semantic model has a refreshable (Import) partition.",
    "total_size": "In-memory VertiPaq size in bytes.",
    "table_count": "Number of tables in the semantic model.",
    "column_count": "Number of columns.",
    "calc_column_count": "Number of calculated columns (a common refresh + memory cost driver).",
    "max_refresh_seconds": "Longest observed refresh duration in seconds.",
    "risk_score": "Workspace risk score 0-100 (higher = worse); weighted from failing findings.",
    "status_rank": "Risk status ordering: 3 red, 2 amber, 1 green, 0 blue, -1 grey. >= 2 means at risk.",
    "issue_count": "Number of failing findings attributed to this workspace.",
    "critical_count": "Number of critical failing findings.",
    "high_count": "Number of high failing findings.",
    "node_type": "Estate-graph node type, e.g. Capacity, Workspace, SemanticModel, Report, Notebook, Pipeline, Lakehouse, Owner.",
    "relationship": "Estate-graph edge type, e.g. hosts, administers, contains, feeds.",
    "deployed_version": "Installed FAR (Fabric Architecture Review) release version.",
    "latest_version": "Latest FAR release available on the source repository.",
    "update_available": "True when a newer FAR release than the deployed one exists.",
}

# Per-(table, column) overrides where a bare column name would otherwise be
# ambiguous or wrong.
_COLUMN_DESCRIPTIONS_BY_TABLE: Dict[tuple, str] = {
    ("gold_workspaces", "description"): "Workspace description text set by its owner.",
    ("gold_model_columns", "cardinality"): "Distinct-value count of the column (drives dictionary size).",
    ("gold_model_columns", "encoding"): "VertiPaq column encoding: VALUE (numeric) or HASH (dictionary).",
    ("gold_model_columns", "data_type"): "Column data type in the semantic model.",
    ("gold_release", "status"): "Deployed-version status line, e.g. 'FAR v2026.06.0 - up to date'.",
    ("gold_release", "update_note"): "Upgrade guidance shown only when a newer release exists.",
}


def _lineage(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _column_description(table: str, name: str) -> str:
    return _COLUMN_DESCRIPTIONS_BY_TABLE.get((table, name)) or _COLUMN_DESCRIPTIONS.get(name, "")


def _column(table: str, name: str, kind: str) -> Dict[str, Any]:
    col: Dict[str, Any] = {
        "name": name,
        "dataType": tmdl_type(kind),
        "sourceColumn": name,
        "summarizeBy": "none",
        "lineageTag": _lineage(table, "col", name),
    }
    if kind in ("int64", "double"):
        col["formatString"] = "0" if kind == "int64" else "0.0"
    if name in _WEB_URL_COLUMNS:
        col["dataCategory"] = "WebUrl"
    desc = _column_description(table, name)
    if desc:
        col["description"] = desc
    return col


def _measures() -> List[Dict[str, Any]]:
    defs = [
        ("Total Findings", "COUNTROWS(gold_findings)", "0",
         "Number of checklist rules evaluated in the current filter context."),
        ("Fail Count", 'COALESCE(CALCULATE(COUNTROWS(gold_findings), gold_findings[status] = "fail"), 0)', "0",
         "Rules that did not meet the best-practice bar (status = fail) in context; 0 (not blank) when none fail."),
        ("Pass Count", 'COALESCE(CALCULATE(COUNTROWS(gold_findings), gold_findings[status] = "pass"), 0)', "0",
         "Rules that met the best-practice bar (status = pass) in context; 0 (not blank) when none pass."),
        ("Info Count", 'CALCULATE(COUNTROWS(gold_findings), gold_findings[status] = "info")', "0",
         "Informational findings that neither pass nor fail."),
        ("Not Applicable Count", 'CALCULATE(COUNTROWS(gold_findings), gold_findings[status] = "not_applicable")', "0",
         "Rules excluded from scoring because they do not apply to the evaluated estate."),
        ("Unknown Count", 'CALCULATE(COUNTROWS(gold_findings), gold_findings[status] = "unknown")', "0",
         "Rules whose applicability could not be established."),
        ("Missing Evidence Count", 'CALCULATE(COUNTROWS(gold_findings), gold_findings[status] = "missing_evidence")', "0",
         "Rules that could not execute because required evidence was unavailable."),
        ("Assessment Coverage",
         "DIVIDE([Total Findings] - [Unknown Count] - [Missing Evidence Count], [Total Findings]) * 100", "0.0",
         "Percent of findings with conclusive applicability and available evidence."),
        ("Best Practice Score",
         "DIVIDE([Pass Count], [Pass Count] + [Fail Count]) * 100", "0.0",
         "Percent of pass/fail rules that passed: Pass / (Pass + Fail) x 100."),
        ("Critical & High Fails",
         'CALCULATE([Fail Count], gold_findings[severity] IN {"critical", "high"})', "0",
         "Failing rules whose severity is critical or high - the items to fix first."),
        ("Score Target", "80", "0",
         "Target best-practice score (the green/healthy threshold) - drives the gauge target line."),
        ("Score Max", "100", "0",
         "Maximum best-practice score - fixes the gauge scale to a 0-100 range."),
        ("Weighted Risk Score",
         'SUMX(FILTER(gold_findings, gold_findings[is_fail] = 1), '
         'SWITCH(gold_findings[severity], "critical", 100, "high", 75, '
         '"medium", 50, "low", 25, 10))', "#,0",
         "Sum of failing-rule severity weights (Critical 100 / High 75 / Medium 50 / Low 25 / Info 10) - one risk number for the estate."),
        ("Health Score", "[Best Practice Score]", "0.0",
         "Platform health: pass-rate %. 80+ healthy, 50-79 needs review, under 50 poor."),
        ("Assessed Assets", "DISTINCTCOUNT(gold_findings[dimension])", "0",
         "Number of assessment dimensions evaluated this run."),
        ("Severity Color",
         'SWITCH(LOWER(SELECTEDVALUE(gold_findings[severity])), "critical", "#A4262C", '
         '"high", "#D13438", "medium", "#E8702A", "low", "#2B88D8", "#605E5C")', "",
         "Hex colour for the row's severity (critical = deep red, high = red, medium = orange, low = blue, info = grey; green is reserved for pass) - drives data-colour-by-value on charts."),
        ("Status Color",
         'SWITCH(LOWER(SELECTEDVALUE(gold_findings[status])), "pass", "#107C10", '
         '"fail", "#D13438", "missing_evidence", "#D83B01", "unknown", "#8A6914", '
         '"info", "#605E5C", "not_applicable", "#A19F9D", "#605E5C")', "",
         "Hex colour for the row's evaluation outcome - drives data-colour-by-value on charts."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _model_measures() -> List[Dict[str, Any]]:
    """VertiPaq model-burden measures placed on ``gold_semantic_models``."""
    defs = [
        ("Model Count", "DISTINCTCOUNT(gold_semantic_models[model_id])", "0",
         "Number of semantic models in the current filter context."),
        ("Total Model Size", "SUM(gold_semantic_models[total_size])", "#,0",
         "Sum of in-memory VertiPaq size (bytes) across the models in context."),
        ("Total Model Size (MB)",
         "DIVIDE(SUM(gold_semantic_models[total_size]), 1048576)", "#,0.0",
         "In-memory VertiPaq size in megabytes (size / 1024 / 1024)."),
        ("Import Models",
         'CALCULATE(DISTINCTCOUNT(gold_semantic_models[model_id]), '
         'NOT(gold_semantic_models[storage_mode] IN {"DirectLake", "DirectQuery"}))', "0",
         "Models that load data into memory (Import / Abf) and so carry a VertiPaq footprint."),
        ("Calculated Columns",
         "SUM(gold_semantic_models[calc_column_count])", "0",
         "Total calculated columns across models - a common refresh + memory cost driver."),
        ("Total Columns", "SUM(gold_semantic_models[column_count])", "0",
         "Total columns materialized across the models in context."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _risk_measures() -> List[Dict[str, Any]]:
    """Workspace-risk + estate measures placed on ``gold_workspace_risk``."""
    defs = [
        ("Workspace Count", "DISTINCTCOUNT(gold_workspace_risk[workspace_id])", "0",
         "Number of workspaces in the current filter context."),
        ("Workspaces at Risk",
         "CALCULATE(DISTINCTCOUNT(gold_workspace_risk[workspace_id]), "
         "gold_workspace_risk[status_rank] >= 2) + 0", "0",
         "Workspaces whose risk status is amber or red (status_rank >= 2)."),
        ("Average Risk Score", "AVERAGE(gold_workspace_risk[risk_score])", "0.0",
         "Mean 0-100 risk score across the workspaces in context."),
        ("Max Risk Score", "MAX(gold_workspace_risk[risk_score])", "0.0",
         "Highest workspace risk score in context - the worst hotspot."),
        ("Workspace Issues", "SUM(gold_workspace_risk[issue_count])", "0",
         "Total failing findings attributed to workspaces in context."),
        ("Total Items", "SUM(gold_workspace_risk[item_count])", "0",
         "Total Fabric items (models, reports, notebooks, pipelines, lakehouses) in context."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _graph_measures(table_name: str) -> List[Dict[str, Any]]:
    if table_name == GRAPH_NODE_TABLE:
        defs = [
            ("Node Count", "COUNTROWS(gold_graph_nodes)", "0",
             "Number of estate nodes (any type) in the current filter context."),
        ]
    else:
        defs = [
            ("Relationship Count", "COUNTROWS(gold_graph_edges)", "0",
             "Number of relationships (edges) between estate nodes in context."),
        ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _run_measures() -> List[Dict[str, Any]]:
    """Trend measures on ``gold_run_summary`` (one row per run -> plot over time)."""
    defs = [
        ("Run Score", "AVERAGE(gold_run_summary[score])", "0.0",
         "Best-practice score for each run - plot over run_timestamp to see the trend."),
        ("Run Fails", "SUM(gold_run_summary[fail_count])", "0",
         "Failing rules per run - the issue trend across reviews."),
        ("Run Critical & High",
         "SUM(gold_run_summary[critical_fail]) + SUM(gold_run_summary[high_fail])", "0",
         "Critical + high fails per run - severity trend across reviews."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _notebook_measures() -> List[Dict[str, Any]]:
    """Code-smell count measure placed on ``gold_notebook_smells``."""
    defs = [
        ("Notebook Smell Count", "COUNTROWS(gold_notebook_smells)", "0",
         "Number of notebook code-smell matches (NBCODE rule hits) in context."),
        ("Smell Severity Color",
         'SWITCH(LOWER(SELECTEDVALUE(gold_notebook_smells[severity])), "critical", "#A4262C", '
         '"high", "#D13438", "medium", "#E8702A", "low", "#2B88D8", "#605E5C")', "",
         "Hex colour for the smell's severity (critical deep red -> low blue; green = pass only) - drives data-colour-by-value on charts."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _internals_measures(table_name: str) -> List[Dict[str, Any]]:
    """Count measures for the VertiPaq internals frames so the Model internals
    page reads as an investigation dashboard, not a raw table dump."""
    if table_name == PARTITION_TABLE:
        defs = [("Partition Count", "COUNTROWS(gold_model_partitions)", "0",
                 "Number of model partitions in context - many small segments hint at refresh cost.")]
    elif table_name == RELATIONSHIP_TABLE:
        defs = [("Model Relationship Count", "COUNTROWS(gold_model_relationships)", "0",
                 "Number of model relationships in context; missing rows flag data-quality smells.")]
    else:
        defs = [("Hierarchy Count", "COUNTROWS(gold_model_hierarchies)", "0",
                 "Number of user hierarchies in context and the extra memory they cost.")]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _bpa_measures() -> List[Dict[str, Any]]:
    """BPA violation count measure placed on ``gold_bpa_violations``."""
    defs = [
        ("BPA Violation Count", "COUNTROWS(gold_bpa_violations)", "0",
         "Number of individual Best Practice Analyzer / health violations in context."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _agent_eval_measures() -> List[Dict[str, Any]]:
    """Deterministic Data Agent accuracy measures on ``gold_agent_eval``.

    Each row is one evaluation case whose expected answer is computed from the
    gold tables, so ``passed`` is a trustworthy ground truth. These roll the
    per-case results up into a pass rate the report and the agent can cite.
    """
    defs = [
        ("Agent Eval Case Count", "COUNTROWS(gold_agent_eval)", "0",
         "Number of Data Agent evaluation cases in context."),
        ("Agent Eval Pass Count", "CALCULATE(COUNTROWS(gold_agent_eval), gold_agent_eval[passed] = 1)", "0",
         "Evaluation cases the agent answered correctly (matched the gold-derived expected answer)."),
        ("Agent Eval Pass Rate",
         "DIVIDE(CALCULATE(COUNTROWS(gold_agent_eval), gold_agent_eval[passed] = 1), COUNTROWS(gold_agent_eval))",
         "0.0%",
         "Share of Data Agent evaluation cases answered correctly - the agent-accuracy KPI."),
        ("Agent Eval Target", "1", "0.0%",
         "Target accuracy for the agent gauge (100%)."),
        ("Agent Eval Max", "1", "0.0%",
         "Full-scale maximum for the agent accuracy gauge (100%)."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _release_measures() -> List[Dict[str, Any]]:
    """Deployed-version banner measure placed on ``gold_release``.

    A ``card`` visual needs an aggregated value, so the Home-page version banner
    binds to this measure rather than the raw text columns (a bare column on a
    card renders blank in Direct Lake). It reads the most recently recorded
    release row (max ``checked_at``), independent of any run slicer / page
    filter, and folds the upgrade note (only present when a newer release exists)
    into the same string so the footer is always a single, non-empty line.
    """
    expr = (
        "VAR _ts = MAXX(ALL(gold_release), gold_release[checked_at]) "
        "VAR _row = FILTER(ALL(gold_release), gold_release[checked_at] = _ts) "
        "VAR _status = MAXX(_row, gold_release[status]) "
        "VAR _note = MAXX(_row, gold_release[update_note]) "
        'RETURN _status & IF(NOT ISBLANK(_note) && LEN(_note) > 0, "   |   " & _note, "")'
    )
    defs = [
        ("Release Banner", expr, "",
         "Deployed FAR version line for the Home-page footer card "
         "(e.g. 'FAR v2026.06.0 - up to date'); appends the upgrade note when a newer release exists."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _severity_matrix_measures() -> List[Dict[str, Any]]:
    """Severity colour measure placed on ``gold_severity_matrix`` (heatmap series)."""
    defs = [
        ("Matrix Severity Color",
         'SWITCH(LOWER(SELECTEDVALUE(gold_severity_matrix[severity])), "critical", "#A4262C", '
         '"high", "#D13438", "medium", "#E8702A", "low", "#2B88D8", "#605E5C")', "",
         "Hex colour for the severity series - drives data-colour-by-value on the dimension chart."),
    ]
    out = []
    for name, expr, fmt, desc in defs:
        out.append({
            "name": name,
            "expression": expr,
            "formatString": fmt,
            "description": desc,
            "lineageTag": _lineage("measure", name),
        })
    return out


def _table(table) -> Dict[str, Any]:
    t: Dict[str, Any] = {
        "name": table.name,
        "lineageTag": _lineage("table", table.name),
        "columns": [_column(table.name, c.name, c.kind) for c in table.columns],
        "partitions": [{
            "name": table.name,
            "mode": "directLake",
            "source": {
                "type": "entity",
                "entityName": table.name,
                "schemaName": "dbo",
                "expressionSource": "DatabaseQuery",
            },
        }],
    }
    if getattr(table, "description", ""):
        t["description"] = table.description
    if table.name == FACT_TABLE:
        t["measures"] = _measures()
    if table.name == MODEL_TABLE:
        t["measures"] = _model_measures()
    if table.name == RISK_TABLE:
        t["measures"] = _risk_measures()
    if table.name == RUN_TABLE:
        t["measures"] = _run_measures()
    if table.name in (GRAPH_NODE_TABLE, GRAPH_EDGE_TABLE):
        t["measures"] = _graph_measures(table.name)
    if table.name == NOTEBOOK_TABLE:
        t["measures"] = _notebook_measures()
    if table.name == BPA_TABLE:
        t["measures"] = _bpa_measures()
    if table.name == SEVERITY_MATRIX_TABLE:
        t["measures"] = _severity_matrix_measures()
    if table.name == RELEASE_TABLE:
        t["measures"] = _release_measures()
    if table.name == AGENT_EVAL_TABLE:
        t["measures"] = _agent_eval_measures()
    if table.name in (PARTITION_TABLE, RELATIONSHIP_TABLE, HIERARCHY_TABLE):
        t["measures"] = _internals_measures(table.name)
    return t


def _relationships() -> List[Dict[str, Any]]:
    rels = []
    for table in GOLD_TABLES:
        if table.name == RUN_TABLE:
            continue
        if not any(c.name == "run_id" for c in table.columns):
            continue
        rels.append({
            "name": _lineage("rel", table.name),
            "fromTable": table.name,
            "fromColumn": "run_id",
            "toTable": RUN_TABLE,
            "toColumn": "run_id",
            "crossFilteringBehavior": "oneDirection",
        })
    return rels


def build_bim(model_name: str, sql_endpoint: str, database_id: str) -> Dict[str, Any]:
    """Build the TMSL model object.

    ``sql_endpoint`` is the Lakehouse SQL analytics endpoint host
    (e.g. ``xxxxx.datawarehouse.fabric.microsoft.com``); ``database_id`` is the
    SQL endpoint id (or Lakehouse name) used as the database in ``Sql.Database``.
    """
    m_expr = (
        "let\n"
        f'    database = Sql.Database("{sql_endpoint}", "{database_id}")\n'
        "in\n"
        "    database"
    )
    return {
        "name": model_name,
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "expressions": [{
                "name": "DatabaseQuery",
                "kind": "m",
                "expression": m_expr,
                "lineageTag": _lineage("expression", "DatabaseQuery"),
            }],
            "tables": [_table(t) for t in GOLD_TABLES],
            "relationships": _relationships(),
            "annotations": [
                {"name": "PBI_QueryOrder", "value": '["DatabaseQuery"]'},
            ],
        },
    }


def build_model_bim_json(model_name: str, sql_endpoint: str, database_id: str) -> str:
    return json.dumps(build_bim(model_name, sql_endpoint, database_id), indent=2)

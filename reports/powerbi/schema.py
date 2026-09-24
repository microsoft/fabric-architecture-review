# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Single source of truth for the gold-layer tables that back the Direct Lake
governance semantic model and report.

Both ``reports.gold_layer`` (which materializes the rows) and
``reports.powerbi.semantic_model`` (which generates the TMDL) import these
specs so the Delta tables, the Spark schema, and the semantic-model columns
never drift apart.

Column ``kind`` is one of: string | int64 | double | dateTime | boolean.

DATA SAFETY: schema only - no data access.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple


class Column(NamedTuple):
    name: str
    kind: str  # string | int64 | double | dateTime | boolean


class Table(NamedTuple):
    name: str
    columns: List[Column]
    # column the report sorts/relates on; first table is the fact table
    description: str


def _c(name: str, kind: str = "string") -> Column:
    return Column(name, kind)


GOLD_TABLES: List[Table] = [
    Table(
        "gold_findings",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("client_name"),
            _c("engagement_name"),
            _c("reviewer_name"),
            _c("rule_id"),
            _c("dimension"),
            _c("severity"),
            _c("severity_rank", "int64"),
            _c("status"),
            _c("is_fail", "int64"),
            _c("is_scored", "int64"),
            _c("title"),
            _c("recommendation"),
            _c("rule_description"),
            _c("microsoft_learn_url"),
            _c("affected"),
            _c("evidence_json"),
        ],
        "One row per evaluated checklist rule per run.",
    ),
    Table(
        "gold_cost_finding_impacts",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("impact_key"),
            _c("show_in_findings", "boolean"),
            _c("rule_id"),
            _c("severity"),
            _c("severity_rank", "int64"),
            _c("status"),
            _c("title"),
            _c("recommendation"),
            _c("affected_type"),
            _c("affected_id"),
            _c("affected_name"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("sku"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("workspace_count", "int64"),
            _c("item_count", "int64"),
            _c("detail"),
        ],
        "One row per Cost rule and affected capacity or workspace. A hidden all-items "
        "row keeps the Cost detail table complete until a finding impact is selected.",
    ),
    Table(
        "gold_cost_impact_items",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("impact_key"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("sku"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("item_id"),
            _c("item_name"),
            _c("item_type"),
        ],
        "Many-to-many Cost finding impact to capacity-content mapping. An item appears "
        "once per applicable impact; it is not a second canonical item inventory. "
        "Selecting an affected object filters this table to matching contents.",
    ),
    Table(
        "gold_run_summary",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("client_name"),
            _c("engagement_name"),
            _c("reviewer_name"),
            _c("total_findings", "int64"),
            _c("pass_count", "int64"),
            _c("fail_count", "int64"),
            _c("info_count", "int64"),
            _c("not_applicable_count", "int64"),
            _c("unknown_count", "int64"),
            _c("missing_evidence_count", "int64"),
            _c("assessment_coverage", "double"),
            _c("critical_fail", "int64"),
            _c("high_fail", "int64"),
            _c("medium_fail", "int64"),
            _c("low_fail", "int64"),
            _c("score", "double"),
            _c("is_latest", "boolean"),
        ],
        "One row per review run - headline scorecard.",
    ),
    Table(
        "gold_dimension_summary",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("dimension"),
            _c("total", "int64"),
            _c("pass_count", "int64"),
            _c("fail_count", "int64"),
            _c("info_count", "int64"),
            _c("not_applicable_count", "int64"),
            _c("unknown_count", "int64"),
            _c("missing_evidence_count", "int64"),
            _c("assessment_coverage", "double"),
            _c("score", "double"),
            _c("worst_severity"),
        ],
        "One row per dimension per run.",
    ),
    Table(
        "gold_capacities",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("sku"),
            _c("kind"),
            _c("is_dedicated", "boolean"),
            _c("state"),
            _c("region"),
            _c("assigned_workspace_count", "int64"),
            _c("observed_workspace_count", "int64"),
            _c("observed_item_count", "int64"),
            _c("workspace_scope_limited", "boolean"),
        ],
        "Capacities seen at scan time. 'kind' classifies the SKU "
        "(Fabric / Premium / Premium Per User / Embedded / Trial); "
        "'is_dedicated' is false for the per-user PPU reservation. Assigned "
        "workspace count is collector-reported; observed counts describe the "
        "workspace review scope and workspace_scope_limited marks partial evidence.",
    ),
    Table(
        "gold_capacity_items",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("sku"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("item_id"),
            _c("item_name"),
            _c("item_type"),
            _c("workspace_scope_limited", "boolean"),
        ],
        "One row per observed Fabric item with its workspace and capacity, for "
        "capacity right-sizing drilldown. Rows reflect the configured workspace scope.",
    ),
    Table(
        "gold_tenant_setting_changes",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("event_id"),
            _c("event_time", "dateTime"),
            _c("actor"),
            _c("operation"),
            _c("setting_name"),
            _c("old_value"),
            _c("new_value"),
            _c("change_details"),
            _c("audit_window_days", "int64"),
            _c("audit_fetched_at", "dateTime"),
        ],
        "Observed tenant-setting changes in the configured activity-log window. "
        "Before/after values are populated only when supplied by the audit event.",
    ),
    Table(
        "gold_workspaces",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("capacity_id"),
            _c("on_capacity", "boolean"),
            _c("item_count", "int64"),
            _c("admin_count", "int64"),
            _c("last_activity", "dateTime"),
            _c("is_inactive", "boolean"),
            _c("description"),
            _c("archetype"),
            _c("environment"),
            _c("classification"),
            _c("profile_reason"),
            _c("item_type_counts_json"),
        ],
        "Workspaces in the review scope, with admin count and last-activity so "
        "single-admin and unused/archival-candidate workspaces are directly queryable.",
    ),
    Table(
        "gold_semantic_models",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("storage_mode"),
            _c("is_refreshable", "boolean"),
            _c("total_size", "int64"),
            _c("table_count", "int64"),
            _c("column_count", "int64"),
            _c("calc_column_count", "int64"),
            _c("max_refresh_seconds", "double"),
        ],
        "Semantic models and their storage mode.",
    ),
    Table(
        "gold_dax_models",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("model_id"),
            _c("model_name"),
            _c("definition_status"),
            _c("measure_count", "int64"),
            _c("flagged_measure_count", "int64"),
            _c("high_risk_count", "int64"),
            _c("max_risk_score", "int64"),
        ],
        "One metadata-only DAX coverage and static-risk summary row per semantic model.",
    ),
    Table(
        "gold_dax_measures",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("model_id"),
            _c("model_name"),
            _c("table_name"),
            _c("measure_name"),
            _c("risk_level"),
            _c("risk_rank", "int64"),
            _c("risk_score", "int64"),
            _c("expression_length", "int64"),
            _c("expression_preview"),
            _c("signal_codes"),
            _c("signal_details_json"),
        ],
        "One row per extracted DAX measure with denormalized capacity/model hierarchy and explainable static-risk signals.",
    ),
    Table(
        "gold_model_tables",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("table_name"),
            _c("row_count", "int64"),
            _c("total_size", "int64"),
            _c("data_size", "int64"),
            _c("dictionary_size", "int64"),
            _c("hierarchy_size", "int64"),
            _c("column_count", "int64"),
            _c("pct_db", "double"),
        ],
        "VertiPaq per-table footprint for each semantic model. workspace_id is set "
        "only for unambiguous collector workspace/model-ID provenance, never a name fallback.",
    ),
    Table(
        "gold_model_columns",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("workspace_name"),
            _c("table_name"),
            _c("column_name"),
            _c("qualified_column"),
            _c("data_type"),
            _c("encoding"),
            _c("cardinality", "int64"),
            _c("total_size", "int64"),
            _c("data_size", "int64"),
            _c("dictionary_size", "int64"),
            _c("hierarchy_size", "int64"),
            _c("pct_table", "double"),
            _c("pct_db", "double"),
            _c("is_calculated", "boolean"),
        ],
        "VertiPaq per-column statistics (size, cardinality, encoding, data type).",
    ),
    Table(
        "gold_model_partitions",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("workspace_name"),
            _c("table_name"),
            _c("partition_name"),
            _c("mode"),
            _c("record_count", "int64"),
            _c("segment_count", "int64"),
            _c("records_per_segment", "double"),
        ],
        "VertiPaq per-partition footprint (mode, record and segment counts).",
    ),
    Table(
        "gold_model_relationships",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("workspace_name"),
            _c("from_object"),
            _c("to_object"),
            _c("multiplicity"),
            _c("used_size", "int64"),
            _c("max_from_cardinality", "int64"),
            _c("max_to_cardinality", "int64"),
            _c("missing_rows", "int64"),
        ],
        "VertiPaq relationships (cardinality, used size, missing rows).",
    ),
    Table(
        "gold_model_hierarchies",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("workspace_name"),
            _c("table_name"),
            _c("hierarchy_name"),
            _c("used_size", "int64"),
        ],
        "VertiPaq user hierarchies and their in-memory size.",
    ),
    Table(
        "gold_notebook_smells",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("rule_id"),
            _c("rule_description"),
            _c("severity"),
            _c("dimension"),
            _c("workspace_id"),
            _c("notebook_id"),
            _c("notebook_name"),
            _c("workspace_name"),
            _c("cells"),
            _c("notebook_url"),
        ],
        "Per-notebook code-smell matches (NBCODE rules). Explicit workspace/notebook "
        "IDs come only from analyzer evidence; legacy name-only evidence has blank IDs.",
    ),
    Table(
        "gold_graph_nodes",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("node_id"),
            _c("node_type"),
            _c("node_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("owner"),
            _c("status"),
            _c("status_rank", "int64"),
            _c("issue_count", "int64"),
            _c("critical_count", "int64"),
            _c("risk_score", "double"),
            _c("importance", "double"),
            _c("kpi_label"),
            _c("kpi_value"),
        ],
        "Every node in the Fabric estate graph (capacities, workspaces, models, "
        "reports, notebooks, pipelines, lakehouses, owners) with risk + status.",
    ),
    Table(
        "gold_graph_edges",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("edge_id"),
            _c("source_id"),
            _c("source_name"),
            _c("source_type"),
            _c("target_id"),
            _c("target_name"),
            _c("target_type"),
            _c("relationship"),
            _c("is_lineage", "boolean"),
        ],
        "Directed relationships between estate nodes (Capacity->Workspace, "
        "Workspace->Model/Notebook/Pipeline/Lakehouse, Model->Report, Owner->Workspace). "
        "is_lineage marks the curated data-lineage chain Capacity->Workspace->Model->Report.",
    ),
    Table(
        "gold_reports",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("report_id"),
            _c("report_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("status"),
            _c("risk_score", "double"),
            _c("issue_count", "int64"),
        ],
        "Power BI / Fabric reports in the estate, keyed to their workspace "
        "(backs the ontology Report entity).",
    ),
    Table(
        "gold_notebooks",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("notebook_id"),
            _c("notebook_name"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("status"),
            _c("risk_score", "double"),
            _c("issue_count", "int64"),
        ],
        "Notebooks in the estate, keyed to their workspace "
        "(backs the ontology Notebook entity).",
    ),
    Table(
        "gold_pipelines",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("pipeline_id"),
            _c("pipeline_name"),
            _c("workspace_id"),
            _c("workspace_name"),
        ],
        "Data pipelines in the estate, keyed to their workspace "
        "(backs the ontology Pipeline entity).",
    ),
    Table(
        "gold_lakehouses",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("lakehouse_id"),
            _c("lakehouse_name"),
            _c("workspace_id"),
            _c("workspace_name"),
        ],
        "Lakehouses in the estate, keyed to their workspace "
        "(backs the ontology Lakehouse entity).",
    ),
    Table(
        "gold_owners",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("owner_id"),
            _c("owner_name"),
        ],
        "Distinct workspace owners/admins seen in the estate "
        "(backs the ontology Owner entity).",
    ),
    Table(
        "gold_owner_edges",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("owner_id"),
            _c("owner_name"),
            _c("workspace_id"),
            _c("workspace_name"),
        ],
        "Owner->Workspace 'administers' edges "
        "(backs the ontology OwnerAdministersWorkspace relationship).",
    ),
    Table(
        "gold_lineage_edges",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("model_id"),
            _c("model_name"),
            _c("report_id"),
            _c("report_name"),
        ],
        "SemanticModel->Report 'feeds' lineage edges "
        "(backs the ontology ModelFeedsReport relationship).",
    ),
    Table(
        "gold_finding_targets",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("rule_id"),
            _c("title"),
            _c("dimension"),
            _c("severity"),
            _c("severity_rank", "int64"),
            _c("status"),
            _c("is_fail", "int64"),
            _c("workspace_id"),
            _c("workspace_name"),
        ],
        "Finding->Workspace edges (a finding's evidence workspace names reverse-"
        "mapped to workspace ids) - backs the ontology AffectsWorkspace relationship "
        "so you can walk from a workspace to the exact rules it triggers.",
    ),
    Table(
        "gold_workspace_risk",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("workspace_id"),
            _c("workspace_name"),
            _c("capacity_id"),
            _c("capacity_name"),
            _c("owner"),
            _c("item_count", "int64"),
            _c("semantic_model_count", "int64"),
            _c("report_count", "int64"),
            _c("notebook_count", "int64"),
            _c("pipeline_count", "int64"),
            _c("lakehouse_count", "int64"),
            _c("issue_count", "int64"),
            _c("critical_count", "int64"),
            _c("high_count", "int64"),
            _c("risk_score", "double"),
            _c("status"),
            _c("status_rank", "int64"),
        ],
        "Per-workspace risk roll-up: item mix, finding counts and a 0-100 risk score.",
    ),
    Table(
        "gold_severity_matrix",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("dimension"),
            _c("severity"),
            _c("severity_rank", "int64"),
            _c("status"),
            _c("issue_count", "int64"),
            _c("weighted_risk", "double"),
        ],
        "Dimension x severity grid of failing findings - backs the severity heatmap.",
    ),
    Table(
        "gold_bpa_violations",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("object_type"),
            _c("object_name"),
            _c("workspace_name"),
            _c("area"),
            _c("rule"),
            _c("severity"),
            _c("severity_rank", "int64"),
        ],
        "One row per individual Best Practice Analyzer / health violation "
        "(model BPA, report BPA, Direct Lake fallback, Delta, unused object).",
    ),
    Table(
        "gold_release",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("deployed_version"),
            _c("latest_version"),
            _c("update_available", "boolean"),
            _c("status"),
            _c("update_note"),
            _c("repo_url"),
            _c("branch"),
            _c("checked_at", "dateTime"),
        ],
        "One row per run - deployed FAR version vs latest published release; "
        "backs the report version banner / update notice.",
    ),
    Table(
        "gold_agent_eval",
        [
            _c("run_id"),
            _c("run_timestamp", "dateTime"),
            _c("case_id"),
            _c("category"),
            _c("question"),
            _c("expected"),
            _c("answer"),
            _c("passed", "int64"),
            _c("detail"),
        ],
        "One row per Data Agent evaluation case per run. The expected answer is "
        "computed deterministically from the gold tables (self-checking, no "
        "hand-maintained ground truth); 'passed'=1 when the agent's reply "
        "matches. Backs the agent-accuracy trend in the report.",
    ),
]


_EVIDENCE_REVIEW = [
    _c("run_id"), _c("run_timestamp", "dateTime"), _c("review_item_key"),
    _c("workspace_id"), _c("workspace_name"),
]

GOLD_TABLES.extend([
    Table("gold_execution_coverage", [
        *_EVIDENCE_REVIEW, _c("item_id"), _c("item_name"), _c("item_type"),
        _c("collection_status"), _c("observed_execution_count", "int64"),
        _c("oldest_start_time", "dateTime"), _c("newest_start_time", "dateTime"),
        _c("history_scope"), _c("notice"), _c("source"),
    ], "One collection-coverage observation per item and FAR review. Recent retained API "
       "history is not guaranteed to cover an entire weekly review interval."),
    Table("gold_item_executions", [
        *_EVIDENCE_REVIEW, _c("execution_key"), _c("execution_id"),
        _c("item_id"), _c("item_name"), _c("item_type"), _c("execution_type"),
        _c("status"), _c("start_time", "dateTime"), _c("end_time", "dateTime"),
        _c("duration_ms", "int64"), _c("source"),
    ], "One observed native refresh or job execution per item and FAR review. The same "
       "execution_key can recur across reviews; use its latest observation for historical "
       "status totals. Duration is milliseconds, not CU, cost or a static score."),
    Table("gold_dataflows", [
        *_EVIDENCE_REVIEW, _c("dataflow_id"), _c("dataflow_name"),
        _c("definition_status"), _c("query_count", "int64"),
        _c("flagged_query_count", "int64"), _c("notice"),
    ], "Dataflow Gen2 inventory and static definition coverage per review. Unsupported or "
       "unavailable definitions are evidence gaps, not clean dataflows."),
    Table("gold_dataflow_queries", [
        *_EVIDENCE_REVIEW, _c("dataflow_id"), _c("dataflow_name"),
        _c("query_name"), _c("signal_codes"), _c("signal_count", "int64"),
        _c("recommendation"), _c("notice"),
    ], "Metadata-only Power Query M signals per Dataflow Gen2 query and review. No M source, "
       "connection literals or customer rows. Signals do not prove folding or runtime impact."),
    Table("gold_dax_object_coverage", [
        *_EVIDENCE_REVIEW, _c("model_id"), _c("model_name"),
        _c("definition_status"), _c("object_count", "int64"),
        _c("flagged_object_count", "int64"), _c("notice"),
    ], "Non-measure DAX definition coverage per semantic model and review. Measures remain "
       "in gold_dax_models/gold_dax_measures; report visual calculations are not collected."),
    Table("gold_dax_objects", [
        *_EVIDENCE_REVIEW, _c("model_id"), _c("model_name"),
        _c("table_name"), _c("object_type"), _c("object_name"),
        _c("risk_level"), _c("risk_rank", "int64"), _c("risk_score", "int64"),
        _c("expression_length", "int64"), _c("signal_codes"), _c("signal_details_json"),
    ], "Typed calculated columns, calculated tables and calculation items per review. "
       "Static syntax signals only; no expressions and no measured runtime or CU impact."),
])

EVIDENCE_RELATIONSHIPS = {
    "gold_item_executions": "gold_execution_coverage",
    "gold_dataflow_queries": "gold_dataflows",
    "gold_dax_objects": "gold_dax_object_coverage",
}

GOLD_TABLES_BY_NAME: Dict[str, Table] = {t.name: t for t in GOLD_TABLES}

# Friendly severity ordering used across the builder and the model.
SEVERITY_RANK: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


def spark_type(kind: str) -> str:
    """Map a schema ``kind`` to a Spark SQL DDL type name."""
    return {
        "string": "string",
        "int64": "bigint",
        "double": "double",
        "dateTime": "timestamp",
        "boolean": "boolean",
    }[kind]


def tmdl_type(kind: str) -> str:
    """Map a schema ``kind`` to a TMDL ``dataType`` value."""
    return {
        "string": "string",
        "int64": "int64",
        "double": "double",
        "dateTime": "dateTime",
        "boolean": "boolean",
    }[kind]

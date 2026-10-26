# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Architecture-only curated contracts. Historical raw tables are not sources."""
from __future__ import annotations

import json
from typing import Any, Dict, List, NamedTuple


class Column(NamedTuple):
    name: str
    kind: str


class Table(NamedTuple):
    name: str
    columns: List[Column]
    description: str


def coerce_value(kind: str, value: Any, label: str = "value") -> Any:
    """Return ``value`` as the exact Python type Spark accepts for a column of ``kind``.

    Spark rejects an ``int`` in a double column and a ``float`` in a long column, so every
    row builder converts here instead of relying on how a number happened to be produced.
    """
    if value is None:
        return None
    if kind == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
        return float(value)
    if kind == "int64":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if not isinstance(value, int):
            raise TypeError(f"{label} must be an integer, got {type(value).__name__}.")
        return value
    if kind == "boolean":
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if not isinstance(value, bool):
            raise TypeError(f"{label} must be a boolean, got {type(value).__name__}.")
        return value
    if kind == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)
    return value


def _c(name: str, kind: str = "string") -> Column:
    return Column(name, kind)


_RUN = [_c("run_id"), _c("run_timestamp", "dateTime"), _c("profile_id"),
        _c("profile_sha256"), _c("configuration_sha256")]


def _table(name: str, columns: list[Column], description: str) -> Table:
    return Table(name, [*_RUN, *columns], description)


_COUNTS = [
    _c("total_findings", "int64"), _c("pass_count", "int64"), _c("fail_count", "int64"),
    _c("info_count", "int64"), _c("not_applicable_count", "int64"),
    _c("unknown_count", "int64"), _c("missing_evidence_count", "int64"),
    _c("assessed_count", "int64"), _c("assessment_coverage", "double"),
    _c("score", "double"), _c("score_status"),
]
_FINDING = [
    _c("rule_id"), _c("rule_kind"), _c("dimension"), _c("severity"),
    _c("severity_rank", "int64"), _c("status"), _c("is_fail", "int64"),
    _c("is_scored", "int64"), _c("title"), _c("recommendation"), _c("review_suggested"),
]
_WORKSPACE = [_c("workspace_id"), _c("workspace_name")]
_ITEM = [*_WORKSPACE, _c("item_id"), _c("item_name"), _c("item_type")]

GOLD_TABLES = [
    _table("gold_findings", [
        *_FINDING, _c("client_name"), _c("engagement_name"), _c("reviewer_name"),
        _c("rule_description"), _c("microsoft_learn_url"), _c("affected"), _c("workspace_name"),
        _c("evidence_json"), _c("reason_code"), _c("status_meaning"), _c("next_action"),
        _c("why_it_matters"), _c("how_to_fix"), _c("configure"),
    ], "Active architectural controls, sanitized evidence, advisory and coverage decisions, with what each result means and what to do next."),
    _table("gold_rules", [
        _c("rule_id"), _c("title"), _c("dimension"), _c("rule_kind"), _c("severity"), _c("is_scored", "int64"),
        _c("what_it_checks"), _c("why_it_matters"), _c("how_to_fix"), _c("configure"), _c("requires"),
        _c("microsoft_learn_url"),
    ], "The rule catalogue: what each rule checks, why it matters, how to fix it and what it needs."),
    _table("gold_run_summary", [
        *_COUNTS, _c("client_name"), _c("engagement_name"), _c("reviewer_name"),
        *[_c(f"{severity}_fail", "int64") for severity in ("critical", "high", "medium", "low")],
        _c("is_latest", "boolean"),
    ], "Profile-specific assessed controls; blank score means no assessable controls."),
    _table("gold_dimension_summary", [
        _c("dimension"), *_COUNTS, _c("total", "int64"), _c("worst_severity"),
    ], "Architecture family assessment with advisory/coverage excluded from failure scoring."),
    _table("gold_workspaces", [
        *_WORKSPACE, _c("capacity_id"), _c("on_capacity", "boolean"),
        _c("item_count", "int64"), _c("admin_count", "int64"), _c("description"),
        _c("environment"), _c("profile_reason"),
        _c("item_type_counts_json"),
    ], "Workspace boundary and declared context; identities, not names, establish joins."),
    _table("gold_capacities", [
        _c("capacity_id"), _c("capacity_name"), _c("sku"), _c("kind"),
        _c("is_dedicated", "boolean"), _c("state"), _c("region"), _c("admin_count", "int64"),
        _c("assigned_workspace_count", "int64"), _c("observed_workspace_count", "int64"),
        _c("observed_item_count", "int64"), _c("workspace_scope_limited", "boolean"),
        _c("fca_region"), _c("fca_resource_name"), _c("tag_names"), _c("missing_tags"),
    ], "Static hosting metadata and, when FCA is connected, the Azure region and tags of the capacity resource; not financial or utilization evidence."),
    _table("gold_capacity_items", [
        _c("capacity_id"), _c("capacity_name"), _c("sku"), *_ITEM,
        _c("workspace_scope_limited", "boolean"),
    ], "Immutable item and hosting identities within the observed scope."),
    _table("gold_tenant_settings", [
        _c("setting_name"), _c("title"), _c("setting_group"), _c("rule_id"), _c("status"), _c("posture"),
        _c("enabled", "boolean"), _c("is_scoped", "boolean"),
        _c("enabled_group_count", "int64"), _c("excluded_group_count", "int64"),
        _c("can_specify_security_groups", "boolean"),
        _c("last_changed_at", "dateTime"), _c("change_count", "int64"), _c("history_source"),
    ], "Every tenant setting recorded for the run, the control it feeds, and its change history when FUAM is connected."),
    _table("gold_semantic_models", [
        *_WORKSPACE, _c("model_id"), _c("model_name"), _c("storage_mode"),
        _c("rls_status"), _c("ols_status"), _c("security_inspection"),
    ], "Storage-design inventory and, from the model definition, whether it defines row-level (RLS) and "
       "object-level (OLS) security: present, absent or unknown. A requirement or outcome is never stored here."),
    _table("gold_model_security", [
        *_WORKSPACE, _c("capacity_id"), _c("capacity_name"), _c("model_id"), _c("model_name"),
        _c("rls_required", "boolean"), _c("ols_required", "boolean"),
        _c("rls_status"), _c("ols_status"), _c("rls_outcome"), _c("ols_outcome"),
        _c("security_inspection"), _c("inspection_reason"), _c("rls_source"),
        _c("role_count", "int64"), _c("reader_role_count", "int64"), _c("rls_filter_count", "int64"),
        _c("ols_table_count", "int64"), _c("ols_column_count", "int64"), _c("roles_without_row_filters", "boolean"),
        _c("role_detail"),
    ], "Per semantic model: RLS and OLS present, absent or unknown; whether you declared each as required and the "
       "outcome (not_required, pass, fail, missing_evidence). Counts only; no role, filter, table or column is stored. "
       "Unknown is never a pass. role_detail says whether the roles of this model are listed in gold_model_security_roles "
       "(collected, partial, unavailable or not_collected)."),
    _table("gold_model_security_roles", [
        *_WORKSPACE, _c("capacity_id"), _c("capacity_name"), _c("model_id"), _c("model_name"),
        _c("role_name"), _c("role_permission"), _c("reads_data", "boolean"), _c("filter_kind"),
        _c("row_filter_table_count", "int64"), _c("dynamic_filter_table_count", "int64"),
        _c("constant_filter_table_count", "int64"), _c("ols_table_count", "int64"), _c("ols_column_count", "int64"),
    ], "One row per role of a semantic model, only when model_security__role_detail is on: the role name, its permission "
       "(read, readRefresh, refresh, administrator), how many tables carry a row filter (and how many of those use the "
       "user's identity), how many tables and columns it hides, and filter_kind (none, constant, static, dynamic, mixed, "
       "unknown, not_applicable). Filter text, table and column names and members are never stored. A configured role is "
       "not proof that it is correct or that anyone is in it."),
    *[_table(table, [*_WORKSPACE, _c(f"{kind}_id"), _c(f"{kind}_name"), *([_c("model_id")] if kind == "report" else [])],
             "Static architectural item inventory.")
      for table, kind in (("gold_reports", "report"), ("gold_notebooks", "notebook"),
                          ("gold_pipelines", "pipeline"), ("gold_lakehouses", "lakehouse"))],
    _table("gold_finding_targets", [*_FINDING, *_WORKSPACE, _c("reason_code"), _c("status_meaning"),
                                    _c("next_action"), _c("why_it_matters"), _c("how_to_fix"), _c("configure")],
           "Explicit ID-scoped architectural finding attribution, with what each result means and what to do next."),
    _table("gold_finding_items", [*_FINDING, *_ITEM, _c("reason")],
           "The pipelines, lakehouses, models and other items a finding points at, by name and type, and why."),
    _table("gold_workspace_risk", [
        *_WORKSPACE, _c("capacity_id"), _c("capacity_name"), _c("owner"),
        _c("item_count", "int64"),
        *[_c(f"{kind}_count", "int64") for kind in ("semantic_model", "report", "notebook", "pipeline", "lakehouse")],
        _c("issue_count", "int64"), _c("critical_count", "int64"), _c("high_count", "int64"),
        _c("risk_score", "double"), _c("status"), _c("status_rank", "int64"),
    ], "Confirmed architectural failures only; no failures does not establish coverage."),
    _table("gold_graph_nodes", [
        _c("node_id"), _c("node_type"), _c("node_name"), *_WORKSPACE,
        _c("capacity_id"), _c("capacity_name"), _c("owner"), _c("status"),
        _c("status_rank", "int64"), _c("issue_count", "int64"),
        _c("critical_count", "int64"), _c("risk_score", "double"),
        _c("importance", "double"), _c("kpi_label"), _c("kpi_value"),
    ], "Review-consistent architectural estate identities."),
    _table("gold_graph_edges", [
        _c("edge_id"), _c("source_id"), _c("source_name"), _c("source_type"),
        _c("target_id"), _c("target_name"), _c("target_type"),
        _c("relationship"), _c("is_lineage", "boolean"),
    ], "Explicit static hosting, access and dependency relationships."),
    _table("gold_lineage_edges", [
        _c("model_id"), _c("model_name"), _c("report_id"), _c("report_name"),
    ], "Verified model-to-report dependencies within a review."),
    _table("gold_owners", [_c("owner_id"), _c("owner_name")],
           "Observed principals; never an owner entitlement source."),
    _table("gold_owner_edges", [_c("owner_id"), _c("owner_name"), *_WORKSPACE],
           "Observed access topology; never grants report access."),
    _table("gold_source_coverage", [
        *_WORKSPACE, _c("evidence_family"), _c("collection_status"),
        _c("source_module"), _c("source_contract"), _c("source_table"),
        _c("source_workspace_id"), _c("source_lakehouse_id"),
        _c("observed_at", "dateTime"), _c("freshness_status"),
        _c("notice"), _c("next_action"),
    ], "Typed architectural evidence coverage and provenance; empty is distinct from denied."),
    _table("gold_severity_matrix", [
        _c("dimension"), _c("severity"), _c("severity_rank", "int64"),
        _c("status"), _c("issue_count", "int64"), _c("weighted_risk", "double"),
    ], "Confirmed architectural failures only."),
    _table("gold_release", [
        _c("deployed_version"), _c("latest_version"), _c("update_available", "boolean"),
        _c("status"), _c("update_note"), _c("repo_url"), _c("branch"), _c("checked_at", "dateTime"),
    ], "Deployment version context."),
]

# Single-column keys for the estate ontology. A table holds one row per entity per run, so an ID is not
# unique across runs; each key joins the run to the ID(s) it is built from and is empty when any part is.
ONTOLOGY_KEYS: Dict[str, Dict[str, tuple]] = {
    "gold_capacities": {"capacity_key": ("capacity_id",)},
    "gold_workspaces": {"workspace_key": ("workspace_id",), "capacity_key": ("capacity_id",)},
    "gold_semantic_models": {"model_key": ("model_id",), "workspace_key": ("workspace_id",)},
    # One row per model per run on the one side; its roles on the many side. Both rows carry the key.
    "gold_model_security": {"model_key": ("model_id",)},
    "gold_model_security_roles": {"model_key": ("model_id",)},
    "gold_reports": {"report_key": ("report_id",), "workspace_key": ("workspace_id",), "model_key": ("model_id",)},
    "gold_notebooks": {"notebook_key": ("notebook_id",), "workspace_key": ("workspace_id",)},
    "gold_pipelines": {"pipeline_key": ("pipeline_id",), "workspace_key": ("workspace_id",)},
    "gold_lakehouses": {"lakehouse_key": ("lakehouse_id",), "workspace_key": ("workspace_id",)},
    "gold_owners": {"owner_key": ("owner_id",)},
    "gold_owner_edges": {"assignment_key": ("owner_id", "workspace_id"), "owner_key": ("owner_id",),
                         "workspace_key": ("workspace_id",)},
    "gold_rules": {"rule_key": ("rule_id",)},
}


KEY_COLUMNS = frozenset(key for keys in ONTOLOGY_KEYS.values() for key in keys)

# Tables whose key a model relationship uses as its one side. Rows written before the key existed are blank, and a
# blank on the one side makes every query that crosses the relationship fail, so setup fills them in.
BACKFILLED_KEY_TABLES = ("gold_model_security",)


def key_backfill(table: str) -> list[tuple[str, str, str]]:
    """(key, condition, value) in Spark SQL that fill a key on rows written before it existed.

    The value is what ``ontology_key`` writes today, and a row whose key cannot be built stays blank, exactly as a new
    row would. Rows that already have the key are never changed.
    """
    if table not in BACKFILLED_KEY_TABLES:
        return []
    result = []
    for key, parts in ONTOLOGY_KEYS[table].items():
        present = " AND ".join(f"{column} IS NOT NULL AND {column} <> ''" for column in ("run_id", *parts))
        result.append((key, f"{key} IS NULL AND {present}", "concat_ws('|', " + ", ".join(("run_id", *parts)) + ")"))
    return result


def ontology_key(run_id: Any, *parts: Any) -> str | None:
    values = [str(part) for part in parts]
    return "|".join([str(run_id), *values]) if run_id and all(values) and all(p is not None for p in parts) else None


GOLD_TABLES = [
    Table(t.name, [*t.columns, *[Column(key, "string") for key in ONTOLOGY_KEYS.get(t.name, {})]], t.description)
    for t in GOLD_TABLES
]
GOLD_TABLES_BY_NAME: Dict[str, Table] = {t.name: t for t in GOLD_TABLES}
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

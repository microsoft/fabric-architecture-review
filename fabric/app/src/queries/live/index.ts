// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { activeArchitectureRules, architectureProfile, architectureProfileHash } from "@/lib/review-data";
function query(table: string, columns: string[], rules = false) {
    const active = [...activeArchitectureRules].map(r => `"${r}"`).join(", ");
    const predicate = `'${table}'[profile_id] = "${architectureProfile}" && '${table}'[profile_sha256] = "${architectureProfileHash}"` +
        (rules ? ` && '${table}'[rule_id] IN {${active}}` : "");
    return `EVALUATE SELECTCOLUMNS(FILTER('${table}', ${predicate}), ${columns.map(c => `"${c}", '${table}'[${c}]`).join(", ")})`;
}
const provenance = ["run_id", "run_timestamp", "profile_id", "profile_sha256", "configuration_sha256"];
export const liveReviewQueries = {
    connection: "reviewModel",
    findings: query("gold_findings", [...provenance, "rule_id", "rule_kind", "dimension", "severity", "status", "is_scored", "title", "affected", "workspace_name", "recommendation", "status_meaning", "next_action", "why_it_matters", "how_to_fix", "configure"], true),
    runSummary: query("gold_run_summary", [...provenance, "score", "score_status", "assessed_count", "total_findings", "fail_count", "unknown_count",
        "missing_evidence_count", "assessment_coverage", "critical_fail", "high_fail", "engagement_name"]),
    dimensions: query("gold_dimension_summary", [...provenance, "dimension", "total_findings", "fail_count", "assessed_count", "score"]),
    workspaces: query("gold_workspaces", [...provenance, "workspace_id", "capacity_id", "environment", "admin_count"]),
    capacityItems: query("gold_capacity_items", [...provenance, "capacity_id", "workspace_id", "workspace_name", "item_id", "item_name", "item_type"]),
    tenantSettings: query("gold_tenant_settings", [...provenance, "setting_name", "title", "setting_group", "rule_id", "status", "posture", "enabled", "change_count"]),
    semanticModels: query("gold_semantic_models", [...provenance, "workspace_id", "model_id", "model_name", "storage_mode"]),
    workspaceRisk: query("gold_workspace_risk", [...provenance, "workspace_id", "workspace_name", "capacity_name", "risk_score", "issue_count", "status"]),
    coverage: query("gold_source_coverage", [...provenance, "workspace_id", "workspace_name", "evidence_family", "collection_status", "source_contract", "observed_at", "freshness_status", "notice"]),
    findingItems: query("gold_finding_items", [...provenance, "workspace_id", "rule_id", "item_id", "item_name", "item_type", "reason"], true),
    capacities: query("gold_capacities", [...provenance, "capacity_id", "capacity_name", "sku", "kind", "state", "region", "assigned_workspace_count", "observed_workspace_count", "observed_item_count", "workspace_scope_limited", "admin_count", "missing_tags"]),
    // Optional: a model deployed before model-security existed has no such table, and the app must keep working.
    modelSecurity: query("gold_model_security", [...provenance, "workspace_id", "workspace_name", "capacity_name", "model_id", "model_name", "rls_status", "ols_status", "rls_required", "ols_required", "rls_outcome", "ols_outcome", "security_inspection", "inspection_reason", "role_detail"]),
    // Optional as well: it holds rows only when role detail is enabled, and an older model has no such table.
    modelRoles: query("gold_model_security_roles", [...provenance, "model_id", "role_name", "role_permission", "reads_data", "filter_kind", "row_filter_table_count", "dynamic_filter_table_count", "constant_filter_table_count", "ols_table_count", "ols_column_count"]),
} as const;

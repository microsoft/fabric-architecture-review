# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Allowlisted owner-report columns, separate from governance Gold.

No tenant score, capacity metadata, source expressions, or mixed evidence is
part of this contract. Access is a separately refreshed, expiring snapshot.
"""
from reports.powerbi.schema import Column, Table

OWNER_CONTRACT_VERSION = 2
DEFAULT_OWNER_MODEL_NAME = "Fabric Arch Review - Workspace Owner Model"
DEFAULT_OWNER_REPORT_NAME = "Fabric Arch Review - Workspace Owner"


def _c(name: str, kind: str = "string") -> Column:
    return Column(name, kind)


_REVIEW = [
    _c("review_key"), _c("workspace_id"), _c("run_id"),
    _c("run_timestamp", "dateTime"),
]

OWNER_TABLES = [
    Table("owner_workspaces", [
        _c("workspace_id"), _c("workspace_name"),
    ], "Unique reviewed workspace IDs; names are display labels, never attribution keys."),
    Table("owner_reviews", [
        *_REVIEW, _c("workspace_name"), _c("is_latest", "boolean"),
        _c("owner_fail_count", "int64"), _c("owner_finding_count", "int64"),
        _c("owner_detail_count", "int64"), _c("technical_detail_count", "int64"),
        _c("assessment_status"), _c("technical_evidence_status"), _c("notice"),
    ], "Partial workspace-scoped review, never a full FAR score. Persistence sets latest per workspace."),
    Table("owner_findings", [
        *_REVIEW, _c("finding_key"), _c("rule_id"), _c("dimension"),
        _c("severity"), _c("status"), _c("is_fail", "int64"),
        _c("item_id"), _c("item_name"), _c("item_type"),
        _c("title"), _c("recommendation"), _c("signal_codes"),
        _c("affected_count", "int64"), _c("notice"),
    ], "Reconstructed technical findings and native failures, not copies of multi-workspace governance findings."),
    Table("owner_details", [
        *_REVIEW, _c("detail_key"), _c("detail_type"),
        _c("item_id"), _c("item_name"), _c("item_type"),
        _c("table_name"), _c("measure_name"), _c("signal_codes"),
        _c("metric_name"), _c("metric_value", "int64"), _c("detail"),
        _c("notice"),
    ], "Approved per-item counters and pattern codes; no source or freeform evidence."),
    Table("owner_access", [
        _c("workspace_id"), _c("principal_object_id"),
        _c("refreshed_at", "dateTime"), _c("expires_at", "dateTime"),
    ], "Current direct-user workspace-admin entitlements, replaced by independent access sync."),
    Table("owner_executions", [
        *_REVIEW, _c("execution_key"), _c("execution_id"),
        _c("item_id"), _c("item_name"), _c("item_type"),
        _c("execution_type"), _c("status"),
        _c("start_time", "dateTime"), _c("end_time", "dateTime"),
        _c("duration_ms", "int64"), _c("source"), _c("notice"),
    ], "Native observed runs, not DAX query timings or consumption. Keys are stable across reviews."),
    Table("owner_coverage", [
        *_REVIEW, _c("coverage_key"), _c("item_id"), _c("item_name"),
        _c("item_type"), _c("evidence_type"), _c("collection_status"),
        _c("observed_count", "int64"), _c("flagged_count", "int64"),
        _c("oldest_start_time", "dateTime"), _c("newest_start_time", "dateTime"),
        _c("history_scope"), _c("source"), _c("notice"),
    ], "Explicit collection scope and incomplete evidence; zero rows never implies success."),
]

OWNER_TABLES_BY_NAME = {table.name: table for table in OWNER_TABLES}

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Allowlisted owner-report columns, separate from governance Gold.

No tenant score, capacity metadata, source expressions, or mixed evidence is
part of this contract. Access is a separately refreshed, expiring snapshot.
"""
from reports.powerbi.schema import Column, Table

OWNER_CONTRACT_VERSION = 3
def _c(name: str, kind: str = "string") -> Column:
    return Column(name, kind)


_REVIEW = [
    _c("review_key"), _c("workspace_id"), _c("run_id"),
    _c("run_timestamp", "dateTime"),
    _c("profile_id"), _c("profile_sha256"), _c("configuration_sha256"),
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
        _c("rule_kind"), _c("is_scored", "int64"), _c("review_suggested"),
        _c("status_meaning"), _c("next_action"), _c("why_it_matters"), _c("how_to_fix"), _c("configure"),
    ], "Explicitly ID-scoped authoritative architectural outcomes; no heuristic re-scoring."),
    Table("owner_details", [
        *_REVIEW, _c("detail_key"), _c("detail_type"),
        _c("item_id"), _c("item_name"), _c("item_type"),
        _c("signal_codes"), _c("detail"),
        _c("notice"),
    ], "Approved per-item counters and pattern codes; no source or freeform evidence."),
    Table("owner_access", [
        _c("workspace_id"), _c("principal_object_id"),
        _c("refreshed_at", "dateTime"), _c("expires_at", "dateTime"),
    ], "Current direct-user workspace-admin entitlements, replaced by independent access sync."),
    Table("owner_coverage", [
        *_REVIEW, _c("coverage_key"), _c("item_id"), _c("item_name"),
        _c("item_type"), _c("evidence_type"), _c("collection_status"),
        _c("source"), _c("source_contract"), _c("observed_at", "dateTime"),
        _c("freshness_status"), _c("notice"),
    ], "Explicit collection scope and incomplete evidence; zero rows never implies success."),
]

OWNER_TABLES_BY_NAME = {table.name: table for table in OWNER_TABLES}

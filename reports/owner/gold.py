# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Fail-closed ID-scoped architecture projection, independent of entitlement sync."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from analyzers.far_profile import ACTIVE_RULE_IDS, NON_SCORED_KINDS, PROFILE_ID, PROFILE_SHA256, RULE_KINDS
from reports.owner.schema import OWNER_TABLES_BY_NAME
from reports.powerbi.schema import coerce_value

PARTIAL_NOTICE = (
    "Partial assessment: only findings attributed to a workspace ID are included; tenant-wide findings "
    "are excluded. Zero failures does not establish complete coverage."
)
STATIC_NOTICE = "Architectural metadata only; heuristic signals need confirmation. Source and secrets are withheld."
def _guid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Owner attribution requires explicit GUIDs.") from None


def _is_guid(value) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _label(value, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid owner metadata label.")
    if required and not value.strip():
        raise ValueError("Missing owner metadata label.")
    return value


def _run(row: dict) -> tuple[str, str]:
    run_id = row.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("Owner projection requires a run identity.")
    value = row.get("run_timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise ValueError
        return run_id, stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        raise ValueError("Owner projection requires timezone-aware review timestamps.") from None


def _key(*parts) -> str:
    return sha256("|".join(map(str, parts)).encode()).hexdigest()


def build_owner_gold(governance_tables: dict[str, list[dict]]) -> dict[str, list[dict]]:
    if not isinstance(governance_tables, dict):
        raise ValueError("Owner projection requires a table dictionary.")
    output = {name: [] for name in OWNER_TABLES_BY_NAME if name != "owner_access"}
    def rows(name: str) -> list[dict]:
        value = governance_tables.get(name) or []
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError(f"Invalid {name} rows.")
        return [row for row in value if row.get("profile_id") == PROFILE_ID
                and row.get("profile_sha256") == PROFILE_SHA256 and row.get("configuration_sha256")]
    workspaces = rows("gold_workspaces")
    runs = {_run(row) for row in workspaces + rows("gold_run_summary")}
    if len(runs) > 1:
        raise ValueError("Owner projection requires one consistent review run.")
    if not runs:
        return output
    run_id, stamp = runs.pop()
    names = {}
    for row in workspaces:
        wid, name = _guid(row.get("workspace_id")), _label(row.get("workspace_name"), required=True)
        if wid in names and names[wid] != name:
            raise ValueError("Ambiguous workspace identity.")
        names[wid] = name
    def scope(row: dict) -> str | None:
        if not row.get("workspace_id"):
            return None
        wid = _guid(row["workspace_id"])
        if wid not in names:
            return None
        if _run(row) != (run_id, stamp):
            raise ValueError("Owner evidence belongs to a different review.")
        if (row.get("profile_sha256"), row.get("configuration_sha256")) != (profile_hash, config_hash):
            raise ValueError("Owner evidence has conflicting profile/configuration versions.")
        return wid
    versions = {(row.get("profile_sha256"), row.get("configuration_sha256"))
                for row in workspaces + rows("gold_run_summary")}
    if len(versions) > 1:
        raise ValueError("Owner evidence has conflicting profile/configuration versions.")
    profile_hash, config_hash = next(iter(versions), (None, None))
    def meta(wid: str) -> dict:
        return {"review_key": f"{run_id}:{wid}", "workspace_id": wid, "run_id": run_id,
                "run_timestamp": stamp, "profile_id": PROFILE_ID,
                "profile_sha256": profile_hash, "configuration_sha256": config_hash}
    emitted = {}
    def emit(table: str, key: str, record: dict) -> None:
        record = {
            c.name: coerce_value(c.kind, record.get(c.name), f"{table}.{c.name}")
            for c in OWNER_TABLES_BY_NAME[table].columns
        }
        if (table, key) in emitted:
            if emitted[(table, key)] != record:
                raise ValueError("Conflicting duplicate owner facts.")
            return
        emitted[(table, key)] = record
        output[table].append(record)
    identities = {}
    for table, id_field, name_field, kind in (
        ("gold_semantic_models", "model_id", "model_name", "SemanticModel"),
        ("gold_notebooks", "notebook_id", "notebook_name", "Notebook"),
        ("gold_pipelines", "pipeline_id", "pipeline_name", "DataPipeline"),
        ("gold_reports", "report_id", "report_name", "Report"),
        ("gold_lakehouses", "lakehouse_id", "lakehouse_name", "Lakehouse"),
    ):
        for row in rows(table):
            wid = scope(row)
            if wid is None:
                continue
            iid = _guid(row.get(id_field))
            identity = (wid, kind, _label(row.get(name_field)) or iid)
            if iid in identities and identities[iid] != identity:
                raise ValueError("Ambiguous item identity.")
            identities[iid] = identity
            key = _key(run_id, wid, iid, "inventory")
            detail = "Static architectural item inventory."
            if table == "gold_semantic_models":
                mode = row.get("storage_mode")
                detail = "Storage mode: " + (mode if mode in {"Import", "DirectLake", "DirectQuery", "Composite", "Abf"} else "Unknown")
            emit("owner_details", key, {**meta(wid), "detail_key": key, "detail_type": "item_inventory",
                 "item_id": iid, "item_name": identity[2], "item_type": kind,
                 "detail": detail, "notice": STATIC_NOTICE})
    states = {"present", "absent", "unknown"}
    for row in rows("gold_model_security"):
        wid = scope(row)
        if wid is None:
            continue
        iid = _guid(row.get("model_id"))
        rls, ols = (row.get(key) if row.get(key) in states else "unknown" for key in ("rls_status", "ols_status"))
        inspection = row.get("security_inspection") if row.get("security_inspection") in {
            "complete", "partial", "unavailable"} else "unavailable"
        key = _key(run_id, wid, iid, "model_security")
        emit("owner_details", key, {**meta(wid), "detail_key": key, "detail_type": "model_security",
             "item_id": iid, "item_name": _label(row.get("model_name")) or iid, "item_type": "SemanticModel",
             "signal_codes": "SEC-026",
             "detail": f"Row-level security: {rls}; object-level security: {ols}; inspection: {inspection}",
             "notice": STATIC_NOTICE})
    # Role detail exists only when the organization enabled it; the role name is the one new label stored.
    for row in rows("gold_model_security_roles"):
        wid = scope(row)
        if wid is None:
            continue
        iid = _guid(row.get("model_id"))
        role = _label(row.get("role_name"), required=True)
        count = lambda column: row.get(column) if isinstance(row.get(column), int) and not isinstance(row.get(column), bool) else 0
        key = _key(run_id, wid, iid, "model_security_role", role)
        emit("owner_details", key, {**meta(wid), "detail_key": key, "detail_type": "model_security_role",
             "item_id": iid, "item_name": _label(row.get("model_name")) or iid, "item_type": "SemanticModel",
             "signal_codes": "SEC-026",
             "detail": f"Role {role}: permission {row.get('role_permission') or 'unknown'}; "
                       f"tables with a row filter {count('row_filter_table_count')} "
                       f"({count('dynamic_filter_table_count')} use the user's identity); "
                       f"hidden tables {count('ols_table_count')}; hidden columns {count('ols_column_count')}; "
                       f"filter {row.get('filter_kind') or 'unknown'}",
             "notice": STATIC_NOTICE})
    for row in rows("gold_finding_targets"):
        wid = scope(row)
        rule = row.get("rule_id")
        if wid is None or rule not in ACTIVE_RULE_IDS:
            continue
        kind = RULE_KINDS[rule]
        status = row.get("status") if row.get("status") in {"pass", "fail", "info", "unknown", "missing_evidence", "not_applicable"} else "unknown"
        if kind in NON_SCORED_KINDS and status in {"pass", "fail"}:
            status = "info"
        key = _key(run_id, wid, rule)
        scored = row.get("is_scored") in (True, 1) and kind not in NON_SCORED_KINDS and status in {"pass", "fail"}
        emit("owner_findings", key, {**meta(wid), "finding_key": key, "rule_id": rule,
             "rule_kind": kind, "dimension": row.get("dimension"),
             "severity": row.get("severity"), "status": status,
             "is_scored": int(scored),
             "is_fail": int(scored and status == "fail"),
             # Titles and recommendations are the rule's static wording; evidence is never copied.
             "title": row.get("title") or f"Architectural control {rule}: {status}",
             "recommendation": row.get("recommendation")
             or "Open the approved architecture control guidance and validate the declared policy with your reviewer.",
             "review_suggested": row.get("review_suggested"),
             "status_meaning": row.get("status_meaning"), "next_action": row.get("next_action"),
             "why_it_matters": row.get("why_it_matters"), "how_to_fix": row.get("how_to_fix"),
             "configure": row.get("configure"),
             "notice": PARTIAL_NOTICE})
    for row in rows("gold_finding_items"):
        wid = scope(row)
        rule = row.get("rule_id")
        if wid is None or rule not in ACTIVE_RULE_IDS or not _is_guid(row.get("item_id")):
            continue
        iid = _guid(row["item_id"])
        key = _key(run_id, wid, iid, rule, "finding_item")
        # The reason is the rule's static wording; the evidence itself is never copied.
        emit("owner_details", key, {**meta(wid), "detail_key": key, "detail_type": "finding_item", "item_id": iid,
             "item_name": _label(row.get("item_name")) or iid, "item_type": _label(row.get("item_type")),
             "signal_codes": rule, "detail": f"{rule}: {_label(row.get('reason'))}", "notice": STATIC_NOTICE})
    for row in rows("gold_source_coverage"):
        wid = scope(row)
        if wid is None:
            continue
        family = _label(row.get("evidence_family"))
        if any(term in family.lower() for term in ("execution", "dax", "vertipaq", "billing", "cost", "utilization")):
            continue
        key = _key(run_id, wid, family, row.get("source_contract"))
        emit("owner_coverage", key, {**meta(wid), "coverage_key": key,
             "evidence_type": family, "collection_status": row.get("collection_status"),
             "source": row.get("source_module"), "source_contract": row.get("source_contract"),
             "observed_at": row.get("observed_at"), "freshness_status": row.get("freshness_status"),
             "notice": PARTIAL_NOTICE})
    for wid, name in names.items():
        output["owner_workspaces"].append({"workspace_id": wid, "workspace_name": name})
        own_findings = [r for r in output["owner_findings"] if r["workspace_id"] == wid]
        details = [r for r in output["owner_details"] if r["workspace_id"] == wid]
        output["owner_reviews"].append({**meta(wid), "workspace_name": name, "is_latest": True,
             "owner_fail_count": sum(r["is_fail"] for r in own_findings),
             "owner_finding_count": len(own_findings), "owner_detail_count": len(details),
             "technical_detail_count": len(details), "assessment_status": "partial",
             "technical_evidence_status": "partial" if details else "missing", "notice": PARTIAL_NOTICE})
    return output

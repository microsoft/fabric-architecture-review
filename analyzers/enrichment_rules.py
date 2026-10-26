# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Rules that read FUAM or FCA data.

They run only when the module is connected and its data is fresh. Otherwise they are not
applicable or report missing evidence; they never fall back to collecting the data from the
APIs, and they never turn a gap into a pass.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from analyzers.far_profile import ENRICHMENT_SOURCES
from analyzers.rule_support import Context, listed

Result = tuple[str, dict[str, Any]]
_SIGNAL = {"assessment_kind": "non_scored_context"}


def _facts(rule_id: str, family: str, ctx: Context) -> tuple[Result | None, dict[str, Any] | None]:
    """The collected facts of a family, or the result that explains why there are none."""
    source = ENRICHMENT_SOURCES[rule_id]
    base = {"required_source": source, "assessment_kind": "enrichment"}
    if not ctx.configuration["sources"][source]["enabled"]:
        return ("not_applicable", {**base, "reason_code": "source_not_enabled",
                                   "reason": f"Evaluated only when {source.upper()} is connected."}), None
    entry = ((ctx.enrichment or {}).get("rule_facts") or {}).get(family)
    if entry is None:
        return ("missing_evidence", {**base, "reason_code": "enrichment_unavailable"}), None
    base.update(source_table=entry.get("table"), observed_at=entry.get("observed_at"),
                freshness_status=entry.get("freshness_status"))
    if entry.get("status") != "collected":
        return ("missing_evidence", {**base, "reason_code": entry.get("reason_code") or "family_unavailable"}), None
    if entry.get("freshness_status") == "stale":
        return ("missing_evidence", {**base, "reason_code": "source_data_stale",
                                     "max_age_hours": ctx.configuration["sources"][source]["max_age_hours"]}), None
    return None, {**entry, "_base": base}


def _rows_for(entry: dict[str, Any], key: str, value: str) -> list[dict[str, Any]]:
    return [row for row in entry["rows"] if str(row.get(key) or "").lower() == str(value).lower()]


def _days_since(timestamp: str, now: datetime) -> int:
    return (now - datetime.fromisoformat(timestamp)).days


def inactive_workspace(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("GOV-007", "workspace_activity", ctx)
    if gap is not None:
        return gap
    days = effective["thresholds"]["inactive_workspace_days"]
    now = datetime.fromisoformat((ctx.enrichment or {})["captured_at_utc"])
    earliest = entry.get("earliest_at")
    covered = earliest is not None and _days_since(earliest, now) >= days
    row = next(iter(_rows_for(entry, "workspace_id", ws["id"])), None)
    last = row["last_activity_at"] if row else None
    base = {**entry["_base"], **_SIGNAL, "inactive_after_days": days, "history_start": earliest}
    if last is None:
        if not covered:
            return "missing_evidence", {**base, "reason_code": "activity_history_shorter_than_window"}
        return "info", {**base, "last_activity_at": None, "review_recommended": True,
                        "limitations": "No activity recorded in the history FUAM holds."}
    since = _days_since(last, now)
    return "info", {**base, "last_activity_at": last, "days_since_activity": since,
                    "review_recommended": since > days and covered,
                    "limitations": "Activity events only; a workspace used through a scheduled job is still active."}


def stale_items(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("GOV-008", "item_age", ctx)
    if gap is not None:
        return gap
    row = next(iter(_rows_for(entry, "workspace_id", ws["id"])), None)
    base = {**entry["_base"], **_SIGNAL, "stale_after_days": effective["thresholds"]["stale_item_days"]}
    if row is None or not row["dated_item_count"]:
        return "not_applicable", {**base, "reason": "FUAM holds no dated items for this workspace."}
    ratio = row["stale_item_count"] / row["dated_item_count"]
    maximum = effective["thresholds"]["stale_item_max_ratio"]
    return "info", {**base, "item_count": row["dated_item_count"], "stale_item_count": row["stale_item_count"],
                    "stale_item_ids": listed(row.get("stale_item_ids") or []), "stale_ratio": ratio, "review_above": maximum, "review_recommended": ratio > maximum,
                    "limitations": "The last modification date, not whether anyone still uses the item."}


def domain_coverage(effective: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("GOV-006", "domains", ctx)
    if gap is not None:
        return gap
    rows = entry["rows"]
    if not rows:
        return "missing_evidence", {**entry["_base"], "reason_code": "workspaces_not_in_source"}
    assigned = sum(bool(row["domain_id"]) for row in rows)
    ratio = assigned / len(rows)
    minimum = effective["thresholds"]["domain_coverage_min_ratio"]
    return "info", {**entry["_base"], **_SIGNAL, "workspace_count": len(rows), "assigned_workspace_count": assigned,
                    "domain_ratio": ratio, "review_below": minimum, "review_recommended": ratio < minimum,
                    "reviewed_workspace_count": len(ctx.workspaces)}


def failure_ratio(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("OPS-002", "item_operations", ctx)
    if gap is not None:
        return gap
    minimum = effective["thresholds"]["failure_min_events"]
    maximum = effective["thresholds"]["failure_max_ratio"]
    judged = [row for row in _rows_for(entry, "workspace_id", ws["id"]) if row["operation_count"] >= minimum]
    flagged = [row for row in judged if row["failure_count"] / row["operation_count"] > maximum]
    worst = max(judged, key=lambda row: row["failure_count"] / row["operation_count"], default=None)
    return "info", {**entry["_base"], **_SIGNAL, "window_days": entry["window_days"],
                    "judged_item_count": len(judged), "flagged_item_count": len(flagged),
                    "failing_item_ids": listed(row["item_id"] for row in flagged),
                    "review_above": maximum, "minimum_operations": minimum,
                    "worst_item_id": worst["item_id"] if worst else None,
                    "worst_failure_ratio": worst["failure_count"] / worst["operation_count"] if worst else None,
                    "review_recommended": bool(flagged),
                    "limitations": "Failed share of capacity operations per item, not individual job runs."}


def expensive_items(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, items_entry = _facts("OPS-003", "item_operations", ctx)
    if gap is not None:
        return gap
    gap, totals = _facts("OPS-003", "capacity_totals", ctx)
    if gap is not None:
        return gap
    capacity_total = {row["capacity_id"]: row["total_cus"] for row in totals["rows"]}
    maximum = effective["thresholds"]["expensive_item_cu_max_ratio"]
    shares = []
    for row in _rows_for(items_entry, "workspace_id", ws["id"]):
        total = capacity_total.get(row["capacity_id"])
        if total and total > 0:
            shares.append((row["total_cus"] / total, row))
    base = {**items_entry["_base"], **_SIGNAL, "window_days": items_entry["window_days"], "review_above": maximum}
    if not shares:
        return "info", {**base, "judged_item_count": 0, "review_recommended": False}
    share, top = max(shares, key=lambda pair: pair[0])
    return "info", {**base, "judged_item_count": len(shares), "top_item_id": top["item_id"],
                    "heavy_item_ids": listed(row["item_id"] for fraction, row in shares if fraction > maximum),
                    "top_item_kind": top["item_kind"], "top_item_capacity_share": share,
                    "review_recommended": share > maximum,
                    "limitations": "Share of the capacity's compute over the window; a heavy item can be intended."}


def workspace_capacity_share(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, items_entry = _facts("OPS-007", "item_operations", ctx)
    if gap is not None:
        return gap
    gap, totals = _facts("OPS-007", "capacity_totals", ctx)
    if gap is not None:
        return gap
    used: dict[str, float] = {}
    for row in _rows_for(items_entry, "workspace_id", ws["id"]):
        if row["capacity_id"]:
            used[row["capacity_id"]] = used.get(row["capacity_id"], 0.0) + row["total_cus"]
    capacity_total = {row["capacity_id"]: row["total_cus"] for row in totals["rows"]}
    shares = [(cu / capacity_total[cap], cap) for cap, cu in used.items() if capacity_total.get(cap)]
    maximum = effective["thresholds"]["workspace_cu_max_ratio"]
    base = {**items_entry["_base"], **_SIGNAL, "window_days": items_entry["window_days"], "review_above": maximum}
    if not shares:
        return "info", {**base, "workspace_cu_share": None, "review_recommended": False}
    share, capacity = max(shares)
    hosted = {str(other.get("id") or "").lower() for other in ctx.workspaces
              if str(other.get("capacityId") or "").lower() == capacity}
    assigned = next((row.get("assignedWorkspaceCount") for row in (ctx.raw.get("capacity_metrics.json") or {}).get("capacities") or []
                     if isinstance(row, dict) and str(row.get("id") or "").lower() == capacity), None)
    alone = max(len(hosted), assigned if isinstance(assigned, int) else 0) <= 1
    return "info", {**base, "workspace_cu_share": share, "capacity_id": capacity, "alone_on_capacity": alone,
                    "review_recommended": share > maximum and not alone,
                    "limitations": "Share of the capacity's compute over the window. A workspace that is alone on its capacity "
                                   "is not a noisy neighbour, so it is reported without a recommendation."}


def capacity_throttling(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("OPS-004", "capacity_throttling", ctx)
    if gap is not None:
        return gap
    capacity = str(ws.get("capacityId") or "")
    if not capacity:
        return "not_applicable", {**entry["_base"], **_SIGNAL, "reason": "The workspace is not on a capacity."}
    row = next(iter(_rows_for(entry, "capacity_id", capacity)), None)
    if row is None or not row["observed_days"]:
        return "missing_evidence", {**entry["_base"], **_SIGNAL, "reason_code": "capacity_not_in_source"}
    ratio = row["throttled_days"] / row["observed_days"]
    maximum = effective["thresholds"]["throttled_day_max_ratio"]
    return "info", {**entry["_base"], **_SIGNAL, "window_days": entry["window_days"],
                    "observed_days": row["observed_days"], "throttled_days": row["throttled_days"],
                    "throttled_day_ratio": ratio, "review_above": maximum, "review_recommended": ratio > maximum,
                    "limitations": "About the capacity, not one workspace: one finding per capacity, and every workspace on it points to it."}


def refresh_failures(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("OPS-005", "refresh_health", ctx)
    if gap is not None:
        return gap
    minimum = effective["thresholds"]["refresh_min_events"]
    maximum = effective["thresholds"]["refresh_failure_max_ratio"]
    rows = _rows_for(entry, "workspace_id", ws["id"])
    judged = [row for row in rows if row["refresh_count"] >= minimum]
    flagged = [row for row in judged if row["failure_count"] / row["refresh_count"] > maximum]
    worst = max(judged, key=lambda row: row["failure_count"] / row["refresh_count"], default=None)
    return "info", {**entry["_base"], **_SIGNAL, "window_days": entry["window_days"],
                    "refreshed_item_count": len(rows), "judged_item_count": len(judged),
                    "flagged_item_count": len(flagged), "refresh_failing_item_ids": listed(row["item_id"] for row in flagged),
                    "review_above": maximum, "minimum_refreshes": minimum,
                    "worst_item_id": worst["item_id"] if worst else None,
                    "worst_failure_ratio": worst["failure_count"] / worst["refresh_count"] if worst else None,
                    "review_recommended": bool(flagged),
                    "limitations": "Completed and failed refreshes recorded by FUAM; cancelled or unknown outcomes are not counted."}


def org_wide_links(ws: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("SEC-019", "org_links", ctx)
    if gap is not None:
        # Like publish to web: an empty transfer is not proof of "no links", but a tenant that
        # disallows organization-wide links cannot hold new ones.
        if gap[1].get("reason_code") == "no_transfer_snapshot" and _setting_disabled(ctx, "ShareLinkToEntireOrg"):
            evidence = {key: value for key, value in gap[1].items() if key != "reason_code"}
            return "info", {**evidence, **_SIGNAL, "org_link_item_count": 0, "review_recommended": False,
                            "reason": "Organization-wide sharing links are disabled for the tenant.",
                            "limitations": "FUAM holds no transfer of organization-wide links; the tenant setting is the evidence."}
        return gap
    found = _rows_for(entry, "workspace_id", ws["id"])
    return "info", {**entry["_base"], **_SIGNAL, "org_link_item_count": len(found),
                    "linked_item_ids": sorted(row["item_id"] for row in found),
                    "linked_artifact_types": sorted({row["artifact_type"] for row in found if row["artifact_type"]}),
                    "snapshot_at": entry.get("snapshot_at"), "review_recommended": bool(found),
                    "limitations": "Items shared with a link that anyone in the organization can open, in the latest FUAM transfer."}


def _setting_disabled(ctx: Context, name: str) -> bool:
    settings = (ctx.raw.get("tenant_settings.json") or {}).get("tenantSettings")
    setting = next((row for row in settings if isinstance(row, dict) and row.get("settingName") == name),
                   None) if isinstance(settings, list) else None
    return bool(setting) and setting.get("enabled") is False


def _publish_to_web_disabled(ctx: Context) -> bool:
    return _setting_disabled(ctx, "PublishToWeb")


def published_to_web(ws: dict[str, Any], ctx: Context) -> Result:
    gap, entry = _facts("SEC-006", "publish_to_web", ctx)
    if gap is not None:
        # An empty FUAM table cannot be told apart from a transfer that never ran. When the tenant
        # setting disables publish to web, no item can be public, which is evidence of its own.
        if gap[1].get("reason_code") == "no_transfer_snapshot" and _publish_to_web_disabled(ctx):
            evidence = {key: value for key, value in gap[1].items() if key != "reason_code"}
            return "pass", {**evidence, "assessment_kind": "published_items", "published_item_count": 0,
                            "reason": "Publish to web is disabled for the tenant, so no item can be public.",
                            "limitations": "FUAM holds no published-to-web transfer; the tenant setting is the evidence."}
        return gap
    found = _rows_for(entry, "workspace_id", ws["id"])
    return ("fail" if found else "pass"), {
        **entry["_base"], "assessment_kind": "published_items", "published_item_count": len(found),
        "published_item_ids": sorted(row["item_id"] for row in found), "snapshot_at": entry.get("snapshot_at"),
        "reason": "Items published to the public web by link." if found else "No item is published to web.",
        "limitations": "The most recent FUAM transfer of published-to-web items.",
    }


def capacity_tags(effective: dict[str, Any], ctx: Context) -> Result:
    required = (effective["policy"] or {}).get("required_tag_names")
    if not required:
        return "not_applicable", {**_SIGNAL, "required_source": "fca", "reason_code": "no_required_tags_declared",
                                  "reason": "Declare policy.required_capacity_tags to evaluate capacity tags."}
    gap, entry = _facts("GOV-009", "capacity_tags", ctx)
    if gap is not None:
        return gap
    wanted = {name.casefold() for name in required}
    rows = entry["rows"]
    if not rows:
        return "missing_evidence", {**entry["_base"], "reason_code": "capacity_resource_bridge_missing",
                                    "reason": "Map capacities in scope.capacity_arm_resource_ids."}
    missing = [row["capacity_id"] for row in rows if not wanted <= {tag.casefold() for tag in row["tag_names"]}]
    return "info", {**entry["_base"], **_SIGNAL, "capacity_count": len(rows), "capacities_missing_tags": len(missing),
                    "required_tag_names": sorted(required), "review_recommended": bool(missing),
                    "limitations": "Only capacities mapped to an Azure resource are checked; tag values are discarded."}

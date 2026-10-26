# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Architecture rules evaluated from the review's own inventory, scanner and capacity metadata."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from analyzers.rule_support import (
    Context, declared_environment, item_inventory_complete, items, listed, optional_facts, rows as _rows, scanner_workspace,
)
from analyzers.structural_contracts import _pipeline_json_from_parts, _walk_activities

Result = tuple[str, dict[str, Any]]

LEGACY_ITEM_TYPES = frozenset({"Datamart"})
# Activities that call out to a service and can fail or hang; control-flow activities cannot.
EXECUTION_ACTIVITY_TYPES = frozenset({
    "copy", "tridentnotebook", "executenotebook", "refreshdataflow", "sparkjobdefinition", "script",
    "sqlserverstoredprocedure", "lookup", "getmetadata", "delete", "webactivity", "executepipeline",
    "invokepipeline",
})
DEFAULT_ACTIVITY_TIMEOUT = "0.12:00:00"
ENDORSED = frozenset({"Promoted", "Certified"})
LABELLED_COLLECTIONS = ("datasets", "reports", "dashboards", "dataflows")
SIGNAL_RULES = frozenset({"ARCH-010", "ARCH-011", "ARCH-012", "SEC-011"})
_SIGNAL = {"assessment_kind": "non_scored_context"}


def _normalize_region(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def orphaned_state(ws: dict[str, Any]) -> Result:
    state = ws.get("state")
    if not isinstance(state, str) or not state:
        return "missing_evidence", {
            "reason_code": "workspace_state_unavailable",
            "reason": "Workspace state is returned by the administrator inventory only.",
        }
    evidence = {"assessment_kind": "workspace_state", "workspace_state": state}
    if state == "Orphaned":
        return "fail", {**evidence, "reason": "The workspace has no administrator."}
    if state == "Active":
        return "pass", evidence
    return "unknown", {**evidence, "reason_code": "workspace_state_unsupported"}


def capacity_isolation(ws: dict[str, Any], ctx: Context) -> Result:
    capacity = str(ws.get("capacityId") or "").lower()
    if not capacity:
        return "not_applicable", {"reason_code": "no_dedicated_capacity",
                                  "reason": "The workspace is not assigned to a capacity."}
    declared = {entry["id"].lower(): entry for entry in ctx.configuration["profiles"].get("capacities", [])}
    capacity_environment = (declared.get(capacity, {}).get("profile") or {}).get("environment")
    sharing = sorted(
        str(other["id"]).lower() for other in ctx.workspaces
        if str(other.get("id") or "").lower() != str(ws["id"]).lower()
        and str(other.get("capacityId") or "").lower() == capacity
        and declared_environment(ctx, other) == "nonproduction"
    )
    violations = []
    if capacity_environment == "nonproduction":
        violations.append("capacity_declared_nonproduction")
    if sharing:
        violations.append("shares_capacity_with_nonproduction_workspace")
    return ("fail" if violations else "pass"), {
        "assessment_kind": "declared_environment_isolation", "signal_codes": violations,
        "nonproduction_workspace_count": len(sharing), "capacity_declared_environment": capacity_environment,
        "limitations": "Compared with the declared environments of the workspaces in this review's scope only.",
    }


def capacity_region(ws: dict[str, Any], policy: dict[str, Any], ctx: Context) -> Result:
    if "allowed_regions" not in policy:
        return "unknown", {"reason_code": "policy_incomplete",
                           "reason": "The requirement is enabled but does not declare allowed_regions."}
    capacity = str(ws.get("capacityId") or "").lower()
    if not capacity:
        return "missing_evidence", {"reason_code": "hosting_identity_unavailable"}
    metrics = ctx.raw.get("capacity_metrics.json") or {}
    rows = metrics.get("capacities")
    region = next((row.get("region") for row in rows if isinstance(row, dict)
                   and str(row.get("id") or "").lower() == capacity), None) if isinstance(rows, list) else None
    if not region:
        return "missing_evidence", {"reason_code": "capacity_region_unavailable"}
    allowed = {_normalize_region(value) for value in policy["allowed_regions"]}
    return ("pass" if _normalize_region(region) in allowed else "fail"), {
        "assessment_kind": "declared_region_boundary", "capacity_region": region,
        "limitations": "The region of the capacity, not where every item's data is stored.",
    }


def _production_only(ws: dict[str, Any], ctx: Context) -> Result | None:
    environment = declared_environment(ctx, ws)
    if environment == "unknown":
        return "not_applicable", {**_SIGNAL, "reason_code": "environment_undeclared",
                                  "reason": "Evaluated for declared production workspaces; production is not inferred."}
    if environment != "production":
        return "not_applicable", {**_SIGNAL, "reason": "Evaluated for declared production workspaces only."}
    return None


def _scan(ws: dict[str, Any], ctx: Context) -> tuple[dict[str, Any] | None, Result | None]:
    scanned = scanner_workspace(ctx, ws["id"])
    if scanned is None:
        return None, ("missing_evidence", {**_SIGNAL, "reason_code": "scanner_unavailable",
                                          "reason": "The administrator scan did not complete or did not return this workspace."})
    return scanned, None


def endorsement(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    models = [row for row in scanned.get("datasets") or [] if isinstance(row, dict)]
    if not models:
        return "not_applicable", {**_SIGNAL, "reason": "No semantic models in the workspace."}
    endorsed = sum((row.get("endorsementDetails") or {}).get("endorsement") in ENDORSED for row in models)
    ratio = endorsed / len(models)
    minimum = effective["thresholds"]["endorsement_min_ratio"]
    return "info", {**_SIGNAL, "model_count": len(models), "endorsed_model_count": endorsed,
                    "unendorsed_model_ids": listed(row.get("id") for row in models
                                                   if (row.get("endorsementDetails") or {}).get("endorsement") not in ENDORSED),
                    "endorsement_ratio": ratio, "review_below": minimum, "review_recommended": ratio < minimum,
                    "limitations": "Endorsement is a governance signal, not a statement about quality."}


def label_coverage(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    entities = [row for name in LABELLED_COLLECTIONS for row in scanned.get(name) or [] if isinstance(row, dict)]
    if not entities:
        return "not_applicable", {**_SIGNAL, "reason": "No labelable items in the workspace."}
    labelled = sum(bool(row.get("sensitivityLabel")) for row in entities)
    ratio = labelled / len(entities)
    minimum = effective["thresholds"]["label_min_ratio"]
    return "info", {**_SIGNAL, "item_count": len(entities), "labelled_item_count": labelled,
                    "unlabelled_item_ids": listed(row.get("id") or row.get("objectId") for row in entities
                                                  if not row.get("sensitivityLabel")),
                    "label_ratio": ratio, "review_below": minimum, "review_recommended": ratio < minimum,
                    "limitations": "Labels on semantic models, reports, dashboards and dataflows; label content is not read."}


def _upstream_workspaces(scanned: dict[str, Any], home: str) -> list[tuple[str, set[str]]]:
    """The semantic models and dataflows that read from another workspace, with the workspaces they read from."""
    dependents: list[tuple[str, set[str]]] = []
    for collection, key in (("datasets", "upstreamDatasets"), ("dataflows", "upstreamDataflows")):
        for row in scanned.get(collection) or []:
            if not isinstance(row, dict):
                continue
            groups = {str(link.get("groupId") or "").lower() for link in row.get(key) or [] if isinstance(link, dict)}
            outside = {group for group in groups if group and group != home}
            if outside:
                dependents.append((str(row.get("id") or row.get("objectId") or ""), outside))
    return dependents


def cross_workspace(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    dependents = _upstream_workspaces(scanned, str(ws["id"]).lower())
    upstream = set().union(*(groups for _, groups in dependents))
    maximum = effective["thresholds"]["cross_workspace_dependency_max"]
    return "info", {**_SIGNAL, "dependent_item_count": len(dependents),
                    "dependent_item_ids": listed(item for item, _ in dependents), "upstream_workspace_count": len(upstream),
                    "review_above": maximum, "review_recommended": len(upstream) > maximum,
                    "limitations": "Upstream semantic models and dataflows only; sharing a workspace boundary is sometimes intended."}


def legacy_items(ws: dict[str, Any]) -> Result:
    if not item_inventory_complete(ws):
        return "missing_evidence", {**_SIGNAL, "reason_code": "item_inventory_incomplete"}
    legacy = [item for item in items(ws) if item["type"] in LEGACY_ITEM_TYPES]
    found = sorted(item["type"] for item in legacy)
    return "info", {**_SIGNAL, "legacy_item_count": len(found), "legacy_item_types": sorted(set(found)),
                    "legacy_item_ids": listed(item["id"] for item in legacy),
                    "review_recommended": bool(found)}


def open_tenant_settings(names: tuple[str, ...], settings: list[dict[str, Any]]) -> Result:
    """Which of a group of tenant settings are enabled for the whole organization."""
    by_name = {row.get("settingName"): row for row in settings if isinstance(row, dict)}
    open_names: list[str] = []
    limited = disabled = fixed = 0
    not_returned: list[str] = []
    unreadable: list[str] = []
    for name in names:
        setting = by_name.get(name)
        if setting is None:
            not_returned.append(name)
        elif not isinstance(setting.get("enabled"), bool) or not isinstance(setting.get("enabledSecurityGroupCount"), int):
            unreadable.append(name)
        elif not setting["enabled"]:
            disabled += 1
        elif setting["enabledSecurityGroupCount"] > 0:
            limited += 1
        elif setting.get("canSpecifySecurityGroups"):
            open_names.append(name)
        else:
            fixed += 1
    judged = len(names) - len(not_returned) - len(unreadable)
    if not judged:
        return "missing_evidence", {**_SIGNAL, "reason_code": "setting_not_returned",
                                    "setting_names": sorted(names), "reason": "The tenant returned none of these settings."}
    return "info", {**_SIGNAL, "setting_count": len(names), "judged_setting_count": judged,
                    "open_setting_count": len(open_names), "open_setting_names": sorted(open_names),
                    "limited_setting_count": limited, "disabled_setting_count": disabled,
                    "not_limitable_setting_count": fixed, "not_returned_setting_names": sorted(not_returned),
                    "unreadable_setting_names": sorted(unreadable), "review_recommended": bool(open_names),
                    "limitations": "Settings enabled for everyone that can be limited to groups; whether open is intended is your decision."}


_ROLE_RANK = {"admin": 4, "member": 3, "contributor": 2, "viewer": 1}
_ELEVATED = frozenset({"admin", "member"})


def _principals(ws: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]] | None:
    """Distinct principals as (kind, entry, highest role); None when membership was not collected."""
    users = ws.get("users")
    if ws.get("usersCollectionStatus") != "collected" or not isinstance(users, list):
        return None
    distinct: dict[tuple[str, str], tuple[str, dict[str, Any], str]] = {}
    for index, user in enumerate(users):
        if not isinstance(user, dict):
            continue
        kind = str(user.get("principalType") or user.get("type") or "").lower()
        kind = "app" if kind == "serviceprincipal" else kind
        identity = user.get("graphId") or user.get("identifier") or user.get("emailAddress")
        key = (kind, str(identity).lower() if identity else f"anonymous:{index}")
        role = str(user.get("groupUserAccessRight") or user.get("role") or "").lower()
        previous = distinct.get(key)
        if previous is None or _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(previous[2], 0):
            distinct[key] = (kind, user, role)
    return list(distinct.values())


def _is_guest(user: dict[str, Any]) -> bool:
    if str(user.get("userType") or "").lower() == "guest":
        return True
    return any("#ext#" in str(user.get(field) or "").lower() for field in ("identifier", "emailAddress"))


def guest_members(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    principals = _principals(ws)
    if principals is None:
        return "missing_evidence", {**_SIGNAL, "reason_code": "workspace_principals_unavailable"}
    guests = [(user, role) for kind, user, role in principals if kind == "user" and _is_guest(user)]
    elevated = sum(role in _ELEVATED for _, role in guests)
    return "info", {**_SIGNAL, "principal_count": len(principals), "guest_principal_count": len(guests),
                    "guest_elevated_role_count": elevated, "review_recommended": bool(guests),
                    "limitations": "Guests with a direct role only; guests inside a group are not expanded. No identity is stored."}


def app_roles(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    principals = _principals(ws)
    if principals is None:
        return "missing_evidence", {**_SIGNAL, "reason_code": "workspace_principals_unavailable"}
    apps = [role for kind, _, role in principals if kind == "app"]
    elevated = sum(role in _ELEVATED for role in apps)
    return "info", {**_SIGNAL, "principal_count": len(principals), "app_principal_count": len(apps),
                    "app_elevated_role_count": elevated, "review_recommended": bool(elevated),
                    "limitations": "Applications with a direct Admin or Member role; an automation that needs write access can be intended."}


NON_PRODUCTION_CLASSES = {"trial": "Trial", "ppu": "Premium per user", "embedded": "Embedded (A or EM)"}


def _capacity_class(sku: Any, per_user: Any) -> str:
    if isinstance(sku, dict):
        sku = sku.get("name")
    text = str(sku or "").strip().upper()
    if per_user is True or text in {"PP3", "PPU"}:
        return "ppu"
    if text.startswith("FT") or "TRIAL" in text:
        return "trial"
    if re.fullmatch(r"(A|EM)\d+", text):
        return "embedded"
    return "standard" if text else "unknown"


def capacity_grade(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    capacity = str(ws.get("capacityId") or "").lower()
    if not capacity:
        return "not_applicable", {**_SIGNAL, "reason_code": "no_dedicated_capacity",
                                  "reason": "The workspace is not assigned to a capacity."}
    sku = ws.get("capacitySku")
    if not sku:
        rows = (ctx.raw.get("capacity_metrics.json") or {}).get("capacities")
        sku = next((row.get("sku") for row in rows if isinstance(row, dict)
                    and str(row.get("id") or "").lower() == capacity), None) if isinstance(rows, list) else None
    kind = _capacity_class(sku, ws.get("isOnPremiumPerUserCapacity"))
    if kind == "unknown":
        return "missing_evidence", {**_SIGNAL, "reason_code": "capacity_sku_unavailable"}
    return "info", {**_SIGNAL, "capacity_class": kind, "capacity_sku": sku if isinstance(sku, str) else None,
                    "review_recommended": kind in NON_PRODUCTION_CLASSES,
                    "limitations": "Trial is recognised by the FT SKU family. Premium per user workspaces can be outside the review scope."}


def _scan_all(ctx: Context) -> list[dict[str, Any]] | None:
    scan = ctx.raw.get("scanner.json") or {}
    if scan.get("collectionComplete") is not True or not isinstance(scan.get("workspaces"), list):
        return None
    return [row for row in scan["workspaces"] if isinstance(row, dict)]


def empty_workspace(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if not item_inventory_complete(ws):
        return "missing_evidence", {**_SIGNAL, "reason_code": "item_inventory_incomplete"}
    count = len(items(ws))
    return "info", {**_SIGNAL, "item_count": count, "review_recommended": count == 0,
                    "limitations": "Items visible to the administrator inventory; a workspace can be reserved on purpose."}


def orphan_reports(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    in_scan = {str(model.get("id") or "").lower() for other in _scan_all(ctx) or []
               for model in other.get("datasets") or [] if isinstance(model, dict)}
    # Paginated reports have no semantic model, so only Power BI reports can be orphaned.
    reports = [row for row in scanned.get("reports") or []
               if isinstance(row, dict) and row.get("reportType") in (None, "PowerBIReport")]
    if not reports:
        return "not_applicable", {**_SIGNAL, "reason": "No Power BI reports in the workspace."}
    lookup, lookup_gap = optional_facts(ctx, "model_lookup")
    elsewhere = {str(row["model_id"]).lower() for row in lookup["rows"]} if lookup else set()
    no_model, outside, orphans, unresolved = [], [], [], []
    for row in reports:
        model = str(row.get("datasetId") or "").lower()
        report = str(row.get("id"))
        if not model:
            no_model.append(report)
        elif model in in_scan:
            continue
        elif model in elsewhere:
            outside.append(report)
        elif lookup:
            orphans.append(report)
        else:
            unresolved.append(report)
    missing = sorted(no_model + orphans)
    return "info", {**_SIGNAL, "report_count": len(reports), "orphan_report_count": len(missing),
                    "orphan_report_ids": missing, "bound_outside_scope_count": len(outside),
                    "unresolved_report_count": len(unresolved), "unresolved_report_ids": sorted(unresolved),
                    "lookup_checked": lookup is not None, "lookup_unavailable_reason": lookup_gap,
                    "review_recommended": bool(missing),
                    "limitations": "A report with no model, or whose model FUAM does not hold anywhere in the tenant, is counted as an "
                                   "orphan. Without a fresh FUAM, a model outside the scan cannot be told from a missing one and is "
                                   "listed as unresolved instead."}


_DATA_STORE_TYPES = frozenset({"Lakehouse", "Warehouse", "SQLEndpoint", "SQLAnalyticsEndpoint", "MirroredDatabase"})


def unused_models(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    models = [row for row in scanned.get("datasets") or [] if isinstance(row, dict) and row.get("id")]
    if not models:
        return "not_applicable", {**_SIGNAL, "reason": "No semantic models in the workspace."}
    used: set[str] = set()
    for other in _scan_all(ctx) or []:
        used |= {str(report["datasetId"]).lower() for report in other.get("reports") or []
                 if isinstance(report, dict) and report.get("datasetId")}
        for model in other.get("datasets") or []:
            if isinstance(model, dict):
                used |= {str(link.get("targetDatasetId")).lower() for link in model.get("upstreamDatasets") or []
                         if isinstance(link, dict) and link.get("targetDatasetId")}
    # A data store's default semantic model carries its name and is created without any report.
    stores = {str(item.get("displayName") or item.get("name") or "").casefold() for item in items(ws)
              if item.get("type") in _DATA_STORE_TYPES}
    defaults = [row for row in models if str(row.get("name") or "").casefold() in stores]
    candidates = [row for row in models if row not in defaults and str(row["id"]).lower() not in used]
    usage, usage_gap = optional_facts(ctx, "model_usage")
    queries = {str(row["item_id"]).lower(): row["query_count"] for row in usage["rows"]} if usage else {}
    on_capacity = bool(ws.get("capacityId"))
    ids = lambda rows: sorted(str(row["id"]) for row in rows)
    base = {**_SIGNAL, "model_count": len(models), "default_model_count": len(defaults),
            "usage_checked": usage is not None, "usage_unavailable_reason": usage_gap,
            "no_report_model_count": len(candidates)}
    if usage is None:
        return "info", {**base, "unused_model_count": len(candidates), "unused_model_ids": ids(candidates),
                        "review_recommended": bool(candidates),
                        "limitations": "Reports and models in the administrator scan only. Excel, Analyze in Excel and reports outside the "
                                       "review scope are not seen; connect a fresh FUAM to count queries as use."}
    measurable = [row for row in candidates if on_capacity]
    unused = [row for row in measurable if queries.get(str(row["id"]).lower(), 0) == 0]
    queried = [row for row in measurable if queries.get(str(row["id"]).lower(), 0) > 0]
    unconfirmed = [row for row in candidates if not on_capacity]
    return "info", {**base, "window_days": usage["window_days"], "unused_model_count": len(unused),
                    "unused_model_ids": ids(unused), "queried_without_report_count": len(queried),
                    "unconfirmed_model_count": len(unconfirmed), "unconfirmed_model_ids": ids(unconfirmed),
                    "review_recommended": bool(unused),
                    "limitations": "No report or model uses it and FUAM recorded no query or XMLA read in the window. Models on a "
                                   "capacity only: usage of a model that is not on a capacity is not recorded."}

def _capacity_row(ws: dict[str, Any], ctx: Context) -> tuple[dict[str, Any] | None, Result | None]:
    """The inventory row of the workspace's capacity, or the result that explains why there is none."""
    capacity = str(ws.get("capacityId") or "").lower()
    if not capacity:
        return None, ("not_applicable", {**_SIGNAL, "reason_code": "no_dedicated_capacity",
                                         "reason": "The workspace is not assigned to a capacity."})
    metrics = ctx.raw.get("capacity_metrics.json") or {}
    rows = metrics.get("capacities")
    if not isinstance(rows, list):
        return None, ("missing_evidence", {**_SIGNAL, "reason_code": "capacity_inventory_unavailable"})
    row = next((r for r in rows if isinstance(r, dict) and str(r.get("id") or "").lower() == capacity), None)
    if row is None:
        return None, ("missing_evidence", {**_SIGNAL, "reason_code": "capacity_not_in_source"})
    return row, None


def capacity_state(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    row, gap = _capacity_row(ws, ctx)
    if gap is not None:
        return gap
    state = row.get("state")
    if not isinstance(state, str) or not state:
        return "missing_evidence", {**_SIGNAL, "reason_code": "capacity_state_unavailable"}
    return "info", {**_SIGNAL, "capacity_state": state, "capacity_sku": row.get("sku"),
                    "review_recommended": state != "Active",
                    "limitations": "The state when the inventory was read. A capacity that is paused on a schedule can be on purpose. "
                                   "One finding per capacity; every workspace on it points to it."}


def capacity_admins(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    row, gap = _capacity_row(ws, ctx)
    if gap is not None:
        return gap
    admins = row.get("admins")
    if not isinstance(admins, list):
        return "missing_evidence", {**_SIGNAL, "reason_code": "capacity_admins_unavailable"}
    minimum = effective["thresholds"]["min_capacity_admins"]
    return "info", {**_SIGNAL, "capacity_admin_count": len(admins), "review_below": minimum,
                    "review_recommended": len(admins) < minimum,
                    "limitations": "Administrators listed on the capacity; no identity is stored. One finding per capacity; every "
                                   "workspace on it points to it."}


def deployment_pipeline(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    data = ctx.raw.get("deployment_pipelines.json") or {}
    if data.get("collectionComplete") is not True or not isinstance(data.get("pipelines"), list):
        return "missing_evidence", {**_SIGNAL, "reason_code": "deployment_pipelines_unavailable"}
    home = str(ws["id"]).lower()
    stages = [(pipeline, stage) for pipeline in data["pipelines"] if isinstance(pipeline, dict)
              for stage in pipeline.get("stages") or []
              if isinstance(stage, dict) and str(stage.get("workspaceId") or "").lower() == home]
    seen = data.get("visibility") == "tenant"
    if not stages and not seen:
        return "missing_evidence", {**_SIGNAL, "reason_code": "pipelines_partly_visible",
                                    "reason": "Only the pipelines the reviewer can see were read, so a missing one proves nothing."}
    return "info", {**_SIGNAL, "in_deployment_pipeline": bool(stages), "stage_orders": sorted({str(s.get("order")) for _, s in stages}),
                    "pipeline_count": len({p.get("id") for p, _ in stages}), "review_recommended": not stages,
                    "limitations": "Source control and release automation outside deployment pipelines are not seen, so a workspace "
                                   "outside a pipeline can be released another way."}


def onelake_roles(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    if not item_inventory_complete(ws):
        return "missing_evidence", {**_SIGNAL, "reason_code": "item_inventory_incomplete"}
    lakehouses = {str(item["id"]).lower() for item in items(ws) if item.get("type") == "Lakehouse"}
    if not lakehouses:
        return "not_applicable", {**_SIGNAL, "reason": "No lakehouses in the workspace."}
    data = ctx.raw.get("onelake_roles.json") or {}
    if not isinstance(data.get("lakehouses"), list):
        return "missing_evidence", {**_SIGNAL, "reason_code": "onelake_roles_unavailable"}
    found = {str(row.get("id") or "").lower(): row for row in data["lakehouses"]
             if isinstance(row, dict) and str(row.get("workspaceId") or "").lower() == str(ws["id"]).lower()}
    unread = [item for item in lakehouses if (found.get(item) or {}).get("status") != "collected"]
    if unread:
        return "missing_evidence", {**_SIGNAL, "reason_code": "onelake_roles_unavailable", "lakehouse_count": len(lakehouses),
                                    "unread_lakehouse_count": len(unread)}
    bare = sorted(item for item in lakehouses if found[item].get("customRoleCount") == 0)
    without = len(bare)
    return "info", {**_SIGNAL, "lakehouse_count": len(lakehouses), "lakehouses_without_custom_roles": without,
                    "lakehouse_ids_without_custom_roles": bare,
                    "custom_role_count": sum(int(found[item].get("customRoleCount") or 0) for item in lakehouses),
                    "review_recommended": without > 0,
                    "limitations": "Whether OneLake data access roles are defined, not what they allow. A lakehouse whose data every "
                                   "workspace member may read can be on purpose."}


def workspace_network(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    data = ctx.raw.get("workspace_network.json") or {}
    if not isinstance(data.get("workspaces"), list):
        return "missing_evidence", {**_SIGNAL, "reason_code": "workspace_network_unavailable"}
    row = next((r for r in data["workspaces"] if isinstance(r, dict)
                and str(r.get("workspaceId") or "").lower() == str(ws["id"]).lower()), None)
    if row is None or row.get("status") != "collected":
        return "missing_evidence", {**_SIGNAL, "reason_code": "workspace_network_unavailable"}
    required = [d for d in (effective["policy"] or {}).get("restricted_directions") or [] if d in {"inbound", "outbound"}]
    open_directions = [d for d in required if row.get(d) == "Allow"]
    rules, broad = row.get("inboundFirewallRuleCount"), row.get("inboundFirewallBroadRangeCount")
    endpoints, protection = row.get("managedPrivateEndpointCount"), row.get("outboundProtection")
    # Contradictions in what is configured need no declared requirement: the setting itself does not do what it implies.
    codes = []
    if row["inbound"] == "Allow" and rules:
        codes.append("firewall_rules_not_enforced")
    if broad:
        codes.append("firewall_range_too_broad")
    if row["outbound"] == "Allow" and endpoints:
        codes.append("outbound_open_with_private_endpoints")
    if "outbound" in open_directions and protection == "disabled":
        codes.append("outbound_protection_disabled")
    return "info", {**_SIGNAL, "inbound_public_access": row["inbound"], "outbound_public_access": row["outbound"],
                    "inbound_firewall_rule_count": rules, "inbound_firewall_broad_range_count": broad,
                    "outbound_protection": protection, "managed_private_endpoint_count": endpoints,
                    "required_restricted_directions": required, "open_required_directions": open_directions,
                    "signal_codes": codes, "policy_declared": bool(required),
                    "review_recommended": bool(open_directions or codes),
                    "limitations": "Default public access, the number of IP firewall rules and managed private endpoints; no address or "
                                   "target is stored. Without a declared requirement only contradictions in the configuration are flagged."}


NETWORK_SWITCHES = {"inbound": "WorkspaceBlockInboundAccess", "outbound": "WorkspaceBlockOutboundAccess",
                    "firewall": "ConfigureWorkspaceLevelIPFirewallRules", "tenant_public": "BlockAccessFromPublicNetworks"}


def network_switches(settings: list[dict[str, Any]] | None, ctx: Context) -> Result:
    """The tenant switches that decide whether a workspace network restriction can be applied at all."""
    from orchestration.rule_policy import resolve_rule
    by_name = {row.get("settingName"): row for row in settings or [] if isinstance(row, dict)}
    state = {}
    for key, name in NETWORK_SWITCHES.items():
        enabled = (by_name.get(name) or {}).get("enabled")
        state[key] = "not_returned" if name not in by_name else "enabled" if enabled is True else "disabled" if enabled is False else "unreadable"
    if all(value == "not_returned" for value in state.values()):
        return "missing_evidence", {**_SIGNAL, "reason_code": "setting_not_returned", "setting_names": sorted(NETWORK_SWITCHES.values()),
                                    "reason": "The tenant returned none of these settings."}
    declared = sorted({direction for ws in ctx.workspaces if declared_environment(ctx, ws) == "production"
                       for direction in resolve_rule(ctx.configuration, "SEC-021", ws)["policy"].get("restricted_directions") or []})
    blocked = [direction for direction in declared if state.get(direction) == "disabled"]
    return "info", {**_SIGNAL, "tenant_switches": state, "declared_restricted_directions": declared,
                    "directions_that_cannot_be_applied": blocked, "review_recommended": bool(blocked),
                    "limitations": "Whether the tenant lets workspaces restrict inbound or outbound access and use IP firewall rules. "
                                   "A direction is recommended only when you declared it and its tenant switch is off."}


_GRANTED_BUCKETS = ("datasets", "Lakehouse", "Warehouse")


def item_grants(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    eligible = [row for bucket in _GRANTED_BUCKETS for row in scanned.get(bucket) or [] if isinstance(row, dict)]
    if not eligible:
        return "not_applicable", {**_SIGNAL, "reason": "No semantic models, lakehouses or warehouses in the workspace."}
    read = [row["accessSummary"] for row in eligible if isinstance(row.get("accessSummary"), dict)]
    if not read:
        return "missing_evidence", {**_SIGNAL, "reason_code": "item_access_unavailable", "eligible_item_count": len(eligible)}
    total = lambda key: sum(int(row.get(key) or 0) for row in read)
    reshare = total("directReshareCount")
    resharing = sorted(str(row.get("id") or "").lower() for row in eligible
                       if isinstance(row.get("accessSummary"), dict) and int(row["accessSummary"].get("directReshareCount") or 0) > 0
                       and row.get("id"))
    return "info", {**_SIGNAL, "eligible_item_count": len(eligible), "items_read": len(read),
                    "item_ids_with_resharing_grants": resharing,
                    "items_unread": len(eligible) - len(read),
                    "items_with_direct_grants": sum(int(row.get("directGrantCount") or 0) > 0 for row in read),
                    "direct_grant_count": total("directGrantCount"), "direct_reshare_count": reshare,
                    "direct_group_count": total("directGroupCount"), "direct_app_count": total("directAppCount"),
                    "direct_guest_count": total("directGuestCount"), "review_recommended": reshare > 0,
                    "limitations": "Semantic models, lakehouses and warehouses. A direct grant is access that is not a workspace "
                                   "role; counts only, nobody is named."}


def capacity_concentration(effective: dict[str, Any], ctx: Context) -> Result:
    """Reliability: how much of declared production runs on one capacity, and how many regions it spans."""
    if (ctx.raw.get("capacity_metrics.json") or {}).get("workspaceScopeLimited"):
        return "not_applicable", {**_SIGNAL, "reason_code": "review_scope_limited",
                                  "reason": "A review limited to chosen workspaces cannot tell how production is spread over capacities."}
    production = [ws for ws in ctx.workspaces if declared_environment(ctx, ws) == "production"]
    if not production:
        return "not_applicable", {**_SIGNAL, "reason_code": "environment_undeclared",
                                  "reason": "Evaluated for declared production workspaces; production is not inferred."}
    per_capacity = Counter(str(ws["capacityId"]).lower() for ws in production if ws.get("capacityId"))
    capacities = set(per_capacity)
    if not capacities:
        return "not_applicable", {**_SIGNAL, "reason_code": "no_dedicated_capacity",
                                  "reason": "No production workspace is assigned to a capacity."}
    rows = (ctx.raw.get("capacity_metrics.json") or {}).get("capacities")
    regions = {str(row.get("id") or "").lower(): _normalize_region(row.get("region")) for row in rows or []
               if isinstance(row, dict) and row.get("region")}
    known = all(capacity in regions for capacity in capacities)
    hosted = sum(per_capacity.values())
    largest = max(per_capacity.values())
    share = largest / hosted
    maximum = effective["thresholds"]["capacity_concentration_max_ratio"]
    return "info", {**_SIGNAL, "production_workspace_count": len(production), "production_capacity_count": len(capacities),
                    "production_region_count": len({regions[c] for c in capacities}) if known else None,
                    "production_workspaces_without_capacity": sum(not ws.get("capacityId") for ws in production),
                    "largest_capacity_workspace_count": largest, "largest_capacity_share": round(share, 3),
                    "review_above": maximum, "review_recommended": hosted >= 2 and share > maximum,
                    "limitations": "Declared production workspaces in this review's scope. One capacity is one failure and throttling "
                                   "domain, and Fabric does not move workloads to another capacity by itself. A second capacity "
                                   "that is paused or nearly empty does not spread the risk, so the share on the largest one is judged."}


def _handles_failure(activity: dict[str, Any]) -> bool:
    return any(str(condition).casefold() in {"failed", "completed"}
               for dependency in activity.get("dependsOn") or [] if isinstance(dependency, dict)
               for condition in dependency.get("dependencyConditions") or [])


def pipeline_failure_paths(ws: dict[str, Any], ctx: Context) -> Result:
    """Reliability: multi-step pipelines where a failed step ends the run with no step that reacts to it."""
    flows, gap = _pipeline_activities(ws, ctx)
    if gap is not None:
        return gap
    multi_step = [(pid, flow) for pid, flow in flows if sum(_executes(a) for a in flow) >= 2]
    without = [pid for pid, flow in multi_step if not any(_handles_failure(a) for a in flow)]
    return "info", {**_SIGNAL, "pipeline_count": len(flows), "multi_step_pipeline_count": len(multi_step),
                    "without_failure_path_count": len(without), "pipeline_ids_without_failure_path": sorted(without),
                    "heuristic": True, "review_recommended": bool(without),
                    "limitations": "A failure branch is an activity that runs when another fails or completes. Alerts set up outside the "
                                   "pipeline, such as monitoring rules, are not seen."}


def production_dependencies(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    """Reliability: a production workspace whose models or dataflows read from a non-production workspace."""
    if (skip := _production_only(ws, ctx)) is not None:
        return skip
    scanned, gap = _scan(ws, ctx)
    if gap is not None:
        return gap
    dependents = _upstream_workspaces(scanned, str(ws["id"]).lower())
    upstream = set().union(*(groups for _, groups in dependents))
    known = {str(other["id"]).lower(): other for other in ctx.workspaces}
    environment = {group: declared_environment(ctx, known.get(group, {"id": group})) for group in upstream}
    nonproduction = list(environment.values()).count("nonproduction")
    flagged = [item for item, groups in dependents if any(environment[group] == "nonproduction" for group in groups)]
    return "info", {**_SIGNAL, "dependent_item_count": len(dependents), "upstream_workspace_count": len(upstream),
                    "nonproduction_upstream_count": nonproduction, "nonproduction_dependent_item_ids": listed(flagged),
                    "undeclared_upstream_count": list(environment.values()).count("unknown"),
                    "review_recommended": nonproduction > 0,
                    "limitations": "Upstream semantic models and dataflows only, compared with the environments you declared. An upstream "
                                   "workspace that is not declared is counted but not judged."}


def empty_capacity(ctx: Context) -> Result:
    metrics = ctx.raw.get("capacity_metrics.json") or {}
    if metrics.get("collectionComplete") is not True or not isinstance(metrics.get("capacities"), list):
        return "missing_evidence", {**_SIGNAL, "reason_code": "capacity_inventory_unavailable"}
    if metrics.get("workspaceScopeLimited"):
        return "not_applicable", {**_SIGNAL, "reason_code": "review_scope_limited",
                                  "reason": "A review limited to chosen workspaces cannot tell whether a capacity is empty."}
    empty = (metrics.get("summary") or {}).get("emptyCapacities") or []
    return "info", {**_SIGNAL, "capacity_count": len(metrics["capacities"]), "empty_capacity_count": len(empty),
                    "review_recommended": bool(empty),
                    "limitations": "Capacities with no workspace in this review. A workspace left out of the review, such as the one "
                                   "the review runs in, is not counted, so a capacity only it uses can look empty."}


def notebook_pattern(rule_id: str, ws: dict[str, Any], ctx: Context) -> Result:
    notebooks = _rows(ctx.raw["pipeline_definitions.json"], "notebooks", ws["id"])
    count = sum((row.get("signals") or {}).get(rule_id, 0) for row in notebooks)
    complete = item_inventory_complete(ws) and all(
        not row.get("error") and row.get("signalCoverage") == "complete" for row in notebooks
    ) and {str(item["id"]).lower() for item in items(ws) if item.get("type") == "Notebook"} == {
        str(row.get("id") or "").lower() for row in notebooks
    }
    return ("info" if complete else "missing_evidence"), {
        "heuristic": True, "pattern_hit_count": count, "coverage_status": "complete" if complete else "partial",
        "notebook_ids_with_patterns": listed(row.get("id") for row in notebooks if (row.get("signals") or {}).get(rule_id, 0)),
        "reason": "Source patterns are review signals, not proof of a defect.",
    }


def _pipeline_activities(ws: dict[str, Any], ctx: Context) -> tuple[list[tuple[str, list[dict[str, Any]]]] | None, Result | None]:
    """The (pipeline id, activities) of each pipeline in the workspace, or the result to return when they cannot all be read."""
    pipelines = _rows(ctx.raw["pipeline_definitions.json"], "pipelines", ws["id"])
    expected = {str(item["id"]).lower() for item in items(ws) if item.get("type") == "DataPipeline"}
    complete = item_inventory_complete(ws) and expected == {str(row.get("id") or "").lower() for row in pipelines}
    if not expected and complete:
        return None, ("not_applicable", {**_SIGNAL, "reason": "The complete inventory contains no pipelines."})
    flows: list[tuple[str, list[dict[str, Any]]]] = []
    for pipeline in pipelines:
        content = _pipeline_json_from_parts(pipeline.get("parts") or [])
        if pipeline.get("error") or content is None:
            complete = False
            continue
        activities = content.get("activities", (content.get("properties") or {}).get("activities"))
        if not isinstance(activities, list):
            complete = False
            continue
        flows.append((str(pipeline.get("id") or "").lower(), list(_walk_activities(activities))))
    if not complete:
        return None, ("missing_evidence", {**_SIGNAL, "reason_code": "pipeline_definitions_incomplete"})
    return flows, None


def _executes(activity: dict[str, Any]) -> bool:
    return str(activity.get("type") or "").lower() in EXECUTION_ACTIVITY_TYPES


def pipeline_resilience(ws: dict[str, Any], ctx: Context) -> Result:
    flows, gap = _pipeline_activities(ws, ctx)
    if gap is not None:
        return gap
    executing = without_retry = default_timeout = 0
    retry_ids: set[str] = set()
    timeout_ids: set[str] = set()
    for pipeline_id, flow in flows:
        for activity in (a for a in flow if _executes(a)):
            policy = activity.get("policy") if isinstance(activity.get("policy"), dict) else {}
            executing += 1
            if policy.get("dynamic"):
                continue
            if not policy.get("retry"):
                without_retry += 1
                retry_ids.add(pipeline_id)
            if policy.get("timeout") in (None, DEFAULT_ACTIVITY_TIMEOUT):
                default_timeout += 1
                timeout_ids.add(pipeline_id)
    return "info", {**_SIGNAL, "executing_activity_count": executing, "without_retry_count": without_retry,
                    "default_timeout_count": default_timeout,
                    "pipeline_ids_without_retry": sorted(retry_ids), "pipeline_ids_with_default_timeout": sorted(timeout_ids),
                    "review_recommended": bool(without_retry or default_timeout),
                    "limitations": "Retry and timeout are set per activity; a transient failure with no retry stops the pipeline."}

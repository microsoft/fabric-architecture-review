# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Semantic model row-level (RLS) and object-level (OLS) security rules.

SEC-024  Models you declare must enforce RLS        (control, declared)
SEC-025  Models you declare must enforce OLS        (control, declared)
SEC-026  RLS and OLS posture of the workspace's models (signal, never scored)
SEC-027  Declared models were found in the review   (coverage, tenant-wide)

A rule decides from what the model definition shows: a reader role with a row filter that restricts rows
(RLS) or that hides a table or column (OLS). That a security mechanism is configured does not prove it is
correct, that the right people are in the role, or that data is protected outside the model. Missing or
partial evidence is never read as "no security".
"""
from __future__ import annotations

import os
from typing import Any

from analyzers.rule_support import Context, item_inventory_complete, items, listed, rows
from collectors.model_security import FILTER_KINDS, INSPECTION_STATES, MAX_ROLE_NAME, MAX_ROLE_RECORDS, ROLE_PERMISSIONS

Result = tuple[str, dict[str, Any]]
RLS, OLS = "rls", "ols"
KINDS = (RLS, OLS)
CONTROL_RULES = {"SEC-024": RLS, "SEC-025": OLS}
MISSING_IDS_KEY = {RLS: "models_missing_rls_ids", OLS: "models_missing_ols_ids"}
LIMITATIONS = ("Roles defined in the model only. A configured filter or hidden object is not proof that it "
               "implements the intended rule; role membership and security enforced at the data source or "
               "through OneLake security are not seen.")
_SIGNAL = {"assessment_kind": "non_scored_context"}


def declared_model_ids(values: Any) -> frozenset[str]:
    return frozenset(str(value).lower() for value in values or [])


def required_models(configuration: dict[str, Any] | None, rule_id: str) -> frozenset[str]:
    """Model IDs declared for a requirement, or none when the rule is switched off or nothing is declared."""
    if not configuration:
        return frozenset()
    from orchestration.rule_policy import resolve_rule
    resolved = resolve_rule(configuration, rule_id, {})
    if resolved["applicability"] != "applicable" or resolved["policy"].get("required") is not True:
        return frozenset()
    return declared_model_ids(resolved["policy"].get("required_model_ids"))


def model_state(row: dict[str, Any] | None) -> dict[str, Any]:
    """RLS and OLS state of one model: present, absent, or unknown, with how it was established."""
    security = row.get("security") if isinstance(row, dict) and isinstance(row.get("security"), dict) else None
    flag = row.get("effectiveIdentityRolesRequired") if isinstance(row, dict) else None
    if security is None:
        inspection, reason = "unavailable", ("definition_unreadable" if row and row.get("error") else "definition_not_collected")
        counts: dict[str, int] = {}
    else:
        inspection = security.get("inspection") if security.get("inspection") in INSPECTION_STATES else "unavailable"
        reason = security.get("reason")
        counts = {key: value for key, value in security.items() if isinstance(value, int) and not isinstance(value, bool)}
    complete = inspection == "complete"
    rls_filters = counts.get("rlsFilterCount", 0)
    ols_objects = counts.get("olsTableCount", 0) + counts.get("olsColumnCount", 0)
    rls_source = None
    if rls_filters:
        rls, rls_source = "present", "definition"
    elif not complete and flag is True:
        # The dataset itself reports that row-level roles are defined: positive evidence when the definition is unreadable.
        rls, rls_source = "present", "dataset_flag"
    else:
        rls = "absent" if complete else "unknown"
    ols = "present" if ols_objects else ("absent" if complete else "unknown")
    return {
        "inspection": inspection, "reason": reason, RLS: rls, OLS: ols, "rls_source": rls_source,
        "role_detail": role_detail_state(security, inspection),
        "role_count": counts.get("roleCount"), "reader_role_count": counts.get("readerRoleCount"),
        "rls_filter_count": counts.get("rlsFilterCount"), "rls_constant_filter_count": counts.get("rlsConstantFilterCount"),
        "ols_table_count": counts.get("olsTableCount"), "ols_column_count": counts.get("olsColumnCount"),
        "roles_without_row_filters": bool(complete and flag is True and not rls_filters),
    }


def role_detail_state(security: dict[str, Any] | None, inspection: str) -> str:
    """not_collected (setting off or older data), collected, partial (not every role could be read) or unavailable."""
    if not isinstance(security, dict) or not isinstance(security.get("roles"), list):
        return "not_collected"
    if inspection == "unavailable":
        return "unavailable"
    return "partial" if inspection != "complete" or security.get("rolesTruncated") is True else "collected"


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def model_roles(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The validated role records of one model definition row; empty unless role detail was collected.

    The raw file is read back from storage, so every field is checked rather than trusted.
    """
    security = row.get("security") if isinstance(row, dict) and isinstance(row.get("security"), dict) else None
    records = security.get("roles") if security and isinstance(security.get("roles"), list) else []
    roles: list[dict[str, Any]] = []
    for record in records[:MAX_ROLE_RECORDS]:
        if not isinstance(record, dict):
            continue
        name = record.get("name")
        name = "".join(ch for ch in name if ord(ch) >= 32).strip()[:MAX_ROLE_NAME] if isinstance(name, str) else ""
        permission, kind = record.get("permission"), record.get("filterKind")
        roles.append({
            "role_name": name or "(unnamed)",
            "role_permission": permission if permission in ROLE_PERMISSIONS else "other",
            "reads_data": record.get("readsData") is True,
            "row_filter_table_count": _count(record.get("rowFilterTables")),
            "dynamic_filter_table_count": _count(record.get("dynamicFilterTables")),
            "constant_filter_table_count": _count(record.get("constantFilterTables")),
            "ols_table_count": _count(record.get("hiddenTables")),
            "ols_column_count": _count(record.get("hiddenColumns")),
            "filter_kind": kind if kind in FILTER_KINDS else "unknown",
        })
    return roles


def requirement_outcome(required: bool, state: dict[str, Any], kind: str) -> str:
    """not_required | pass | fail | missing_evidence, for one model and one mechanism."""
    if not required:
        return "not_required"
    return {"present": "pass", "absent": "fail"}.get(state[kind], "missing_evidence")


def definition_index(ctx: Context, wid: str) -> dict[str, dict[str, Any] | None]:
    """Definition rows of a workspace by model id; an id seen twice is ambiguous and reads as not collected."""
    index: dict[str, dict[str, Any] | None] = {}
    for row in rows(ctx.raw.get("semantic_model_definitions.json") or {}, "models", wid):
        key = str(row.get("id") or "").lower()
        index[key] = None if key in index else row
    return index


def workspace_models(ws: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in items(ws) if item.get("type") == "SemanticModel"]


def required_control(kind: str, ws: dict[str, Any], policy: dict[str, Any], ctx: Context) -> Result:
    """SEC-024 / SEC-025: every declared model in the workspace has the required mechanism configured."""
    declared = declared_model_ids(policy.get("required_model_ids"))
    if not declared:
        return "unknown", {"reason_code": "policy_incomplete",
                           "reason": "The requirement is enabled but does not declare any model."}
    if not item_inventory_complete(ws):
        return "missing_evidence", {"reason_code": "model_inventory_incomplete"}
    targeted = [item for item in workspace_models(ws) if str(item["id"]).lower() in declared]
    if not targeted:
        return "not_applicable", {"reason_code": "no_declared_model_in_workspace",
                                  "reason": "None of the models declared for this requirement is in this workspace."}
    index = definition_index(ctx, str(ws["id"]))
    missing, unverified, reasons, compliant = [], [], set(), 0
    for item in targeted:
        state = model_state(index.get(str(item["id"]).lower()))
        outcome = requirement_outcome(True, state, kind)
        if outcome == "fail":
            missing.append(item["id"])
        elif outcome == "pass":
            compliant += 1
        else:
            unverified.append(item["id"])
            reasons.add(state["reason"] or "inspection_partial")
    evidence = {
        "assessment_kind": "declared_model_security_requirement", "security_mechanism": kind,
        "targeted_model_count": len(targeted), "compliant_model_count": compliant,
        MISSING_IDS_KEY[kind]: listed(missing), "unverified_model_ids": listed(unverified),
        "inspection_reasons": sorted(reasons), "limitations": LIMITATIONS,
    }
    if missing:
        return "fail", evidence
    if unverified:
        return "missing_evidence", {**evidence, "reason_code": "model_security_unavailable"}
    return "pass", evidence


def posture(ws: dict[str, Any], effective: dict[str, Any], ctx: Context) -> Result:
    """SEC-026: which of the workspace's models define RLS and OLS. Context only; never a failure."""
    if not item_inventory_complete(ws):
        return "missing_evidence", {**_SIGNAL, "reason_code": "model_inventory_incomplete"}
    models = workspace_models(ws)
    if not models:
        return "not_applicable", {**_SIGNAL, "reason": "No semantic models in complete workspace inventory."}
    index = definition_index(ctx, str(ws["id"]))
    states = {str(item["id"]).lower(): model_state(index.get(str(item["id"]).lower())) for item in models}
    count = lambda kind, value: sum(state[kind] == value for state in states.values())
    neither = [key for key, state in states.items() if state[RLS] == "absent" and state[OLS] == "absent"]
    both = sum(state[RLS] == "present" and state[OLS] == "present" for state in states.values())
    unverified = [key for key, state in states.items() if "unknown" in (state[RLS], state[OLS])]
    return "info", {
        **_SIGNAL, "model_count": len(models),
        "inspected_model_count": sum(state["inspection"] == "complete" for state in states.values()),
        "rls_present_count": count(RLS, "present"), "rls_absent_count": count(RLS, "absent"),
        "rls_unknown_count": count(RLS, "unknown"), "ols_present_count": count(OLS, "present"),
        "ols_absent_count": count(OLS, "absent"), "ols_unknown_count": count(OLS, "unknown"),
        "both_count": both, "neither_count": len(neither), "unverified_count": len(unverified),
        "models_without_security_ids": listed(neither), "unverified_model_ids": listed(unverified),
        "review_recommended": bool(neither),
        "limitations": LIMITATIONS + " Having neither mechanism is intended for a model every authorized reader may see in full.",
    }


def declared_resolution(ctx: Context) -> Result:
    """SEC-027: every declared model was found in the reviewed workspaces, so no declaration is silently ignored."""
    declared_rls = required_models(ctx.configuration, "SEC-024")
    declared_ols = required_models(ctx.configuration, "SEC-025")
    declared = declared_rls | declared_ols
    evidence: dict[str, Any] = {"assessment_kind": "declaration_coverage", "declared_rls_model_count": len(declared_rls),
                                "declared_ols_model_count": len(declared_ols), "declared_model_count": len(declared)}
    if not declared:
        return "info", {**evidence, "coverage_status": "not_declared",
                        "reason": "No model is declared for RLS or OLS, so nothing needs resolving."}
    if not ctx.workspaces or not all(item_inventory_complete(ws) for ws in ctx.workspaces):
        return "missing_evidence", {**evidence, "reason_code": "model_inventory_incomplete"}
    found = {str(item["id"]).lower() for ws in ctx.workspaces for item in workspace_models(ws)}
    unresolved = sorted(declared - found)
    limited = bool(os.environ.get("WORKSPACE_IDS", "").strip())
    evidence.update(resolved_model_count=len(declared) - len(unresolved), unresolved_model_count=len(unresolved),
                    unresolved_model_ids=unresolved[:200], review_scope_limited=limited)
    if unresolved and not limited:
        return "missing_evidence", {**evidence, "coverage_status": "unresolved", "reason_code": "declared_models_not_found"}
    if unresolved:
        return "info", {**evidence, "coverage_status": "outside_review_scope",
                        "reason": "Some declared models are not in the workspaces this review was limited to."}
    return "info", {**evidence, "coverage_status": "complete"}

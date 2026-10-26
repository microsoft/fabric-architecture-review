# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Validated workspace/capacity declarations for the architectural rules."""
from __future__ import annotations

import copy
from datetime import datetime
import math
import re
from typing import Any
from uuid import UUID

from analyzers.far_profile import ACTIVE_RULE_IDS, RULE_KINDS, TENANT_SETTING_NAMES

# Controls that need no declared requirement: structural checks of definitions, the
# workspace-state check, the FUAM published-to-web check and the tenant-setting controls,
# which evaluate the recorded tenant settings.
UNDECLARED_CONTROLS = frozenset({"ARCH-004", "ARCH-005", "GOV-003", "SEC-006", *TENANT_SETTING_NAMES})

# Declared requirements a rule can evaluate. Rules absent from this mapping
# (ARCH-004, ARCH-005 and every signal/coverage rule) accept no policy.
POLICY_FIELDS = {
    "ARCH-001": {"allowed_item_types": list},
    "ARCH-002": {"allowed_capacity_ids": list, "require_shared_workspace": bool},
    "ARCH-003": {"allowed_storage_modes": list},
    "ARCH-006": {},
    "ARCH-007": {"allowed_regions": list},
    "GOV-009": {"required_tag_names": list},
    "SEC-021": {"restricted_directions": list},
    "GOV-001": {"eligible_admin_ids": list},
    "OPS-001": {},
    "SEC-001": {"allowed_principal_ids": list},
    "SEC-002": {"allowed_roles": list},
    "SEC-024": {"required_model_ids": list},
    "SEC-025": {"required_model_ids": list},
}
# Requirements that apply without any declaration. Every other control is
# evaluated only when its requirement is declared for the workspace.
DEFAULT_POLICY = {
    "GOV-001": {"required": True},
    "ARCH-002": {"required": True, "production_only": True, "require_shared_workspace": True},
    "ARCH-006": {"required": True, "production_only": True},
    "OPS-001": {"required": True, "production_only": True},
}
SCOPED_THRESHOLDS = {
    "ARCH-008": {"monolith_max_items": ("architecture", "monolith_max_items")},
    "ARCH-009": {"description_coverage_min_ratio": ("architecture", "description_coverage_min_ratio")},
    "ARCH-014": {"cross_workspace_dependency_max": ("architecture", "cross_workspace_dependency_max")},
    "GOV-001": {"min_admins": ("governance", "min_admins")},
    "GOV-004": {"naming_pattern": ("governance", "naming_pattern"),
                "naming_coverage_min_ratio": ("governance", "naming_coverage_min_ratio")},
    "GOV-005": {"endorsement_min_ratio": ("governance", "endorsement_min_ratio")},
    "GOV-006": {"domain_coverage_min_ratio": ("governance", "domain_coverage_min_ratio")},
    "GOV-007": {"inactive_workspace_days": ("governance", "inactive_workspace_days")},
    "GOV-008": {"stale_item_days": ("governance", "stale_item_days"),
                "stale_item_max_ratio": ("governance", "stale_item_max_ratio")},
    "OPS-002": {"window_days": ("operations", "window_days"),
                "failure_min_events": ("operations", "failure_min_events"),
                "failure_max_ratio": ("operations", "failure_max_ratio")},
    "OPS-003": {"window_days": ("operations", "window_days"),
                "expensive_item_cu_max_ratio": ("operations", "expensive_item_cu_max_ratio")},
    "OPS-004": {"window_days": ("operations", "window_days"),
                "throttled_day_max_ratio": ("operations", "throttled_day_max_ratio")},
    "OPS-009": {"min_capacity_admins": ("operations", "min_capacity_admins")},
    "REL-001": {"capacity_concentration_max_ratio": ("operations", "capacity_concentration_max_ratio")},
    "OPS-007": {"window_days": ("operations", "window_days"),
                "workspace_cu_max_ratio": ("operations", "workspace_cu_max_ratio")},
    "OPS-005": {"window_days": ("operations", "window_days"),
                "refresh_min_events": ("operations", "refresh_min_events"),
                "refresh_failure_max_ratio": ("operations", "refresh_failure_max_ratio")},
    "SEC-009": {"broad_access_max_principals": ("security", "broad_access_max_principals")},
    "SEC-010": {"gateway_min_version": ("security", "gateway_min_version")},
    "SEC-012": {"label_min_ratio": ("security", "label_min_ratio")},
}


def aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Evidence timestamps must be ISO-8601 strings.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps require a time zone.")
    return parsed


def validate_override(rule_id: str, override: Any) -> None:
    if rule_id not in ACTIVE_RULE_IDS:
        raise ValueError(f"Unknown architectural rule: {rule_id}")
    if not isinstance(override, dict) or set(override) - {"applicability", "reason", "policy", "thresholds"}:
        raise ValueError(f"Unsupported rule override fields: {rule_id}")
    if not isinstance(override.get("reason"), str) or not override["reason"].strip():
        raise ValueError("Rule overrides require a nonempty reason.")
    applicability = override.get("applicability", "applicable")
    if applicability not in {"applicable", "not_applicable", "unknown", "disabled"}:
        raise ValueError(f"Invalid applicability: {rule_id}")
    policy = override.get("policy", {})
    permitted = {"required": bool, "production_only": bool, **POLICY_FIELDS.get(rule_id, {})}
    if not isinstance(policy, dict) or set(policy) - set(permitted):
        raise ValueError(f"Unsupported architectural policy: {rule_id}")
    if policy and rule_id not in POLICY_FIELDS:
        raise ValueError(f"This rule does not accept enforcement policy: {rule_id}")
    for key, value in policy.items():
        if not isinstance(value, permitted[key]):
            raise ValueError(f"Invalid policy type: {rule_id}.{key}")
        if isinstance(value, list) and (
            not value or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"Policy lists must contain distinct nonempty strings: {rule_id}.{key}")
        if key in {"allowed_capacity_ids", "eligible_admin_ids", "allowed_principal_ids", "required_model_ids"}:
            canonical = []
            for identity in value:
                try:
                    canonical.append(str(UUID(identity)))
                except ValueError as exc:
                    raise ValueError(f"Policy identities must be GUIDs: {rule_id}.{key}") from exc
            if len(set(canonical)) != len(canonical):
                raise ValueError(f"Duplicate policy identity: {rule_id}.{key}")
            policy[key] = canonical
        if key == "restricted_directions" and set(value) - {"inbound", "outbound"}:
            raise ValueError("Network directions must be inbound or outbound.")
        if key == "allowed_roles" and set(value) - {"Admin", "Member", "Contributor", "Viewer"}:
            raise ValueError("Unsupported workspace role.")
        if key == "allowed_storage_modes" and set(value) - {"Import", "DirectQuery", "DirectLake", "Dual"}:
            raise ValueError("Unsupported storage mode.")
    thresholds = override.get("thresholds", {})
    if not isinstance(thresholds, dict) or set(thresholds) - set(SCOPED_THRESHOLDS.get(rule_id, {})):
        raise ValueError(f"Unsupported scoped threshold: {rule_id}")
    for key, value in thresholds.items():
        validate_rule_threshold(key, value)


def validate_rule_threshold(key: str, value: Any) -> None:
    if key in {"naming_pattern", "gateway_min_version"}:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string.")
        if key == "naming_pattern":
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("Invalid naming_pattern regular expression.") from exc
        elif value and not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
            raise ValueError("gateway_min_version must be empty or a dotted numeric version.")
    elif "ratio" in key:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be a finite ratio between zero and one.")
    elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Scoped count thresholds must be nonnegative integers.")


# Signals that are judged for declared production workspaces only, so no other workspace is read for them.
PRODUCTION_ONLY_SIGNALS = frozenset({"OPS-010", "REL-003", "SEC-020", "SEC-021"})


def evidence_demanded(rule_id: str, resolved: dict[str, Any]) -> bool:
    """Whether evaluating the rule for this workspace will read collected evidence.

    Mirrors the analyzer: a control that needs a declared requirement reads evidence only
    when that requirement is required, and a production-only requirement only for declared
    production workspaces. Everything else is decided before any evidence is read.
    """
    if resolved["applicability"] != "applicable":
        return False
    if rule_id in PRODUCTION_ONLY_SIGNALS and resolved["environment"] != "production":
        return False
    if RULE_KINDS[rule_id] == "control" and rule_id not in UNDECLARED_CONTROLS:
        policy = resolved["policy"]
        if policy.get("required") is not True:
            return False
        if policy.get("production_only") and resolved["environment"] != "production":
            return False
    return True


def resolve_rule(config: dict[str, Any], rule_id: str, workspace: dict[str, Any]) -> dict[str, Any]:
    profiles = config["profiles"]
    wid = str(workspace.get("id") or "").lower()
    cid = str(workspace.get("capacityId") or "").lower()
    ws = next((row for row in profiles.get("workspaces", []) if row["id"].lower() == wid), {})
    capacity = next((row for row in profiles.get("capacities", []) if row["id"].lower() == cid), {})
    inherited = capacity if capacity.get("inherit_rules", False) else {}
    declared_profile = profiles.get("rule_profiles", {}).get(profiles.get("default_rule_profile"), {})
    result: dict[str, Any] = {"applicability": "applicable", "thresholds": {}, "origins": []}
    declared: dict[str, Any] | None = None
    override = declared_profile.get("rule_overrides", {}).get(rule_id)
    if override is not None:
        for key, value in override.items():
            if key == "policy":
                declared = dict(value)
            elif key == "thresholds":
                result[key] = {**result[key], **value}
            else:
                result[key] = value
        result["origins"].append("policy_variables")
    # A declared policy replaces the built-in default requirement for the rule.
    result["policy"] = declared if declared is not None else copy.deepcopy(DEFAULT_POLICY.get(rule_id, {}))
    result["environment"] = ws.get("profile", {}).get(
        "environment", inherited.get("profile", {}).get("environment", "unknown"),
    )
    for key, (group, name) in SCOPED_THRESHOLDS.get(rule_id, {}).items():
        result["thresholds"].setdefault(key, config["thresholds"][group][name])
    return result

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evidence-gated architectural review for the standalone FAR module."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from analyzers._common import load_rules, make_finding, write_findings
from analyzers import enrichment_rules, model_security_rules, native_rules
from analyzers.far_profile import (
    ACTIVE_RULE_IDS, ENABLED_IS_SECURE, PROFILE_ID, PROFILE_SHA256, RULE_KINDS, RULE_ORDER,
    CAPACITY_RULES, TENANT_RULES, TENANT_SETTING_NAMES, TENANT_SIGNAL_SETTINGS, normalize_findings,
)
from analyzers.rule_support import Context, listed
from analyzers.rule_support import item_inventory_complete as _item_inventory_complete
from analyzers.rule_support import items as _items
from analyzers.rule_support import rows as _rows
from analyzers.structural_contracts import _dependency_graph, _pipeline_json_from_parts, _walk_activities
from orchestration.review_config import ROOT, activate, read_snapshot
from orchestration.rule_policy import aware_datetime, resolve_rule

# Fabric passes these notebook-activity parameters to the runtime; notebooks never declare them.
RESERVED_NOTEBOOK_PARAMETERS = frozenset({"_inlineinstallationenabled"})

TITLES = {
    "ARCH-001": "Workspace holds only declared item types",
    "ARCH-002": "Workspace hosting and shared ownership",
    "ARCH-003": "Semantic model storage modes",
    "ARCH-004": "Pipeline-to-notebook parameter contracts",
    "ARCH-005": "Pipeline dependency integrity",
    "ARCH-006": "Production isolated from non-production capacity",
    "ARCH-007": "Capacity in an approved region",
    "ARCH-008": "Workspace size",
    "ARCH-009": "Documentation coverage",
    "ARCH-010": "Runtime-portability patterns in notebooks",
    "ARCH-011": "Hard-coded environment identifiers and endpoints in notebooks",
    "ARCH-012": "Unpinned package installs in notebooks",
    "ARCH-013": "Pipeline activity retry and timeout",
    "ARCH-014": "Cross-workspace dependencies",
    "ARCH-015": "Legacy item types",
    "ARCH-016": "Empty capacities",
    "ARCH-017": "Empty workspaces",
    "ARCH-018": "Reports without a semantic model",
    "ARCH-019": "Semantic models no report uses",
    "ARCH-020": "Inventory coverage",
    "GOV-001": "Administrative continuity",
    "GOV-002": "Fabric item creation scoped to security groups",
    "GOV-003": "Workspace has an administrator",
    "GOV-004": "Naming convention",
    "GOV-005": "Production semantic models endorsed",
    "GOV-006": "Workspaces assigned to a domain",
    "GOV-007": "Workspace activity",
    "GOV-008": "Stale items",
    "GOV-009": "Capacity tags",
    "GOV-010": "Tenant settings recorded",
    "OPS-001": "Source control",
    "OPS-002": "Item failure rate",
    "OPS-003": "Items dominating capacity compute",
    "OPS-004": "Capacity throttling",
    "OPS-005": "Refresh failures",
    "OPS-006": "Production on a production-grade capacity",
    "OPS-007": "Workspace share of its capacity",
    "OPS-008": "Capacity state",
    "OPS-009": "Capacity administrators",
    "OPS-010": "Production in a deployment pipeline",
    "REL-001": "Production capacity concentration",
    "REL-002": "Pipeline failure paths",
    "REL-003": "Production depending on non-production",
    "SEC-001": "Direct principals within declared boundary",
    "SEC-002": "Direct workspace roles within declared roles",
    "SEC-003": "Publish to web disabled or scoped",
    "SEC-004": "External data sharing disabled or scoped",
    "SEC-005": "Service principal API access scoped",
    "SEC-006": "No item published to web",
    "SEC-007": "Guest invitations through sharing scoped",
    "SEC-008": "Resource-key authentication blocked",
    "SEC-009": "Direct principal count",
    "SEC-010": "Gateway redundancy and versions",
    "SEC-011": "Credential-management patterns in notebooks",
    "SEC-012": "Sensitivity-label coverage",
    "SEC-013": "Tenant-level Private Link",
    "SEC-014": "Export and download settings open to everyone",
    "SEC-015": "Sharing and guest settings open to everyone",
    "SEC-016": "Automation and embedding settings open to everyone",
    "SEC-017": "Guest members in the workspace",
    "SEC-018": "Applications with elevated workspace roles",
    "SEC-019": "Organization-wide sharing links",
    "SEC-020": "OneLake data access roles",
    "SEC-021": "Workspace network access",
    "SEC-022": "Direct item grants with re-share",
    "SEC-023": "Tenant network switches",
    "SEC-024": "Declared semantic models enforce row-level security",
    "SEC-025": "Declared semantic models enforce object-level security",
    "SEC-026": "Semantic model row-level and object-level security posture",
    "SEC-027": "Declared model-security models found in the review",
}


def _scope(value: Any) -> frozenset[str]:
    """Canonical workspace scope: case, order, separators and whitespace do not matter."""
    if not isinstance(value, str):
        return frozenset({"invalid"})
    tokens = [token for token in re.split(r"[,;\s]+", value) if token]
    try:
        return frozenset(str(UUID(token)) for token in tokens)
    except ValueError:
        return frozenset({"invalid:" + value})


def _read(raw_dir: Path, filename: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    path = raw_dir / filename
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Architectural evidence must be a mapping: {filename}")
    provenance = value.get("_provenance")
    expected = {
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "configuration_sha256": snapshot["sha256"],
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
    }
    if not isinstance(provenance, dict) or any(provenance.get(key) != item for key, item in expected.items()) \
            or _scope(provenance.get("workspace_scope")) != _scope(os.environ.get("WORKSPACE_IDS", "")):
        raise ValueError(f"Unbound or stale architectural evidence: {filename}. Run the collection stage again.")
    if provenance.get("source") != "native" or provenance.get("capture_status") not in {"collected", "partial"}:
        raise ValueError(f"Unsupported architectural evidence capture contract: {filename}")
    evidence_time = aware_datetime(provenance.get("captured_at_utc"))
    if evidence_time < aware_datetime(snapshot["captured_at_utc"]):
        raise ValueError(f"Architectural evidence predates the frozen review: {filename}")
    return value


def _principal_id(user: dict[str, Any]) -> str | None:
    value = user.get("graphId") or user.get("identifier")
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _structural(rule_id: str, workspace: dict[str, Any], definitions: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    wid = workspace["id"]
    pipelines = _rows(definitions, "pipelines", wid)
    expected = {str(item["id"]).lower() for item in _items(workspace) if item.get("type") == "DataPipeline"}
    complete = _item_inventory_complete(workspace) and expected == {str(row.get("id") or "").lower() for row in pipelines}
    if len(pipelines) != len({str(row.get("id") or "").lower() for row in pipelines}):
        complete = False
    if not expected and complete:
        return "not_applicable", {"reason_code": "no_pipeline_items", "reason": "Complete inventory contains no pipelines."}
    gaps, violations, checked = [], [], 0
    notebooks = {}
    ambiguous_notebooks = set()
    for row in definitions.get("notebooks", []):
        key = (str(row.get("workspaceId") or "").lower(), str(row.get("id") or "").lower())
        if key in notebooks:
            ambiguous_notebooks.add(key)
        notebooks[key] = row
    for pipeline in pipelines:
        content = _pipeline_json_from_parts(pipeline.get("parts") or [])
        if pipeline.get("error") or content is None:
            gaps.append("definition_unavailable")
            continue
        if rule_id == "ARCH-005":
            graph = _dependency_graph(content)
            checked += graph["activity_count"]
            if graph["coverage_status"] != "complete" or graph["unresolved_dependency_count"]:
                gaps.append("dynamic_or_partial_dependency_scope")
            if graph["signal_codes"]:
                violations.append({"item_id": pipeline["id"], "signal_codes": graph["signal_codes"]})
            continue
        activities = content.get("activities", (content.get("properties") or {}).get("activities"))
        if not isinstance(activities, list) or _dependency_graph(content)["coverage_status"] != "complete":
            gaps.append("activity_scope_unresolved")
            continue
        for activity in _walk_activities(activities):
            if str(activity.get("type") or "").lower() not in {"tridentnotebook", "executenotebook"}:
                continue
            tp = activity.get("typeProperties") or {}
            target = tp.get("notebookId") or (tp.get("notebook") or {}).get("referenceName")
            target_ws = tp.get("workspaceId") or tp.get("notebookWorkspaceId") or wid
            params = tp.get("parameters", {})
            if not isinstance(target, str) or target.startswith("@") or not isinstance(target_ws, str) \
                    or target_ws.startswith("@") or not isinstance(params, dict):
                gaps.append("dynamic_parameter_contract")
                continue
            key = (target_ws.lower(), target.lower())
            notebook = notebooks.get(key) if key not in ambiguous_notebooks else None
            if not notebook or notebook.get("error") or notebook.get("parameterCoverage") != "complete":
                gaps.append("notebook_parameter_contract_unavailable")
                continue
            declared = notebook.get("parameterNames")
            if not isinstance(declared, list) or any(not isinstance(name, str) for name in declared):
                gaps.append("notebook_parameter_contract_unavailable")
                continue
            checked += 1
            extra = sorted(name for name in set(params) - set(declared) if name.lower() not in RESERVED_NOTEBOOK_PARAMETERS)
            if extra:
                violations.append({"item_id": pipeline["id"], "notebook_id": target,
                                   "undeclared_parameter_names": extra})
    evidence = {"assessment_kind": "static_structure", "checked_count": checked, "violations": violations,
                "coverage_status": "complete" if complete and not gaps else "partial",
                "reason_codes": sorted(set(gaps + ([] if complete else ["pipeline_inventory_incomplete"])))}
    if not complete or gaps:
        return "unknown", evidence
    if rule_id == "ARCH-004" and not checked:
        return "not_applicable", {**evidence, "reason": "No literal notebook invocation contracts in complete definitions."}
    return ("fail" if violations else "pass"), evidence


def _native_policy(rule_id: str, ws: dict[str, Any], policy: dict[str, Any],
                   effective: dict[str, Any], ctx: Context) -> tuple[str, dict[str, Any]]:
    raw = ctx.raw
    model_definitions = raw["semantic_model_definitions.json"]
    if rule_id in model_security_rules.CONTROL_RULES:
        return model_security_rules.required_control(model_security_rules.CONTROL_RULES[rule_id], ws, policy, ctx)
    if rule_id == "ARCH-006":
        return native_rules.capacity_isolation(ws, ctx)
    if rule_id == "ARCH-007":
        return native_rules.capacity_region(ws, policy, ctx)
    if rule_id == "ARCH-001" and "allowed_item_types" in policy:
        if not _item_inventory_complete(ws):
            return "missing_evidence", {"reason_code": "item_inventory_incomplete"}
        outside = [item["id"] for item in ws["items"] if item.get("type") not in policy["allowed_item_types"]]
        return ("fail" if outside else "pass"), {"violating_item_ids": outside, "assessment_kind": "declared_item_boundary"}
    if rule_id == "ARCH-002" and any(key in policy for key in ("allowed_capacity_ids", "require_shared_workspace")):
        if policy.get("require_shared_workspace") and not ws.get("type"):
            return "missing_evidence", {"reason_code": "workspace_type_unavailable"}
        if policy.get("require_shared_workspace") and ws["type"] not in {"Workspace", "Group", "PersonalGroup"}:
            return "unknown", {"reason_code": "workspace_type_unsupported"}
        violations = []
        if policy.get("require_shared_workspace") and ws["type"] == "PersonalGroup":
            violations.append("required_shared_ownership")
        if "allowed_capacity_ids" in policy:
            if not ws.get("capacityId"):
                return "missing_evidence", {"reason_code": "hosting_identity_unavailable"}
            try:
                capacity_id = str(UUID(ws["capacityId"]))
            except (ValueError, TypeError, AttributeError):
                return "unknown", {"reason_code": "hosting_identity_unresolved"}
            if capacity_id not in {str(UUID(value)) for value in policy["allowed_capacity_ids"]}:
                violations.append("hosting_outside_declared_boundary")
        return ("fail" if violations else "pass"), {"signal_codes": violations, "assessment_kind": "declared_hosting_boundary"}
    if rule_id in {"SEC-001", "SEC-002", "GOV-001"}:
        if ws.get("usersCollectionStatus") != "collected" or not isinstance(ws.get("users"), list):
            return "missing_evidence", {"reason_code": "principal_inventory_incomplete"}
        users = ws["users"]
        if rule_id == "SEC-001" and "allowed_principal_ids" in policy:
            if any(_principal_id(user) is None for user in users):
                return "unknown", {"reason_code": "principal_identity_unresolved"}
            allowed = {value.lower() for value in policy["allowed_principal_ids"]}
            outside = sum(_principal_id(user) not in allowed for user in users)
            return ("fail" if outside else "pass"), {"outside_principal_count": outside,
                                                      "assessment_kind": "declared_direct_grant_boundary",
                                                      "limitations": "Does not establish transitive membership or effective external access."}
        if rule_id == "SEC-002" and "allowed_roles" in policy:
            if any(not user.get("groupUserAccessRight") for user in users):
                return "unknown", {"reason_code": "principal_role_unresolved"}
            outside = sum(user["groupUserAccessRight"] not in policy["allowed_roles"] for user in users)
            return ("fail" if outside else "pass"), {"outside_role_count": outside,
                                                      "limitations": "Direct workspace roles only; not effective item permissions."}
        if rule_id == "GOV-001":
            if any(not user.get("groupUserAccessRight") or _principal_id(user) is None
                   or not user.get("principalType") for user in users):
                return "unknown", {"reason_code": "administrator_identity_unresolved"}
            eligible = {value.lower() for value in policy["eligible_admin_ids"]} if "eligible_admin_ids" in policy else None
            independent = {
                _principal_id(user) for user in users
                if user["groupUserAccessRight"] == "Admin" and user["principalType"] == "User"
                and (eligible is None or _principal_id(user) in eligible)
            }
            if len(independent) < effective["thresholds"]["min_admins"] and any(
                user["groupUserAccessRight"] == "Admin" and user["principalType"] == "Group" for user in users
            ):
                return "unknown", {"reason_code": "administrator_group_membership_unverified",
                                    "eligible_independent_admin_count": len(independent)}
            return ("pass" if len(independent) >= effective["thresholds"]["min_admins"] else "fail"), {
                "eligible_independent_admin_count": len(independent),
                "required_count": effective["thresholds"]["min_admins"],
                "limitations": "Groups and applications are not assumed to contain independent maintainers.",
            }
    if rule_id == "OPS-001":
        entries = raw["git_integration.json"].get("workspaces")
        if not isinstance(entries, list):
            return "missing_evidence", {"reason_code": "git_connection_unavailable"}
        entry = next((row for row in entries if isinstance(row, dict)
                      and str(row.get("workspaceId") or "").lower() == str(ws["id"]).lower()), None)
        if entry is None or entry.get("error") or not isinstance(entry.get("connected"), bool):
            return "missing_evidence", {"reason_code": "git_connection_unavailable"}
        details = entry.get("gitProviderDetails") or {}
        return ("pass" if entry["connected"] else "fail"), {
            "assessment_kind": "git_connection", "git_connected": entry["connected"],
            "git_provider": details.get("gitProviderType") if isinstance(details, dict) else None,
            "limitations": "A connection is evidence of source control, not of its branching or review process.",
        }
    if rule_id == "ARCH-003" and "allowed_storage_modes" in policy:
        models = [item for item in _items(ws) if item.get("type") == "SemanticModel"]
        if not _item_inventory_complete(ws):
            return "missing_evidence", {"reason_code": "model_inventory_incomplete"}
        if not models:
            return "not_applicable", {"reason": "No semantic models in complete workspace inventory."}
        rows = _rows(model_definitions, "models", ws["id"])
        modes = {str(row["id"]).lower(): row.get("storageModes") for row in rows if not row.get("error")}
        if any(not isinstance(modes.get(str(item["id"]).lower()), list)
               or not modes[str(item["id"]).lower()] for item in models):
            return "missing_evidence", {"reason_code": "storage_bindings_unavailable"}
        if any(set(modes[str(item["id"]).lower()]) - {"Import", "DirectQuery", "DirectLake", "Dual"} for item in models):
            return "unknown", {"reason_code": "storage_mode_unsupported"}
        outside = [item["id"] for item in models if set(modes[str(item["id"]).lower()]) - set(policy["allowed_storage_modes"])]
        return ("fail" if outside else "pass"), {"violating_item_ids": outside,
                                                "assessment_kind": "declared_storage_requirement",
                                                "limitations": "No migration feasibility or runtime fallback claim."}
    return "unknown", {"reason_code": "policy_incomplete",
                       "reason": "The requirement is enabled but does not declare the fields this rule evaluates."}


_WORKSPACE_SIGNALS = {
    "GOV-005": native_rules.endorsement, "SEC-012": native_rules.label_coverage,
    "ARCH-014": native_rules.cross_workspace,
    "GOV-007": enrichment_rules.inactive_workspace, "GOV-008": enrichment_rules.stale_items,
    "OPS-002": enrichment_rules.failure_ratio, "OPS-003": enrichment_rules.expensive_items,
    "OPS-004": enrichment_rules.capacity_throttling, "OPS-005": enrichment_rules.refresh_failures,
    "OPS-006": native_rules.capacity_grade, "OPS-007": enrichment_rules.workspace_capacity_share,
    "ARCH-017": native_rules.empty_workspace, "ARCH-018": native_rules.orphan_reports,
    "ARCH-019": native_rules.unused_models, "OPS-008": native_rules.capacity_state,
    "OPS-009": native_rules.capacity_admins, "OPS-010": native_rules.deployment_pipeline,
    "SEC-020": native_rules.onelake_roles, "SEC-021": native_rules.workspace_network,
    "SEC-022": native_rules.item_grants, "SEC-017": native_rules.guest_members,
    "SEC-018": native_rules.app_roles, "REL-003": native_rules.production_dependencies,
    "SEC-026": model_security_rules.posture,
    "REL-002": lambda ws, effective, ctx: native_rules.pipeline_failure_paths(ws, ctx),
    "SEC-019": lambda ws, effective, ctx: enrichment_rules.org_wide_links(ws, ctx),
}


def _assess(rule_id: str, ws: dict[str, Any], effective: dict[str, Any],
            ctx: Context) -> tuple[str, dict[str, Any]]:
    raw = ctx.raw
    applicability = effective["applicability"]
    if applicability != "applicable":
        return ("not_applicable" if applicability in {"disabled", "not_applicable"} else "unknown"), {
            "reason": effective["reason"], "applicability": applicability,
        }
    if rule_id in {"ARCH-004", "ARCH-005"}:
        return _structural(rule_id, ws, raw["pipeline_definitions.json"])
    if rule_id in native_rules.SIGNAL_RULES:
        return native_rules.notebook_pattern(rule_id, ws, ctx)
    if rule_id == "ARCH-013":
        return native_rules.pipeline_resilience(ws, ctx)
    if rule_id == "ARCH-015":
        return native_rules.legacy_items(ws)
    if rule_id == "GOV-003":
        return native_rules.orphaned_state(ws)
    if rule_id == "SEC-006":
        return enrichment_rules.published_to_web(ws, ctx)
    if rule_id in _WORKSPACE_SIGNALS:
        return _WORKSPACE_SIGNALS[rule_id](ws, effective, ctx)
    kind = RULE_KINDS[rule_id]
    if kind == "coverage":
        complete = _item_inventory_complete(ws) and ws.get("usersCollectionStatus") == "collected"
        return ("info" if complete else "missing_evidence"), {
            "coverage_status": "complete" if complete else "partial",
            "items_collected": ws.get("itemsCollectionStatus") == "collected",
            "principals_collected": ws.get("usersCollectionStatus") == "collected",
            "reason": "Whether the workspace inventory needed for this review was available; absence is never a pass.",
        }
    if kind == "signal":
        evidence: dict[str, Any] = {"assessment_kind": "non_scored_context"}
        if rule_id == "ARCH-008" and _item_inventory_complete(ws):
            evidence.update(item_count=len(ws["items"]), review_above=effective["thresholds"]["monolith_max_items"])
        elif rule_id == "ARCH-009":
            if not _item_inventory_complete(ws):
                return "missing_evidence", {**evidence, "reason_code": "item_inventory_incomplete"}
            evidence["description_present"] = bool(ws.get("description"))
            entities = [ws, *_items(ws)]
            ratio = sum(bool(entity.get("description")) for entity in entities) / len(entities)
            minimum = effective["thresholds"]["description_coverage_min_ratio"]
            evidence.update(documentation_ratio=ratio, entity_count=len(entities),
                            undocumented_item_ids=listed(item["id"] for item in _items(ws) if not item.get("description")),
                            review_below=minimum, review_recommended=ratio < minimum,
                            limitations="Description metadata only; approved external documentation may satisfy purpose.")
        elif rule_id == "GOV-004":
            pattern = effective["thresholds"]["naming_pattern"]
            if pattern:
                names = [entity.get("name") or entity.get("displayName") for entity in [ws, *_items(ws)]]
                if not _item_inventory_complete(ws) or any(not isinstance(name, str) or not name for name in names):
                    return "missing_evidence", {**evidence, "reason_code": "naming_metadata_unavailable"}
                ratio = sum(re.fullmatch(pattern, name) is not None for name in names) / len(names)
                minimum = effective["thresholds"]["naming_coverage_min_ratio"]
                evidence.update(naming_ratio=ratio, entity_count=len(names),
                                misnamed_item_ids=listed(
                                    item["id"] for item in _items(ws)
                                    if re.fullmatch(pattern, item.get("name") or item.get("displayName")) is None),
                                review_below=minimum, review_recommended=ratio < minimum,
                                limitations="Naming is a human-review hint, not production or boundary classification.")
            else:
                evidence["reason"] = "No naming convention declared; no convention is inferred."
        elif rule_id == "SEC-009" and ws.get("usersCollectionStatus") == "collected":
            evidence.update(direct_principal_count=sum(user.get("principalType") != "Group" for user in ws["users"]),
                            review_above=effective["thresholds"]["broad_access_max_principals"])
        else:
            evidence["reason"] = "Context only; no count, name, endorsement or version constitutes an architectural defect."
        if rule_id in {"ARCH-008", "SEC-009"} and not (
            _item_inventory_complete(ws) if rule_id == "ARCH-008" else ws.get("usersCollectionStatus") == "collected"
        ):
            return "missing_evidence", {**evidence, "reason_code": "advisory_inventory_unavailable"}
        return "info", evidence
    policy = effective["policy"]
    if "required" not in policy:
        return "unknown", {"reason_code": "policy_undeclared",
                            "reason": "No architectural requirement declared for this workspace."}
    if not policy["required"]:
        return "not_applicable", {"reason": effective["reason"]}
    if policy.get("production_only"):
        if effective["environment"] == "unknown":
            return "unknown", {"reason_code": "environment_undeclared",
                                "reason": "Production is not inferred from workspace names."}
        if effective["environment"] != "production":
            return "not_applicable", {"reason": "Requirement explicitly limited to declared production workspaces."}
    return _native_policy(rule_id, ws, policy, effective, ctx)


def _tenant_setting(settings: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any] | None:
    by_name = {row.get("settingName"): row for row in settings if isinstance(row, dict)}
    return next((by_name[name] for name in names if name in by_name), None)


def _setting_scope(setting: dict[str, Any], enabled_is_secure: bool = False) -> tuple[str, dict[str, Any]]:
    """Disabled or limited to security groups passes; enabled for everyone fails.

    For a protective setting (enabled is the secure state) the result is the other way round.
    """
    enabled = setting.get("enabled")
    evidence = {
        "setting_name": setting.get("settingName"), "enabled": enabled,
        "enabled_group_count": setting.get("enabledSecurityGroupCount"),
        "excluded_group_count": setting.get("excludedSecurityGroupCount"),
        "can_specify_security_groups": setting.get("canSpecifySecurityGroups"),
    }
    if not isinstance(enabled, bool) or not isinstance(setting.get("enabledSecurityGroupCount"), int):
        return "missing_evidence", {**evidence, "reason_code": "setting_state_unavailable"}
    if enabled_is_secure:
        return ("pass", {**evidence, "reason": "The protective setting is enabled."}) if enabled else (
            "fail", {**evidence, "reason": "The protective setting is disabled."})
    if not enabled:
        return "pass", {**evidence, "reason": "The setting is disabled."}
    if setting["enabledSecurityGroupCount"] > 0:
        return "pass", {**evidence, "reason": "The setting is limited to security groups."}
    return "fail", {**evidence, "reason": "The setting is enabled for the entire organization."}


def _gateway_signal(gateways: Any, minimum: str) -> tuple[str, dict[str, Any]]:
    evidence: dict[str, Any] = {"assessment_kind": "non_scored_context"}
    unavailable = ("missing_evidence", {**evidence, "reason_code": "gateway_version_metadata_unavailable"})
    if not isinstance(gateways, list) or any(
        not isinstance(gateway, dict) or not isinstance(gateway.get("members"), list) or not gateway["members"]
        for gateway in gateways
    ):
        return unavailable
    members = [member for gateway in gateways for member in gateway["members"]]
    if any(not isinstance(member, dict) for member in members):
        return unavailable
    versions = [member.get("version") for member in members]
    if any(not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", version) for version in versions):
        return unavailable

    def version_key(value: str) -> tuple[int, ...]:
        parts = tuple(int(part) for part in value.split("."))
        return parts + (0,) * (4 - len(parts))

    single = sum(len(gateway["members"]) == 1 for gateway in gateways)
    below = sum(version_key(version) < version_key(minimum) for version in versions) if minimum else 0
    evidence.update(
        gateway_min_version=minimum, observed_versions=sorted(set(versions)),
        single_member_cluster_count=single, below_floor_count=below,
        review_recommended=len(set(versions)) > 1 or single > 0 or below > 0,
        limitations="Visible gateway clusters and versions only; not mapped to a workspace, and not a patch-compliance claim.",
    )
    return "info", evidence


def _assess_tenant(rule_id: str, effective: dict[str, Any], ctx: Context) -> tuple[str, dict[str, Any]]:
    """Evaluate a rule that applies to the whole tenant rather than to one workspace."""
    raw = ctx.raw
    if effective["applicability"] != "applicable":
        return ("not_applicable" if effective["applicability"] in {"disabled", "not_applicable"} else "unknown"), {
            "reason": effective["reason"], "applicability": effective["applicability"],
        }
    if rule_id == "ARCH-016":
        return native_rules.empty_capacity(ctx)
    if rule_id == "REL-001":
        return native_rules.capacity_concentration(effective, ctx)
    if rule_id == "SEC-027":
        return model_security_rules.declared_resolution(ctx)
    if rule_id == "GOV-006":
        return enrichment_rules.domain_coverage(effective, ctx)
    if rule_id == "GOV-009":
        return enrichment_rules.capacity_tags(effective, ctx)
    if rule_id == "SEC-010":
        return _gateway_signal(raw["gateways.json"].get("gateways"), effective["thresholds"]["gateway_min_version"])
    tenant = raw["tenant_settings.json"]
    settings = tenant.get("tenantSettings")
    recorded = tenant.get("collectionComplete") is True and isinstance(settings, list)
    if rule_id == "GOV-010":
        return ("info" if recorded else "missing_evidence"), {
            "coverage_status": "complete" if recorded else "unavailable",
            "recorded_setting_count": len(settings) if recorded else 0,
            "reason": "Tenant settings are recorded per run as an audit trail; reading them needs the Fabric administrator role.",
        }
    if not recorded:
        return "missing_evidence", {"reason_code": "tenant_settings_unavailable",
                                    "reason": "Tenant settings could not be read; they need the Fabric administrator role."}
    if rule_id == "SEC-023":
        return native_rules.network_switches(settings, ctx)
    if rule_id in TENANT_SIGNAL_SETTINGS:
        return native_rules.open_tenant_settings(TENANT_SIGNAL_SETTINGS[rule_id], settings)
    setting = _tenant_setting(settings, TENANT_SETTING_NAMES[rule_id])
    if setting is None:
        return "missing_evidence", {"reason_code": "setting_not_returned",
                                    "setting_names": list(TENANT_SETTING_NAMES[rule_id]),
                                    "reason": "The tenant did not return this setting; its API name may have changed."}
    status, evidence = _setting_scope(setting, rule_id in ENABLED_IS_SECURE)
    return status, {**evidence, "assessment_kind": "tenant_setting"}


def _recommendation(status: str, tenant_rule: bool, evidence: dict[str, Any]) -> str:
    where = "this tenant" if tenant_rule else "this capacity" if evidence.get("scope") == "capacity" else "this workspace"
    reason = evidence.get("reason_code")
    kind = evidence.get("assessment_kind")
    if reason == "source_not_enabled":
        return f"Connect {str(evidence.get('required_source')).upper()} in the Connections library to evaluate this rule."
    if status == "missing_evidence" and evidence.get("required_source"):
        return (f"Refresh the {str(evidence['required_source']).upper()} data or check read access to its Lakehouse, "
                "then rerun the review.")
    if status == "fail":
        if kind == "declared_model_security_requirement":
            return ("Add row-level security to the listed models (a role that can read the model, with a table filter that "
                    "restricts rows), or correct the declared list if the requirement is wrong."
                    if evidence.get("security_mechanism") == "rls" else
                    "Add object-level security (hide the table or column) in a role that can read the listed models, "
                    "or correct the declared list if the requirement is wrong.")
        if kind == "published_items":
            return "Remove the publish-to-web link of the listed items in the Fabric admin portal."
        if kind == "workspace_state":
            return "Assign an administrator to the workspace."
        if kind == "declared_environment_isolation":
            return "Move the production workspace to a capacity that hosts production workloads only."
        if kind == "declared_region_boundary":
            return "Move the workspace to a capacity in an approved region, or correct the declared regions."
        if tenant_rule:
            return ("Enable the setting in the Fabric admin portal." if "protective" in str(evidence.get("reason"))
                    else "Restrict the setting to security groups or disable it in the Fabric admin portal.")
        return "Align the workspace with its intended design, or correct the declared requirement if it is wrong."
    if status in {"unknown", "missing_evidence"}:
        if kind == "declaration_coverage":
            return ("Correct the model IDs in policy__rls_required_model_ids and policy__ols_required_model_ids, "
                    "or review the workspaces that hold them, then rerun the review.")
        if kind == "declared_model_security_requirement":
            return ("Give the review identity read and write access to the listed models so their definition can be "
                    "read, then rerun the review.")
        return ("Restore access to the tenant settings (Fabric administrator role) and rerun the review."
                if tenant_rule else
                "Declare the requirement for this workspace or restore the missing evidence, then rerun the review.")
    if status == "info":
        return ("Worth a review; this signal never lowers the score."
                if evidence.get("review_recommended") else "Context for the review; no action required.")
    if status == "not_applicable":
        return f"Not applicable to {where}."
    return "No action required."


def _read_enrichment(raw_dir: Path, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """FUAM and FCA facts, accepted only when they belong to this frozen review."""
    path = raw_dir / "far_evidence.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    provenance = value.get("_provenance") if isinstance(value, dict) else None
    expected = {
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256, "source": "enrichment",
        "configuration_sha256": snapshot["sha256"],
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
    }
    if not isinstance(provenance, dict) or any(provenance.get(key) != item for key, item in expected.items()):
        raise ValueError("Unbound or stale FUAM/FCA evidence: far_evidence.json. Run the collection stage again.")
    if aware_datetime(provenance.get("captured_at_utc")) < aware_datetime(snapshot["captured_at_utc"]):
        raise ValueError("FUAM/FCA evidence predates the frozen review: far_evidence.json")
    return value


def _capacity_subjects(workspaces: list[dict[str, Any]], raw: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One subject per capacity the reviewed workspaces run on, carrying the workspaces that depend on it."""
    names = {str(row.get("id") or "").lower(): row.get("displayName") or row.get("name")
             for row in (raw["capacity_metrics.json"].get("capacities") or []) if isinstance(row, dict)}
    hosted: dict[str, list[str]] = {}
    for ws in workspaces:
        capacity = str(ws.get("capacityId") or "").lower()
        if capacity:
            hosted.setdefault(capacity, []).append(str(ws["id"]).lower())
    return [{"id": None, "name": None, "capacityId": capacity, "scope": "capacity",
             "capacityName": names.get(capacity) or capacity, "workspaceIds": sorted(ids)}
            for capacity, ids in sorted(hosted.items())] or [{"id": None, "name": None}]


def analyze(raw_dir: Path, out_dir: Path, checklist: Path) -> Path:
    snapshot = read_snapshot(out_dir / "review_config.json")
    activate(out_dir / "review_config.json")
    rules = load_rules(checklist)
    if set(rules) != ACTIVE_RULE_IDS:
        raise ValueError("Rule catalogue does not match the approved architecture profile.")
    inventory = _read(raw_dir, "workspace_inventory.json", snapshot)
    workspaces = inventory.get("workspaces", [])
    if not isinstance(workspaces, list) or any(not isinstance(row, dict) or not row.get("id") for row in workspaces):
        raise ValueError("Invalid scoped workspace inventory.")
    raw = {name: _read(raw_dir, name, snapshot) for name in (
        "pipeline_definitions.json", "semantic_model_definitions.json", "git_integration.json", "gateways.json",
        "tenant_settings.json", "scanner.json", "capacity_metrics.json", "deployment_pipelines.json",
        "onelake_roles.json", "workspace_network.json",
    )}
    context = Context(snapshot, workspaces, raw, _read_enrichment(raw_dir, snapshot))
    findings = []
    scope_complete = inventory.get("workspaceListComplete") is True
    for rule_id in RULE_ORDER:
        tenant_rule = rule_id in TENANT_RULES
        if tenant_rule:
            subjects = [{"id": None, "name": None}]
        elif rule_id in CAPACITY_RULES and workspaces:
            subjects = _capacity_subjects(workspaces, raw)
        else:
            subjects = workspaces or [{"id": None, "name": None}]
        for ws in subjects:
            effective = resolve_rule(snapshot["configuration"], rule_id, ws)
            if not workspaces:
                status, evidence = (
                    ("not_applicable", {"reason": "No workspaces in complete review scope."}) if scope_complete else
                    ("missing_evidence", {"reason": "Workspace discovery evidence is unavailable."})
                )
            elif tenant_rule:
                status, evidence = _assess_tenant(rule_id, effective, context)
            else:
                status, evidence = _assess(rule_id, ws, effective, context)
            evidence.update(workspace_id=ws["id"], workspace_name=ws.get("name"),
                            policy_origins=effective["origins"], declared_environment=effective["environment"])
            if ws.get("scope") == "capacity":
                # Each workspace on the capacity is attributed to this one finding through affected_workspaces.
                evidence.update(scope="capacity", capacity_id=ws["capacityId"], capacity_name=ws["capacityName"],
                                workspace_count=len(ws["workspaceIds"]),
                                affected_workspaces=[{"workspace_id": wid} for wid in ws["workspaceIds"]])
            # A signal never passes or fails, so its advice must not read like a failed control's.
            advised = "info" if RULE_KINDS[rule_id] == "signal" and status in {"pass", "fail"} else status
            finding = make_finding(rules[rule_id], dimension=rules[rule_id]["dimension"], status=status, title=TITLES[rule_id],
                                   evidence=evidence, recommendation=_recommendation(advised, tenant_rule, evidence))
            finding.update(profile_id=PROFILE_ID, profile_sha256=PROFILE_SHA256,
                           configuration_sha256=snapshot["sha256"],
                           configuration_captured_at_utc=snapshot["captured_at_utc"])
            findings.append(finding)
    target = out_dir / "findings_architecture.json"
    write_findings(normalize_findings(findings), target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/far/raw")
    parser.add_argument("--out-dir", default="output/far")
    parser.add_argument("--checklist", default=str(ROOT / "config" / "review-checklist.yaml"))
    args = parser.parse_args()
    analyze(Path(args.raw_dir), Path(args.out_dir), Path(args.checklist))


if __name__ == "__main__":
    main()

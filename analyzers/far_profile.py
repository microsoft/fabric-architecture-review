# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The versioned architecture-review rule set, shared by every consuming surface."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROFILE_ID = "far-architecture-v1"
PROFILE_LOGIC_VERSION = 10

# Rule kinds: a *control* can pass or fail and contributes to the score; a *signal*
# is non-scored architectural context; *coverage* reports whether the evidence a
# review needs was available. IDs are contiguous within a dimension prefix and
# controls precede signals, which precede coverage, so the catalogue reads in order.
# review-checklist.yaml lists the original rules controls first, then signals, then coverage. Rules added
# later keep their identifiers stable and are appended in ID order within their dimension.
_GROUPS = {
    "control": (
        "ARCH-001", "ARCH-002", "ARCH-003", "ARCH-004", "ARCH-005", "ARCH-006", "ARCH-007",
        "GOV-001", "GOV-002", "GOV-003",
        "OPS-001",
        "SEC-001", "SEC-002", "SEC-003", "SEC-004", "SEC-005", "SEC-006", "SEC-007", "SEC-008",
        "SEC-024", "SEC-025",
    ),
    "signal": (
        "ARCH-008", "ARCH-009", "ARCH-010", "ARCH-011", "ARCH-012", "ARCH-013", "ARCH-014",
        "ARCH-015", "ARCH-016", "ARCH-017", "ARCH-018", "ARCH-019",
        "GOV-004", "GOV-005", "GOV-006", "GOV-007", "GOV-008", "GOV-009",
        "OPS-002", "OPS-003", "OPS-004", "OPS-005", "OPS-006", "OPS-007", "OPS-008", "OPS-009", "OPS-010",
        "REL-001", "REL-002", "REL-003",
        "SEC-009", "SEC-010", "SEC-011", "SEC-012", "SEC-013",
        "SEC-014", "SEC-015", "SEC-016", "SEC-017", "SEC-018", "SEC-019", "SEC-020", "SEC-021", "SEC-022", "SEC-023",
        "SEC-026",
    ),
    "coverage": ("ARCH-020", "GOV-010", "SEC-027"),
}
RULE_KINDS = {rule: kind for kind, rules in _GROUPS.items() for rule in rules}
ACTIVE_RULE_IDS = frozenset(RULE_KINDS)
NON_SCORED_KINDS = frozenset({"signal", "coverage"})
RULE_ORDER = tuple(sorted(RULE_KINDS))

# Tenant-wide settings behind each tenant-level rule, by the settingName the Fabric admin
# API returns (verified against a live tenant). If a tenant does not return the name the rule
# reports missing evidence, never a pass.
TENANT_SETTING_NAMES = {
    "GOV-002": ("FabricGAWorkloads",),
    "SEC-003": ("PublishToWeb",),
    "SEC-004": ("AllowExternalDataSharingSwitch",),
    "SEC-005": ("ServicePrincipalAccessPermissionAPIs",),
    "SEC-007": ("ExternalSharingV2",),
    "SEC-008": ("BlockResourceKeyAuthentication",),
    "SEC-013": ("AllowAccessOverPrivateLinks",),
}
# Settings where being enabled is the secure state. For every other setting, enabled for the
# whole organization is the finding.
ENABLED_IS_SECURE = frozenset({"SEC-008", "SEC-013"})

# Tenant settings behind the tenant-wide signals. A signal never fails: it reports which of its
# settings are open to the whole organization so the reviewer can decide whether that is intended.
TENANT_SIGNAL_SETTINGS = {
    "SEC-014": (
        "ExportToExcelSetting", "ExportToCsv", "ExportReport", "ExportToPowerPoint", "ExportToMHTML",
        "ExportToWord", "ExportToXML", "Printing", "AllowCVToExportDataToFileTenant", "AllowNotebookDataExport",
    ),
    "SEC-015": (
        "ShareLinkToEntireOrg", "AllowGuestUserToAccessSharedContent", "ElevatedGuestsTenant", "AllowGuestLookup",
        "EmailSubscriptionsToB2BUsers", "EmailSubscriptionsToExternalUsers", "EnableDatasetInPlaceSharing",
    ),
    "SEC-016": (
        "ServicePrincipalAccessGlobalAPIs", "AllowServicePrincipalsCreateAndUseProfiles", "Embedding",
        "CustomVisualsTenant", "AdminApisIncludeExpressions",
    ),
}
# Tenant switches that decide whether a restriction can be applied at all. They are reported, not judged as open or closed.
TENANT_SWITCH_SETTINGS = {
    "SEC-023": (
        "BlockAccessFromPublicNetworks", "WorkspaceBlockInboundAccess", "WorkspaceBlockOutboundAccess",
        "ConfigureWorkspaceLevelIPFirewallRules",
    ),
}
# Every setting a rule reads, by rule.
JUDGED_SETTING_NAMES = {**TENANT_SETTING_NAMES, **TENANT_SIGNAL_SETTINGS, **TENANT_SWITCH_SETTINGS}

# Rules that read FUAM or FCA data. They are evaluated only when that module is enabled and
# its data is fresh; otherwise they are not applicable or report missing evidence. The
# native baseline never depends on them.
ENRICHMENT_SOURCES = {
    "GOV-006": "fuam", "GOV-007": "fuam", "GOV-008": "fuam", "GOV-009": "fca",
    "OPS-002": "fuam", "OPS-003": "fuam", "OPS-004": "fuam", "OPS-005": "fuam", "OPS-007": "fuam",
    "SEC-006": "fuam", "SEC-019": "fuam",
}
# Rules about a capacity as a whole. One finding per capacity; every workspace on it points to that finding.
CAPACITY_RULES = frozenset({"OPS-004", "OPS-008", "OPS-009"})
# Rules evaluated once per review, not once per workspace.
TENANT_RULES = frozenset({*JUDGED_SETTING_NAMES, "GOV-006", "GOV-009", "GOV-010", "SEC-010", "SEC-027", "ARCH-016", "REL-001"})

_ROOT = Path(__file__).resolve().parents[1]
PROFILE_SHA256 = hashlib.sha256(json.dumps({
    "profile_id": PROFILE_ID, "logic_version": PROFILE_LOGIC_VERSION, "rules": RULE_KINDS,
    "implementation": {
        name: hashlib.sha256((_ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
        for name in (
            "analyzers/far_profile.py", "analyzers/far_review.py", "analyzers/structural_contracts.py",
            "analyzers/native_rules.py", "analyzers/enrichment_rules.py", "analyzers/rule_support.py", "orchestration/rule_policy.py", "config/review-checklist.yaml", "config/analyzer-registry.yaml",
            "analyzers/model_security_rules.py", "collectors/model_security.py",
        )
    },
}, sort_keys=True).encode()).hexdigest()


def normalize_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for finding in findings:
        rule_id = finding.get("rule_id")
        if rule_id not in ACTIVE_RULE_IDS:
            raise ValueError(f"Unknown FAR rule ID: {rule_id!r}")
        row = dict(finding)
        kind = RULE_KINDS[rule_id]
        if row.get("profile_id") != PROFILE_ID or row.get("profile_sha256") != PROFILE_SHA256:
            row.update(
                status="unknown", title="Earlier architectural finding requires reassessment",
                evidence={"reason_code": "profile_not_comparable"},
                recommendation="Run a new review with the current architectural profile.",
            )
        row.update(rule_kind=kind, profile_id=PROFILE_ID, profile_sha256=PROFILE_SHA256)
        if kind == "signal" and row.get("status") in {"pass", "fail"}:
            row["status"] = "info"
        if kind == "coverage":
            row["status"] = "missing_evidence" if row.get("status") in {"fail", "unknown", "missing_evidence"} else "info"
        row["scored"] = (
            kind == "control" and row.get("status") in {"pass", "fail"}
            and not (row.get("evidence") or {}).get("heuristic", False)
        )
        result.append(row)
    return result

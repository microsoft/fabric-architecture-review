# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evaluate tenant-wide Fabric / Power BI settings against the security & governance baseline.

Reads:  output/raw/tenant_settings.json
        config/review-checklist.yaml
Emits:  list of finding dicts (caller is responsible for merging into output/findings.json)

DATA SAFETY: Consumes the tenant settings JSON only — no data access.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

# --- Baseline: tenant settings that must be scoped (not enabled tenant-wide) ---
# Key = setting name as it appears in the Fabric admin tenantsettings response.
# Each entry includes the rule_id from config/review-checklist.yaml and the
# expectation that should hold.
BASELINE: Dict[str, Dict[str, Any]] = {
    # SEC-001: Publish to web must be disabled or scoped to a security group
    "PublishToWeb": {
        "rule_id": "SEC-001",
        "title": "Publish to web is disabled or restricted to a security group",
        "expect": "disabled_or_scoped",
    },
    # SEC-002: Export data must be restricted to a security group
    "ExportData": {
        "rule_id": "SEC-002",
        "title": "Export data is restricted to a security group",
        "expect": "scoped",
    },
    # TENANT-001: Create Fabric items must be scoped to a security group
    "CreateFabricItem": {
        "rule_id": "TENANT-001",
        "title": "Users can create Fabric items is scoped to a security group",
        "expect": "scoped",
    },
    # TENANT-002: SP Fabric API access must be enabled and scoped to a SG
    "ServicePrincipalAccess": {
        "rule_id": "TENANT-002",
        "title": "Service principals can use Fabric APIs is enabled and scoped",
        "expect": "enabled_and_scoped",
    },
    # TENANT-003: External data sharing must be disabled or scoped
    "AllowExternalDataSharing": {
        "rule_id": "TENANT-003",
        "title": "External data sharing is disabled or scoped to a security group",
        "expect": "disabled_or_scoped",
        "aliases": ("ExternalDataSharing", "ExternalDataSharingReceiveSettings",
                    "ExternalDataSharingSendSettings", "ShareReportWithEntireOrg"),
    },
    # TENANT-004: Custom (uncertified) visuals must be restricted
    "CustomVisualsTenantSettings": {
        "rule_id": "TENANT-004",
        "title": "Uncertified / custom visuals are restricted to a security group",
        "expect": "scoped",
        "aliases": ("AddCertifiedVisualsOnly", "AddAndUseCertifiedVisualsOnly",
                    "OrgVisualsTenantSetting", "CustomVisualsTenantSetting"),
    },
    # TENANT-005: R / Python visual & script runtime restricted
    "RScriptVisualsTenantSettings": {
        "rule_id": "TENANT-005",
        "title": "R / Python visuals and scripts are restricted to a security group",
        "expect": "scoped",
        "aliases": ("RPythonVisualsTenantSettings", "PythonVisualsTenantSettings",
                    "PythonScriptsTenantSettings", "RScriptVisualsTenantSetting"),
    },
}


def _load_rules(checklist_path: Path) -> Dict[str, Dict[str, Any]]:
    with checklist_path.open("r", encoding="utf-8-sig") as f:
        raw = yaml.safe_load(f)
    return {r["id"]: r for r in raw.get("rules", [])}


def _iter_settings(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    # The Fabric admin tenantsettings response wraps the array under "tenantSettings".
    return payload.get("tenantSettings") or payload.get("value") or []


def _evaluate(setting: Dict[str, Any], expect: str) -> tuple[str, str]:
    """Return (status, reason) where status is 'pass' | 'fail' | 'info'."""
    enabled = bool(setting.get("enabled"))
    can_specify_security_groups = bool(setting.get("canSpecifySecurityGroups"))
    enabled_security_groups = setting.get("enabledSecurityGroups") or []
    excluded_security_groups = setting.get("excludedSecurityGroups") or []
    tenant_setting_group = setting.get("tenantSettingGroup")  # rarely present

    is_scoped = bool(enabled_security_groups) or bool(excluded_security_groups)

    if expect == "disabled_or_scoped":
        if not enabled:
            return "pass", "Setting is disabled tenant-wide."
        if is_scoped:
            return "pass", f"Enabled but scoped to {len(enabled_security_groups)} security group(s)."
        return "fail", "Setting is enabled for the entire organization with no security group scoping."

    if expect == "scoped":
        if not enabled:
            return "pass", "Setting is disabled (effectively scoped to no one)."
        if is_scoped:
            return "pass", f"Enabled but scoped to {len(enabled_security_groups)} security group(s)."
        return "fail", "Setting is enabled for the entire organization with no security group scoping."

    if expect == "enabled_and_scoped":
        if not enabled:
            return "fail", "Setting is disabled — service principals cannot use Fabric APIs."
        if is_scoped:
            return "pass", f"Enabled and scoped to {len(enabled_security_groups)} security group(s)."
        return "fail", "Enabled tenant-wide; should be scoped to an automation security group."

    return "info", f"Unknown expectation '{expect}'."


def analyze(
    raw_dir: str | os.PathLike = "output/raw",
    checklist_path: str | os.PathLike = "config/review-checklist.yaml",
) -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    # Accept either a directory (standard interface, matching every other
    # analyzer) or a direct path to the JSON file (backward compatible with the
    # old --raw <file> call site).
    raw_path = raw_dir if raw_dir.suffix == ".json" else raw_dir / "tenant_settings.json"
    checklist_path = Path(checklist_path)

    with raw_path.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    rules = _load_rules(checklist_path)
    settings_by_name = {s.get("settingName"): s for s in _iter_settings(payload)}

    findings: List[Dict[str, Any]] = []
    for setting_name, spec in BASELINE.items():
        rule = rules.get(spec["rule_id"], {})
        setting = settings_by_name.get(setting_name)
        if setting is None:
            for alias in spec.get("aliases", ()):
                if alias in settings_by_name:
                    setting = settings_by_name[alias]
                    setting_name = alias
                    break
        if setting is None:
            findings.append(
                {
                    "rule_id": spec["rule_id"],
                    "dimension": "tenant_settings",
                    "severity": rule.get("severity", "medium"),
                    "status": "info",
                    "title": spec["title"],
                    "evidence": {
                        "setting_name": setting_name,
                        "present": False,
                        "reason": "Setting not returned by the tenant settings API.",
                    },
                    "recommendation": (
                        f"Setting '{setting_name}' was not returned by the tenant settings "
                        "API. Verify the signed-in user holds Fabric Administrator (or Power BI "
                        "Administrator) and that the setting name has not been renamed."
                    ),
                    "microsoft_learn_url": rule.get("microsoft_learn_url"),
                }
            )
            continue

        status, reason = _evaluate(setting, spec["expect"])
        findings.append(
            {
                "rule_id": spec["rule_id"],
                "dimension": "tenant_settings",
                "severity": rule.get("severity", "medium"),
                "status": status,
                "title": spec["title"],
                "evidence": {
                    "setting_name": setting_name,
                    "enabled": setting.get("enabled"),
                    "enabledSecurityGroups": setting.get("enabledSecurityGroups"),
                    "excludedSecurityGroups": setting.get("excludedSecurityGroups"),
                    "canSpecifySecurityGroups": setting.get("canSpecifySecurityGroups"),
                    "reason": reason,
                },
                "recommendation": rule.get("description", "").strip(),
                "microsoft_learn_url": rule.get("microsoft_learn_url"),
            }
        )

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-dir",
        default="output/raw",
        help="Directory containing tenant_settings.json (standard analyzer interface).",
    )
    parser.add_argument(
        "--raw",
        default=None,
        help="Deprecated: direct path to tenant_settings.json. Prefer --raw-dir.",
    )
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_tenant_settings.json")
    args = parser.parse_args()

    source = args.raw if args.raw else args.raw_dir
    findings = analyze(source, args.checklist)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2, ensure_ascii=False)

    fail_count = sum(1 for x in findings if x["status"] == "fail")
    print(f"Tenant settings: {len(findings)} rules evaluated, {fail_count} fail(s). Wrote {out}")


if __name__ == "__main__":
    main()

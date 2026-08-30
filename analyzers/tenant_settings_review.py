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

from analyzers._common import load_rules, make_finding


def _iter_settings(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    # The Fabric admin tenantsettings response wraps the array under "tenantSettings".
    return payload.get("tenantSettings") or payload.get("value") or []


def _evaluate(setting: Dict[str, Any], expect: str) -> tuple[str, str]:
    """Return the canonical status and supporting reason."""
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

    return "unknown", f"Unknown expectation '{expect}'."


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

    rules = load_rules(checklist_path)
    settings_by_name = {s.get("settingName"): s for s in _iter_settings(payload)}

    findings: List[Dict[str, Any]] = []
    for rule in rules.values():
        spec = rule.get("tenant_setting")
        if not isinstance(spec, dict):
            continue
        setting_name = str(spec["name"])
        setting = settings_by_name.get(setting_name)
        if setting is None:
            for alias in spec.get("aliases", ()):
                if alias in settings_by_name:
                    setting = settings_by_name[alias]
                    setting_name = alias
                    break
        if setting is None:
            findings.append(make_finding(
                rule,
                dimension=rule["dimension"],
                status="missing_evidence",
                title=spec["title"],
                evidence={
                    "setting_name": setting_name,
                    "present": False,
                    "reason": "Setting not returned by the tenant settings API.",
                },
                recommendation=(
                    f"Setting '{setting_name}' was not returned by the tenant settings "
                    "API. Verify the signed-in user holds Fabric Administrator (or Power BI "
                    "Administrator) and that the setting name has not been renamed."
                ),
            ))
            continue

        status, reason = _evaluate(setting, spec["expect"])
        findings.append(make_finding(
            rule,
            dimension=rule["dimension"],
            status=status,
            title=spec["title"],
            evidence={
                "setting_name": setting_name,
                "enabled": setting.get("enabled"),
                "enabledSecurityGroups": setting.get("enabledSecurityGroups"),
                "excludedSecurityGroups": setting.get("excludedSecurityGroups"),
                "canSpecifySecurityGroups": setting.get("canSpecifySecurityGroups"),
                "reason": reason,
            },
            recommendation=rule.get("description", "").strip(),
        ))

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

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared helpers for analyzers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import yaml
from orchestration.project import load_environment

# Load .env once when any analyzer imports the helpers so environment-driven
# settings apply regardless of which entry point runs.
load_environment()

FINDING_STATUSES = frozenset({
    "pass", "fail", "info", "not_applicable", "unknown", "missing_evidence",
})


def load_rules(checklist_path: str | Path) -> Dict[str, Dict[str, Any]]:
    p = Path(checklist_path)
    with p.open("r", encoding="utf-8-sig") as f:
        raw = yaml.safe_load(f)
    return {r["id"]: r for r in raw.get("rules", []) if r.get("enabled", True)}


def make_finding(
    rule: Dict[str, Any],
    *,
    dimension: str,
    status: str,
    title: str,
    evidence: Dict[str, Any],
    recommendation: str,
) -> Dict[str, Any]:
    normalized_status = status.lower()
    if normalized_status not in FINDING_STATUSES:
        raise ValueError(
            f"Unsupported finding status '{status}'. Expected one of: "
            f"{', '.join(sorted(FINDING_STATUSES))}"
        )
    return {
        "rule_id": rule.get("id"),
        "dimension": dimension,
        "severity": rule.get("severity", "medium"),
        "status": normalized_status,
        "title": title,
        "evidence": evidence,
        "recommendation": recommendation,
        "microsoft_learn_url": rule.get("microsoft_learn_url"),
    }


def write_findings(findings: List[Dict[str, Any]], out_path: str | Path) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2, ensure_ascii=False)

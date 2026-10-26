# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Fail-closed consumption boundary shared by every FAR output."""
from __future__ import annotations

import re
from typing import Any

from analyzers.far_profile import (
    ACTIVE_RULE_IDS, NON_SCORED_KINDS, PROFILE_ID, RULE_KINDS, normalize_findings,
)

_FORBIDDEN_KEYS = re.compile(
    r"expression|vertipaq|cardinality|dictionary|total.?size|data.?size|hierarchy.?size|"
    r"refresh|execution|duration|avg.?hours|utilization|"
    r"billing|savings|cost.?estimate|right.?siz|signal.?details|raw.?source|secret|password|token",
    re.IGNORECASE,
)


def sanitize_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_evidence(item) for key, item in value.items()
                if not _FORBIDDEN_KEYS.search(str(key))}
    if isinstance(value, list):
        return [sanitize_evidence(item) for item in value]
    return value


def architectural_findings(findings: list[dict]) -> list[dict]:
    result = []
    for finding in normalize_findings(findings):
        if finding.get("rule_id") not in ACTIVE_RULE_IDS:
            continue
        row = dict(finding)
        kind = RULE_KINDS[row["rule_id"]]
        row["profile_id"] = PROFILE_ID
        row["rule_kind"] = kind
        if kind in NON_SCORED_KINDS:
            # A signal never passes or fails, but "not applicable" and "missing evidence" stay visible.
            kept = {"info", "not_applicable", "unknown", "missing_evidence"} if kind == "signal" else {
                "unknown", "missing_evidence"}
            row["status"] = row.get("status") if row.get("status") in kept else "info"
        row["is_scored"] = bool(row.get("scored")) and kind not in NON_SCORED_KINDS and row.get("status") in {"pass", "fail"}
        row["evidence"] = sanitize_evidence(row.get("evidence") or {})
        result.append(row)
    return result


def assessment(findings: list[dict]) -> dict:
    counts = {status + "_count": 0 for status in (
        "pass", "fail", "info", "not_applicable", "unknown", "missing_evidence")}
    controls = [f for f in findings if f.get("rule_kind") not in NON_SCORED_KINDS]
    for finding in findings:
        status = finding.get("status", "unknown")
        counts[(status if status + "_count" in counts else "unknown") + "_count"] += 1
    assessed = sum(bool(f.get("is_scored")) and f.get("status") in {"pass", "fail"} for f in controls)
    passed = sum(bool(f.get("is_scored")) and f.get("status") == "pass" for f in controls)
    applicable = sum(f.get("status") != "not_applicable" for f in controls)
    return {**counts, "total_findings": len(findings), "total": len(findings),
            "assessed_count": assessed,
            "assessment_coverage": round(100 * assessed / applicable, 1) if applicable else None,
            "score": round(100 * passed / assessed, 1) if assessed else None,
            "score_status": "assessed" if assessed else "no_assessable_controls"}

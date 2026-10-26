# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Plain-language meaning and next action for every finding, from config/rule-guidance.yaml.

Guidance only explains a result. It is never an input to a rule, a score or the rule-logic hash.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from analyzers.far_profile import ACTIVE_RULE_IDS, ENRICHMENT_SOURCES, NON_SCORED_KINDS, RULE_KINDS
from orchestration.review_config import ROOT, read_yaml

NO_ACTION = "No action required."
_GENERIC_ACTION = "Review the evidence for this finding and rerun the review after addressing it."


@lru_cache(maxsize=1)
def guidance() -> dict[str, Any]:
    data = read_yaml(ROOT / "config" / "rule-guidance.yaml")
    rules = data.get("rules") or {}
    missing = sorted(ACTIVE_RULE_IDS - set(rules))
    if missing:
        raise ValueError("config/rule-guidance.yaml has no guidance for: " + ", ".join(missing))
    for rule_id, entry in rules.items():
        if set(entry) != {"why", "fix", "configure"} or not entry["why"] or not entry["fix"]:
            raise ValueError(f"config/rule-guidance.yaml entry {rule_id} needs why, fix and configure.")
    return data


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def reason_code(finding: dict[str, Any]) -> str:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    code = evidence.get("reason_code")
    if not code and isinstance(evidence.get("reason_codes"), list) and evidence["reason_codes"]:
        code = evidence["reason_codes"][0]
    return str(code or "")


def explain(finding: dict[str, Any]) -> dict[str, str]:
    """Meaning of the status, the next action, and the rule's why and fix text."""
    data = guidance()
    rule = data["rules"].get(finding.get("rule_id"), {})
    status = finding.get("status")
    code = reason_code(finding)
    reason = data["reasons"].get(code, {})
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    if status in {"unknown", "missing_evidence", "not_applicable"}:
        meaning = reason.get("meaning") or _text(evidence.get("reason")) or data["statuses"].get(status, "")
        action = reason.get("action") or (NO_ACTION if status == "not_applicable" else _GENERIC_ACTION)
        configure = _text(rule.get("configure"))
        if configure:
            action = action.replace("(see Configure for the rule)", f"({configure})")
    elif status == "fail":
        meaning, action = data["statuses"]["fail"], rule.get("fix", _GENERIC_ACTION)
    elif status == "info":
        meaning = data["statuses"]["info"]
        action = ("Worth a review: " + rule["fix"]) if evidence.get("review_recommended") and rule else NO_ACTION
    else:
        meaning, action = data["statuses"].get(status, ""), NO_ACTION
    suggested = status == "info" and bool(evidence.get("review_recommended"))
    return {
        "reason_code": code, "status_meaning": _text(meaning), "next_action": _text(action),
        "review_suggested": "Yes" if suggested else "No",
        "why_it_matters": _text(rule.get("why")), "how_to_fix": _text(rule.get("fix")),
        "configure": _text(rule.get("configure")),
    }


def collection_guidance(status: str | None, reason: str | None) -> tuple[str, str]:
    """Notice and next action for an evidence-collection record (not a rule result)."""
    data = guidance()
    reason = str(reason or "")
    if reason in data["reasons"]:
        entry = data["reasons"][reason]
        return _text(entry["meaning"]), _text(entry["action"])
    entry = data["collection"].get(str(status or ""), {})
    return _text(reason or entry.get("meaning")), _text(entry.get("action"))


def requirement(rule_id: str) -> str:
    """What a rule needs to be assessable, for the rule catalogue."""
    source = ENRICHMENT_SOURCES.get(rule_id)
    if source:
        return source.upper() + " connected and fresh"
    if _text(guidance()["rules"][rule_id]["configure"]):
        return "A declaration (see Configure)"
    return "Nothing beyond the collected metadata"


def catalog(titles: dict[str, str] | None = None, checklist: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if titles is None:
        from analyzers.far_review import TITLES as titles
    if checklist is None:
        checklist = {rule["id"]: rule for rule in read_yaml(ROOT / "config" / "review-checklist.yaml")["rules"]}
    rows = []
    for rule_id in sorted(ACTIVE_RULE_IDS):
        entry, rule = guidance()["rules"][rule_id], checklist[rule_id]
        kind = RULE_KINDS[rule_id]
        rows.append({
            "rule_id": rule_id, "title": titles[rule_id], "dimension": rule["dimension"], "rule_kind": kind,
            "severity": rule["severity"], "is_scored": int(kind not in NON_SCORED_KINDS),
            "what_it_checks": _text(rule["description"]), "why_it_matters": _text(entry["why"]),
            "how_to_fix": _text(entry["fix"]), "configure": _text(entry["configure"]),
            "requires": requirement(rule_id), "microsoft_learn_url": rule.get("microsoft_learn_url", ""),
        })
    return rows

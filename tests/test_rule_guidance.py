# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Every rule and every reason a result can carry has plain-language guidance."""
import re
from pathlib import Path

import pytest

from analyzers.far_profile import ACTIVE_RULE_IDS, ENRICHMENT_SOURCES
from reports.rule_guidance import NO_ACTION, catalog, explain, guidance, reason_code

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [*ROOT.glob("analyzers/*.py"), ROOT / "collectors" / "enrichment_facts.py"]
CODE_PATTERNS = (
    r'reason_code["\']?\s*[:=]\s*["\']([a-z0-9_]+)["\']',
    r'["\']reason_code["\'],\s*["\']([a-z0-9_]+)["\']',
    r'Unavailable\(["\']([a-z0-9_]+)["\']\)',
    r'["\']([a-z]+_(?:unavailable|incomplete|undeclared|missing|unsupported|stale|failed|limited|disabled|enabled))["\']',
)


def codes_in_source():
    found = set()
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for pattern in CODE_PATTERNS:
            found.update(re.findall(pattern, text))
    return found


def test_every_active_rule_has_guidance_and_nothing_else():
    assert set(guidance()["rules"]) == ACTIVE_RULE_IDS


def test_guidance_texts_are_short_and_complete():
    for rule_id, entry in guidance()["rules"].items():
        assert 30 <= len(entry["why"]) <= 260, rule_id
        assert 20 <= len(entry["fix"]) <= 260, rule_id
        assert isinstance(entry["configure"], str)


def test_every_reason_code_in_the_code_has_guidance():
    known = set(guidance()["reasons"])
    missing = sorted(codes_in_source() - known)
    assert not missing, "Add these reason codes to config/rule-guidance.yaml: " + ", ".join(missing)


def test_rules_that_need_a_module_say_so_in_the_catalogue():
    rows = {row["rule_id"]: row for row in catalog()}
    assert len(rows) == len(ACTIVE_RULE_IDS) == 70
    for rule_id, module in ENRICHMENT_SOURCES.items():
        assert module.upper() in rows[rule_id]["requires"]
    assert rows["ARCH-001"]["requires"].startswith("A declaration")
    assert rows["ARCH-004"]["requires"] == "Nothing beyond the collected metadata"
    assert all(row["microsoft_learn_url"].startswith("https://learn.microsoft.com/") for row in rows.values())


def finding(status, rule="ARCH-001", **evidence):
    return {"rule_id": rule, "status": status, "evidence": evidence}


@pytest.mark.parametrize("status, evidence, action_contains", [
    ("pass", {}, NO_ACTION),
    ("fail", {}, "Move or delete"),
    ("info", {}, NO_ACTION),
    ("info", {"review_recommended": True}, "Worth a review"),
    ("unknown", {"reason_code": "policy_undeclared"}, "policy__allowed_item_types"),
    ("missing_evidence", {"reason_code": "source_data_stale"}, "max_age_hours"),
    ("not_applicable", {"reason": "Evaluated for declared production workspaces only."}, NO_ACTION),
])
def test_explain_gives_a_meaning_and_a_next_action_for_every_status(status, evidence, action_contains):
    result = explain(finding(status, **evidence))
    assert result["status_meaning"] and action_contains in result["next_action"]
    assert result["why_it_matters"] and result["how_to_fix"]


def test_not_applicable_uses_the_reason_the_rule_gave():
    result = explain(finding("not_applicable", **{"reason": "Evaluated for declared production workspaces only."}))
    assert result["status_meaning"] == "Evaluated for declared production workspaces only."


def test_unknown_reason_codes_still_get_a_generic_action():
    result = explain(finding("unknown", reason_code="brand_new_reason"))
    assert result["reason_code"] == "brand_new_reason" and result["next_action"]


def test_reason_code_reads_the_first_of_a_list():
    assert reason_code({"evidence": {"reason_codes": ["definition_unavailable", "x"]}}) == "definition_unavailable"
    assert reason_code({"evidence": {}}) == ""

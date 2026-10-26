# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Gold carries the meaning and next action of every finding, the rule catalogue and the tenant posture."""
import json

import pytest

from analyzers.far_profile import ACTIVE_RULE_IDS
from reports.gold_layer import _posture, build_gold
from tests._far_surfaces import finding, raw


@pytest.fixture
def gold(tmp_path, monkeypatch):
    raw(tmp_path, monkeypatch)
    findings = [
        finding("ARCH-001", status="fail"),
        finding("ARCH-003", status="unknown", evidence={"workspace_id": "x", "reason_code": "policy_undeclared"}),
        finding("ARCH-020", status="info"),
    ]
    findings[1]["evidence"] = {**findings[0]["evidence"], "reason_code": "policy_undeclared"}
    (tmp_path / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
    return build_gold(findings, tmp_path, run_id="guide", run_timestamp="2026-10-13T08:00:00Z", check_remote=False)


def test_every_finding_row_says_what_it_means_and_what_to_do(gold):
    rows = {row["rule_id"]: row for row in gold["gold_findings"]}
    assert rows["ARCH-001"]["next_action"].startswith("Move or delete")
    assert rows["ARCH-001"]["why_it_matters"] and rows["ARCH-001"]["how_to_fix"]
    unknown = rows["ARCH-003"]
    assert unknown["reason_code"] == "policy_undeclared"
    assert "policy__allowed_storage_modes" in unknown["next_action"]
    assert "declare" in unknown["status_meaning"].lower()
    assert all(row["status_meaning"] and row["next_action"] for row in rows.values())


def test_rule_description_explains_the_rule_not_just_its_title(gold):
    row = next(row for row in gold["gold_findings"] if row["rule_id"] == "ARCH-001")
    assert row["rule_description"] != row["title"] and len(row["rule_description"]) > 40


def test_the_rule_catalogue_is_published_with_every_active_rule(gold):
    assert {row["rule_id"] for row in gold["gold_rules"]} == ACTIVE_RULE_IDS
    assert all(row["what_it_checks"] and row["why_it_matters"] and row["how_to_fix"] for row in gold["gold_rules"])


def test_rules_that_could_not_decide_are_not_listed_as_evidence_coverage(gold):
    families = {row["evidence_family"] for row in gold["gold_source_coverage"]}
    assert "ARCH-003" not in families
    assert "ARCH-020" in families


def test_no_catalogue_is_published_without_a_bound_review(tmp_path, monkeypatch):
    raw(tmp_path, monkeypatch)
    (tmp_path / "run_manifest.json").write_text(json.dumps({}), encoding="utf-8")
    tables = build_gold([finding()], tmp_path, run_id="unbound", run_timestamp="2026-10-13T08:00:00Z",
                        check_remote=False)
    assert tables["gold_rules"] == [] and tables["gold_findings"] == []


@pytest.mark.parametrize("rule, setting, expected", [
    ("SEC-003", {"enabled": True}, "Judged by a rule"),
    (None, {"enabled": False}, "Disabled"),
    (None, {"enabled": None}, "Disabled"),
    (None, {"enabled": True, "enabledSecurityGroupCount": 2}, "Limited to groups"),
    (None, {"enabled": True, "canSpecifySecurityGroups": True}, "Open to everyone (can be limited)"),
    (None, {"enabled": True, "canSpecifySecurityGroups": False}, "Enabled (cannot be limited)"),
])
def test_tenant_setting_posture(rule, setting, expected):
    assert _posture(rule, setting) == expected

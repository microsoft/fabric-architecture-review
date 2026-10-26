# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""What Gold records about a capacity: its administrators, and what FCA adds."""
import json
from pathlib import Path

from reports.gold_layer import _fca_capacities, build_gold

CAP = "52a93989-4c00-446e-ae26-337dfb225a2b"


def gold(tmp_path: Path, admins):
    (tmp_path / "capacity_metrics.json").write_text(json.dumps({"workspaceScopeLimited": False, "capacities": [
        {"id": CAP, "displayName": "Prod", "sku": "F8", "state": "Active", "region": "West US 3", "admins": admins}]}))
    (tmp_path / "scanner.json").write_text(json.dumps({"workspaces": [{"id": "ws-1", "name": "Finance", "capacityId": CAP}]}))
    return build_gold([], tmp_path, run_id="r", run_timestamp="2026-09-01T12:00:00Z", check_remote=False)


def test_the_administrator_count_is_recorded_without_naming_anyone(tmp_path):
    tables = gold(tmp_path, ["alice@contoso.com", "bob@contoso.com"])
    assert tables["gold_capacities"][0]["admin_count"] == 2
    assert "contoso" not in json.dumps(tables)


def test_unreadable_administrators_are_unknown_not_zero(tmp_path):
    assert gold(tmp_path, None)["gold_capacities"][0]["admin_count"] is None


def test_fca_region_and_tags_are_shown_with_the_required_tags_still_missing():
    far = {"tables": {"far_evidence_capacity_resources": [
        {"capacity_id": CAP, "RegionId": "westus3", "ResourceName": "rvfabriccapacity"}]},
        "rule_facts": {"capacity_tags": {"status": "collected", "rows": [{"capacity_id": CAP, "tag_names": ["Owner", "env"]}]}}}
    frozen = {"profiles": {"rule_profiles": {"declared": {"rule_overrides": {"GOV-009": {
        "reason": "x", "policy": {"required": True, "required_tag_names": ["owner", "cost-center"]}}}}},
        "default_rule_profile": "declared", "workspaces": [], "capacities": []},
        "thresholds": {}, "sources": {}}
    assert _fca_capacities(far, frozen)[CAP] == {
        "fca_region": "westus3", "fca_resource_name": "rvfabriccapacity", "tag_names": "Owner, env", "missing_tags": "cost-center"}


def test_without_fca_nothing_is_invented():
    assert _fca_capacities({}, None) == {}


# --- a capacity finding belongs to every workspace on the capacity -----------------------------

def test_a_capacity_finding_is_attributed_to_every_workspace_on_it_and_labelled_with_the_capacity(tmp_path):
    from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256, normalize_findings
    (tmp_path / "capacity_metrics.json").write_text(json.dumps({"workspaceScopeLimited": False, "capacities": [
        {"id": CAP, "displayName": "alpha", "sku": "F8", "state": "Suspended", "admins": ["a"]}]}))
    (tmp_path / "scanner.json").write_text(json.dumps({"workspaces": [
        {"id": "ws-1", "name": "One", "capacityId": CAP}, {"id": "ws-2", "name": "Two", "capacityId": CAP},
        {"id": "ws-3", "name": "Elsewhere"}]}))
    (tmp_path / "run_manifest.json").write_text(json.dumps(
        {"profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256, "configuration_sha256": "c" * 64}))
    finding = {"rule_id": "OPS-008", "status": "info", "dimension": "operations", "severity": "info", "title": "Capacity state",
               "recommendation": "x", "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
               "evidence": {"scope": "capacity", "capacity_id": CAP, "capacity_name": "alpha", "workspace_id": None,
                            "workspace_name": None, "review_recommended": True,
                            "affected_workspaces": [{"workspace_id": "ws-1"}, {"workspace_id": "ws-2"}]}}
    tables = build_gold(normalize_findings([finding]), tmp_path, run_id="r", run_timestamp="2026-09-01T12:00:00Z", check_remote=False)
    assert [row["workspace_name"] for row in tables["gold_findings"]] == ["Capacity: alpha"]
    assert {row["workspace_id"] for row in tables["gold_finding_targets"]} == {"ws-1", "ws-2"}
    assert set(tables["gold_findings"][0]["affected"].split(", ")) == {"ws-1", "ws-2"}


# --- the items a finding points at -------------------------------------------------------------

def test_findings_list_the_items_they_point_at_by_name_and_type(tmp_path):
    from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256, normalize_findings
    (tmp_path / "scanner.json").write_text(json.dumps({"workspaces": [{"id": "ws-1", "name": "Finance", "capacityId": CAP, "items": [
        {"id": "p-1", "name": "Load sales", "type": "DataPipeline"}, {"id": "l-1", "name": "Bronze", "type": "Lakehouse"}]}]}))
    (tmp_path / "run_manifest.json").write_text(json.dumps(
        {"profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256, "configuration_sha256": "c" * 64}))
    base = {"status": "info", "dimension": "reliability", "severity": "info", "recommendation": "x",
            "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256}
    findings = normalize_findings([
        {**base, "rule_id": "ARCH-013", "title": "Retry", "evidence": {
            "workspace_id": "ws-1", "workspace_name": "Finance", "review_recommended": True,
            "pipeline_ids_without_retry": ["P-1", "gone"], "pipeline_ids_with_default_timeout": ["p-1"]}},
        {**base, "rule_id": "SEC-020", "title": "Roles", "evidence": {
            "workspace_id": "ws-1", "workspace_name": "Finance", "review_recommended": True,
            "lakehouse_ids_without_custom_roles": ["l-1"]}},
        {**base, "rule_id": "GOV-008", "title": "Stale", "evidence": {
            "workspace_id": "ws-1", "workspace_name": "Finance", "review_recommended": False, "stale_item_ids": ["p-1"]}},
        {**base, "rule_id": "ARCH-004", "title": "Contracts", "status": "fail", "severity": "high", "evidence": {
            "workspace_id": "ws-1", "workspace_name": "Finance", "violations": [{"item_id": "p-1", "notebook_id": "n"}]}},
        {**base, "rule_id": "ARCH-010", "title": "Portability", "evidence": {
            "workspace_id": "ws-1", "workspace_name": "Finance", "notebook_ids_with_patterns": ["nb-1"]}},
        {**base, "rule_id": "SEC-017", "title": "Guests", "evidence": {"workspace_id": "ws-1", "guest_principal_count": 0}}])
    rows = build_gold(findings, tmp_path, run_id="r", run_timestamp="2026-09-01T12:00:00Z", check_remote=False)["gold_finding_items"]
    by = {(r["rule_id"], r["item_id"]): r for r in rows}
    # GOV-008 does not suggest a review, so its items are not listed.
    assert set(by) == {("ARCH-013", "p-1"), ("ARCH-013", "gone"), ("SEC-020", "l-1"), ("ARCH-004", "p-1"), ("ARCH-010", "nb-1")}
    assert by[("ARCH-004", "p-1")]["reason"] == "Passes a parameter its notebook does not declare"
    assert by[("ARCH-004", "p-1")]["item_name"] == "Load sales"
    pipeline = by[("ARCH-013", "p-1")]
    assert (pipeline["item_name"], pipeline["item_type"], pipeline["workspace_name"], pipeline["review_suggested"]) == (
        "Load sales", "DataPipeline", "Finance", "Yes")
    assert pipeline["reason"] == "An activity has no retry; An activity uses the default 12-hour timeout"
    assert (by[("ARCH-013", "gone")]["item_name"], by[("ARCH-013", "gone")]["item_type"]) == ("gone", "Unknown")
    assert by[("ARCH-013", "gone")]["reason"].endswith("Not in the reviewed inventory, so it may have been deleted")
    assert "deleted" not in pipeline["reason"]
    assert (by[("SEC-020", "l-1")]["item_name"], by[("SEC-020", "l-1")]["review_suggested"]) == ("Bronze", "Yes")


def test_the_report_has_an_items_page_with_its_measure_and_columns():
    from reports.powerbi.report import _PAGE_INDEX, _content_specs
    from reports.powerbi.schema import GOLD_TABLES_BY_NAME
    from reports.powerbi.semantic_model import _measures
    assert "items" in [key for key, _, _ in _PAGE_INDEX]
    spec = _content_specs()["items"]
    columns = {c.name for c in GOLD_TABLES_BY_NAME["gold_finding_items"].columns}
    table = spec["tables"][0]
    assert set(table["columns"]) <= columns and {"item_name", "item_type", "reason"} <= set(table["columns"])
    assert all(entity == "gold_finding_items" and column in columns for entity, column, _ in spec["slicers"])
    assert {"Items To Review", "Workspaces With Items"} <= {m["name"] for m in _measures()}


# --- every surface knows about capacity findings and item lists ---------------------------------

def test_the_capacities_page_lists_the_capacity_findings():
    from analyzers.far_profile import CAPACITY_RULES
    from reports.powerbi.report import _content_specs
    table = next(t for t in _content_specs()["capacities"]["tables"] if t["title"].startswith("Capacity findings"))
    assert table["entity"] == "gold_findings" and table["where"] == [("gold_findings", "rule_id", sorted(CAPACITY_RULES))]
    assert sorted(CAPACITY_RULES) == ["OPS-004", "OPS-008", "OPS-009"]


def test_the_agents_know_capacity_findings_and_the_item_lists():
    from reports.agent.data_agent import DEFAULT_AI_INSTRUCTIONS
    from reports.agent.owner_agent import owner_instructions
    central, owner = " ".join(DEFAULT_AI_INSTRUCTIONS.split()), " ".join(owner_instructions().split())
    assert "Capacity: <name>" in central and "gold_finding_targets lists every workspace" in central
    assert "gold_finding_items" in central
    assert "detail starts with a rule ID" in owner and "A capacity finding" in owner


# --- signals that suggest a review -------------------------------------------------------------

def test_only_an_info_signal_that_recommends_a_review_is_marked_for_review():
    from reports.rule_guidance import explain
    rec = {"rule_id": "OPS-009", "status": "info", "evidence": {"review_recommended": True}}
    assert explain(rec)["review_suggested"] == "Yes"
    assert explain({**rec, "evidence": {"review_recommended": False}})["review_suggested"] == "No"
    assert explain({**rec, "evidence": {}})["review_suggested"] == "No"
    # A failing control is a failure, not a suggestion, and gaps are not suggestions either.
    for status in ("fail", "pass", "unknown", "missing_evidence", "not_applicable"):
        assert explain({**rec, "status": status})["review_suggested"] == "No"


def test_gold_records_the_flag_on_findings_and_targets_and_the_owner_copy(tmp_path):
    from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256
    from reports.powerbi.schema import GOLD_TABLES_BY_NAME
    from reports.owner.schema import OWNER_TABLES_BY_NAME
    for table in ("gold_findings", "gold_finding_targets"):
        assert "review_suggested" in {c.name for c in GOLD_TABLES_BY_NAME[table].columns}
    assert "review_suggested" in {c.name for c in OWNER_TABLES_BY_NAME["owner_findings"].columns}
    assert PROFILE_ID and PROFILE_SHA256


def test_the_report_counts_and_lists_signals_to_review():
    from reports.powerbi.report import _content_specs
    from reports.powerbi.semantic_model import _measures
    measure = next(m for m in _measures() if m["name"] == "Signals To Review")
    assert 'review_suggested] = "Yes"' in measure["expression"]
    specs = _content_specs()
    assert any(card[1] == "Signals To Review" for card in specs["overview"]["cards"])
    table = next(t for t in specs["actions"]["tables"] if t["title"].startswith("Worth a look"))
    assert table["where"] == [("gold_findings", "review_suggested", ["Yes"])]
    assert ("gold_findings", "review_suggested", "Review suggested") in specs["findings"]["slicers"]

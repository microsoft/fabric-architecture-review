# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
import json
import pytest
from reports.owner.gold import build_owner_gold
from tests._far_surfaces import META, MID, WID, owner_input


def test_owner_projection_does_not_copy_unattributed_or_optimizer_details():
    source = owner_input()
    source["gold_findings"] = [{**META, "rule_id": "ARCH-009", "title": "Global secret"}]
    result = build_owner_gold(source)
    assert set(result) == {"owner_workspaces", "owner_reviews", "owner_findings", "owner_details", "owner_coverage"}
    assert "Global secret" not in json.dumps(result)
    assert "DO-NOT-EXPOSE" not in json.dumps(result)
    assert result["owner_reviews"][0]["owner_fail_count"] == 1
    assert result["owner_details"][0]["detail"] == "Storage mode: DirectLake"


def test_owner_projection_lists_the_items_a_finding_points_at_for_the_owners_workspace_only():
    source = owner_input()
    other = "99999999-9999-4999-8999-999999999999"
    row = {**META, "workspace_id": WID, "rule_id": "ARCH-013", "item_id": MID, "item_name": "Model",
           "item_type": "SemanticModel", "reason": "An activity has no retry"}
    source["gold_finding_items"] = [
        row, {**row, "workspace_id": other}, {**row, "item_id": "not-a-guid"}, {**row, "rule_id": "PERF-001"},
        {**row, "profile_sha256": "legacy"}]
    result = build_owner_gold(source)
    items = [d for d in result["owner_details"] if d["detail_type"] == "finding_item"]
    assert [(d["workspace_id"], d["item_id"], d["item_name"], d["detail"], d["signal_codes"]) for d in items] == [
        (WID, MID, "Model", "ARCH-013: An activity has no retry", "ARCH-013")]
    assert result["owner_reviews"][0]["owner_detail_count"] == len(result["owner_details"])


@pytest.mark.parametrize("field,value", [("workspace_id", "bad"), ("workspace_name", ""),
                                        ("run_timestamp", "2026-01-01"), ("run_timestamp", None)])
def test_owner_identity_and_timestamp_are_validated(field, value):
    source = owner_input()
    source["gold_workspaces"][0][field] = value
    with pytest.raises(ValueError):
        build_owner_gold(source)


@pytest.mark.parametrize("field,value", [("run_id", "another"), ("configuration_sha256", "another")])
def test_owner_facts_cannot_cross_review_versions(field, value):
    source = owner_input()
    source["gold_semantic_models"][0][field] = value
    with pytest.raises(ValueError):
        build_owner_gold(source)


def test_owner_historical_hash_and_absent_explicit_id_fail_closed():
    source = owner_input()
    for rows in source.values():
        for row in rows:
            row["profile_sha256"] = "legacy"
    assert all(not rows for rows in build_owner_gold(source).values())
    source = owner_input()
    source["gold_finding_targets"][0].pop("workspace_id")
    assert build_owner_gold(source)["owner_findings"] == []


def test_heuristic_never_becomes_owner_failure():
    source = owner_input()
    source["gold_finding_targets"][0].update(rule_id="SEC-011", is_scored=0)
    result = build_owner_gold(source)
    assert result["owner_findings"][0]["is_fail"] == 0
    assert result["owner_reviews"][0]["owner_fail_count"] == 0


def test_conflicting_duplicate_item_and_workspace_ids_fail_closed():
    source = owner_input()
    source["gold_semantic_models"].append({**source["gold_semantic_models"][0], "model_name": "Conflict"})
    with pytest.raises(ValueError, match="identity"):
        build_owner_gold(source)
    source = owner_input()
    source["gold_workspaces"].append({**source["gold_workspaces"][0], "workspace_name": "Conflict"})
    with pytest.raises(ValueError, match="identity"):
        build_owner_gold(source)

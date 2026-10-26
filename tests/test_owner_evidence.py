# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from reports.owner.gold import build_owner_gold
from tests._far_surfaces import META, owner_input


def test_owner_source_coverage_is_typed_and_excludes_monitoring_subjects():
    source = owner_input()
    source["gold_source_coverage"] += [
        {**source["gold_source_coverage"][0], "evidence_family": family}
        for family in ("dax_analysis", "item_executions", "billing", "vertipaq", "utilization")]
    rows = build_owner_gold(source)["owner_coverage"]
    assert len(rows) == 1
    assert rows[0]["collection_status"] == "partial"
    assert rows[0]["source_contract"] == "far-native-v1"
    assert rows[0]["profile_sha256"] == META["profile_sha256"]
    assert "partial" in rows[0]["notice"].lower()

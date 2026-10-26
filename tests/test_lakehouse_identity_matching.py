# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Regression tests for findings made against a live FUAM Lakehouse.

The Lakehouse SQL endpoint compares text case-sensitively (Latin1_General_100_BIN2_UTF8) and FUAM
stores identifiers in upper case, so a predicate on a lower-case GUID matched nothing and every
family came back "collected" with zero rows.
"""
from datetime import datetime, timezone

from analyzers.enrichment_rules import published_to_web
from analyzers.rule_support import Context
from collectors.enrichment_facts import fuam_facts
from orchestration.review_config import local_configuration

WORKSPACE = "11111111-1111-4111-8111-111111111111"
CAPACITY = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
CONNECTION = {"workspace_id": WORKSPACE, "lakehouse_id": CAPACITY, "max_age_hours": 24, "enabled": True}


def test_every_identifier_predicate_lowers_the_column():
    statements = []

    def query(statement):
        statements.append(statement)
        if "MIN(CreationDate)" in statement:
            return [{"earliest": datetime(2026, 1, 1), "newest": datetime(2026, 10, 12)}]
        if "MAX(TransferDateTime)" in statement:
            return [{"snapshot_at": datetime(2026, 10, 12)}]
        return []

    fuam_facts(query, CONNECTION, local_configuration()["thresholds"], {WORKSPACE}, {CAPACITY}, NOW)
    filtered = [statement for statement in statements if " IN (" in statement]
    assert len(filtered) >= 7
    for statement in filtered:
        assert "LOWER(" in statement.split(" IN (")[0].rsplit("WHERE", 1)[-1], statement


def test_upper_case_identifiers_from_fuam_are_returned_as_canonical_guids():
    def query(statement):
        if "MIN(CreationDate)" in statement:
            return [{"earliest": datetime(2026, 1, 1), "newest": datetime(2026, 10, 12)}]
        if "FROM dbo.activities" in statement:
            return [{"WorkspaceId": WORKSPACE.upper(), "last_activity": datetime(2026, 10, 10)}]
        return []

    facts = fuam_facts(query, CONNECTION, local_configuration()["thresholds"], {WORKSPACE}, {CAPACITY}, NOW)
    assert facts["workspace_activity"]["rows"] == [{"workspace_id": WORKSPACE, "last_activity_at": "2026-10-10T00:00:00+00:00"}]


def _context(setting_enabled, *, fuam=True):
    configuration = local_configuration()
    configuration["sources"]["fuam"] = {**CONNECTION, "enabled": fuam}
    tenant = {"tenantSettings": [{"settingName": "PublishToWeb", "enabled": setting_enabled}]} if setting_enabled is not None else {}
    enrichment = {"captured_at_utc": NOW.isoformat(), "rule_facts": {"publish_to_web": {
        "table": "widelyshared_publishtoweb", "status": "unavailable", "reason_code": "no_transfer_snapshot", "rows": []}}}
    return Context({"configuration": configuration, "sha256": "x", "captured_at_utc": NOW.isoformat()}, [],
                   {"tenant_settings.json": tenant}, enrichment)


def test_empty_published_to_web_table_passes_only_when_the_tenant_setting_disables_it():
    status, evidence = published_to_web({"id": WORKSPACE}, _context(False))
    assert (status, evidence["published_item_count"]) == ("pass", 0)
    assert "disabled" in evidence["reason"]
    assert "reason_code" not in evidence


def test_empty_published_to_web_table_is_a_gap_when_publishing_is_allowed_or_unknown():
    for enabled in (True, None):
        status, evidence = published_to_web({"id": WORKSPACE}, _context(enabled))
        assert (status, evidence["reason_code"]) == ("missing_evidence", "no_transfer_snapshot")

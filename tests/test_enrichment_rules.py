# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evaluators of the rules that read FUAM or FCA facts: gating, freshness and thresholds."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from analyzers import enrichment_rules
from analyzers.far_profile import ENRICHMENT_SOURCES, TENANT_RULES
from analyzers.far_review import _assess, _assess_tenant, _recommendation
from analyzers.rule_support import Context
from orchestration.review_config import (
    ROOT, declared_profiles, local_configuration, read_yaml, validate_configuration,
)
from orchestration.rule_policy import resolve_rule

WS = "11111111-1111-1111-1111-111111111111"
OTHER_WS = "22222222-2222-2222-2222-222222222222"
CAP = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_CAP = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ITEM = "cccccccc-cccc-cccc-cccc-cccccccccccc"
NOW = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
CONNECTION = {"workspace_id": WS, "lakehouse_id": OTHER_WS}


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def configuration(fuam=True, fca=True, tags=""):
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["policy"]["required_capacity_tags"] = tags
    config = local_configuration()
    config["profiles"] = declared_profiles(values)
    for name, enabled in (("fuam", fuam), ("fca", fca)):
        config["sources"][name].update(enabled=enabled, **(CONNECTION if enabled else {}))
    validate_configuration(config)
    return config


def entry(rows, **extra):
    return {"table": "table", "status": "collected", "observed_at": ago(0), "freshness_status": "recent",
            "rows": rows, **CONNECTION, **extra}


def context(config=None, facts=None, workspaces=()):
    enrichment = None if facts is None else {"captured_at_utc": NOW.isoformat(), "rule_facts": facts}
    return Context({"configuration": config or configuration()}, list(workspaces), {}, enrichment)


def workspace(wid=WS, capacity=None):
    return {"id": wid, "name": "workspace", "capacityId": capacity}


def evaluate(rule_id, facts, config=None, ws=None):
    """The finding status and evidence through the analyzer's own dispatch."""
    config = config or configuration()
    ws = ws or workspace()
    ctx = context(config, facts, [ws])
    if rule_id in TENANT_RULES:
        return _assess_tenant(rule_id, resolve_rule(config, rule_id, {"id": None}), ctx)
    return _assess(rule_id, ws, resolve_rule(config, rule_id, ws), ctx)


# --- gating ------------------------------------------------------------------------------------

FAMILIES = {
    "GOV-006": ("domains",), "GOV-007": ("workspace_activity",), "GOV-008": ("item_age",),
    "GOV-009": ("capacity_tags",), "OPS-002": ("item_operations",),
    "OPS-003": ("item_operations", "capacity_totals"), "OPS-004": ("capacity_throttling",),
    "SEC-006": ("publish_to_web",), "OPS-005": ("refresh_health",), "OPS-007": ("item_operations", "capacity_totals"), "SEC-019": ("org_links",),
}


def test_every_enrichment_rule_is_covered_by_these_tests():
    assert set(FAMILIES) == set(ENRICHMENT_SOURCES)


def tagged(rule_id):
    return "owner" if rule_id == "GOV-009" else ""


@pytest.mark.parametrize("rule_id", sorted(ENRICHMENT_SOURCES))
def test_disabled_source_is_not_applicable_and_the_recommendation_says_to_connect_it(rule_id):
    source = ENRICHMENT_SOURCES[rule_id]
    config = configuration(fuam=False, fca=False, tags=tagged(rule_id))
    facts = {family: entry([]) for family in FAMILIES[rule_id]}  # facts are ignored when the source is off
    status, evidence = evaluate(rule_id, facts, config)
    assert status == "not_applicable"
    assert evidence["reason_code"] == "source_not_enabled"
    assert evidence["required_source"] == source
    text = _recommendation(status, rule_id in TENANT_RULES, evidence)
    assert f"Connect {source.upper()}" in text


def test_connecting_one_module_does_not_enable_the_other():
    status, evidence = evaluate("GOV-007", {"workspace_activity": entry([])}, configuration(fuam=False, fca=True))
    assert (status, evidence["required_source"]) == ("not_applicable", "fuam")
    status, evidence = evaluate("GOV-009", {"capacity_tags": entry([])}, configuration(fuam=True, fca=False, tags="owner"))
    assert (status, evidence["required_source"]) == ("not_applicable", "fca")


@pytest.mark.parametrize("rule_id", sorted(ENRICHMENT_SOURCES))
@pytest.mark.parametrize("facts", [None, {}])
def test_missing_family_is_missing_evidence(rule_id, facts):
    config = configuration(tags=tagged(rule_id))
    status, evidence = evaluate(rule_id, facts, config)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "enrichment_unavailable")
    assert "Refresh the" in _recommendation(status, rule_id in TENANT_RULES, evidence)


@pytest.mark.parametrize("rule_id", ["OPS-003", "OPS-007"])
def test_every_family_a_rule_reads_is_required(rule_id):
    for missing in FAMILIES[rule_id]:
        facts = {family: entry([]) for family in FAMILIES[rule_id] if family != missing}
        assert evaluate(rule_id, facts)[1]["reason_code"] == "enrichment_unavailable"


@pytest.mark.parametrize("rule_id", sorted(ENRICHMENT_SOURCES))
@pytest.mark.parametrize("reason", ["query_failed", "incompatible_schema", None])
def test_unavailable_family_is_missing_evidence_with_its_reason(rule_id, reason):
    unavailable = {"table": "t", "status": "unavailable", "rows": [], **({"reason_code": reason} if reason else {})}
    facts = {family: unavailable for family in FAMILIES[rule_id]}
    status, evidence = evaluate(rule_id, facts, configuration(tags=tagged(rule_id)))
    assert (status, evidence["reason_code"]) == ("missing_evidence", reason or "family_unavailable")


@pytest.mark.parametrize("rule_id", sorted(set(ENRICHMENT_SOURCES) - {"GOV-009"}))
def test_stale_family_is_missing_evidence_never_a_pass(rule_id):
    facts = {family: entry([], freshness_status="stale") for family in FAMILIES[rule_id]}
    status, evidence = evaluate(rule_id, facts)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "source_data_stale")
    assert evidence["max_age_hours"] == 24
    assert evidence["freshness_status"] == "stale"


def test_stale_fca_data_reports_the_fca_age_limit():
    status, evidence = evaluate("GOV-009", {"capacity_tags": entry([], freshness_status="stale")},
                                configuration(tags="owner"))
    assert (status, evidence["reason_code"], evidence["max_age_hours"]) == ("missing_evidence", "source_data_stale", 48)


def test_gap_evidence_carries_the_source_provenance():
    status, evidence = evaluate("GOV-007", {"workspace_activity": entry([], status="unavailable", reason_code="query_failed",
                                                                        observed_at=None, table="activities")})
    assert status == "missing_evidence"
    assert (evidence["source_table"], evidence["required_source"], evidence["assessment_kind"]) == (
        "activities", "fuam", "enrichment")


# --- GOV-007 -----------------------------------------------------------------------------------

def activity_facts(rows, earliest):
    return {"workspace_activity": entry(rows, earliest_at=earliest)}


def test_history_shorter_than_the_window_is_missing_evidence():
    status, evidence = evaluate("GOV-007", activity_facts([], ago(30)))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "activity_history_shorter_than_window")
    assert evidence["inactive_after_days"] == 90
    assert evidence["history_start"] == ago(30)
    assert evaluate("GOV-007", activity_facts([], None))[1]["reason_code"] == "activity_history_shorter_than_window"


@pytest.mark.parametrize("last, recommended", [(200, True), (91, True), (90, False), (10, False)])
def test_last_activity_age_against_the_window(last, recommended):
    rows = [{"workspace_id": WS, "last_activity_at": ago(last)}]
    status, evidence = evaluate("GOV-007", activity_facts(rows, ago(400)))
    assert status == "info"
    assert evidence["days_since_activity"] == last
    assert evidence["review_recommended"] is recommended


def test_no_activity_within_a_covered_history_recommends_review():
    status, evidence = evaluate("GOV-007", activity_facts([{"workspace_id": OTHER_WS, "last_activity_at": ago(1)}], ago(400)))
    assert (status, evidence["last_activity_at"], evidence["review_recommended"]) == ("info", None, True)


def test_old_activity_is_not_inactivity_when_the_history_is_shorter_than_the_window():
    rows = [{"workspace_id": WS, "last_activity_at": ago(60)}]
    status, evidence = evaluate("GOV-007", activity_facts(rows, ago(60)))
    assert (status, evidence["review_recommended"]) == ("info", False)
    rows = [{"workspace_id": WS, "last_activity_at": ago(100)}]
    assert evaluate("GOV-007", activity_facts(rows, ago(89)))[1]["review_recommended"] is False


def test_activity_rows_match_the_workspace_without_case():
    rows = [{"workspace_id": WS.upper(), "last_activity_at": ago(5)}]
    assert evaluate("GOV-007", activity_facts(rows, ago(400)))[1]["last_activity_at"] == ago(5)


def test_inactivity_window_is_a_scoped_threshold():
    config = configuration()
    config["thresholds"]["governance"]["inactive_workspace_days"] = 30
    rows = [{"workspace_id": WS, "last_activity_at": ago(45)}]
    status, evidence = evaluate("GOV-007", activity_facts(rows, ago(60)), config)
    assert (evidence["inactive_after_days"], evidence["review_recommended"]) == (30, True)


# --- GOV-008 -----------------------------------------------------------------------------------

def age_facts(*rows):
    return {"item_age": entry([{"workspace_id": wid, "item_count": total, "dated_item_count": dated,
                                "stale_item_count": stale} for wid, total, dated, stale in rows])}


@pytest.mark.parametrize("stale, recommended", [(3, False), (4, True), (0, False), (6, True)])
def test_stale_item_ratio_against_the_maximum(stale, recommended):
    status, evidence = evaluate("GOV-008", age_facts((WS, 8, 6, stale)))
    assert status == "info"
    assert evidence["stale_ratio"] == stale / 6
    assert evidence["item_count"] == 6  # undated items are not judged
    assert evidence["review_above"] == 0.5
    assert evidence["review_recommended"] is recommended


def test_stale_items_without_dated_items_is_not_applicable():
    for facts in (age_facts((WS, 3, 0, 0)), age_facts((OTHER_WS, 3, 3, 3)), age_facts()):
        status, evidence = evaluate("GOV-008", facts)
        assert status == "not_applicable"
        assert evidence["stale_after_days"] == 180


# --- GOV-006 -----------------------------------------------------------------------------------

def domain_facts(*domains):
    return {"domains": entry([{"workspace_id": f"{index:08d}-0000-0000-0000-000000000000", "domain_id": domain}
                              for index, domain in enumerate(domains, 1)])}


@pytest.mark.parametrize("domains, recommended", [
    (["d", "d", "d", "d", None], False),  # 0.8 is the minimum
    (["d", "d", "d", None, None], True),
    (["d", "d"], False),
    ([None, None], True),
])
def test_domain_coverage_ratio_against_the_minimum(domains, recommended):
    status, evidence = evaluate("GOV-006", domain_facts(*domains))
    assert status == "info"
    assert evidence["workspace_count"] == len(domains)
    assert evidence["assigned_workspace_count"] == sum(bool(domain) for domain in domains)
    assert evidence["review_below"] == 0.8
    assert evidence["review_recommended"] is recommended
    assert evidence["reviewed_workspace_count"] == 1


def test_domain_coverage_without_workspaces_in_the_source_is_missing_evidence():
    status, evidence = evaluate("GOV-006", domain_facts())
    assert (status, evidence["reason_code"]) == ("missing_evidence", "workspaces_not_in_source")


# --- OPS-002 -----------------------------------------------------------------------------------

def operation(item, ops, failed, cus=1.0, wid=WS, capacity=CAP):
    return {"capacity_id": capacity, "workspace_id": wid, "item_id": item, "item_kind": "Notebook",
            "operation_count": ops, "failure_count": failed, "total_cus": cus}


def operation_facts(*rows, window_days=30):
    return {"item_operations": entry(list(rows), window_days=window_days)}


def test_items_below_the_minimum_events_are_not_judged():
    status, evidence = evaluate("OPS-002", operation_facts(operation("a", 19, 19)))
    assert status == "info"
    assert (evidence["judged_item_count"], evidence["flagged_item_count"], evidence["review_recommended"]) == (0, 0, False)
    assert evidence["worst_item_id"] is None and evidence["worst_failure_ratio"] is None
    assert evidence["minimum_operations"] == 20


def test_failure_ratio_flags_items_above_the_maximum_and_names_the_worst():
    status, evidence = evaluate("OPS-002", operation_facts(
        operation("a", 100, 30),
        operation("b", 100, 20),  # exactly at the maximum
        operation("c", 20, 12),   # exactly at the minimum events
        operation("d", 5, 5),     # not judged
        operation("e", 100, 50, wid=OTHER_WS),
    ))
    assert status == "info"
    assert (evidence["judged_item_count"], evidence["flagged_item_count"]) == (3, 2)
    assert (evidence["worst_item_id"], evidence["worst_failure_ratio"]) == ("c", 0.6)
    assert evidence["review_recommended"] is True
    assert (evidence["review_above"], evidence["window_days"]) == (0.2, 30)


def test_failure_ratio_at_the_maximum_is_not_flagged():
    evidence = evaluate("OPS-002", operation_facts(operation("a", 100, 20), operation("b", 50, 0)))[1]
    assert (evidence["judged_item_count"], evidence["flagged_item_count"], evidence["review_recommended"]) == (2, 0, False)
    assert evidence["worst_item_id"] == "a"


def test_failure_thresholds_are_scoped_per_rule():
    config = configuration()
    config["thresholds"]["operations"].update(failure_min_events=5, failure_max_ratio=0.5)
    evidence = evaluate("OPS-002", operation_facts(operation("a", 10, 5), operation("b", 10, 6)), config)[1]
    assert (evidence["judged_item_count"], evidence["flagged_item_count"], evidence["worst_item_id"]) == (2, 1, "b")


# --- OPS-003 -----------------------------------------------------------------------------------

def expensive_facts(items, totals):
    return {"item_operations": entry(items, window_days=30),
            "capacity_totals": entry([{"capacity_id": capacity, "total_cus": total} for capacity, total in totals])}


@pytest.mark.parametrize("cus, recommended", [(30.0, True), (25.0, False), (1.0, False)])
def test_item_share_of_capacity_compute_against_the_maximum(cus, recommended):
    status, evidence = evaluate("OPS-003", expensive_facts([operation("a", 10, 0, cus=cus)], [(CAP, 100.0)]))
    assert status == "info"
    assert evidence["top_item_capacity_share"] == cus / 100
    assert (evidence["top_item_id"], evidence["top_item_kind"], evidence["judged_item_count"]) == ("a", "Notebook", 1)
    assert evidence["review_above"] == 0.25
    assert evidence["review_recommended"] is recommended


def test_the_largest_share_among_the_workspace_items_is_reported():
    items = [operation("small", 10, 0, cus=5.0), operation("big", 10, 0, cus=40.0, capacity=OTHER_CAP),
             operation("foreign", 10, 0, cus=99.0, wid=OTHER_WS)]
    evidence = evaluate("OPS-003", expensive_facts(items, [(CAP, 100.0), (OTHER_CAP, 100.0)]))[1]
    assert (evidence["top_item_id"], evidence["judged_item_count"], evidence["review_recommended"]) == ("big", 2, True)


@pytest.mark.parametrize("totals", [[], [(OTHER_CAP, 100.0)], [(CAP, 0.0)]])
def test_items_without_a_known_capacity_total_are_not_judged(totals):
    status, evidence = evaluate("OPS-003", expensive_facts([operation("a", 10, 0, cus=500.0)], totals))
    assert status == "info"
    assert (evidence["judged_item_count"], evidence["review_recommended"]) == (0, False)
    assert "top_item_id" not in evidence


def test_items_without_a_capacity_are_not_judged():
    items = [operation("a", 10, 0, cus=500.0, capacity=None)]
    assert evaluate("OPS-003", expensive_facts(items, [(CAP, 100.0)]))[1]["judged_item_count"] == 0


def test_either_family_being_stale_withholds_the_judgement():
    facts = expensive_facts([operation("a", 10, 0, cus=50.0)], [(CAP, 100.0)])
    facts["capacity_totals"]["freshness_status"] = "stale"
    assert evaluate("OPS-003", facts)[1]["reason_code"] == "source_data_stale"
    facts = expensive_facts([operation("a", 10, 0, cus=50.0)], [(CAP, 100.0)])
    facts["item_operations"]["status"] = "unavailable"
    assert evaluate("OPS-003", facts)[0] == "missing_evidence"


# --- OPS-004 -----------------------------------------------------------------------------------

def throttle_facts(*rows):
    return {"capacity_throttling": entry([{"capacity_id": capacity, "observed_days": observed, "throttled_days": throttled}
                                          for capacity, observed, throttled in rows], window_days=30)}


@pytest.mark.parametrize("throttled, recommended", [(3, False), (4, True), (0, False), (30, True)])
def test_throttled_day_ratio_against_the_maximum(throttled, recommended):
    status, evidence = evaluate("OPS-004", throttle_facts((CAP, 30, throttled)), ws=workspace(WS, CAP))
    assert status == "info"
    assert evidence["throttled_day_ratio"] == throttled / 30
    assert (evidence["observed_days"], evidence["throttled_days"], evidence["review_above"]) == (30, throttled, 0.1)
    assert evidence["review_recommended"] is recommended


def test_workspace_on_no_capacity_is_not_applicable_for_throttling():
    for capacity in (None, ""):
        status, _ = evaluate("OPS-004", throttle_facts((CAP, 30, 30)), ws=workspace(WS, capacity))
        assert status == "not_applicable"


@pytest.mark.parametrize("rows", [[], [(OTHER_CAP, 30, 30)], [(CAP, 0, 0)]])
def test_capacity_absent_from_the_source_is_missing_evidence(rows):
    status, evidence = evaluate("OPS-004", throttle_facts(*rows), ws=workspace(WS, CAP))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "capacity_not_in_source")


def test_throttling_matches_the_capacity_without_case():
    status, evidence = evaluate("OPS-004", throttle_facts((CAP, 10, 5)), ws=workspace(WS, CAP.upper()))
    assert (status, evidence["review_recommended"]) == ("info", True)


# --- SEC-006 -----------------------------------------------------------------------------------

def publish_facts(*rows):
    return {"publish_to_web": entry([{"workspace_id": wid, "item_id": item, "artifact_type": "Report"}
                                     for wid, item in rows], snapshot_at=ago(0))}


def test_published_items_fail_and_list_the_item_ids():
    second = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    status, evidence = evaluate("SEC-006", publish_facts((WS, second), (WS, ITEM), (OTHER_WS, "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")))
    assert status == "fail"
    assert evidence["published_item_ids"] == [ITEM, second]
    assert evidence["published_item_count"] == 2
    assert evidence["assessment_kind"] == "published_items"
    assert "Remove the publish-to-web link" in _recommendation(status, False, evidence)


def test_nothing_published_in_a_fresh_snapshot_passes():
    status, evidence = evaluate("SEC-006", publish_facts((OTHER_WS, ITEM)))
    assert status == "pass"
    assert (evidence["published_item_count"], evidence["published_item_ids"]) == (0, [])
    assert evidence["snapshot_at"] == ago(0)


def test_publish_to_web_in_a_stale_snapshot_is_never_a_pass():
    facts = publish_facts()
    facts["publish_to_web"]["freshness_status"] = "stale"
    assert evaluate("SEC-006", facts)[0] == "missing_evidence"


# --- OPS-005 -----------------------------------------------------------------------------------

def refresh_facts(*rows):
    return {"refresh_health": entry([{"workspace_id": wid, "item_id": item, "refresh_count": count, "failure_count": failed}
                                     for wid, item, count, failed in rows], window_days=30)}


@pytest.mark.parametrize("count, failed, recommended", [(10, 2, False), (10, 3, True), (4, 4, False), (5, 5, True)])
def test_refresh_failure_ratio_is_judged_only_above_the_minimum(count, failed, recommended):
    status, evidence = evaluate("OPS-005", refresh_facts((WS, ITEM, count, failed)))
    assert status == "info"
    assert evidence["review_recommended"] is recommended
    assert (evidence["review_above"], evidence["minimum_refreshes"]) == (0.2, 5)
    assert evidence["judged_item_count"] == (1 if count >= 5 else 0)


def test_refresh_failures_name_only_the_worst_item_of_this_workspace():
    worst = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    status, evidence = evaluate("OPS-005", refresh_facts(
        (WS, ITEM, 10, 3), (WS, worst, 10, 9), (OTHER_WS, "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", 10, 10)))
    assert (evidence["refreshed_item_count"], evidence["flagged_item_count"]) == (2, 2)
    assert (evidence["worst_item_id"], evidence["worst_failure_ratio"]) == (worst, 0.9)


def test_a_workspace_without_refreshes_in_a_fresh_source_is_a_quiet_signal():
    status, evidence = evaluate("OPS-005", refresh_facts((OTHER_WS, ITEM, 10, 10)))
    assert (status, evidence["refreshed_item_count"], evidence["review_recommended"]) == ("info", 0, False)


# --- OPS-007 -----------------------------------------------------------------------------------

def share_facts(workspace_cus, total):
    rows = [{"capacity_id": CAP, "workspace_id": wid, "item_id": item, "item_kind": "Notebook", "operation_count": 1,
             "failure_count": 0, "total_cus": cus} for wid, item, cus in workspace_cus]
    return {"item_operations": entry(rows, window_days=30),
            "capacity_totals": entry([{"capacity_id": CAP, "total_cus": total}], window_days=30)}


def shared_context(config, facts, others=2):
    ws = workspace(WS, CAP)
    neighbours = [workspace(f"{index:08d}-0000-4000-8000-000000000000", CAP) for index in range(others)]
    return ws, context(config, facts, [ws, *neighbours])


@pytest.mark.parametrize("cus, recommended", [(50, False), (51, True), (10, False)])
def test_a_workspace_over_half_its_shared_capacity_recommends_review(cus, recommended):
    config = configuration()
    ws, ctx = shared_context(config, share_facts([(WS, ITEM, cus - 20 if cus > 20 else cus), (WS, OTHER_WS, 20 if cus > 20 else 0),
                                                  (OTHER_WS, "dddddddd-dddd-dddd-dddd-dddddddddddd", 90)], 100))
    status, evidence = enrichment_rules.workspace_capacity_share(ws, resolve_rule(config, "OPS-007", ws), ctx)
    assert status == "info"
    assert evidence["workspace_cu_share"] == cus / 100 and evidence["review_above"] == 0.5
    assert evidence["review_recommended"] is recommended and evidence["alone_on_capacity"] is False


def test_a_workspace_alone_on_its_capacity_is_reported_without_a_recommendation():
    config = configuration()
    ws = workspace(WS, CAP)
    ctx = context(config, share_facts([(WS, ITEM, 100)], 100), [ws])
    _, evidence = enrichment_rules.workspace_capacity_share(ws, resolve_rule(config, "OPS-007", ws), ctx)
    assert (evidence["workspace_cu_share"], evidence["alone_on_capacity"], evidence["review_recommended"]) == (1.0, True, False)


def test_a_workspace_with_no_compute_in_the_window_is_a_quiet_signal():
    config = configuration()
    ws, ctx = shared_context(config, share_facts([(OTHER_WS, ITEM, 10)], 100))
    status, evidence = enrichment_rules.workspace_capacity_share(ws, resolve_rule(config, "OPS-007", ws), ctx)
    assert (status, evidence["workspace_cu_share"], evidence["review_recommended"]) == ("info", None, False)


def test_the_share_threshold_is_configurable():
    config = configuration()
    config["thresholds"]["operations"]["workspace_cu_max_ratio"] = 0.3
    ws, ctx = shared_context(config, share_facts([(WS, ITEM, 40)], 100))
    assert enrichment_rules.workspace_capacity_share(ws, resolve_rule(config, "OPS-007", ws), ctx)[1]["review_recommended"] is True


# --- SEC-019 -----------------------------------------------------------------------------------

def link_facts(*rows, **extra):
    return {"org_links": entry([{"workspace_id": wid, "item_id": item, "artifact_type": kind} for wid, item, kind in rows],
                               snapshot_at=ago(0), **extra)}


def test_organization_wide_links_name_the_items_and_recommend_review():
    second = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    status, evidence = evaluate("SEC-019", link_facts((WS, second, "Report"), (WS, ITEM, "Dashboard"), (OTHER_WS, ITEM, "Report")))
    assert status == "info"
    assert evidence["linked_item_ids"] == [ITEM, second]
    assert (evidence["org_link_item_count"], evidence["review_recommended"]) == (2, True)
    assert evidence["linked_artifact_types"] == ["Dashboard", "Report"]


def test_no_links_in_a_fresh_snapshot_does_not_recommend_review():
    status, evidence = evaluate("SEC-019", link_facts((OTHER_WS, ITEM, "Report")))
    assert (status, evidence["org_link_item_count"], evidence["review_recommended"]) == ("info", 0, False)


def test_an_empty_transfer_is_missing_evidence_unless_the_tenant_disallows_org_links():
    gap = {"table": "t", "status": "unavailable", "rows": [], "reason_code": "no_transfer_snapshot"}
    status, evidence = evaluate("SEC-019", {"org_links": gap})
    assert (status, evidence["reason_code"]) == ("missing_evidence", "no_transfer_snapshot")
    ctx = context(facts={"org_links": gap}, workspaces=[workspace()])
    ctx.raw["tenant_settings.json"] = {"tenantSettings": [{"settingName": "ShareLinkToEntireOrg", "enabled": False}]}
    status, evidence = enrichment_rules.org_wide_links(workspace(), ctx)
    assert (status, evidence["org_link_item_count"], evidence["review_recommended"]) == ("info", 0, False)


def test_org_links_in_a_stale_snapshot_are_never_reported_as_none():
    facts = link_facts()
    facts["org_links"]["freshness_status"] = "stale"
    assert evaluate("SEC-019", facts)[0] == "missing_evidence"


# --- GOV-009 -----------------------------------------------------------------------------------

def tag_facts(*rows):
    return {"capacity_tags": entry([{"capacity_id": capacity, "tag_names": names} for capacity, names in rows],
                                   freshness_status="unknown")}


def test_no_declared_tags_is_not_applicable_even_when_fca_is_connected_or_not():
    for fca in (True, False):
        status, evidence = evaluate("GOV-009", tag_facts((CAP, [])), configuration(fca=fca))
        assert (status, evidence["reason_code"]) == ("not_applicable", "no_required_tags_declared")


def test_declared_tags_with_fca_disabled_is_not_applicable():
    status, evidence = evaluate("GOV-009", tag_facts((CAP, ["owner"])), configuration(fca=False, tags="owner"))
    assert (status, evidence["reason_code"], evidence["required_source"]) == ("not_applicable", "source_not_enabled", "fca")


def test_capacities_missing_a_required_tag_recommend_review():
    config = configuration(tags="owner, cost-center")
    status, evidence = evaluate("GOV-009", tag_facts(
        (CAP, ["owner", "cost-center", "extra"]), (OTHER_CAP, ["owner"]),
        ("dddddddd-dddd-dddd-dddd-dddddddddddd", [])), config)
    assert status == "info"
    assert (evidence["capacity_count"], evidence["capacities_missing_tags"]) == (3, 2)
    assert evidence["required_tag_names"] == ["cost-center", "owner"]
    assert evidence["review_recommended"] is True


def test_tag_names_are_compared_without_case():
    config = configuration(tags="Owner")
    status, evidence = evaluate("GOV-009", tag_facts((CAP, ["OWNER"])), config)
    assert (status, evidence["capacities_missing_tags"], evidence["review_recommended"]) == ("info", 0, False)


def test_empty_rows_are_missing_evidence_with_the_bridge_hint():
    status, evidence = evaluate("GOV-009", tag_facts(), configuration(tags="owner"))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "capacity_resource_bridge_missing")
    assert "capacity_arm_resource_ids" in evidence["reason"]


def test_every_mapped_capacity_tagged_does_not_recommend_review():
    evidence = evaluate("GOV-009", tag_facts((CAP, ["owner"]), (OTHER_CAP, ["owner"])), configuration(tags="owner"))[1]
    assert (evidence["capacities_missing_tags"], evidence["review_recommended"]) == (0, False)


def test_direct_call_without_a_policy_is_not_applicable():
    ctx = context(configuration(), {"capacity_tags": entry([])})
    status, _ = enrichment_rules.capacity_tags({"policy": None, "thresholds": {}}, ctx)
    assert status == "not_applicable"

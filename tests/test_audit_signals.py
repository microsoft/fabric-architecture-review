# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Rules added for a fuller architecture review: tenant exposure, principals, capacity grade."""
import copy

import pytest

from analyzers.far_profile import RULE_KINDS, TENANT_RULES, TENANT_SIGNAL_SETTINGS
from analyzers.far_review import _assess, _assess_tenant
from analyzers.rule_support import Context
from orchestration.review_config import (
    ROOT, declared_profiles, local_configuration, read_yaml, validate_configuration,
)
from orchestration.rule_policy import resolve_rule

PROD = "11111111-1111-1111-1111-111111111111"
NONPROD = "22222222-2222-2222-2222-222222222222"
UNDECLARED = "44444444-4444-4444-4444-444444444444"
CAP = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def configuration():
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["scope"].update({"production_workspace_ids": PROD, "nonproduction_workspace_ids": NONPROD})
    config = local_configuration()
    config["profiles"] = declared_profiles(values)
    validate_configuration(config)
    return config


def context(**raw):
    return Context({"configuration": configuration()}, [], raw)


def run(rule_id, ws, facts=None, fuam=True, **raw):
    ctx = context(**raw)
    if facts is not None:
        ctx.snapshot["configuration"]["sources"]["fuam"].update(enabled=fuam, workspace_id=PROD, lakehouse_id=NONPROD)
        ctx.enrichment = {"captured_at_utc": "2026-03-01T12:00:00+00:00", "rule_facts": facts}
    return _assess(rule_id, ws, resolve_rule(ctx.configuration, rule_id, ws), ctx)


def setting(name, enabled=True, groups=0, can_limit=True):
    return {"settingName": name, "enabled": enabled, "enabledSecurityGroupCount": groups,
            "canSpecifySecurityGroups": can_limit}


def tenant(*settings, complete=True):
    return {"tenant_settings.json": {"collectionComplete": complete, "tenantSettings": list(settings)}}


def all_settings(rule_id, **kwargs):
    return [setting(name, **kwargs) for name in TENANT_SIGNAL_SETTINGS[rule_id]]


def run_tenant(rule_id, **raw):
    ctx = context(**raw)
    return _assess_tenant(rule_id, resolve_rule(ctx.configuration, rule_id, {}), ctx)


def test_the_new_rules_are_registered_as_non_scored_signals():
    new = ["OPS-005", "OPS-006", "SEC-014", "SEC-015", "SEC-016", "SEC-017", "SEC-018", "SEC-019"]
    assert {RULE_KINDS[rule] for rule in new} == {"signal"}
    assert set(TENANT_SIGNAL_SETTINGS) <= TENANT_RULES and not set(TENANT_SIGNAL_SETTINGS) & {"SEC-017"}


# --- SEC-014 to SEC-016 ------------------------------------------------------------------------

@pytest.mark.parametrize("rule_id", sorted(TENANT_SIGNAL_SETTINGS))
def test_settings_open_to_everyone_are_named_and_recommend_review(rule_id):
    status, evidence = run_tenant(rule_id, **tenant(*all_settings(rule_id)))
    names = TENANT_SIGNAL_SETTINGS[rule_id]
    assert status == "info"
    assert evidence["open_setting_names"] == sorted(names)
    assert (evidence["open_setting_count"], evidence["judged_setting_count"]) == (len(names), len(names))
    assert evidence["review_recommended"] is True


@pytest.mark.parametrize("state", [
    {"enabled": False}, {"enabled": True, "groups": 2}, {"enabled": True, "can_limit": False},
])
def test_disabled_limited_or_unlimitable_settings_do_not_recommend_review(state):
    status, evidence = run_tenant("SEC-014", **tenant(*all_settings("SEC-014", **state)))
    assert (status, evidence["open_setting_count"], evidence["review_recommended"]) == ("info", 0, False)


def test_a_partly_returned_group_is_judged_on_what_was_returned():
    names = TENANT_SIGNAL_SETTINGS["SEC-015"]
    status, evidence = run_tenant("SEC-015", **tenant(setting(names[0]), setting(names[1], enabled=False)))
    assert status == "info"
    assert (evidence["judged_setting_count"], evidence["open_setting_names"]) == (2, [names[0]])
    assert evidence["not_returned_setting_names"] == sorted(names[2:])


def test_a_group_with_no_returned_setting_is_missing_evidence():
    status, evidence = run_tenant("SEC-016", **tenant(setting("SomethingElse")))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "setting_not_returned")


def test_an_unreadable_setting_state_is_not_counted_as_closed():
    names = TENANT_SIGNAL_SETTINGS["SEC-016"]
    broken = {"settingName": names[0], "enabled": None, "enabledSecurityGroupCount": None}
    status, evidence = run_tenant("SEC-016", **tenant(broken, setting(names[1], enabled=False)))
    assert (status, evidence["unreadable_setting_names"], evidence["judged_setting_count"]) == ("info", [names[0]], 1)


@pytest.mark.parametrize("raw", [tenant(complete=False), {"tenant_settings.json": {}}])
def test_unavailable_tenant_settings_are_missing_evidence(raw):
    status, evidence = run_tenant("SEC-014", **raw)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "tenant_settings_unavailable")


# --- SEC-017 and SEC-018 -----------------------------------------------------------------------

def person(identity, role="Viewer", **extra):
    return {"graphId": identity, "principalType": "User", "userType": "Member", "groupUserAccessRight": role, **extra}


def workspace(*users, status="collected", wid=PROD):
    return {"id": wid, "name": "workspace", "usersCollectionStatus": status, "users": list(users)}


def test_guests_are_counted_by_user_type_and_by_external_identifier():
    ws = workspace(
        person("a", "Admin", userType="Guest"), person("b", "Viewer", identifier="x_partner.com#EXT#@contoso.com"),
        person("c", "Member"), {"graphId": "g", "principalType": "Group", "groupUserAccessRight": "Member", "userType": "Guest"},
    )
    status, evidence = run("SEC-017", ws)
    assert status == "info"
    assert (evidence["principal_count"], evidence["guest_principal_count"], evidence["guest_elevated_role_count"]) == (4, 2, 1)
    assert evidence["review_recommended"] is True
    assert "graphId" not in str(evidence) and "partner" not in str(evidence)


def test_a_workspace_without_guests_does_not_recommend_review():
    status, evidence = run("SEC-017", workspace(person("a"), person("b", "Admin")))
    assert (status, evidence["guest_principal_count"], evidence["review_recommended"]) == ("info", 0, False)


def test_the_highest_role_of_a_principal_counts_once():
    ws = workspace(person("a", "Viewer", userType="Guest"), person("a", "Admin", userType="Guest"))
    evidence = run("SEC-017", ws)[1]
    assert (evidence["guest_principal_count"], evidence["guest_elevated_role_count"]) == (1, 1)


@pytest.mark.parametrize("rule_id", ["SEC-017", "SEC-018"])
@pytest.mark.parametrize("ws", [workspace(status="error"), {"id": PROD, "usersCollectionStatus": "collected"}])
def test_principals_that_were_not_collected_are_missing_evidence(rule_id, ws):
    status, evidence = run(rule_id, ws)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "workspace_principals_unavailable")


def app(identity, role, kind="App"):
    return {"graphId": identity, "principalType": kind, "groupUserAccessRight": role}


def test_applications_with_an_admin_or_member_role_recommend_review():
    ws = workspace(app("a", "Admin"), app("b", "Member", "ServicePrincipal"), app("c", "Viewer"), person("d", "Admin"))
    status, evidence = run("SEC-018", ws)
    assert status == "info"
    assert (evidence["app_principal_count"], evidence["app_elevated_role_count"]) == (3, 2)
    assert evidence["review_recommended"] is True


def test_read_only_applications_do_not_recommend_review():
    evidence = run("SEC-018", workspace(app("a", "Viewer"), app("b", "Contributor")))[1]
    assert (evidence["app_principal_count"], evidence["review_recommended"]) == (2, False)


# --- OPS-006 -----------------------------------------------------------------------------------

def hosted(sku, wid=PROD, capacity=CAP, **extra):
    return {"id": wid, "name": "workspace", "capacityId": capacity, "capacitySku": sku, **extra}


@pytest.mark.parametrize("sku, kind, recommended", [
    ("FT1", "trial", True), ("A2", "embedded", True), ("EM3", "embedded", True), ("PP3", "ppu", True),
    ("F8", "standard", False), ("P1", "standard", False),
])
def test_production_on_a_non_production_grade_capacity_recommends_review(sku, kind, recommended):
    status, evidence = run("OPS-006", hosted(sku))
    assert (status, evidence["capacity_class"], evidence["review_recommended"]) == ("info", kind, recommended)


def test_a_premium_per_user_flag_is_enough():
    assert run("OPS-006", hosted(None, isOnPremiumPerUserCapacity=True))[1]["capacity_class"] == "ppu"


def test_the_sku_is_read_from_the_capacity_inventory_when_the_workspace_lacks_it():
    raw = {"capacity_metrics.json": {"capacities": [{"id": CAP.upper(), "sku": "FT1"}]}}
    assert run("OPS-006", hosted(None), **raw)[1]["capacity_class"] == "trial"


def test_an_unknown_sku_is_missing_evidence():
    status, evidence = run("OPS-006", hosted(None))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "capacity_sku_unavailable")


@pytest.mark.parametrize("wid, code", [(NONPROD, None), (UNDECLARED, "environment_undeclared")])
def test_only_declared_production_workspaces_are_judged_for_capacity_grade(wid, code):
    status, evidence = run("OPS-006", hosted("FT1", wid=wid))
    assert (status, evidence.get("reason_code")) == ("not_applicable", code)


def test_a_production_workspace_without_a_capacity_is_not_applicable():
    status, evidence = run("OPS-006", hosted(None, capacity=None))
    assert (status, evidence["reason_code"]) == ("not_applicable", "no_dedicated_capacity")


# --- ARCH-017 to ARCH-019 ----------------------------------------------------------------------

def inventory(*item_ids, status="collected", wid=PROD):
    return {"id": wid, "name": "workspace", "itemsCollectionStatus": status,
            "items": [{"id": item, "type": "Notebook"} for item in item_ids]}


def test_an_empty_workspace_is_flagged_and_one_with_items_is_not():
    assert run("ARCH-017", inventory())[1]["review_recommended"] is True
    status, evidence = run("ARCH-017", inventory("n1", "n2"))
    assert (status, evidence["item_count"], evidence["review_recommended"]) == ("info", 2, False)


def test_an_incomplete_inventory_is_never_reported_as_empty():
    status, evidence = run("ARCH-017", inventory(status="unavailable"))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "item_inventory_incomplete")


def scan(*workspaces, complete=True):
    return {"scanner.json": {"collectionComplete": complete, "workspaces": list(workspaces)}}


def scanned(wid, models=(), reports=()):
    return {"id": wid, "datasets": [{"id": m, **({"upstreamDatasets": up} if up else {})} for m, up in models],
            "reports": [{"id": r, "datasetId": d, "reportType": kind} for r, d, kind in reports]}


def test_a_report_with_no_model_is_an_orphan_and_one_whose_model_is_unknown_is_only_unresolved_without_fuam():
    raw = scan(scanned(PROD, models=[("m1", None)], reports=[("r1", "M1", "PowerBIReport"), ("r2", "gone", "PowerBIReport"),
                                                         ("r3", None, "PowerBIReport"), ("r4", None, "PaginatedReport")]))
    status, evidence = run("ARCH-018", workspace(), **raw)
    assert status == "info" and evidence["report_count"] == 3 and evidence["lookup_checked"] is False
    assert (evidence["orphan_report_count"], evidence["orphan_report_ids"], evidence["review_recommended"]) == (1, ["r3"], True)
    assert (evidence["unresolved_report_count"], evidence["unresolved_report_ids"]) == (1, ["r2"])
    assert evidence["lookup_unavailable_reason"] == "source_not_enabled"

def test_a_workspace_without_power_bi_reports_is_not_applicable_for_orphans():
    assert run("ARCH-018", workspace(), **scan(scanned(PROD, reports=[("r", None, "PaginatedReport")])))[0] == "not_applicable"


def test_models_read_by_a_report_or_built_on_by_another_model_are_used():
    raw = scan(scanned(PROD, models=[("m1", None), ("m2", None), ("m3", None), ("m4", [{"targetDatasetId": "M3"}])],
                       reports=[("r1", "m1", "PowerBIReport")]),
               scanned(NONPROD, reports=[("r2", "M2", "PowerBIReport")]))
    status, evidence = run("ARCH-019", workspace(), **raw)
    assert status == "info" and evidence["model_count"] == 4
    assert (evidence["unused_model_ids"], evidence["review_recommended"]) == (["m4"], True)


@pytest.mark.parametrize("rule_id", ["ARCH-018", "ARCH-019"])
@pytest.mark.parametrize("raw", [{}, scan(complete=False), scan(scanned(NONPROD))])
def test_scan_gaps_are_missing_evidence_for_report_and_model_rules(rule_id, raw):
    status, evidence = run(rule_id, workspace(), **raw)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "scanner_unavailable")

# --- ARCH-018 and ARCH-019 with FUAM ------------------------------------------------------------

def family(rows, status="collected", freshness="recent", **extra):
    return {"table": "t", "status": status, "freshness_status": freshness, "rows": rows, "observed_at": "2026-03-01T00:00:00+00:00",
            "window_days": 30, **extra}


def test_a_model_found_elsewhere_in_the_tenant_is_not_an_orphan_and_one_found_nowhere_is():
    raw = scan(scanned(PROD, reports=[("r1", "m9", "PowerBIReport"), ("r2", "m10", "PowerBIReport")]))
    facts = {"model_lookup": family([{"model_id": "m9", "workspace_id": NONPROD}])}
    status, evidence = run("ARCH-018", workspace(), facts=facts, **raw)
    assert status == "info" and evidence["lookup_checked"] is True
    assert (evidence["bound_outside_scope_count"], evidence["orphan_report_ids"], evidence["unresolved_report_count"]) == (1, ["r2"], 0)
    assert evidence["review_recommended"] is True


@pytest.mark.parametrize("facts, reason", [
    ({"model_lookup": family([], freshness="stale")}, "source_data_stale"),
    ({"model_lookup": family([], status="unavailable", reason_code="query_failed")}, "query_failed"),
    ({}, "enrichment_unavailable"),
])
def test_an_unusable_lookup_never_turns_an_unknown_model_into_an_orphan(facts, reason):
    raw = scan(scanned(PROD, reports=[("r1", "m9", "PowerBIReport")]))
    evidence = run("ARCH-018", workspace(), facts=facts, **raw)[1]
    assert (evidence["orphan_report_count"], evidence["unresolved_report_count"], evidence["lookup_unavailable_reason"]) == (0, 1, reason)


def hosted_scan(*models, capacity=CAP):
    return scan(scanned(PROD, models=[(model, None) for model in models])), {"id": PROD, "name": "w", "capacityId": capacity,
                                                                            "itemsCollectionStatus": "collected", "items": []}


def test_queries_count_as_use_even_without_a_report():
    raw, ws = hosted_scan("m1", "m2", "m3")
    facts = {"model_usage": family([{"item_id": "m1", "query_count": 4}])}
    status, evidence = run("ARCH-019", ws, facts=facts, **raw)
    assert status == "info" and evidence["usage_checked"] is True
    assert (evidence["unused_model_ids"], evidence["queried_without_report_count"], evidence["review_recommended"]) == (["m2", "m3"], 1, True)


def test_a_queried_model_is_not_unused():
    raw, ws = hosted_scan("m1")
    evidence = run("ARCH-019", ws, facts={"model_usage": family([{"item_id": "M1", "query_count": 1}])}, **raw)[1]
    assert (evidence["unused_model_count"], evidence["review_recommended"], evidence["no_report_model_count"]) == (0, False, 1)


def test_a_model_not_on_a_capacity_has_no_recorded_usage_so_it_is_unconfirmed_not_unused():
    raw, ws = hosted_scan("m1", capacity=None)
    evidence = run("ARCH-019", ws, facts={"model_usage": family([])}, **raw)[1]
    assert (evidence["unused_model_count"], evidence["unconfirmed_model_ids"], evidence["review_recommended"]) == (0, ["m1"], False)


@pytest.mark.parametrize("facts, reason", [
    ({"model_usage": family([], freshness="stale")}, "source_data_stale"),
    ({"model_usage": family([], status="unavailable", reason_code="incompatible_schema")}, "incompatible_schema"),
])
def test_without_usable_usage_the_native_result_stands_and_says_why(facts, reason):
    raw, ws = hosted_scan("m1")
    evidence = run("ARCH-019", ws, facts=facts, **raw)[1]
    assert (evidence["usage_checked"], evidence["usage_unavailable_reason"], evidence["unused_model_ids"]) == (False, reason, ["m1"])


def test_a_data_store_default_model_is_not_a_candidate():
    ws = {"id": PROD, "name": "w", "capacityId": CAP, "itemsCollectionStatus": "collected",
          "items": [{"id": "l1", "type": "Lakehouse", "displayName": "Sales"}, {"id": "n1", "type": "Notebook", "displayName": "Sales"}]}
    raw = scan({"id": PROD, "datasets": [{"id": "d1", "name": "sales"}, {"id": "d2", "name": "Mine"}], "reports": []})
    evidence = run("ARCH-019", ws, **raw)[1]
    assert (evidence["default_model_count"], evidence["unused_model_ids"]) == (1, ["d2"])
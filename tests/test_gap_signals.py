# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capacity, deployment pipeline, OneLake role, network, item-grant and reliability signals, and the collectors behind them."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analyzers.far_profile import RULE_KINDS, TENANT_SWITCH_SETTINGS
from analyzers.far_review import _assess, _assess_tenant
from analyzers.rule_support import Context
from collectors import deployment_pipelines, onelake_roles, workspace_network
from collectors._http import HttpError
from orchestration.review_config import (
    ROOT, declared_profiles, local_configuration, read_yaml, validate_configuration,
)
from orchestration.rule_policy import evidence_demanded, resolve_rule, validate_override

PROD = "11111111-1111-1111-1111-111111111111"
NONPROD = "22222222-2222-2222-2222-222222222222"
UNDECLARED = "44444444-4444-4444-4444-444444444444"
CAP = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
LH = "bbbbbbbb-0000-4000-8000-000000000001"
SM = "bbbbbbbb-0000-4000-8000-000000000002"


def configuration(production=PROD, **policy):
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["scope"].update({"production_workspace_ids": production, "nonproduction_workspace_ids": NONPROD})
    values["policy"].update(policy)
    config = local_configuration()
    config["profiles"] = declared_profiles(values)
    validate_configuration(config)
    return config


def run(rule_id, ws, config=None, **raw):
    config = config or configuration()
    ctx = Context({"configuration": config}, [ws], raw)
    return _assess(rule_id, ws, resolve_rule(config, rule_id, ws), ctx)


def run_tenant(rule_id, workspaces, config=None, **raw):
    config = config or configuration()
    ctx = Context({"configuration": config}, list(workspaces), raw)
    return _assess_tenant(rule_id, resolve_rule(config, rule_id, {}), ctx)


def hosted(wid=PROD, capacity=CAP, **extra):
    return {"id": wid, "name": "workspace", "capacityId": capacity, **extra}


def metrics(*capacities):
    return {"capacity_metrics.json": {"collectionComplete": True, "capacities": list(capacities)}}


def test_the_new_rules_are_non_scored_signals():
    assert {RULE_KINDS[r] for r in ("OPS-008", "OPS-009", "OPS-010", "SEC-020", "SEC-021", "SEC-022")} == {"signal"}


# --- OPS-008 and OPS-009 -----------------------------------------------------------------------

@pytest.mark.parametrize("state, recommended", [("Active", False), ("Suspended", True), ("Inactive", True)])
def test_a_capacity_that_is_not_active_recommends_review(state, recommended):
    status, evidence = run("OPS-008", hosted(), **metrics({"id": CAP.upper(), "state": state, "sku": "F8"}))
    assert (status, evidence["capacity_state"], evidence["review_recommended"]) == ("info", state, recommended)


@pytest.mark.parametrize("rule_id", ["OPS-008", "OPS-009"])
def test_capacity_rules_need_a_capacity_that_is_in_the_inventory(rule_id):
    assert run(rule_id, hosted(capacity=None), **metrics())[0] == "not_applicable"
    status, evidence = run(rule_id, hosted(), **metrics({"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "state": "Active", "admins": []}))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "capacity_not_in_source")
    assert run(rule_id, hosted(), **{})[1]["reason_code"] == "capacity_inventory_unavailable"


def test_a_capacity_without_a_state_is_missing_evidence():
    assert run("OPS-008", hosted(), **metrics({"id": CAP}))[1]["reason_code"] == "capacity_state_unavailable"


@pytest.mark.parametrize("admins, recommended", [(["alice@contoso.com"], True), (["alice@contoso.com", "bob@contoso.com"], False), ([], True)])
def test_fewer_capacity_administrators_than_the_minimum_recommends_review(admins, recommended):
    status, evidence = run("OPS-009", hosted(), **metrics({"id": CAP, "admins": admins}))
    assert (status, evidence["capacity_admin_count"], evidence["review_below"], evidence["review_recommended"]) == (
        "info", len(admins), 2, recommended)
    assert "contoso" not in json.dumps(evidence)


def test_unreadable_capacity_administrators_are_missing_evidence_and_the_minimum_is_configurable():
    assert run("OPS-009", hosted(), **metrics({"id": CAP, "admins": None}))[1]["reason_code"] == "capacity_admins_unavailable"
    config = configuration()
    config["thresholds"]["operations"]["min_capacity_admins"] = 1
    assert run("OPS-009", hosted(), config, **metrics({"id": CAP, "admins": ["a"]}))[1]["review_recommended"] is False


# --- production-only signals -------------------------------------------------------------------

@pytest.mark.parametrize("rule_id, raw", [
    ("OPS-010", {"deployment_pipelines.json": {"collectionComplete": True, "pipelines": [], "visibility": "tenant"}}),
    ("SEC-020", {"onelake_roles.json": {"lakehouses": []}}),
    ("SEC-021", {"workspace_network.json": {"workspaces": []}}),
    ("SEC-022", {"scanner.json": {"collectionComplete": True, "workspaces": [{"id": NONPROD}]}}),
])
@pytest.mark.parametrize("wid, code", [(NONPROD, None), (UNDECLARED, "environment_undeclared")])
def test_only_declared_production_workspaces_are_judged(rule_id, raw, wid, code):
    status, evidence = run(rule_id, hosted(wid), **raw)
    assert (status, evidence.get("reason_code")) == ("not_applicable", code)


@pytest.mark.parametrize("rule_id", ["OPS-010", "SEC-020", "SEC-021"])
def test_evidence_is_demanded_for_production_workspaces_only(rule_id):
    config = configuration()
    for wid, expected in ((PROD, True), (NONPROD, False), (UNDECLARED, False)):
        assert evidence_demanded(rule_id, resolve_rule(config, rule_id, {"id": wid})) is expected


# --- OPS-010 -----------------------------------------------------------------------------------

def pipelines(*stages, visibility="tenant", complete=True):
    return {"deployment_pipelines.json": {"collectionComplete": complete, "visibility": visibility, "pipelines": [
        {"id": "p1", "stages": [{"order": order, "workspaceId": wid} for order, wid in stages]}] if stages else []}}


def test_a_production_workspace_in_a_pipeline_stage_is_not_flagged():
    status, evidence = run("OPS-010", hosted(), **pipelines((2, PROD.upper())))
    assert (status, evidence["in_deployment_pipeline"], evidence["stage_orders"], evidence["review_recommended"]) == (
        "info", True, ["2"], False)


def test_a_production_workspace_outside_every_pipeline_recommends_review_when_every_pipeline_was_read():
    status, evidence = run("OPS-010", hosted(), **pipelines((0, NONPROD)))
    assert (status, evidence["in_deployment_pipeline"], evidence["review_recommended"]) == ("info", False, True)


def test_pipelines_the_reviewer_cannot_see_never_prove_a_workspace_is_outside_them():
    status, evidence = run("OPS-010", hosted(), **pipelines((0, NONPROD), visibility="caller"))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "pipelines_partly_visible")
    assert run("OPS-010", hosted(), **pipelines((2, PROD), visibility="caller"))[1]["in_deployment_pipeline"] is True


@pytest.mark.parametrize("raw", [{}, pipelines(complete=False)])
def test_unread_pipelines_are_missing_evidence(raw):
    assert run("OPS-010", hosted(), **raw)[1]["reason_code"] == "deployment_pipelines_unavailable"


# --- SEC-020 -----------------------------------------------------------------------------------

def with_lakehouses(*ids, status="collected"):
    return hosted(itemsCollectionStatus=status, items=[{"id": i, "type": "Lakehouse"} for i in ids])


def roles(*rows):
    return {"onelake_roles.json": {"lakehouses": [{"workspaceId": PROD, "status": "collected", **row} for row in rows]}}


def test_lakehouses_without_custom_roles_recommend_review_and_custom_roles_are_counted():
    ws = with_lakehouses(LH, "bbbbbbbb-0000-4000-8000-0000000000ff")
    status, evidence = run("SEC-020", ws, **roles({"id": LH, "customRoleCount": 0}, {"id": "bbbbbbbb-0000-4000-8000-0000000000ff", "customRoleCount": 3}))
    assert status == "info"
    assert (evidence["lakehouse_count"], evidence["lakehouses_without_custom_roles"], evidence["custom_role_count"]) == (2, 1, 3)
    assert evidence["review_recommended"] is True
    assert run("SEC-020", with_lakehouses(LH), **roles({"id": LH, "customRoleCount": 2}))[1]["review_recommended"] is False


def test_an_unread_lakehouse_is_never_reported_as_having_roles_or_not():
    raw = {"onelake_roles.json": {"lakehouses": [{"workspaceId": PROD, "id": LH, "status": "unavailable"}]}}
    status, evidence = run("SEC-020", with_lakehouses(LH), **raw)
    assert (status, evidence["reason_code"], evidence["unread_lakehouse_count"]) == ("missing_evidence", "onelake_roles_unavailable", 1)
    assert run("SEC-020", with_lakehouses(LH), **{})[1]["reason_code"] == "onelake_roles_unavailable"
    assert run("SEC-020", with_lakehouses(LH, status="unavailable"), **roles())[1]["reason_code"] == "item_inventory_incomplete"
    assert run("SEC-020", with_lakehouses(), **roles())[0] == "not_applicable"


# --- SEC-021 -----------------------------------------------------------------------------------

def network(inbound="Allow", outbound="Allow", status="collected", **extra):
    row = {"workspaceId": PROD, "status": status, **({"inbound": inbound, "outbound": outbound, **extra} if status == "collected" else {})}
    return {"workspace_network.json": {"workspaces": [row]}}


def test_firewall_rules_that_restrict_nothing_are_flagged_without_a_declaration():
    evidence = run("SEC-021", hosted(), **network("Allow", "Deny", inboundFirewallRuleCount=2, inboundFirewallBroadRangeCount=0))[1]
    assert (evidence["signal_codes"], evidence["review_recommended"], evidence["policy_declared"]) == (
        ["firewall_rules_not_enforced"], True, False)
    assert evidence["inbound_firewall_rule_count"] == 2


def test_a_very_wide_firewall_range_is_flagged_even_when_inbound_is_denied():
    evidence = run("SEC-021", hosted(), **network("Deny", "Deny", inboundFirewallRuleCount=3, inboundFirewallBroadRangeCount=1))[1]
    assert (evidence["signal_codes"], evidence["review_recommended"]) == (["firewall_range_too_broad"], True)


def test_public_outbound_left_open_beside_managed_private_endpoints_is_flagged():
    evidence = run("SEC-021", hosted(), **network("Deny", "Allow", managedPrivateEndpointCount=2))[1]
    assert (evidence["signal_codes"], evidence["managed_private_endpoint_count"]) == (["outbound_open_with_private_endpoints"], 2)


def test_a_declared_outbound_restriction_is_reported_as_blocked_while_outbound_protection_is_off():
    config = configuration(restricted_network_directions="outbound")
    evidence = run("SEC-021", hosted(), config, **network("Deny", "Allow", outboundProtection="disabled"))[1]
    assert (evidence["signal_codes"], evidence["outbound_protection"], evidence["open_required_directions"]) == (
        ["outbound_protection_disabled"], "disabled", ["outbound"])


def test_a_consistent_locked_down_workspace_is_not_flagged_and_unread_extras_stay_empty():
    evidence = run("SEC-021", hosted(), **network("Deny", "Deny", inboundFirewallRuleCount=2, inboundFirewallBroadRangeCount=0,
                                                   outboundProtection="enabled", managedPrivateEndpointCount=1))[1]
    assert (evidence["signal_codes"], evidence["review_recommended"]) == ([], False)
    bare = run("SEC-021", hosted(), **network("Allow", "Allow"))[1]
    assert (bare["inbound_firewall_rule_count"], bare["outbound_protection"], bare["review_recommended"]) == (None, None, False)


# --- SEC-023 -----------------------------------------------------------------------------------

def switches(**states):
    names = dict(zip(("inbound", "outbound", "firewall", "public"), TENANT_SWITCH_SETTINGS["SEC-023"][1:] + TENANT_SWITCH_SETTINGS["SEC-023"][:1]))
    rows = [{"settingName": names[key], "enabled": value} for key, value in states.items()]
    return {"tenant_settings.json": {"collectionComplete": True, "tenantSettings": rows}}


def test_the_network_switches_are_the_ones_the_rule_reads():
    from analyzers.native_rules import NETWORK_SWITCHES
    assert set(NETWORK_SWITCHES.values()) == set(TENANT_SWITCH_SETTINGS["SEC-023"])
    assert RULE_KINDS["SEC-023"] == "signal"


def test_a_declared_direction_with_its_tenant_switch_off_recommends_review():
    config = configuration(restricted_network_directions="inbound, outbound")
    status, evidence = run_tenant("SEC-023", [hosted()], config, **switches(inbound=True, outbound=False, firewall=True, public=False))
    assert status == "info"
    assert evidence["directions_that_cannot_be_applied"] == ["outbound"]
    assert evidence["review_recommended"] is True
    assert evidence["tenant_switches"] == {"inbound": "enabled", "outbound": "disabled", "firewall": "enabled", "tenant_public": "disabled"}


def test_the_switches_are_reported_without_a_recommendation_until_a_direction_is_declared():
    evidence = run_tenant("SEC-023", [hosted()], **switches(inbound=False, outbound=False))[1]
    assert (evidence["declared_restricted_directions"], evidence["review_recommended"]) == ([], False)


def test_unreturned_network_switches_are_missing_evidence_and_unreadable_tenant_settings_a_gap():
    status, evidence = run_tenant("SEC-023", [hosted()], **switches())
    assert (status, evidence["reason_code"]) == ("missing_evidence", "setting_not_returned")
    gap = run_tenant("SEC-023", [hosted()], **{"tenant_settings.json": {"collectionComplete": False}})
    assert gap[1]["reason_code"] == "tenant_settings_unavailable"


# --- REL-001 -----------------------------------------------------------------------------------

PROD2 = "55555555-5555-5555-5555-555555555555"
CAP2 = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_the_reliability_rules_are_non_scored_signals():
    assert {RULE_KINDS[r] for r in ("REL-001", "REL-002", "REL-003")} == {"signal"}


def test_production_workspaces_sharing_one_capacity_recommend_review():
    config = configuration(f"{PROD},{PROD2}")
    rows = metrics({"id": CAP.upper(), "region": "West Europe"})
    status, evidence = run_tenant("REL-001", [hosted(), hosted(PROD2)], config, **rows)
    assert status == "info"
    assert (evidence["production_workspace_count"], evidence["production_capacity_count"],
            evidence["production_region_count"], evidence["review_recommended"]) == (2, 1, 1, True)


def test_a_review_limited_to_chosen_workspaces_cannot_judge_production_concentration():
    config = configuration(f"{PROD},{PROD2}")
    rows = metrics({"id": CAP, "region": "West Europe"})
    rows["capacity_metrics.json"]["workspaceScopeLimited"] = True
    status, evidence = run_tenant("REL-001", [hosted(), hosted(PROD2)], config, **rows)
    assert (status, evidence["reason_code"]) == ("not_applicable", "review_scope_limited")


def test_production_spread_over_two_capacities_is_not_flagged_and_unknown_regions_are_not_guessed():
    config = configuration(f"{PROD},{PROD2}")
    rows = metrics({"id": CAP, "region": "West Europe"}, {"id": CAP2, "region": "North Europe"})
    evidence = run_tenant("REL-001", [hosted(), hosted(PROD2, CAP2)], config, **rows)[1]
    assert (evidence["production_capacity_count"], evidence["production_region_count"], evidence["review_recommended"]) == (2, 2, False)
    unknown = run_tenant("REL-001", [hosted(), hosted(PROD2, CAP2)], config, **metrics({"id": CAP}))[1]
    assert unknown["production_region_count"] is None


def test_one_production_workspace_is_reported_but_not_flagged_and_undeclared_production_is_not_inferred():
    evidence = run_tenant("REL-001", [hosted()], **metrics({"id": CAP, "region": "West Europe"}))[1]
    assert (evidence["production_workspace_count"], evidence["review_recommended"]) == (1, False)
    status, evidence = run_tenant("REL-001", [hosted(NONPROD), hosted(UNDECLARED)], **metrics())
    assert (status, evidence["reason_code"]) == ("not_applicable", "environment_undeclared")
    assert run_tenant("REL-001", [hosted(capacity=None)])[1]["reason_code"] == "no_dedicated_capacity"


def test_production_mostly_on_one_capacity_is_flagged_even_when_a_second_capacity_exists():
    ids = [f"{n:08d}-0000-4000-8000-000000000000" for n in range(1, 12)]
    config = configuration(",".join(ids))
    workspaces = [hosted(wid) for wid in ids[:10]] + [hosted(ids[10], CAP2)]
    rows = metrics({"id": CAP, "region": "West US 3"}, {"id": CAP2, "region": "West Europe", "state": "Suspended"})
    status, evidence = run_tenant("REL-001", workspaces, config, **rows)
    assert status == "info"
    assert (evidence["production_capacity_count"], evidence["largest_capacity_workspace_count"],
            evidence["largest_capacity_share"], evidence["review_above"], evidence["review_recommended"]) == (2, 10, 0.909, 0.8, True)


def test_the_concentration_threshold_decides_and_an_even_spread_is_not_flagged():
    ids = [f"{n:08d}-0000-4000-8000-000000000000" for n in range(1, 6)]
    config = configuration(",".join(ids))
    workspaces = [hosted(wid) for wid in ids[:4]] + [hosted(ids[4], CAP2)]
    rows = metrics({"id": CAP, "region": "West US 3"}, {"id": CAP2, "region": "West US 3"})
    assert run_tenant("REL-001", workspaces, config, **rows)[1]["review_recommended"] is False  # 4 of 5 is not above 0.8
    config["thresholds"]["operations"]["capacity_concentration_max_ratio"] = 0.7
    assert run_tenant("REL-001", workspaces, config, **rows)[1]["review_recommended"] is True
    even = [hosted(ids[0]), hosted(ids[1], CAP2)]
    assert run_tenant("REL-001", even, configuration(f"{ids[0]},{ids[1]}"), **rows)[1]["review_recommended"] is False


# --- findings that name what to look at ----------------------------------------------------------

def test_the_pipeline_signals_name_the_pipelines_to_look_at():
    from analyzers import native_rules
    from tests.test_native_rules import activity, after, context, definitions, inventory, pipeline
    a, b, c = "a" * 8 + "-0000-4000-8000-000000000001", "b" * 8 + "-0000-4000-8000-000000000002", "c" * 8 + "-0000-4000-8000-000000000003"
    ok = {"retry": 2, "timeout": "0.01:00:00"}
    pipes = [
        pipeline([activity(name="x", policy=ok), activity("TridentNotebook", "y", policy=ok, **after("x", "Succeeded"))], pid=a),
        pipeline([activity(name="x"), activity("TridentNotebook", "y", policy=ok, **after("x", "Failed"))], pid=b),
        pipeline([activity(name="x", policy=ok)], pid=c),
    ]
    ws = {"id": PROD, **inventory((a, "DataPipeline"), (b, "DataPipeline"), (c, "DataPipeline"))}
    ctx = context(**definitions(pipelines=pipes))
    failure = native_rules.pipeline_failure_paths(ws, ctx)[1]
    assert (failure["pipeline_ids_without_failure_path"], failure["without_failure_path_count"]) == ([a], 1)
    resilience = native_rules.pipeline_resilience(ws, ctx)[1]
    assert resilience["pipeline_ids_without_retry"] == [b] and resilience["pipeline_ids_with_default_timeout"] == [b]


def test_the_onelake_and_grant_signals_name_the_items_to_look_at():
    roles = {"onelake_roles.json": {"lakehouses": [
        {"workspaceId": PROD, "id": LH, "status": "collected", "customRoleCount": 0},
        {"workspaceId": PROD, "id": "dddddddd-0000-4000-8000-000000000004", "status": "collected", "customRoleCount": 2}]}}
    ws = hosted(items=[{"id": LH, "type": "Lakehouse"}, {"id": "dddddddd-0000-4000-8000-000000000004", "type": "Lakehouse"}],
                itemsCollectionStatus="collected")
    assert run("SEC-020", ws, **roles)[1]["lakehouse_ids_without_custom_roles"] == [LH]
    raw = granted(datasets=[{"id": SM, **summary(2, 1)}], Lakehouse=[{"id": LH, **summary(1, 0)}])
    assert run("SEC-022", hosted(), **raw)[1]["item_ids_with_resharing_grants"] == [SM]


# --- REL-003 -----------------------------------------------------------------------------------

def reading(*groups):
    return granted(datasets=[{"id": SM, "upstreamDatasets": [{"groupId": group} for group in groups]}])


def test_a_production_model_reading_from_a_declared_nonproduction_workspace_recommends_review():
    status, evidence = run("REL-003", hosted(), **reading(NONPROD.upper(), UNDECLARED))
    assert status == "info"
    assert (evidence["upstream_workspace_count"], evidence["nonproduction_upstream_count"],
            evidence["undeclared_upstream_count"], evidence["review_recommended"]) == (2, 1, 1, True)


def test_dependencies_on_production_or_on_the_same_workspace_are_not_flagged():
    config = configuration(f"{PROD},{PROD2}")
    evidence = run("REL-003", hosted(), config, **reading(PROD2, PROD))[1]
    assert (evidence["upstream_workspace_count"], evidence["nonproduction_upstream_count"], evidence["review_recommended"]) == (1, 0, False)


def test_production_dependencies_are_judged_for_production_workspaces_only_and_need_the_scan():
    assert run("REL-003", hosted(NONPROD), **reading(PROD))[0] == "not_applicable"
    assert run("REL-003", hosted(), **{"scanner.json": {"collectionComplete": False, "workspaces": []}})[1]["reason_code"] == "scanner_unavailable"
    assert evidence_demanded("REL-003", resolve_rule(configuration(), "REL-003", {"id": NONPROD})) is False


def test_the_network_policy_is_reported_without_a_recommendation_until_a_direction_is_declared():
    status, evidence = run("SEC-021", hosted(), **network())
    assert (status, evidence["inbound_public_access"], evidence["policy_declared"], evidence["review_recommended"]) == (
        "info", "Allow", False, False)


@pytest.mark.parametrize("declared, inbound, outbound, recommended, open_directions", [
    ("inbound", "Allow", "Allow", True, ["inbound"]),
    ("inbound", "Deny", "Allow", False, []),
    ("Inbound, Outbound", "Deny", "Allow", True, ["outbound"]),
    ("outbound", "Allow", "Deny", False, []),
])
def test_only_the_declared_directions_that_are_open_recommend_review(declared, inbound, outbound, recommended, open_directions):
    config = configuration(restricted_network_directions=declared)
    evidence = run("SEC-021", hosted(), config, **network(inbound, outbound))[1]
    assert (evidence["review_recommended"], evidence["open_required_directions"]) == (recommended, open_directions)


def test_an_invalid_direction_is_rejected_and_an_unread_policy_is_missing_evidence():
    with pytest.raises(ValueError, match="inbound or outbound"):
        configuration(restricted_network_directions="sideways")
    with pytest.raises(ValueError, match="inbound or outbound"):
        validate_override("SEC-021", {"reason": "x", "policy": {"required": True, "restricted_directions": ["up"]}})
    assert run("SEC-021", hosted(), **network(status="unavailable"))[1]["reason_code"] == "workspace_network_unavailable"
    assert run("SEC-021", hosted(), **{})[1]["reason_code"] == "workspace_network_unavailable"


# --- SEC-022 -----------------------------------------------------------------------------------

def granted(**buckets):
    return {"scanner.json": {"collectionComplete": True, "workspaces": [{"id": PROD, **buckets}]}}


def summary(grants=0, reshare=0, group=0, app=0, guest=0):
    return {"accessSummary": {"principalCount": grants + 1, "directGrantCount": grants, "directReshareCount": reshare,
                              "directGroupCount": group, "directAppCount": app, "directGuestCount": guest}}


def test_direct_grants_with_the_right_to_share_again_recommend_review():
    raw = granted(datasets=[{"id": SM, **summary(2, 1, group=1, guest=1)}], Lakehouse=[{"id": LH, **summary()}])
    status, evidence = run("SEC-022", hosted(), **raw)
    assert status == "info"
    assert (evidence["eligible_item_count"], evidence["items_read"], evidence["items_with_direct_grants"],
            evidence["direct_grant_count"], evidence["direct_reshare_count"], evidence["direct_guest_count"]) == (2, 2, 1, 2, 1, 1)
    assert evidence["review_recommended"] is True


def test_direct_grants_without_re_share_are_counted_but_do_not_recommend_review():
    evidence = run("SEC-022", hosted(), **granted(Warehouse=[{"id": SM, **summary(3, 0)}]))[1]
    assert (evidence["direct_grant_count"], evidence["review_recommended"]) == (3, False)


def test_items_without_access_counts_are_never_reported_as_having_no_grants():
    status, evidence = run("SEC-022", hosted(), **granted(datasets=[{"id": SM}]))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "item_access_unavailable")
    evidence = run("SEC-022", hosted(), **granted(datasets=[{"id": SM}, {"id": LH, **summary(1, 1)}]))[1]
    assert (evidence["items_read"], evidence["items_unread"]) == (1, 1)


def test_other_item_types_are_not_judged_and_a_missing_scan_is_missing_evidence():
    assert run("SEC-022", hosted(), **granted(Notebook=[{"id": "n", **summary(5, 5)}]))[0] == "not_applicable"
    assert run("SEC-022", hosted(), **{"scanner.json": {"collectionComplete": False, "workspaces": []}})[1]["reason_code"] == "scanner_unavailable"


# --- collectors --------------------------------------------------------------------------------

class Api:
    """A fake HTTP layer keyed on a URL fragment."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def response(self, url):
        self.calls.append(url)
        for fragment, answer in self.routes.items():
            if fragment in url:
                return answer
        raise AssertionError(url)


def reply(payload=None, status=200):
    return SimpleNamespace(status_code=status, content=b"x" if payload is not None else b"", json=lambda: payload)


@pytest.fixture
def estate(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    monkeypatch.delenv("FAR_CONFIG_SNAPSHOT", raising=False)
    workspaces = [
        {"id": PROD, "name": "p", "itemsCollectionStatus": "collected", "usersCollectionStatus": "collected",
         "items": [{"id": LH, "type": "Lakehouse"}, {"id": SM, "type": "SemanticModel"}, {"id": "n1", "type": "Notebook"}],
         "users": [{"graphId": "user-role", "principalType": "User", "groupUserAccessRight": "Admin"}]},
    ]
    for module in (workspace_network, onelake_roles, deployment_pipelines):
        monkeypatch.setattr(module, "get_default_provider", Mock())
        monkeypatch.setattr(module, "load_workspace_inventory", lambda _raw, rows=workspaces: rows)
    return workspaces


POLICY = {"inbound": {"publicAccessRules": {"defaultAction": "Deny"}}, "outbound": {"publicAccessRules": {"defaultAction": "Allow"}}}


def network_api(monkeypatch, **routes):
    api = Api({"inbound/firewall": reply(status=404), "outbound/connections": reply(status=404),
               "communicationPolicy": reply(POLICY), **routes})
    monkeypatch.setattr(workspace_network, "request", lambda method, url, headers: api.response(url))
    return api


def test_the_network_collector_keeps_counts_and_never_an_address_or_a_name(tmp_path, estate, monkeypatch):
    network_api(monkeypatch, **{
        "inbound/firewall": reply({"rules": [{"displayName": "Finance office", "value": "203.0.113.0/24"},
                                             {"displayName": "Everyone", "value": "10.0.0.0/8"},
                                             {"displayName": "Range", "value": "198.51.100.1-198.51.100.9"},
                                             {"displayName": "Wide range", "value": "20.0.0.0-20.2.0.0"},
                                             {"displayName": "Broken", "value": "not an address"}]}),
        "outbound/connections": reply({"errorCode": "OutboundAccessProtectionSettingsDisabled"}, status=403),
    })
    monkeypatch.setattr(workspace_network, "collect_value", lambda url, headers: [{"name": "mpe-secret-vault"}, {"name": "mpe-two"}])
    data = json.loads(workspace_network.collect(tmp_path).read_text())
    assert data["workspaces"] == [{
        "workspaceId": PROD, "status": "collected", "inbound": "Deny", "outbound": "Allow",
        "inboundFirewallRuleCount": 5, "inboundFirewallBroadRangeCount": 2,
        "outboundProtection": "disabled", "managedPrivateEndpointCount": 2}]
    text = json.dumps(data)
    assert data["collectionComplete"] is True
    assert not any(secret in text for secret in ("203.0.113", "Finance", "mpe-secret", "10.0.0.0", "not an address"))


def test_outbound_protection_that_is_on_is_recorded_and_other_failures_leave_the_extras_empty(tmp_path, estate, monkeypatch):
    network_api(monkeypatch, **{"outbound/connections": reply({"rules": []})})
    monkeypatch.setattr(workspace_network, "collect_value", Mock(side_effect=HttpError("x", status_code=403)))
    row = json.loads(workspace_network.collect(tmp_path).read_text())["workspaces"][0]
    assert row["outboundProtection"] == "enabled" and "managedPrivateEndpointCount" not in row and "inboundFirewallRuleCount" not in row
    network_api(monkeypatch, **{"outbound/connections": reply({"errorCode": "SomethingElse"}, status=403)})
    row = json.loads(workspace_network.collect(tmp_path).read_text())["workspaces"][0]
    assert "outboundProtection" not in row and row["status"] == "collected"


@pytest.mark.parametrize("answer", [reply(status=404), reply({"inbound": {}}), reply(status=403)])
def test_an_unreadable_network_policy_is_recorded_as_a_gap(tmp_path, estate, monkeypatch, answer):
    monkeypatch.setattr(workspace_network, "request", lambda method, url, headers: answer)
    data = json.loads(workspace_network.collect(tmp_path).read_text())
    assert data["workspaces"] == [{"workspaceId": PROD, "status": "unavailable"}]
    assert data["collectionComplete"] is False and data["collectionErrors"][0]["workspaceId"] == PROD


def test_the_onelake_collector_counts_roles_without_names_and_records_failures(tmp_path, estate, monkeypatch):
    monkeypatch.setattr(onelake_roles, "collect_value", lambda url, headers: [
        {"name": "DefaultReader", "members": {"x": 1}}, {"name": "Finance secret role"}])
    data = json.loads(onelake_roles.collect(tmp_path).read_text())
    assert data["lakehouses"] == [{"workspaceId": PROD, "id": LH, "status": "collected", "roleCount": 2, "customRoleCount": 1}]
    assert "Finance" not in json.dumps(data)

    def fail(url, headers):
        raise HttpError("boom", status_code=403)
    monkeypatch.setattr(onelake_roles, "collect_value", fail)
    data = json.loads(onelake_roles.collect(tmp_path).read_text())
    assert data["lakehouses"][0]["status"] == "unavailable" and data["collectionComplete"] is False


def test_the_pipeline_collector_prefers_the_administrator_listing_and_keeps_only_scoped_stages(tmp_path, estate, monkeypatch):
    page = {"value": [{"id": "p1", "displayName": "secret", "stages": [
        {"order": 0, "workspaceId": NONPROD, "workspaceName": "other"}, {"order": 2, "workspaceId": PROD.upper(), "workspaceName": "x"}]},
        {"id": "p2", "stages": [{"order": 0, "workspaceId": NONPROD}]}]}
    monkeypatch.setattr(deployment_pipelines, "get_json", lambda url, headers, params=None: page)
    data = json.loads(deployment_pipelines.collect(tmp_path).read_text())
    assert data["visibility"] == "tenant" and data["pipelineCount"] == 2
    assert data["pipelines"] == [{"id": "p1", "stages": [{"order": 2, "workspaceId": PROD}]}]
    assert "secret" not in json.dumps(data) and "other" not in json.dumps(data)


def test_the_pipeline_collector_falls_back_to_the_visible_pipelines_when_not_an_administrator(tmp_path, estate, monkeypatch):
    def denied(url, headers, params=None):
        raise HttpError("no", status_code=403)

    def listing(url, headers):
        return [{"id": "p1"}] if url.endswith("/deploymentPipelines") else [{"order": 1, "workspaceId": PROD}]
    monkeypatch.setattr(deployment_pipelines, "get_json", denied)
    monkeypatch.setattr(deployment_pipelines, "collect_value", listing)
    data = json.loads(deployment_pipelines.collect(tmp_path).read_text())
    assert (data["visibility"], data["collectionComplete"]) == ("caller", True)
    assert data["pipelines"][0]["stages"] == [{"order": 1, "workspaceId": PROD}]


def test_the_pipeline_collector_fails_loudly_on_other_errors(tmp_path, estate, monkeypatch):
    def broken(url, headers, params=None):
        raise HttpError("server", status_code=500)
    monkeypatch.setattr(deployment_pipelines, "get_json", broken)
    data = json.loads(deployment_pipelines.collect(tmp_path).read_text())
    assert data["collectionComplete"] is False

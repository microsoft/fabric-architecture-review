# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evaluators of the rules that read the review's own inventory, scanner and capacity metadata."""
import copy
import json
import os

import pytest

from analyzers import native_rules
from analyzers.far_profile import ACTIVE_RULE_IDS, CAPACITY_RULES, PROFILE_ID, PROFILE_SHA256, RULE_ORDER, TENANT_RULES
from analyzers.far_review import analyze
from analyzers.rule_support import Context
from orchestration.review_config import (
    ROOT, activate, capture, declared_profiles, local_configuration, read_snapshot, read_yaml, validate_configuration,
)
from orchestration.rule_policy import resolve_rule

PROD = "11111111-1111-1111-1111-111111111111"
NONPROD = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"
UNDECLARED = "44444444-4444-4444-4444-444444444444"
CAP_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CAP_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PIPELINE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
NOTEBOOK = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def configuration(scope=None, policy=None):
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["scope"].update({
        "production_workspace_ids": PROD, "nonproduction_workspace_ids": NONPROD, **(scope or {}),
    })
    values["policy"].update(policy or {})
    config = local_configuration()
    config["profiles"] = declared_profiles(values)
    validate_configuration(config)
    return config


def context(config=None, workspaces=(), **raw):
    return Context({"configuration": config or configuration()}, list(workspaces), raw)


def workspace(wid=PROD, capacity=None, **extra):
    return {"id": wid, "name": "workspace", "capacityId": capacity, **extra}


def effective(rule_id, ws, config=None):
    return resolve_rule(config or configuration(), rule_id, ws)


def inventory(*items, status="collected"):
    return {"itemsCollectionStatus": status, "items": [{"id": item_id, "type": kind} for item_id, kind in items]}


def scanner(wid=PROD, complete=True, **collections):
    return {"collectionComplete": complete, "workspaces": [{"id": wid, **collections}]}


# --- GOV-003 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("state, status", [("Orphaned", "fail"), ("Active", "pass"), ("Deleted", "unknown")])
def test_orphaned_state(state, status):
    result, evidence = native_rules.orphaned_state({"id": PROD, "state": state})
    assert result == status
    assert evidence["workspace_state"] == state


@pytest.mark.parametrize("ws", [{"id": PROD}, {"id": PROD, "state": ""}, {"id": PROD, "state": None},
                                {"id": PROD, "state": 3}])
def test_orphaned_state_missing_is_never_a_pass(ws):
    status, evidence = native_rules.orphaned_state(ws)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "workspace_state_unavailable")


def test_unsupported_workspace_state_is_unknown_with_reason():
    assert native_rules.orphaned_state({"id": PROD, "state": "Removing"})[1]["reason_code"] == "workspace_state_unsupported"


# --- ARCH-006 ----------------------------------------------------------------------------------

def test_production_sharing_a_capacity_with_declared_nonproduction_workspace_fails():
    prod, nonprod = workspace(PROD, CAP_A), workspace(NONPROD, CAP_A)
    status, evidence = native_rules.capacity_isolation(prod, context(workspaces=[prod, nonprod]))
    assert status == "fail"
    assert evidence["signal_codes"] == ["shares_capacity_with_nonproduction_workspace"]
    assert evidence["nonproduction_workspace_count"] == 1


def test_capacity_identity_is_compared_case_insensitively():
    prod, nonprod = workspace(PROD, CAP_A), workspace(NONPROD, CAP_A.upper())
    assert native_rules.capacity_isolation(prod, context(workspaces=[prod, nonprod]))[0] == "fail"


def test_production_on_a_capacity_declared_nonproduction_fails():
    config = configuration(scope={"nonproduction_capacity_ids": CAP_A})
    prod = workspace(PROD, CAP_A)
    status, evidence = native_rules.capacity_isolation(prod, context(config, [prod]))
    assert status == "fail"
    assert evidence["signal_codes"] == ["capacity_declared_nonproduction"]
    assert evidence["capacity_declared_environment"] == "nonproduction"


def test_both_isolation_violations_are_reported():
    config = configuration(scope={"nonproduction_capacity_ids": CAP_A})
    prod, nonprod = workspace(PROD, CAP_A), workspace(NONPROD, CAP_A)
    status, evidence = native_rules.capacity_isolation(prod, context(config, [prod, nonprod]))
    assert status == "fail"
    assert evidence["signal_codes"] == ["capacity_declared_nonproduction",
                                        "shares_capacity_with_nonproduction_workspace"]


def test_workspace_inheriting_a_nonproduction_capacity_counts_as_nonproduction():
    config = configuration(scope={"nonproduction_capacity_ids": CAP_B, "production_capacity_ids": CAP_A})
    prod = workspace(PROD, CAP_B)
    inherited = workspace(OTHER, CAP_B)
    assert native_rules.capacity_isolation(prod, context(config, [prod, inherited]))[0] == "fail"


def test_isolated_production_passes():
    prod = workspace(PROD, CAP_A)
    nonprod = workspace(NONPROD, CAP_B)
    peer = workspace(OTHER, CAP_A)
    undeclared = workspace(UNDECLARED, CAP_A)
    status, evidence = native_rules.capacity_isolation(prod, context(workspaces=[prod, nonprod, peer, undeclared]))
    assert status == "pass"
    assert evidence["signal_codes"] == []
    assert "limitations" in evidence


@pytest.mark.parametrize("capacity", [None, ""])
def test_workspace_without_capacity_is_not_applicable_for_isolation(capacity):
    prod = workspace(PROD, capacity)
    status, evidence = native_rules.capacity_isolation(prod, context(workspaces=[prod]))
    assert (status, evidence["reason_code"]) == ("not_applicable", "no_dedicated_capacity")


# --- ARCH-007 ----------------------------------------------------------------------------------

def metrics(*capacities, complete=True, **extra):
    return {"collectionComplete": complete, "capacities": [
        {"id": capacity, "region": region} for capacity, region in capacities], **extra}


@pytest.mark.parametrize("region, allowed, expected", [
    ("West Europe", ["westeurope"], "pass"),
    ("westeurope", ["West Europe"], "pass"),
    ("WEST-EUROPE", ["westeurope", "northeurope"], "pass"),
    ("North Europe", ["westeurope"], "fail"),
    ("West Europe 2", ["westeurope"], "fail"),
])
def test_capacity_region_normalizes_names(region, allowed, expected):
    ws = workspace(PROD, CAP_A)
    ctx = context(**{"capacity_metrics.json": metrics((CAP_A, region))})
    status, evidence = native_rules.capacity_region(ws, {"required": True, "allowed_regions": allowed}, ctx)
    assert status == expected
    assert evidence["capacity_region"] == region


def test_capacity_region_matches_the_capacity_identity_without_case():
    ws = workspace(PROD, CAP_A.upper())
    ctx = context(**{"capacity_metrics.json": metrics((CAP_A, "westeurope"))})
    assert native_rules.capacity_region(ws, {"allowed_regions": ["westeurope"]}, ctx)[0] == "pass"


@pytest.mark.parametrize("ws, raw, code", [
    (workspace(PROD, None), metrics((CAP_A, "westeurope")), "hosting_identity_unavailable"),
    (workspace(PROD, CAP_A), metrics((CAP_B, "westeurope")), "capacity_region_unavailable"),
    (workspace(PROD, CAP_A), metrics((CAP_A, None)), "capacity_region_unavailable"),
    (workspace(PROD, CAP_A), {"collectionComplete": False}, "capacity_region_unavailable"),
    (workspace(PROD, CAP_A), {"capacities": "invalid"}, "capacity_region_unavailable"),
])
def test_capacity_region_gaps_are_missing_evidence(ws, raw, code):
    status, evidence = native_rules.capacity_region(
        ws, {"allowed_regions": ["westeurope"]}, context(**{"capacity_metrics.json": raw}))
    assert (status, evidence["reason_code"]) == ("missing_evidence", code)


def test_capacity_region_without_capacity_metrics_file_is_missing_evidence():
    status, evidence = native_rules.capacity_region(
        workspace(PROD, CAP_A), {"allowed_regions": ["westeurope"]}, context())
    assert (status, evidence["reason_code"]) == ("missing_evidence", "capacity_region_unavailable")


def test_capacity_region_policy_without_allowed_regions_is_unknown():
    status, evidence = native_rules.capacity_region(
        workspace(PROD, CAP_A), {"required": True}, context(**{"capacity_metrics.json": metrics((CAP_A, "westeurope"))}))
    assert (status, evidence["reason_code"]) == ("unknown", "policy_incomplete")


# --- GOV-005 and SEC-012 -----------------------------------------------------------------------

def models(*endorsements):
    return [{"endorsementDetails": {"endorsement": value} if value else None} for value in endorsements]


@pytest.mark.parametrize("rule, function", [("GOV-005", native_rules.endorsement), ("SEC-012", native_rules.label_coverage)])
def test_production_only_signals_skip_other_environments(rule, function):
    for wid, code in ((NONPROD, None), (UNDECLARED, "environment_undeclared")):
        ws = workspace(wid)
        status, evidence = function(ws, effective(rule, ws), context(**{"scanner.json": scanner(wid)}))
        assert status == "not_applicable"
        assert evidence.get("reason_code") == code
        assert evidence["assessment_kind"] == "non_scored_context"


@pytest.mark.parametrize("rule, function", [("GOV-005", native_rules.endorsement), ("SEC-012", native_rules.label_coverage)])
@pytest.mark.parametrize("scan", [
    None, {}, {"collectionComplete": False, "workspaces": []},
    {"collectionComplete": True, "workspaces": "x"},
    {"collectionComplete": True, "workspaces": [{"id": OTHER}]},
])
def test_scanner_gaps_are_missing_evidence(rule, function, scan):
    ws = workspace(PROD)
    raw = {} if scan is None else {"scanner.json": scan}
    status, evidence = function(ws, effective(rule, ws), context(**raw))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "scanner_unavailable")


def test_endorsement_ratio_against_threshold():
    ws = workspace(PROD)
    cases = [(["Promoted", "Certified", None, "None"], 0.5, False),  # exactly at the minimum
             (["Promoted", None, None, None], 0.25, True),
             (["Certified"], 1.0, False)]
    for endorsements, ratio, recommended in cases:
        scan = scanner(PROD, datasets=models(*endorsements))
        status, evidence = native_rules.endorsement(ws, effective("GOV-005", ws), context(**{"scanner.json": scan}))
        assert status == "info"
        assert evidence["model_count"] == len(endorsements)
        assert evidence["endorsement_ratio"] == ratio
        assert evidence["review_below"] == 0.5
        assert evidence["review_recommended"] is recommended


def test_endorsement_without_models_is_not_applicable_and_honours_a_scoped_threshold():
    ws = workspace(PROD)
    ctx = context(**{"scanner.json": scanner(PROD, datasets=[])})
    assert native_rules.endorsement(ws, effective("GOV-005", ws), ctx)[0] == "not_applicable"
    strict = {"thresholds": {"endorsement_min_ratio": 0.9}}
    scan = scanner(PROD, datasets=models("Promoted", None))
    evidence = native_rules.endorsement(ws, strict, context(**{"scanner.json": scan}))[1]
    assert evidence["review_recommended"] is True and evidence["review_below"] == 0.9


def test_label_coverage_counts_every_labelable_collection():
    ws = workspace(PROD)
    scan = scanner(
        PROD,
        datasets=[{"sensitivityLabel": {"labelId": "x"}}, {}],
        reports=[{"sensitivityLabel": {"labelId": "x"}}],
        dashboards=[{"sensitivityLabel": None}],
        dataflows=[{"sensitivityLabel": {"labelId": "y"}}],
        lakehouses=[{}],  # not a labelled collection
    )
    status, evidence = native_rules.label_coverage(ws, effective("SEC-012", ws), context(**{"scanner.json": scan}))
    assert status == "info"
    assert (evidence["item_count"], evidence["labelled_item_count"], evidence["label_ratio"]) == (5, 3, 0.6)
    assert evidence["review_recommended"] is False


def test_label_coverage_below_threshold_and_empty_workspace():
    ws = workspace(PROD)
    scan = scanner(PROD, datasets=[{}, {}, {"sensitivityLabel": {"labelId": "x"}}])
    evidence = native_rules.label_coverage(ws, effective("SEC-012", ws), context(**{"scanner.json": scan}))[1]
    assert evidence["review_recommended"] is True
    empty = context(**{"scanner.json": scanner(PROD)})
    assert native_rules.label_coverage(ws, effective("SEC-012", ws), empty)[0] == "not_applicable"


# --- ARCH-014 ----------------------------------------------------------------------------------

def test_cross_workspace_counts_other_groups_and_ignores_the_home_workspace():
    ws = workspace(PROD)
    scan = scanner(
        PROD,
        datasets=[
            {"upstreamDatasets": [{"groupId": OTHER}, {"groupId": PROD.upper()}]},
            {"upstreamDatasets": [{"groupId": PROD}]},
            {"upstreamDatasets": [{"groupId": ""}, {}, "invalid"]},
        ],
        dataflows=[{"upstreamDataflows": [{"groupId": OTHER.upper()}, {"groupId": NONPROD}]}],
    )
    status, evidence = native_rules.cross_workspace(ws, effective("ARCH-014", ws), context(**{"scanner.json": scan}))
    assert status == "info"
    assert evidence["dependent_item_count"] == 2
    assert evidence["upstream_workspace_count"] == 2
    assert evidence["review_recommended"] is False


def test_cross_workspace_only_same_workspace_links_is_zero():
    ws = workspace(NONPROD)
    scan = scanner(NONPROD, datasets=[{"upstreamDatasets": [{"groupId": NONPROD}]}])
    evidence = native_rules.cross_workspace(ws, effective("ARCH-014", ws), context(**{"scanner.json": scan}))[1]
    assert (evidence["dependent_item_count"], evidence["upstream_workspace_count"]) == (0, 0)


@pytest.mark.parametrize("upstream, recommended", [(5, False), (6, True)])
def test_cross_workspace_threshold_boundary(upstream, recommended):
    ws = workspace(PROD)
    links = [{"groupId": f"{index:08d}-0000-0000-0000-000000000000"} for index in range(1, upstream + 1)]
    scan = scanner(PROD, datasets=[{"upstreamDatasets": links}])
    evidence = native_rules.cross_workspace(ws, effective("ARCH-014", ws), context(**{"scanner.json": scan}))[1]
    assert evidence["upstream_workspace_count"] == upstream
    assert evidence["review_above"] == 5
    assert evidence["review_recommended"] is recommended


def test_cross_workspace_needs_a_complete_scan():
    ws = workspace(PROD)
    status, evidence = native_rules.cross_workspace(
        ws, effective("ARCH-014", ws), context(**{"scanner.json": scanner(PROD, complete=False)}))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "scanner_unavailable")


# --- ARCH-015 ----------------------------------------------------------------------------------

def test_legacy_items_detects_datamarts():
    ws = {"id": PROD, **inventory((NOTEBOOK, "Notebook"), (PIPELINE, "Datamart"),
                                  ("ffffffff-ffff-ffff-ffff-ffffffffffff", "Datamart"))}
    status, evidence = native_rules.legacy_items(ws)
    assert status == "info"
    assert (evidence["legacy_item_count"], evidence["legacy_item_types"], evidence["review_recommended"]) == (
        2, ["Datamart"], True)


def test_legacy_items_none_found_does_not_recommend_review():
    evidence = native_rules.legacy_items({"id": PROD, **inventory((NOTEBOOK, "Notebook"))})[1]
    assert (evidence["legacy_item_count"], evidence["review_recommended"]) == (0, False)
    assert native_rules.legacy_items({"id": PROD, **inventory()})[1]["review_recommended"] is False


@pytest.mark.parametrize("ws", [
    {"id": PROD},
    {"id": PROD, **inventory((NOTEBOOK, "Datamart"), status="unavailable")},
    {"id": PROD, "itemsCollectionStatus": "collected", "items": [{"id": NOTEBOOK}]},
    {"id": PROD, "itemsCollectionStatus": "collected", "items": [{"type": "Datamart"}]},
    {"id": PROD, "itemsCollectionStatus": "collected", "items": ["Datamart"]},
])
def test_legacy_items_incomplete_inventory_is_missing_evidence(ws):
    status, evidence = native_rules.legacy_items(ws)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "item_inventory_incomplete")


# --- ARCH-016 ----------------------------------------------------------------------------------

def test_empty_capacity_reports_the_summary():
    ctx = context(**{"capacity_metrics.json": metrics(
        (CAP_A, "westeurope"), (CAP_B, "westeurope"), summary={"emptyCapacities": [CAP_B]})})
    status, evidence = native_rules.empty_capacity(ctx)
    assert status == "info"
    assert (evidence["capacity_count"], evidence["empty_capacity_count"], evidence["review_recommended"]) == (2, 1, True)


def test_no_empty_capacity_does_not_recommend_review():
    for summary in ({"emptyCapacities": []}, {}, None):
        ctx = context(**{"capacity_metrics.json": metrics((CAP_A, "westeurope"), summary=summary)})
        evidence = native_rules.empty_capacity(ctx)[1]
        assert (evidence["empty_capacity_count"], evidence["review_recommended"]) == (0, False)


def test_empty_capacity_with_a_limited_scope_is_not_applicable():
    ctx = context(**{"capacity_metrics.json": metrics(
        (CAP_A, "westeurope"), workspaceScopeLimited=True, summary={"emptyCapacities": [CAP_A]})})
    status, evidence = native_rules.empty_capacity(ctx)
    assert (status, evidence["reason_code"]) == ("not_applicable", "review_scope_limited")


@pytest.mark.parametrize("raw", [
    {}, {"capacity_metrics.json": {}}, {"capacity_metrics.json": {"capacities": []}},
    {"capacity_metrics.json": {"collectionComplete": False, "capacities": []}},
    {"capacity_metrics.json": {"collectionComplete": True, "capacities": None}},
    {"capacity_metrics.json": {"collectionComplete": True, "capacities": {}}},
])
def test_incomplete_capacity_inventory_is_missing_evidence(raw):
    status, evidence = native_rules.empty_capacity(context(**raw))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "capacity_inventory_unavailable")


def test_limited_scope_does_not_hide_an_incomplete_inventory():
    ctx = context(**{"capacity_metrics.json": {"collectionComplete": False, "capacities": [], "workspaceScopeLimited": True}})
    assert native_rules.empty_capacity(ctx)[0] == "missing_evidence"


# --- ARCH-010, ARCH-011, ARCH-012 and SEC-011 --------------------------------------------------

def notebook_row(notebook=NOTEBOOK, wid=PROD, coverage="complete", **extra):
    return {"id": notebook, "workspaceId": wid, "signalCoverage": coverage, **extra}


def definitions(notebooks=(), pipelines=()):
    return {"pipeline_definitions.json": {"notebooks": list(notebooks), "pipelines": list(pipelines),
                                          "collectionComplete": True}}


def test_notebook_pattern_sums_hits_across_the_workspace_notebooks():
    other = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    ws = {"id": PROD, **inventory((NOTEBOOK, "Notebook"), (other, "Notebook"), (PIPELINE, "DataPipeline"))}
    ctx = context(**definitions([
        notebook_row(NOTEBOOK, signals={"ARCH-011": 2, "ARCH-012": 1}),
        notebook_row(other, signals={"ARCH-011": 3}),
        notebook_row("99999999-9999-9999-9999-999999999999", wid=OTHER, signals={"ARCH-011": 100}),
    ]))
    status, evidence = native_rules.notebook_pattern("ARCH-011", ws, ctx)
    assert status == "info"
    assert (evidence["pattern_hit_count"], evidence["coverage_status"], evidence["heuristic"]) == (5, "complete", True)
    assert native_rules.notebook_pattern("ARCH-012", ws, ctx)[1]["pattern_hit_count"] == 1
    assert native_rules.notebook_pattern("SEC-011", ws, ctx)[1]["pattern_hit_count"] == 0


def test_notebook_pattern_without_notebooks_is_complete_and_zero():
    ws = {"id": PROD, **inventory((PIPELINE, "DataPipeline"))}
    status, evidence = native_rules.notebook_pattern("ARCH-010", ws, context(**definitions()))
    assert (status, evidence["pattern_hit_count"]) == ("info", 0)


@pytest.mark.parametrize("mutation", ["row_error", "partial_coverage", "no_coverage", "missing_row", "unlisted_row",
                                      "inventory_incomplete"])
def test_notebook_pattern_incomplete_evidence_is_never_reported_as_clean(mutation):
    ws = {"id": PROD, **inventory((NOTEBOOK, "Notebook"))}
    row = notebook_row(signals={"ARCH-010": 0})
    rows = [row]
    if mutation == "row_error":
        row["error"] = "denied"
    elif mutation == "partial_coverage":
        row["signalCoverage"] = "partial"
    elif mutation == "no_coverage":
        del row["signalCoverage"]
    elif mutation == "missing_row":
        rows = []
    elif mutation == "unlisted_row":
        rows.append(notebook_row("ffffffff-ffff-ffff-ffff-ffffffffffff", signals={}))
    else:
        ws["itemsCollectionStatus"] = "unavailable"
    status, evidence = native_rules.notebook_pattern("ARCH-010", ws, context(**definitions(rows)))
    assert (status, evidence["coverage_status"]) == ("missing_evidence", "partial")


def test_notebook_pattern_malformed_inventory_raises():
    ctx = context(**{"pipeline_definitions.json": {"notebooks": ["bad"]}})
    with pytest.raises(ValueError, match="notebooks"):
        native_rules.notebook_pattern("ARCH-010", {"id": PROD, **inventory()}, ctx)


# --- ARCH-013 ----------------------------------------------------------------------------------

def activity(kind="Copy", name="a", **extra):
    return {"name": name, "type": kind, **extra}


def pipeline(activities, pid=PIPELINE, wid=PROD, shape="properties", **extra):
    content = {"properties": {"activities": activities}} if shape == "properties" else {"activities": activities}
    return {"id": pid, "workspaceId": wid, "parts": [{"path": "pipeline-content.json", "decoded": content}], **extra}


def resilience(activities, **kwargs):
    ws = {"id": PROD, **inventory((PIPELINE, "DataPipeline"))}
    return native_rules.pipeline_resilience(ws, context(**definitions(pipelines=[pipeline(activities, **kwargs)])))


def test_pipeline_resilience_flags_missing_or_zero_retry_and_the_default_timeout():
    status, evidence = resilience([
        activity(name="zero", policy={"retry": 0, "timeout": "0.01:00:00"}),
        activity("TridentNotebook", "missing", policy={"timeout": "0.01:00:00"}),
        activity("Lookup", "default", policy={"retry": 2, "timeout": "0.12:00:00"}),
        activity("Script", "nopolicy"),
        activity("WebActivity", "unset-timeout", policy={"retry": 1}),
    ])
    assert status == "info"
    assert evidence["executing_activity_count"] == 5
    assert evidence["without_retry_count"] == 3  # zero, missing, nopolicy
    assert evidence["default_timeout_count"] == 3  # default, nopolicy, unset-timeout
    assert evidence["review_recommended"] is True


def test_pipeline_resilience_with_explicit_retry_and_timeout_is_clean():
    status, evidence = resilience([
        activity(policy={"retry": 3, "timeout": "0.02:00:00"}),
        activity("SqlServerStoredProcedure", policy={"retry": 1, "timeout": "1.00:00:00"}),
    ])
    assert status == "info"
    assert (evidence["executing_activity_count"], evidence["without_retry_count"],
            evidence["default_timeout_count"], evidence["review_recommended"]) == (2, 0, 0, False)


def test_pipeline_resilience_skips_dynamic_policy_values():
    status, evidence = resilience([
        activity(policy={"dynamic": True}),
        activity(policy={"retry": 1, "timeout": "0.01:00:00", "dynamic": True}),
    ])
    assert status == "info"
    assert evidence["executing_activity_count"] == 2
    assert (evidence["without_retry_count"], evidence["default_timeout_count"], evidence["review_recommended"]) == (
        0, 0, False)


def test_pipeline_resilience_ignores_activities_that_cannot_fail_independently():
    status, evidence = resilience([
        activity("Wait"), activity("SetVariable"), activity("IfCondition"), activity("Fail"), activity(None),
        {"name": "untyped"},
    ])
    assert status == "info"
    assert (evidence["executing_activity_count"], evidence["review_recommended"]) == (0, False)


def test_pipeline_resilience_descends_into_container_activities_and_ignores_type_case():
    nested = activity("copy", "inner")
    status, evidence = resilience([
        activity("ForEach", "loop", typeProperties={"activities": [nested]}),
        activity("IfCondition", "branch", typeProperties={
            "ifTrueActivities": [activity("DELETE", "gone", policy={"retry": 1, "timeout": "0.01:00:00"})],
            "ifFalseActivities": [],
        }),
        activity("Switch", "cases", typeProperties={"cases": [{"value": "x", "activities": [activity("GetMetadata")]}],
                                                    "defaultActivities": []}),
    ])
    assert status == "info"
    assert evidence["executing_activity_count"] == 3
    assert evidence["without_retry_count"] == 2


def test_pipeline_resilience_reads_top_level_activities_shape():
    status, evidence = resilience([activity()], shape="top")
    assert (status, evidence["executing_activity_count"]) == ("info", 1)


def test_pipeline_resilience_with_a_complete_inventory_and_no_pipelines_is_not_applicable():
    ws = {"id": PROD, **inventory((NOTEBOOK, "Notebook"))}
    status, evidence = native_rules.pipeline_resilience(ws, context(**definitions()))
    assert status == "not_applicable"
    assert evidence["assessment_kind"] == "non_scored_context"


@pytest.mark.parametrize("mutation", [
    "inventory_incomplete", "definition_error", "no_content", "activities_not_a_list", "pipeline_missing",
    "pipeline_unlisted",
])
def test_pipeline_resilience_incomplete_definitions_are_missing_evidence(mutation):
    ws = {"id": PROD, **inventory((PIPELINE, "DataPipeline"))}
    row = pipeline([activity(policy={"retry": 1, "timeout": "0.01:00:00"})])
    rows = [row]
    if mutation == "inventory_incomplete":
        ws["itemsCollectionStatus"] = "partial"
    elif mutation == "definition_error":
        row["error"] = "denied"
    elif mutation == "no_content":
        row["parts"] = []
    elif mutation == "activities_not_a_list":
        row["parts"][0]["decoded"] = {"properties": {"activities": "dynamic"}}
    elif mutation == "pipeline_missing":
        rows = []
    else:
        rows.append(pipeline([], pid="ffffffff-ffff-ffff-ffff-ffffffffffff"))
    status, evidence = native_rules.pipeline_resilience(ws, context(**definitions(pipelines=rows)))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "pipeline_definitions_incomplete")


def test_pipeline_resilience_ignores_pipelines_of_other_workspaces():
    ws = {"id": PROD, **inventory((PIPELINE, "DataPipeline"))}
    foreign = pipeline([activity()], pid="ffffffff-ffff-ffff-ffff-ffffffffffff", wid=OTHER)
    own = pipeline([activity(policy={"retry": 1, "timeout": "0.01:00:00"})])
    status, evidence = native_rules.pipeline_resilience(ws, context(**definitions(pipelines=[own, foreign])))
    assert (status, evidence["executing_activity_count"]) == ("info", 1)


# --- REL-002 -----------------------------------------------------------------------------------

def failure_paths(activities, **kwargs):
    ws = {"id": PROD, **inventory((PIPELINE, "DataPipeline"))}
    return native_rules.pipeline_failure_paths(ws, context(**definitions(pipelines=[pipeline(activities, **kwargs)])))


def after(name, *conditions):
    return {"dependsOn": [{"activity": name, "dependencyConditions": list(conditions)}]}


def test_a_multi_step_pipeline_without_a_failure_branch_recommends_review():
    status, evidence = failure_paths([activity(name="load"), activity("TridentNotebook", "transform", **after("load", "Succeeded"))])
    assert status == "info"
    assert (evidence["multi_step_pipeline_count"], evidence["without_failure_path_count"], evidence["review_recommended"]) == (1, 1, True)


@pytest.mark.parametrize("condition", ["Failed", "Completed", "failed"])
def test_an_activity_that_runs_on_failure_or_completion_is_a_failure_path(condition):
    status, evidence = failure_paths([
        activity(name="load"), activity("TridentNotebook", "transform", **after("load", "Succeeded")),
        activity("WebActivity", "notify", **after("transform", condition)),
    ])
    assert (status, evidence["without_failure_path_count"], evidence["review_recommended"]) == ("info", 0, False)


def test_a_failure_path_inside_a_container_counts_and_a_single_step_pipeline_is_not_judged():
    nested = [activity(name="inner"), activity("Script", "alert", **after("inner", "Failed"))]
    flow = [activity("ForEach", "loop", typeProperties={"activities": nested}), activity(name="next", **after("loop", "Succeeded"))]
    assert failure_paths(flow)[1]["without_failure_path_count"] == 0
    evidence = failure_paths([activity(name="only")])[1]
    assert (evidence["multi_step_pipeline_count"], evidence["review_recommended"]) == (0, False)


def test_failure_paths_are_missing_evidence_when_a_definition_is_unreadable_and_not_applicable_without_pipelines():
    status, evidence = failure_paths([activity()], error="denied")
    assert (status, evidence["reason_code"]) == ("missing_evidence", "pipeline_definitions_incomplete")
    ws = {"id": PROD, **inventory((NOTEBOOK, "Notebook"))}
    assert native_rules.pipeline_failure_paths(ws, context(**definitions()))[0] == "not_applicable"


# --- the analyzer end to end -------------------------------------------------------------------

def stamp(snapshot):
    return {"source": "native", "collector": "collectors.workspace_inventory",
            "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
            "configuration_sha256": snapshot["sha256"],
            "configuration_captured_at_utc": snapshot["captured_at_utc"],
            "captured_at_utc": snapshot["captured_at_utc"],
            "workspace_scope": os.environ.get("WORKSPACE_IDS", ""), "capture_status": "collected"}


@pytest.fixture
def isolated(monkeypatch):
    for key in ("FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG", "WORKSPACE_IDS"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)


def run_analyzer(tmp_path, workspaces, **raw):
    config = configuration()
    snapshot = read_snapshot(capture(tmp_path, config))
    activate(tmp_path / "review_config.json")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    payloads = {"workspace_inventory.json": {"workspaceListComplete": True, "workspaces": workspaces}, **raw}
    for name, payload in payloads.items():
        (raw_dir / name).write_text(json.dumps({**payload, "_provenance": stamp(snapshot)}), encoding="utf-8")
    return json.loads(analyze(raw_dir, tmp_path, ROOT / "config" / "review-checklist.yaml").read_text())


def collected(wid, capacity, name):
    return {"id": wid, "name": name, "type": "Workspace", "capacityId": capacity, "state": "Active",
            "items": [], "itemsCollectionStatus": "collected", "users": [], "usersCollectionStatus": "collected"}


def test_analyzer_emits_every_rule_with_the_right_cardinality(isolated, tmp_path):
    workspaces = [collected(PROD, CAP_A, "first"), collected(NONPROD, CAP_A, "second"), collected(OTHER, CAP_B, "third")]
    rows = run_analyzer(tmp_path, workspaces)
    assert len(ACTIVE_RULE_IDS) == 70
    assert {row["rule_id"] for row in rows} == ACTIVE_RULE_IDS
    per_rule = {}
    for row in rows:
        per_rule.setdefault(row["rule_id"], []).append(row)
    for rule_id, findings in per_rule.items():
        if rule_id in TENANT_RULES:
            assert len(findings) == 1 and findings[0]["evidence"]["workspace_id"] is None, rule_id
        elif rule_id in CAPACITY_RULES:
            assert sorted(row["evidence"]["capacity_id"] for row in findings) == [CAP_A, CAP_B], rule_id
        else:
            assert sorted(row["evidence"]["workspace_id"] for row in findings) == sorted(
                ws["id"] for ws in workspaces), rule_id
    assert [row["rule_id"] for row in rows] == sorted((row["rule_id"] for row in rows), key=RULE_ORDER.index)


def test_capacity_rules_give_one_finding_per_capacity_that_every_workspace_on_it_points_to(isolated, tmp_path):
    workspaces = [collected(PROD, CAP_A, "first"), collected(NONPROD, CAP_A, "second"), collected(OTHER, CAP_B, "third")]
    metrics = {"collectionComplete": True, "capacities": [
        {"id": CAP_A, "displayName": "alpha", "sku": "F8", "state": "Suspended", "admins": ["x"]},
        {"id": CAP_B, "displayName": "beta", "sku": "F8", "state": "Active", "admins": ["x", "y"]}]}
    rows = run_analyzer(tmp_path, workspaces, **{"capacity_metrics.json": metrics})
    state = {row["evidence"]["capacity_id"]: row["evidence"] for row in rows if row["rule_id"] == "OPS-008"}
    alpha = state[CAP_A]
    assert (alpha["scope"], alpha["capacity_name"], alpha["workspace_id"], alpha["workspace_name"],
            alpha["workspace_count"], alpha["review_recommended"]) == ("capacity", "alpha", None, None, 2, True)
    assert {entry["workspace_id"] for entry in alpha["affected_workspaces"]} == {PROD, NONPROD}
    assert state[CAP_B]["review_recommended"] is False
    assert {entry["workspace_id"] for entry in state[CAP_B]["affected_workspaces"]} == {OTHER}
    admins = {row["evidence"]["capacity_id"]: row["evidence"]["review_recommended"] for row in rows if row["rule_id"] == "OPS-009"}
    assert admins == {CAP_A: True, CAP_B: False}


def test_a_capacity_rule_with_no_capacity_hosted_workspace_still_reports_once(isolated, tmp_path):
    rows = run_analyzer(tmp_path, [collected(PROD, None, "first")])
    findings = [row for row in rows if row["rule_id"] == "OPS-008"]
    assert [(row["status"], row["evidence"]["reason_code"]) for row in findings] == [("not_applicable", "no_dedicated_capacity")]


def test_analyzer_fails_production_sharing_a_capacity_with_declared_nonproduction(isolated, tmp_path):
    workspaces = [collected(PROD, CAP_A, "first"), collected(NONPROD, CAP_A, "second"), collected(OTHER, CAP_B, "third")]
    rows = run_analyzer(tmp_path, workspaces)
    isolation = {row["evidence"]["workspace_id"]: row for row in rows if row["rule_id"] == "ARCH-006"}
    prod = isolation[PROD]
    assert (prod["status"], prod["scored"]) == ("fail", True)
    assert prod["evidence"]["signal_codes"] == ["shares_capacity_with_nonproduction_workspace"]
    assert prod["evidence"]["declared_environment"] == "production"
    assert "production workloads only" in prod["recommendation"]
    assert isolation[NONPROD]["status"] == "not_applicable"
    assert (isolation[OTHER]["status"], isolation[OTHER]["evidence"]["reason_code"]) == (
        "unknown", "environment_undeclared")


def test_analyzer_passes_isolated_production(isolated, tmp_path):
    workspaces = [collected(PROD, CAP_A, "first"), collected(NONPROD, CAP_B, "second")]
    rows = run_analyzer(tmp_path, workspaces)
    prod = next(row for row in rows if row["rule_id"] == "ARCH-006" and row["evidence"]["workspace_id"] == PROD)
    assert prod["status"] == "pass"


def test_analyzer_orphaned_workspace_fails_gov_003(isolated, tmp_path):
    orphan = {**collected(OTHER, CAP_B, "third"), "state": "Orphaned"}
    rows = run_analyzer(tmp_path, [collected(PROD, CAP_A, "first"), orphan])
    status = {row["evidence"]["workspace_id"]: row["status"] for row in rows if row["rule_id"] == "GOV-003"}
    assert status == {PROD: "pass", OTHER: "fail"}

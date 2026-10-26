# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import copy
import json
import os

import pytest
import yaml

from analyzers.far_profile import (
    ACTIVE_RULE_IDS, CAPACITY_RULES, ENABLED_IS_SECURE, ENRICHMENT_SOURCES, NON_SCORED_KINDS, PROFILE_ID, PROFILE_SHA256, RULE_KINDS, RULE_ORDER, TENANT_RULES,
    TENANT_SETTING_NAMES,
    normalize_findings,
)
from analyzers.far_review import _assess, _assess_tenant, _read, _structural, analyze
from analyzers.merge_findings import merge
from analyzers.rule_support import Context
from orchestration.review_config import ROOT, activate, capture, local_configuration, read_snapshot, validate_configuration
from orchestration.rule_policy import resolve_rule, validate_override

WID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
PID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
NID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


@pytest.fixture
def config(monkeypatch):
    for key in ("FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG",
                "ARCH_MONOLITH_THRESHOLD", "SEC_BROAD_ACCESS_THRESHOLD"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)
    result = local_configuration()
    result["profiles"] = {"workspaces": [{"id": WID}], "capacities": []}
    return result


def workspace(items=None, users=None, **extra):
    return {"id": WID, "name": "prod-bronze-but-undeclared", "type": "Workspace", "capacityId": CID,
            "items": items or [], "itemsCollectionStatus": "collected",
            "users": users or [], "usersCollectionStatus": "collected", **extra}


def definitions(activities, notebooks=None):
    return {"pipelines": [{"id": PID, "workspaceId": WID, "parts": [
        {"path": "pipeline-content.json", "decoded": {"activities": activities}},
    ]}], "notebooks": notebooks or [], "collectionComplete": True}


def effective(rule_id, config, override=None, environment=None):
    config = copy.deepcopy(config)
    if override:
        config["profiles"]["rule_profiles"] = {"declared": {"rule_overrides": {rule_id: override}}}
        config["profiles"]["default_rule_profile"] = "declared"
    if environment:
        config["profiles"]["workspaces"][0]["profile"] = {"environment": environment}
    validate_configuration(config)
    return resolve_rule(config, rule_id, workspace())


def raw():
    return {name: {} for name in (
        "pipeline_definitions.json", "semantic_model_definitions.json", "git_integration.json", "gateways.json",
        "tenant_settings.json", "scanner.json", "capacity_metrics.json",
    )}


def ctx(source=None, config=None, workspaces=()):
    snapshot = {"configuration": config or {"profiles": {}}}
    return Context(snapshot, list(workspaces), raw() if source is None else source)


def provenance(snapshot):
    return {"source": "native", "collector": "collectors.workspace_inventory",
            "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
            "configuration_sha256": snapshot["sha256"],
            "configuration_captured_at_utc": snapshot["captured_at_utc"],
            "captured_at_utc": snapshot["captured_at_utc"],
            "workspace_scope": os.environ.get("WORKSPACE_IDS", ""), "capture_status": "collected"}


def test_catalogue_registry_and_runtime_agree():
    catalogue = yaml.safe_load((ROOT / "config" / "review-checklist.yaml").read_text(encoding="utf-8"))
    assert len(ACTIVE_RULE_IDS) == len(catalogue["rules"]) == 70
    assert [row["id"] for row in catalogue["rules"]] == list(RULE_ORDER)
    assert TENANT_RULES <= ACTIVE_RULE_IDS
    assert {"ARCH-016", "GOV-006", "GOV-009", "SEC-007", "SEC-008", "SEC-013"} <= TENANT_RULES
    for row in catalogue["rules"]:
        assert row["kind"] == RULE_KINDS[row["id"]]
        assert row["enabled"] is True
        assert row["dimension"] in {"architecture", "governance", "operations", "reliability", "security"}
        assert (row["severity"] == "info") == (row["kind"] != "control")
        assert set(row) == {"id", "enabled", "kind", "dimension", "severity", "description", "microsoft_learn_url"}
    registry = yaml.safe_load((ROOT / "config" / "analyzer-registry.yaml").read_text(encoding="utf-8"))
    assert [row["module"] for row in registry["analyzers"]] == ["analyzers.far_review"]
    assert set(registry["analyzers"][0]["rule_ids"]) == ACTIVE_RULE_IDS


@pytest.mark.parametrize("status", ["info", "not_applicable", "unknown", "missing_evidence"])
def test_signals_keep_their_non_outcome_statuses_and_only_pass_or_fail_collapse_to_info(status):
    def normalized(rule_id, value):
        return normalize_findings([{"rule_id": rule_id, "status": value, "evidence": {},
                                    "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256}])[0]["status"]
    assert normalized("GOV-007", status) == status
    assert normalized("GOV-007", "pass") == normalized("GOV-007", "fail") == "info"


@pytest.mark.parametrize("rule_id", sorted(ACTIVE_RULE_IDS))
def test_normalizer_applies_the_rule_kind(rule_id):
    row = {"rule_id": rule_id, "status": "fail", "evidence": {},
           "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256}
    result = normalize_findings([row])[0]
    assert result["rule_kind"] == RULE_KINDS[rule_id]
    assert result["scored"] == (RULE_KINDS[rule_id] == "control")
    if RULE_KINDS[rule_id] in NON_SCORED_KINDS:
        assert result["status"] != "fail"


def test_rule_ids_are_contiguous_per_dimension_and_ordered_by_kind():
    # Rules added after the first release keep every earlier identifier stable, so they follow the originals.
    appended = {"SEC-024", "SEC-025", "SEC-026", "SEC-027"}
    by_prefix = {}
    for rule in RULE_ORDER:
        prefix, number = rule.split("-")
        by_prefix.setdefault(prefix, []).append(int(number))
    assert list(by_prefix) == ["ARCH", "GOV", "OPS", "REL", "SEC"]
    for numbers in by_prefix.values():
        assert numbers == list(range(1, len(numbers) + 1))
    rank = {"control": 0, "signal": 1, "coverage": 2}
    for prefix in by_prefix:
        kinds = [rank[RULE_KINDS[rule]] for rule in RULE_ORDER if rule.startswith(prefix) and rule not in appended]
        assert kinds == sorted(kinds)
    assert all(rule.startswith("SEC-") and int(rule[4:]) > 23 for rule in appended)


def test_unknown_rules_cannot_be_overridden():
    with pytest.raises(ValueError, match="Unknown architectural rule"):
        validate_override("PERF-001", {"reason": "test", "applicability": "applicable"})


def test_unknown_rule_ids_fail_explicitly():
    with pytest.raises(ValueError, match="Unknown FAR rule ID"):
        normalize_findings([{"rule_id": "NOPE-001", "status": "pass"}])


def test_earlier_profile_rows_are_not_relabelled_as_current_failures():
    result = normalize_findings([{"rule_id": "ARCH-002", "status": "fail", "title": "PPU is invalid",
                                  "recommendation": "Migrate everything", "evidence": {"legacy": True}}])
    assert result[0]["status"] == "unknown"
    assert result[0]["scored"] is False
    assert "PPU" not in json.dumps(result)
    assert result[0]["evidence"] == {"reason_code": "profile_not_comparable"}


@pytest.mark.parametrize("rule_id", ["ARCH-001", "ARCH-003", "ARCH-007", "SEC-001", "SEC-002"])
def test_declared_controls_are_unassessed_until_a_requirement_is_declared(config, rule_id):
    status, evidence = _assess(rule_id, workspace(), effective(rule_id, config), ctx())
    assert status == "unknown"
    assert evidence["reason_code"] == "policy_undeclared"


@pytest.mark.parametrize("rule_id", ["ARCH-001", "ARCH-007"])
def test_required_policy_without_the_rule_fields_is_incomplete(config, rule_id):
    override = {"reason": "Boundary required", "policy": {"required": True}}
    status, evidence = _assess(rule_id, workspace(), effective(rule_id, config, override), ctx())
    assert (status, evidence["reason_code"]) == ("unknown", "policy_incomplete")


@pytest.mark.parametrize("rule_id", ["OPS-001", "ARCH-006"])
def test_production_is_not_inferred_from_names(config, rule_id):
    status, evidence = _assess(rule_id, workspace(), effective(rule_id, config), ctx())
    assert status == "unknown"
    assert evidence["reason_code"] == "environment_undeclared"
    status, _ = _assess(rule_id, workspace(), effective(rule_id, config, environment="nonproduction"), ctx())
    assert status == "not_applicable"


@pytest.mark.parametrize("entry, expected", [
    ({"workspaceId": WID, "connected": True, "gitProviderDetails": {"gitProviderType": "GitHub"}}, "pass"),
    ({"workspaceId": WID, "connected": False, "gitConnectionState": "NotConnected"}, "fail"),
    ({"workspaceId": WID, "connected": False, "error": "insufficient_permissions (403)"}, "missing_evidence"),
    ({"workspaceId": OTHER, "connected": True}, "missing_evidence"),
])
def test_source_control_is_evaluated_for_declared_production(config, entry, expected):
    source = raw()
    source["git_integration.json"] = {"workspaces": [entry]}
    resolved = effective("OPS-001", config, environment="production")
    assert _assess("OPS-001", workspace(), resolved, ctx(source))[0] == expected
    assert _assess("OPS-001", workspace(), resolved, ctx())[0] == "missing_evidence"


def test_hosting_default_requires_shared_ownership_for_production_only(config):
    personal = workspace(type="PersonalGroup")
    production = effective("ARCH-002", config, environment="production")
    assert _assess("ARCH-002", personal, production, ctx())[0] == "fail"
    assert _assess("ARCH-002", workspace(), production, ctx())[0] == "pass"
    assert _assess("ARCH-002", personal, effective("ARCH-002", config), ctx())[0] == "unknown"
    assert _assess("ARCH-002", personal, effective("ARCH-002", config, environment="nonproduction"), ctx())[0] == "not_applicable"


def test_declared_policy_replaces_the_default_requirement(config):
    override = {"reason": "Approved capacity", "policy": {"required": True, "allowed_capacity_ids": [CID]}}
    resolved = effective("ARCH-002", config, override)
    assert "production_only" not in resolved["policy"]
    assert _assess("ARCH-002", workspace(type="PersonalGroup"), resolved, ctx())[0] == "pass"


def test_default_profile_applies_everywhere_and_environment_follows_workspace_then_capacity(config):
    config["profiles"].update(rule_profiles={"batch": {"rule_overrides": {
        "GOV-001": {"reason": "Profile requirement", "thresholds": {"min_admins": 3}},
    }}}, default_rule_profile="batch")
    config["profiles"]["capacities"] = [{"id": CID, "inherit_rules": True, "profile": {"environment": "production"}}]
    validate_configuration(config)
    resolved = resolve_rule(config, "GOV-001", workspace())
    assert resolved["thresholds"]["min_admins"] == 3
    assert resolved["environment"] == "production"
    config["profiles"]["workspaces"][0]["profile"] = {"environment": "nonproduction"}
    assert resolve_rule(config, "GOV-001", workspace())["environment"] == "nonproduction"
    config["profiles"]["workspaces"][0].pop("profile")
    config["profiles"]["capacities"][0]["inherit_rules"] = False
    resolved = resolve_rule(config, "GOV-001", workspace())
    assert resolved["thresholds"]["min_admins"] == 3
    assert resolved["environment"] == "unknown"


@pytest.mark.parametrize("rule_id, override", [
    ("GOV-001", {"reason": "test", "policy": {"wrong_field": True}}),
    ("GOV-001", {"reason": "test", "thresholds": {"cu_avg_warn_pct": 99}}),
    ("GOV-001", {"reason": "test", "policy": {"required": "true"}}),
    ("GOV-001", {"reason": "test", "assessment": {"status": "pass"}}),
    ("GOV-001", {"policy": {"required": True}}),
    ("ARCH-004", {"reason": "test", "policy": {"required": True}}),
    ("SEC-002", {"reason": "test", "policy": {"allowed_roles": ["Owner"]}}),
    ("ARCH-006", {"reason": "test", "policy": {"allowed_regions": ["westeurope"]}}),
    ("ARCH-007", {"reason": "test", "policy": {"allowed_regions": "westeurope"}}),
    ("GOV-009", {"reason": "test", "policy": {"required_tag_names": []}}),
])
def test_unsupported_or_unsafe_policy_fails_explicitly(rule_id, override):
    with pytest.raises(ValueError):
        validate_override(rule_id, override)


def test_parameter_defaults_are_optional_and_unrecognised_parameters_fail():
    ws = workspace([{"id": PID, "type": "DataPipeline"}, {"id": NID, "type": "Notebook"}])
    nb = {"id": NID, "workspaceId": WID, "parameterNames": ["optional_default"], "parameterCoverage": "complete"}
    activity = {"name": "Invoke", "type": "TridentNotebook", "typeProperties": {"notebookId": NID, "parameters": {}}}
    assert _structural("ARCH-004", ws, definitions([activity], [nb]))[0] == "pass"
    activity["typeProperties"]["parameters"] = {"wrong_name": None}
    assert _structural("ARCH-004", ws, definitions([activity], [nb]))[0] == "fail"


def test_reserved_runtime_parameter_is_not_an_undeclared_parameter():
    ws = workspace([{"id": PID, "type": "DataPipeline"}, {"id": NID, "type": "Notebook"}])
    nb = {"id": NID, "workspaceId": WID, "parameterNames": [], "parameterCoverage": "complete"}
    activity = {"name": "Invoke", "type": "TridentNotebook",
                "typeProperties": {"notebookId": NID, "parameters": {"_inlineInstallationEnabled": None}}}
    assert _structural("ARCH-004", ws, definitions([activity], [nb]))[0] == "pass"


def test_parameter_contract_resolves_workspace_and_notebook_identity():
    ws = workspace([{"id": PID, "type": "DataPipeline"}])
    nb = {"id": NID, "workspaceId": OTHER, "parameterNames": [], "parameterCoverage": "complete"}
    act = {"name": "Invoke", "type": "TridentNotebook", "typeProperties": {"notebookId": NID, "parameters": {}}}
    assert _structural("ARCH-004", ws, definitions([act], [nb]))[0] == "unknown"
    act["typeProperties"]["workspaceId"] = OTHER
    assert _structural("ARCH-004", ws, definitions([act], [nb]))[0] == "pass"


@pytest.mark.parametrize("activities, expected", [
    ([{"name": "A"}, {"name": "B", "dependsOn": [{"activity": "A"}]}], "pass"),
    ([{"name": "A", "dependsOn": [{"activity": "missing"}]}], "fail"),
    ([{"name": "A", "dependsOn": [{"activity": "A"}]}], "fail"),
    ([{"name": "A"}, {"name": "A"}], "fail"),
    ([{"name": "A", "dependsOn": [{"activity": "@unresolved"}]}], "unknown"),
    ([{"name": "Loop", "type": "ForEach", "typeProperties": {}}], "unknown"),
])
def test_static_dependency_contract(activities, expected):
    assert _structural("ARCH-005", workspace([{"id": PID, "type": "DataPipeline"}]),
                       definitions(activities))[0] == expected


def test_partial_inventory_never_passes_structural_controls():
    ws = workspace([{"id": PID, "type": "DataPipeline"}])
    ws["itemsCollectionStatus"] = "unavailable"
    assert _structural("ARCH-005", ws, definitions([{"name": "A"}]))[0] == "unknown"


def admin(identity, kind="User"):
    return {"graphId": identity, "principalType": kind, "groupUserAccessRight": "Admin"}


def test_administrative_continuity_counts_independent_user_administrators_by_default(config):
    resolved = effective("GOV-001", config)
    assert _assess("GOV-001", workspace(users=[admin(OTHER), admin(CID)]), resolved, ctx())[0] == "pass"
    assert _assess("GOV-001", workspace(users=[admin(OTHER)]), resolved, ctx())[0] == "fail"


def test_group_membership_is_not_assumed_to_prove_admin_continuity(config):
    status, evidence = _assess("GOV-001", workspace(users=[admin(OTHER, "Group")]), effective("GOV-001", config), ctx())
    assert status == "unknown"
    assert evidence["reason_code"] == "administrator_group_membership_unverified"


def test_eligible_administrators_restrict_who_counts(config):
    override = {"reason": "Named maintainers", "policy": {"required": True, "eligible_admin_ids": [OTHER]}}
    users = [admin(OTHER), admin(CID)]
    assert _assess("GOV-001", workspace(users=users), effective("GOV-001", config, override), ctx())[0] == "fail"


@pytest.mark.parametrize("rule_id, field", [
    ("SEC-001", "allowed_principal_ids"), ("GOV-001", "eligible_admin_ids"),
])
def test_upn_does_not_substitute_for_object_identity(config, rule_id, field):
    override = {"reason": "Immutable identity boundary", "policy": {"required": True, field: [OTHER]}}
    ws = workspace(users=[{"identifier": "someone@example.com", "principalType": "User", "groupUserAccessRight": "Admin"}])
    assert _assess(rule_id, ws, effective(rule_id, config, override), ctx())[0] == "unknown"


@pytest.mark.parametrize("rule_id, overrides, ws", [
    ("ARCH-009", {"description_coverage_min_ratio": 1.0}, workspace()),
    ("GOV-004", {"naming_pattern": r"engineering-.*", "naming_coverage_min_ratio": 1.0}, workspace()),
])
def test_signal_thresholds_are_used_without_failures(config, rule_id, overrides, ws):
    override = {"reason": "Declared review signal", "thresholds": overrides}
    status, evidence = _assess(rule_id, ws, effective(rule_id, config, override), ctx())
    assert status == "info"
    assert evidence["review_recommended"] is True


@pytest.mark.parametrize("gateways, expected", [
    ([{"members": [{"version": "3000.9"}, {"version": "3000.11"}]}], 1),
    ([{"members": [{"version": "3000.11"}]}], 0),
])
def test_gateway_signal_compares_numeric_versions_and_single_member_clusters(config, gateways, expected):
    override = {"reason": "Approved supportability floor", "thresholds": {"gateway_min_version": "3000.10"}}
    source = raw()
    source["gateways.json"] = {"gateways": gateways}
    status, evidence = _assess_tenant("SEC-010", effective("SEC-010", config, override), ctx(source))
    assert status == "info"
    assert evidence["below_floor_count"] == expected
    assert evidence["single_member_cluster_count"] == (1 if len(gateways[0]["members"]) == 1 else 0)
    assert evidence["review_recommended"] is True


@pytest.mark.parametrize("rule_id, threshold_key, entities, ws_name, ratio_key, ratio, stricter", [
    ("ARCH-009", "description_coverage_min_ratio",
     [{"id": NID, "type": "Notebook", "description": "Approved purpose"}], "workspace", "documentation_ratio", 0.5, 0.6),
    ("GOV-004", "naming_coverage_min_ratio",
     [{"id": NID, "type": "Notebook", "displayName": "engineering-notebook"}], "engineering-workspace", "naming_ratio", 1.0, None),
])
def test_signal_ratios_use_entity_coverage_and_exact_boundary(
        config, rule_id, threshold_key, entities, ws_name, ratio_key, ratio, stricter):
    thresholds = {threshold_key: ratio}
    if rule_id == "GOV-004":
        thresholds["naming_pattern"] = "engineering-.*"
    ws = workspace(entities, name=ws_name)
    override = {"reason": "Approved signal boundary", "thresholds": thresholds}
    status, evidence = _assess(rule_id, ws, effective(rule_id, config, override), ctx())
    assert status == "info"
    assert evidence[ratio_key] == ratio
    assert evidence["review_recommended"] is False
    if stricter is not None:
        thresholds[threshold_key] = stricter
        _, evidence = _assess(rule_id, ws, effective(rule_id, config, override), ctx())
        assert evidence["review_recommended"] is True


@pytest.mark.parametrize("gateways", [None, [{"members": []}], [{"members": [{"version": None}]}]])
def test_missing_gateway_versions_are_not_claimed_as_supported(config, gateways):
    source = raw()
    source["gateways.json"] = {"gateways": gateways}
    status, evidence = _assess_tenant("SEC-010", effective("SEC-010", config), ctx(source))
    assert status == "missing_evidence"
    assert evidence["reason_code"] == "gateway_version_metadata_unavailable"


@pytest.mark.parametrize("rule_id, thresholds", [
    ("ARCH-009", {"description_coverage_min_ratio": 1.5}),
    ("GOV-004", {"naming_pattern": "["}),
    ("SEC-010", {"gateway_min_version": "not-a-version"}),
    ("GOV-004", {"endorsement_min_ratio": 0.3}),
])
def test_invalid_thresholds_are_rejected(rule_id, thresholds):
    with pytest.raises(ValueError):
        validate_override(rule_id, {"reason": "test", "thresholds": thresholds})


def test_inventory_coverage_reports_gaps_without_claiming_a_pass(config):
    resolved = effective("ARCH-020", config)
    assert _assess("ARCH-020", workspace(), resolved, ctx())[0] == "info"
    assert _assess("ARCH-020", workspace(usersCollectionStatus="unavailable"), resolved, ctx())[0] == "missing_evidence"


@pytest.mark.parametrize("rule_id, policy, ws, payload, expected", [
    ("ARCH-001", {"allowed_item_types": ["Lakehouse"]}, workspace([{"id": NID, "type": "Lakehouse"}]), {}, "pass"),
    ("ARCH-001", {"allowed_item_types": ["Lakehouse"]}, workspace([{"id": NID, "type": "Notebook"}]), {}, "fail"),
    ("ARCH-002", {"allowed_capacity_ids": [CID]}, workspace(), {}, "pass"),
    ("ARCH-002", {"allowed_capacity_ids": [OTHER]}, workspace(), {}, "fail"),
    ("GOV-001", {"eligible_admin_ids": [OTHER, CID]}, workspace(users=[admin(OTHER), admin(CID)]), {}, "pass"),
    ("GOV-001", {"eligible_admin_ids": [OTHER]}, workspace(users=[admin(OTHER)]), {}, "fail"),
    ("SEC-001", {"allowed_principal_ids": [OTHER]}, workspace(users=[{"graphId": OTHER}]), {}, "pass"),
    ("SEC-001", {"allowed_principal_ids": [OTHER]}, workspace(users=[{"graphId": CID}]), {}, "fail"),
    ("SEC-002", {"allowed_roles": ["Admin"]}, workspace(users=[{"groupUserAccessRight": "Admin"}]), {}, "pass"),
    ("SEC-002", {"allowed_roles": ["Viewer"]}, workspace(users=[{"groupUserAccessRight": "Admin"}]), {}, "fail"),
    ("ARCH-003", {"allowed_storage_modes": ["Import"]}, workspace([{"id": NID, "type": "SemanticModel"}]),
        {"models": [{"id": NID, "workspaceId": WID, "storageModes": ["Import"]}]}, "pass"),
    ("ARCH-003", {"allowed_storage_modes": ["DirectLake"]}, workspace([{"id": NID, "type": "SemanticModel"}]),
        {"models": [{"id": NID, "workspaceId": WID, "storageModes": ["Import"]}]}, "fail"),
])
def test_native_requirement_positive_negative_and_missing_cases(config, rule_id, policy, ws, payload, expected):
    override = {"reason": "Declared design boundary", "policy": {"required": True, **policy}}
    source = raw()
    source["semantic_model_definitions.json"] = payload
    resolved = effective(rule_id, config, override)
    assert _assess(rule_id, ws, resolved, ctx(source))[0] == expected
    ws = copy.deepcopy(ws)
    ws.update(itemsCollectionStatus="unavailable", usersCollectionStatus="unavailable", capacityId=None, type=None)
    source["semantic_model_definitions.json"] = {}
    assert _assess(rule_id, ws, resolved, ctx(source))[0] == "missing_evidence"


def test_complete_empty_scope_and_manifest_do_not_include_stale_optimizers(config, tmp_path):
    capture(tmp_path, config)
    activate(tmp_path / "review_config.json")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "workspace_inventory.json").write_text(json.dumps(
        {"workspaces": [], "workspaceListComplete": True,
         "_provenance": provenance(read_snapshot(tmp_path / "review_config.json"))}), encoding="utf-8")
    target = analyze(raw_dir, tmp_path, ROOT / "config" / "review-checklist.yaml")
    rows = json.loads(target.read_text())
    assert len(rows) == len(ACTIVE_RULE_IDS)
    assert not any(row["scored"] for row in rows)
    (tmp_path / "findings_best_practices.json").write_text('[{"rule_id":"BPA-001","status":"fail"}]')
    merge(tmp_path)
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    assert manifest["profile_id"] == PROFILE_ID
    assert manifest["configuration_sha256"] == read_snapshot(tmp_path / "review_config.json")["sha256"]
    assert manifest["excluded_unregistered_artifacts"] == ["findings_best_practices.json"]
    assert {row["rule_id"] for row in json.loads((tmp_path / "findings.json").read_text())} == ACTIVE_RULE_IDS


def test_old_snapshot_profile_is_rejected(config, tmp_path):
    path = capture(tmp_path, config)
    value = json.loads(path.read_text())
    value["profile_id"] = "legacy-far"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_snapshot(path)


@pytest.mark.parametrize("field,value", [
    ("profile_id", "legacy-far"), ("profile_sha256", "old-hash"),
    ("configuration_sha256", "old-config"),
    ("configuration_captured_at_utc", "2020-01-01T00:00:00Z"),
    ("workspace_scope", "wrong-scope"), ("source", "enrichment"),
    ("captured_at_utc", "2020-01-01T00:00:00Z"), ("capture_status", "unknown"),
])
def test_analyzer_rejects_stale_or_unqualified_producer_evidence(config, tmp_path, field, value):
    snapshot = read_snapshot(capture(tmp_path, config))
    stamp = provenance(snapshot)
    stamp[field] = value
    (tmp_path / "workspace_inventory.json").write_text(json.dumps(
        {"workspaces": [], "workspaceListComplete": True, "_provenance": stamp}), encoding="utf-8")
    with pytest.raises(ValueError):
        _read(tmp_path, "workspace_inventory.json", snapshot)


def test_analyzer_rejects_unstamped_old_raw_even_if_scope_looks_complete(config, tmp_path):
    snapshot = read_snapshot(capture(tmp_path, config))
    (tmp_path / "workspace_inventory.json").write_text(
        '{"workspaces":[],"workspaceListComplete":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="Unbound or stale"):
        _read(tmp_path, "workspace_inventory.json", snapshot)


def test_evidence_scope_is_compared_as_a_set_of_workspace_ids(config, tmp_path, monkeypatch):
    snapshot = read_snapshot(capture(tmp_path, config))
    stamp = provenance(snapshot)
    stamp["workspace_scope"] = f"{OTHER.upper()}, {WID}"
    (tmp_path / "workspace_inventory.json").write_text(json.dumps(
        {"workspaces": [], "workspaceListComplete": True, "_provenance": stamp}), encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_IDS", f"{WID};{OTHER}")
    assert _read(tmp_path, "workspace_inventory.json", snapshot)["workspaceListComplete"] is True
    monkeypatch.setenv("WORKSPACE_IDS", WID)
    with pytest.raises(ValueError, match="Unbound or stale"):
        _read(tmp_path, "workspace_inventory.json", snapshot)


@pytest.mark.parametrize("rule_id, override, environment, expected", [
    ("OPS-001", None, "production", True),
    ("OPS-001", None, "nonproduction", False),
    ("OPS-001", None, None, False),
    ("ARCH-001", None, None, False),
    ("ARCH-001", {"reason": "Boundary", "policy": {"required": True, "allowed_item_types": ["Lakehouse"]}}, None, True),
    ("ARCH-001", {"reason": "Opt out", "applicability": "not_applicable"}, None, False),
    ("ARCH-004", None, None, True),
    ("SEC-010", None, None, True),
    ("ARCH-020", None, None, True),
    ("SEC-005", None, None, True),
    ("SEC-008", None, None, True),
    ("GOV-003", None, None, True),
    ("SEC-006", None, None, True),
    ("ARCH-006", None, "production", True),
    ("ARCH-006", None, "nonproduction", False),
    ("ARCH-006", None, None, False),
    ("ARCH-007", None, None, False),
    ("ARCH-007", {"reason": "Boundary", "policy": {"required": True, "allowed_regions": ["westeurope"]}}, None, True),
])
def test_collection_demand_matches_what_the_analyzer_will_read(config, rule_id, override, environment, expected):
    from orchestration.rule_policy import evidence_demanded
    assert evidence_demanded(rule_id, effective(rule_id, config, override, environment)) is expected


def tenant_payload(**states):
    """Tenant settings as the collector records them; state is disabled, scoped or everyone."""
    settings = []
    for name, state in states.items():
        settings.append({"settingName": name, "title": name, "tenantSettingGroup": "Test",
                         "enabled": state != "disabled", "canSpecifySecurityGroups": True,
                         "enabledSecurityGroupCount": 1 if state == "scoped" else 0,
                         "excludedSecurityGroupCount": 0})
    return {"tenantSettings": settings, "collectionComplete": True}


@pytest.mark.parametrize("rule_id", sorted(TENANT_SETTING_NAMES))
@pytest.mark.parametrize("state, expected", [("disabled", "pass"), ("scoped", "pass"), ("everyone", "fail")])
def test_tenant_setting_controls_pass_when_disabled_or_scoped(config, rule_id, state, expected):
    if rule_id in ENABLED_IS_SECURE:
        expected = "fail" if state == "disabled" else "pass"
    source = raw()
    source["tenant_settings.json"] = tenant_payload(**{TENANT_SETTING_NAMES[rule_id][0]: state})
    status, evidence = _assess_tenant(rule_id, effective(rule_id, config), ctx(source))
    assert status == expected
    assert evidence["assessment_kind"] == "tenant_setting"


@pytest.mark.parametrize("rule_id", sorted(TENANT_SETTING_NAMES))
def test_tenant_setting_aliases_are_recognised(config, rule_id):
    source = raw()
    source["tenant_settings.json"] = tenant_payload(**{TENANT_SETTING_NAMES[rule_id][-1]: "everyone"})
    expected = "pass" if rule_id in ENABLED_IS_SECURE else "fail"
    assert _assess_tenant(rule_id, effective(rule_id, config), ctx(source))[0] == expected


def test_unreadable_or_missing_tenant_settings_are_never_a_pass(config):
    resolved = effective("SEC-003", config)
    assert _assess_tenant("SEC-003", resolved, ctx())[1]["reason_code"] == "tenant_settings_unavailable"
    source = raw()
    source["tenant_settings.json"] = tenant_payload(SomethingElse="disabled")
    status, evidence = _assess_tenant("SEC-003", resolved, ctx(source))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "setting_not_returned")
    source["tenant_settings.json"]["tenantSettings"] = [{"settingName": "PublishToWeb", "enabled": "yes"}]
    assert _assess_tenant("SEC-003", resolved, ctx(source))[1]["reason_code"] == "setting_state_unavailable"
    coverage = _assess_tenant("GOV-010", effective("GOV-010", config), ctx())
    assert coverage[0] == "missing_evidence"
    source["tenant_settings.json"] = tenant_payload(PublishToWeb="disabled")
    assert _assess_tenant("GOV-010", effective("GOV-010", config), ctx(source))[0] == "info"


def test_tenant_controls_can_be_disabled_through_the_default_rule_profile(config):
    config["profiles"].update(rule_profiles={"quiet": {"rule_overrides": {
        "SEC-003": {"reason": "Handled by the tenant admin team", "applicability": "not_applicable"},
    }}}, default_rule_profile="quiet")
    validate_configuration(config)
    resolved = resolve_rule(config, "SEC-003", {})
    assert _assess_tenant("SEC-003", resolved, ctx())[0] == "not_applicable"


def test_tenant_rules_emit_one_finding_per_review_and_the_rest_one_per_workspace(config, tmp_path):
    snapshot = read_snapshot(capture(tmp_path, config))
    activate(tmp_path / "review_config.json")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    def write(name, payload):
        (raw_dir / name).write_text(json.dumps({**payload, "_provenance": provenance(snapshot)}), encoding="utf-8")

    write("workspace_inventory.json", {"workspaceListComplete": True, "workspaces": [
        workspace(), {**workspace(), "id": OTHER, "name": "second"}]})
    write("tenant_settings.json", tenant_payload(PublishToWeb="everyone"))
    rows = json.loads(analyze(raw_dir, tmp_path, ROOT / "config" / "review-checklist.yaml").read_text())
    per_rule = {}
    for row in rows:
        per_rule[row["rule_id"]] = per_rule.get(row["rule_id"], 0) + 1
    # Both workspaces share one capacity, so a capacity rule gives one finding as well.
    assert {rule for rule, count in per_rule.items() if count == 1} == TENANT_RULES | CAPACITY_RULES
    assert all(count == 2 for rule, count in per_rule.items() if rule not in TENANT_RULES | CAPACITY_RULES)
    enrichment = [row for row in rows if row["rule_id"] in ENRICHMENT_SOURCES]
    assert {row["status"] for row in enrichment} == {"not_applicable"}
    assert {row["rule_id"]: row["evidence"]["reason_code"] for row in enrichment} == {
        rule: "no_required_tags_declared" if rule == "GOV-009" else "source_not_enabled"
        for rule in ENRICHMENT_SOURCES}
    publish = next(row for row in rows if row["rule_id"] == "SEC-003")
    assert (publish["status"], publish["scored"], publish["evidence"]["workspace_id"]) == ("fail", True, None)
    assert [row["rule_id"] for row in rows] == sorted(
        [row["rule_id"] for row in rows], key=RULE_ORDER.index)

def test_storage_mode_policy_is_decided_from_real_tmdl_not_hand_written_modes(config):
    from collectors.definition_safety import model_summary
    from tests.test_definition_signals import definition, part
    tmdl = "table Sales\n\tpartition Sales = entity\n\t\tmode: directLake\n"
    summary = model_summary(definition(part("definition/tables/Sales.tmdl", tmdl)))
    assert summary["storageModes"] == ["DirectLake"]
    ws = workspace([{"id": NID, "type": "SemanticModel"}])
    source = raw()
    source["semantic_model_definitions.json"] = {"models": [{"id": NID, "workspaceId": WID, **summary}]}
    for allowed, expected in ((["DirectLake"], "pass"), (["Import"], "fail")):
        override = {"reason": "Declared design boundary", "policy": {"required": True, "allowed_storage_modes": allowed}}
        resolved = effective("ARCH-003", config, override)
        assert _assess("ARCH-003", ws, resolved, ctx(source))[0] == expected

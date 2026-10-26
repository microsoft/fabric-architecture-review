# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Policy declarations, scoped thresholds and tenant-setting polarity added with the extended rule set."""
import copy

import pytest

from analyzers.far_profile import ENABLED_IS_SECURE, TENANT_SETTING_NAMES
from analyzers.far_review import _assess_tenant, _recommendation, _setting_scope
from analyzers.rule_support import Context
from orchestration.review_config import (
    DECLARED_REASON, POLICY_LISTS, ROOT, declared_profiles, local_configuration, read_yaml, validate_configuration,
    validate_thresholds,
)
from orchestration.rule_policy import SCOPED_THRESHOLDS, resolve_rule, validate_override, validate_rule_threshold

REASON = "Declared for the review."
RATIO_KEYS = [
    ("governance", "endorsement_min_ratio"), ("governance", "domain_coverage_min_ratio"),
    ("governance", "stale_item_max_ratio"), ("operations", "failure_max_ratio"),
    ("operations", "expensive_item_cu_max_ratio"), ("operations", "throttled_day_max_ratio"),
    ("operations", "refresh_failure_max_ratio"), ("operations", "workspace_cu_max_ratio"),
    ("security", "label_min_ratio"), ("operations", "capacity_concentration_max_ratio"),
]
COUNT_KEYS = [
    ("architecture", "cross_workspace_dependency_max"), ("governance", "inactive_workspace_days"),
    ("governance", "stale_item_days"), ("operations", "window_days"), ("operations", "failure_min_events"),
    ("operations", "refresh_min_events"), ("operations", "min_capacity_admins"),
]


def declarations(**policy):
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["policy"].update(policy)
    return values


def configuration(**policy):
    config = local_configuration()
    config["profiles"] = declared_profiles(declarations(**policy))
    validate_configuration(config)
    return config


def override(policy=None, **extra):
    value = {"reason": REASON, **extra}
    if policy is not None:
        value["policy"] = policy
    return value


# --- declared policy lists ---------------------------------------------------------------------

def test_allowed_regions_declare_the_arch_007_requirement():
    profiles = declared_profiles(declarations(allowed_regions="westeurope, northeurope"))
    declared = profiles["rule_profiles"]["declared"]["rule_overrides"]
    assert declared == {"ARCH-007": {
        "reason": DECLARED_REASON, "policy": {"required": True, "allowed_regions": ["westeurope", "northeurope"]},
    }}
    assert profiles["default_rule_profile"] == "declared"


def test_required_capacity_tags_declare_the_gov_009_requirement_under_its_policy_field():
    profiles = declared_profiles(declarations(required_capacity_tags="owner; cost-center\nenvironment"))
    declared = profiles["rule_profiles"]["declared"]["rule_overrides"]
    assert declared == {"GOV-009": {
        "reason": DECLARED_REASON,
        "policy": {"required": True, "required_tag_names": ["owner", "cost-center", "environment"]},
    }}


def test_both_new_lists_can_be_declared_together_with_the_existing_ones():
    config = configuration(allowed_regions="westeurope", required_capacity_tags="owner", allowed_roles="Admin")
    assert set(config["profiles"]["rule_profiles"]["declared"]["rule_overrides"]) == {"ARCH-007", "GOV-009", "SEC-002"}
    assert POLICY_LISTS["allowed_regions"] == "ARCH-007" and POLICY_LISTS["required_capacity_tags"] == "GOV-009"


def test_declared_policies_resolve_for_any_workspace():
    config = configuration(allowed_regions="westeurope", required_capacity_tags="owner")
    for rule_id, policy in (("ARCH-007", {"required": True, "allowed_regions": ["westeurope"]}),
                            ("GOV-009", {"required": True, "required_tag_names": ["owner"]})):
        resolved = resolve_rule(config, rule_id, {"id": "11111111-1111-1111-1111-111111111111"})
        assert resolved["policy"] == policy
        assert resolved["origins"] == ["policy_variables"]


def test_undeclared_lists_leave_the_rules_without_a_requirement():
    config = configuration()
    assert resolve_rule(config, "ARCH-007", {})["policy"] == {}
    assert resolve_rule(config, "GOV-009", {})["policy"] == {}


def test_region_and_tag_names_with_spaces_are_kept_whole():
    """policy.yaml documents "West Europe, North Europe" as the example for allowed_regions."""
    profiles = declared_profiles(declarations(allowed_regions="West Europe, North Europe",
                                              required_capacity_tags="cost center;\nowner"))
    overrides = profiles["rule_profiles"]["declared"]["rule_overrides"]
    assert overrides["ARCH-007"]["policy"]["allowed_regions"] == ["West Europe", "North Europe"]
    assert overrides["GOV-009"]["policy"]["required_tag_names"] == ["cost center", "owner"]
    single = declared_profiles(declarations(allowed_regions="West Europe"))
    assert single["rule_profiles"]["declared"]["rule_overrides"]["ARCH-007"]["policy"]["allowed_regions"] == [
        "West Europe"]


# --- validate_override -------------------------------------------------------------------------

@pytest.mark.parametrize("rule_id, policy", [
    ("ARCH-007", {"required": True, "allowed_regions": ["westeurope", "North Europe"]}),
    ("ARCH-007", {"required": True, "production_only": True, "allowed_regions": ["westeurope"]}),
    ("GOV-009", {"required": True, "required_tag_names": ["owner", "cost-center"]}),
    ("ARCH-006", {"required": True}),
    ("ARCH-006", {"required": False}),
])
def test_validate_override_accepts_the_new_policy_fields(rule_id, policy):
    validate_override(rule_id, override(policy))


@pytest.mark.parametrize("rule_id, policy", [
    ("ARCH-007", {"required": True, "allowed_region": ["westeurope"]}),
    ("ARCH-007", {"required": True, "required_tag_names": ["owner"]}),
    ("GOV-009", {"required": True, "required_capacity_tags": ["owner"]}),
    ("GOV-009", {"required": True, "allowed_regions": ["westeurope"]}),
    ("ARCH-006", {"required": True, "allowed_regions": ["westeurope"]}),
    ("ARCH-006", {"required": True, "allowed_capacity_ids": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]}),
    ("GOV-005", {"required": True}),
    ("OPS-002", {"required": True, "window_days": 5}),
])
def test_validate_override_rejects_unknown_policy_fields(rule_id, policy):
    with pytest.raises(ValueError):
        validate_override(rule_id, override(policy))


@pytest.mark.parametrize("rule_id, key", [("ARCH-007", "allowed_regions"), ("GOV-009", "required_tag_names")])
@pytest.mark.parametrize("value", ["westeurope", [], ["a", "a"], [" "], [1], None])
def test_validate_override_rejects_malformed_lists(rule_id, key, value):
    with pytest.raises(ValueError):
        validate_override(rule_id, override({"required": True, key: value}))


@pytest.mark.parametrize("extra", [{"unknown": 1}, {"owner": "x"}])
def test_validate_override_rejects_unknown_override_fields(extra):
    with pytest.raises(ValueError, match="Unsupported rule override fields"):
        validate_override("ARCH-007", override({"required": True, "allowed_regions": ["westeurope"]}, **extra))


def test_validate_override_requires_a_reason():
    with pytest.raises(ValueError, match="reason"):
        validate_override("ARCH-007", {"policy": {"required": True, "allowed_regions": ["westeurope"]}})


# --- thresholds --------------------------------------------------------------------------------

def thresholds():
    return read_yaml(ROOT / "config" / "thresholds.yaml")


@pytest.mark.parametrize("group, key", RATIO_KEYS)
def test_new_ratio_thresholds_exist_and_are_ratios(group, key):
    value = thresholds()[group][key]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    assert 0 <= value <= 1


@pytest.mark.parametrize("group, key", COUNT_KEYS)
def test_new_count_thresholds_exist_and_are_nonnegative_integers(group, key):
    value = thresholds()[group][key]
    assert isinstance(value, int) and not isinstance(value, bool) and value >= 0


def test_the_documented_defaults():
    defaults = thresholds()
    assert defaults["operations"] == {
        "window_days": 30, "failure_min_events": 20, "failure_max_ratio": 0.2,
        "expensive_item_cu_max_ratio": 0.25, "throttled_day_max_ratio": 0.1,
        "refresh_min_events": 5, "refresh_failure_max_ratio": 0.2, "workspace_cu_max_ratio": 0.5,
        "min_capacity_admins": 2, "capacity_concentration_max_ratio": 0.8,
    }
    assert defaults["governance"]["inactive_workspace_days"] == 90
    assert defaults["governance"]["stale_item_days"] == 180


def test_every_scoped_threshold_points_at_an_existing_default():
    defaults = thresholds()
    for rule_id, mapping in SCOPED_THRESHOLDS.items():
        for key, (group, name) in mapping.items():
            assert name in defaults[group], f"{rule_id}.{key}"


@pytest.mark.parametrize("rule_id, expected", [
    ("OPS-002", {"window_days": 30, "failure_min_events": 20, "failure_max_ratio": 0.2}),
    ("OPS-003", {"window_days": 30, "expensive_item_cu_max_ratio": 0.25}),
    ("OPS-004", {"window_days": 30, "throttled_day_max_ratio": 0.1}),
    ("OPS-005", {"window_days": 30, "refresh_min_events": 5, "refresh_failure_max_ratio": 0.2}),
    ("OPS-007", {"window_days": 30, "workspace_cu_max_ratio": 0.5}),
    ("OPS-009", {"min_capacity_admins": 2}),
    ("REL-001", {"capacity_concentration_max_ratio": 0.8}),
    ("GOV-006", {"domain_coverage_min_ratio": 0.8}),
    ("GOV-007", {"inactive_workspace_days": 90}),
    ("GOV-008", {"stale_item_days": 180, "stale_item_max_ratio": 0.5}),
    ("GOV-005", {"endorsement_min_ratio": 0.5}),
    ("SEC-012", {"label_min_ratio": 0.5}),
    ("ARCH-014", {"cross_workspace_dependency_max": 5}),
])
def test_resolve_rule_exposes_the_thresholds_of_each_new_rule(rule_id, expected):
    assert resolve_rule(configuration(), rule_id, {})["thresholds"] == expected


def test_resolve_rule_follows_the_configured_values_and_scoped_overrides():
    config = configuration()
    config["thresholds"]["operations"]["failure_max_ratio"] = 0.4
    assert resolve_rule(config, "OPS-002", {})["thresholds"]["failure_max_ratio"] == 0.4
    config["profiles"].update(
        rule_profiles={"declared": {"rule_overrides": {"OPS-002": override(thresholds={"failure_min_events": 3})}}},
        default_rule_profile="declared",
    )
    validate_configuration(config)
    resolved = resolve_rule(config, "OPS-002", {})["thresholds"]
    assert (resolved["failure_min_events"], resolved["failure_max_ratio"], resolved["window_days"]) == (3, 0.4, 30)


def test_scoped_threshold_overrides_are_limited_to_the_rule_keys():
    validate_override("OPS-002", override(thresholds={"failure_max_ratio": 0.5, "window_days": 7}))
    with pytest.raises(ValueError, match="Unsupported scoped threshold"):
        validate_override("OPS-002", override(thresholds={"stale_item_days": 5}))
    with pytest.raises(ValueError, match="Unsupported scoped threshold"):
        validate_override("OPS-004", override(thresholds={"failure_max_ratio": 0.5}))


@pytest.mark.parametrize("group, key", RATIO_KEYS)
@pytest.mark.parametrize("value", [1.01, 2, -0.1, True, "0.5", float("nan"), float("inf")])
def test_validate_thresholds_rejects_invalid_ratios(group, key, value):
    values = thresholds()
    values[group][key] = value
    with pytest.raises(ValueError):
        validate_thresholds(values)


@pytest.mark.parametrize("group, key", RATIO_KEYS)
@pytest.mark.parametrize("value", [0, 1, 0.5])
def test_validate_thresholds_accepts_the_ratio_boundaries(group, key, value):
    values = thresholds()
    values[group][key] = value
    validate_thresholds(values)


@pytest.mark.parametrize("group, key", COUNT_KEYS)
@pytest.mark.parametrize("value", [-1, 1.5, True, "5", None])
def test_validate_thresholds_rejects_invalid_counts(group, key, value):
    values = thresholds()
    values[group][key] = value
    with pytest.raises(ValueError):
        validate_thresholds(values)


@pytest.mark.parametrize("group, key", COUNT_KEYS)
def test_validate_thresholds_accepts_zero_counts(group, key):
    values = thresholds()
    values[group][key] = 0
    validate_thresholds(values)


def test_validate_thresholds_rejects_missing_and_unknown_keys():
    for group, key in RATIO_KEYS + COUNT_KEYS:
        values = thresholds()
        del values[group][key]
        with pytest.raises(ValueError):
            validate_thresholds(values)
    values = thresholds()
    values["operations"]["surprise_ratio"] = 0.1
    with pytest.raises(ValueError):
        validate_thresholds(values)


@pytest.mark.parametrize("key, value", [("failure_max_ratio", 1.5), ("failure_min_events", -1), ("window_days", 2.5)])
def test_scoped_threshold_values_are_validated(key, value):
    with pytest.raises(ValueError):
        validate_rule_threshold(key, value)
    with pytest.raises(ValueError):
        validate_override("OPS-002", override(thresholds={key: value}))


# --- tenant settings ---------------------------------------------------------------------------

def setting(name="Setting", enabled=True, groups=0, **extra):
    return {"settingName": name, "enabled": enabled, "enabledSecurityGroupCount": groups,
            "excludedSecurityGroupCount": 0, "canSpecifySecurityGroups": True, **extra}


def test_tenant_setting_registry_covers_the_new_rules():
    assert TENANT_SETTING_NAMES["SEC-007"] == ("ExternalSharingV2",)
    assert TENANT_SETTING_NAMES["SEC-008"] == ("BlockResourceKeyAuthentication",)
    assert ENABLED_IS_SECURE == {"SEC-008", "SEC-013"}


def test_protective_settings_pass_when_enabled_and_fail_when_disabled():
    status, evidence = _setting_scope(setting(enabled=True), enabled_is_secure=True)
    assert (status, evidence["enabled"]) == ("pass", True)
    status, evidence = _setting_scope(setting(enabled=False), enabled_is_secure=True)
    assert (status, evidence["enabled"], "protective" in evidence["reason"]) == ("fail", False, True)
    assert _setting_scope(setting(enabled=True, groups=3), enabled_is_secure=True)[0] == "pass"


@pytest.mark.parametrize("enabled, groups, expected", [(False, 0, "pass"), (True, 2, "pass"), (True, 0, "fail")])
def test_default_polarity_is_unchanged(enabled, groups, expected):
    assert _setting_scope(setting(enabled=enabled, groups=groups))[0] == expected
    assert _setting_scope(setting(enabled=enabled, groups=groups), enabled_is_secure=False)[0] == expected


@pytest.mark.parametrize("secure", [True, False])
@pytest.mark.parametrize("broken", [{"enabled": "yes"}, {"enabled": None}, {"enabledSecurityGroupCount": None},
                                    {"enabledSecurityGroupCount": "1"}])
def test_unreadable_setting_state_is_missing_evidence_with_either_polarity(secure, broken):
    status, evidence = _setting_scope({**setting(), **broken}, enabled_is_secure=secure)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "setting_state_unavailable")


def tenant_context(*settings, complete=True):
    config = local_configuration()
    raw = {"tenant_settings.json": {"tenantSettings": list(settings), "collectionComplete": complete}}
    return Context({"configuration": config}, [], raw)


def assess(rule_id, *settings, complete=True):
    ctx = tenant_context(*settings, complete=complete)
    return _assess_tenant(rule_id, resolve_rule(ctx.configuration, rule_id, {"id": None}), ctx)


def test_sec_008_passes_when_resource_key_authentication_is_blocked():
    status, evidence = assess("SEC-008", setting("BlockResourceKeyAuthentication", enabled=True))
    assert (status, evidence["assessment_kind"], evidence["setting_name"]) == ("pass", "tenant_setting", "BlockResourceKeyAuthentication")


def test_sec_008_fails_when_it_is_not_blocked_and_recommends_enabling_it():
    status, evidence = assess("SEC-008", setting("BlockResourceKeyAuthentication", enabled=False))
    assert status == "fail"
    assert _recommendation(status, True, evidence) == "Enable the setting in the Fabric admin portal."


def test_sec_008_absent_setting_is_missing_evidence():
    status, evidence = assess("SEC-008", setting("SomethingElse", enabled=True))
    assert (status, evidence["reason_code"]) == ("missing_evidence", "setting_not_returned")
    assert evidence["setting_names"] == ["BlockResourceKeyAuthentication"]
    assert assess("SEC-008")[1]["reason_code"] == "setting_not_returned"


@pytest.mark.parametrize("rule_id", ["SEC-007", "SEC-008"])
def test_tenant_settings_that_could_not_be_read_are_never_a_pass(rule_id):
    status, evidence = assess(rule_id, setting(TENANT_SETTING_NAMES[rule_id][0]), complete=False)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "tenant_settings_unavailable")
    ctx = Context({"configuration": local_configuration()}, [], {"tenant_settings.json": {}})
    assert _assess_tenant(rule_id, resolve_rule(ctx.configuration, rule_id, {"id": None}), ctx)[0] == "missing_evidence"


@pytest.mark.parametrize("enabled, groups, expected", [(True, 0, "fail"), (True, 2, "pass"), (False, 0, "pass")])
def test_sec_007_external_sharing_is_judged_by_its_scope(enabled, groups, expected):
    status, evidence = assess("SEC-007", setting("ExternalSharingV2", enabled=enabled, groups=groups))
    assert status == expected
    if expected == "fail":
        assert _recommendation(status, True, evidence).startswith("Restrict the setting to security groups")


def test_sec_007_ignores_other_settings_with_the_same_state():
    status, evidence = assess("SEC-007", setting("PublishToWeb", enabled=True), setting("ExternalSharingV2", enabled=False))
    assert (status, evidence["setting_name"]) == ("pass", "ExternalSharingV2")


def test_a_scoped_disabled_rule_does_not_read_the_setting():
    config = local_configuration()
    config["profiles"] = declared_profiles(declarations(disabled_rules="SEC-008"))
    ctx = Context({"configuration": config}, [], {"tenant_settings.json": {}})
    status, _ = _assess_tenant("SEC-008", resolve_rule(config, "SEC-008", {"id": None}), ctx)
    assert status == "not_applicable"


def test_private_link_enabled_does_not_advise_disabling_it():
    """SEC-013 is context: an enabled tenant-level Private Link is not something to switch off."""
    status, evidence = assess("SEC-013", setting("AllowAccessOverPrivateLinks", enabled=True))
    assert "disable it" not in _recommendation(status, True, evidence)

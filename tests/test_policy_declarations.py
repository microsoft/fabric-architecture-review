# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The scope and policy variables are the only way an estate declares what it expects."""
import base64
import copy
import json

import pytest

from analyzers.far_profile import ACTIVE_RULE_IDS
from orchestration.config_deployment import library_definition
from orchestration.review_config import (
    ROOT, declared_profiles, fabric_configuration, local_configuration, read_yaml, validate_configuration,
)
from orchestration.rule_policy import resolve_rule

PROD_WS = "11111111-1111-1111-1111-111111111111"
NONPROD_WS = "22222222-2222-2222-2222-222222222222"
OTHER_WS = "33333333-3333-3333-3333-333333333333"
PROD_CAP = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
NONPROD_CAP = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OTHER_USER = "dddddddd-dddd-dddd-dddd-dddddddddddd"
ARM = (
    "/subscriptions/44444444-4444-4444-4444-444444444444/resourceGroups/rg-fabric"
    "/providers/Microsoft.Fabric/capacities/prodcap"
)


def declarations(scope=None, policy=None):
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["scope"].update(scope or {})
    values["policy"].update(policy or {})
    return values


def configuration(**groups):
    config = local_configuration()
    config["profiles"] = declared_profiles(declarations(**groups))
    validate_configuration(config)
    return config


def environment(config, workspace, capacity=None):
    row = {"id": workspace, "capacityId": capacity}
    return resolve_rule(config, "OPS-001", row)["environment"]


def test_empty_declarations_declare_nothing():
    profiles = declared_profiles(declarations())
    assert profiles == {"workspaces": [], "capacities": []}
    assert declared_profiles(read_yaml(ROOT / "config" / "policy.yaml")) == profiles


def test_scope_lists_become_workspace_and_capacity_entries():
    profiles = declared_profiles(declarations(scope={
        "production_workspace_ids": f"{PROD_WS}, {OTHER_WS.upper()}",
        "nonproduction_workspace_ids": NONPROD_WS,
        "production_capacity_ids": PROD_CAP,
        "nonproduction_capacity_ids": f"{NONPROD_CAP};",
    }))
    assert {(row["id"], row["profile"]["environment"]) for row in profiles["workspaces"]} == {
        (PROD_WS, "production"), (OTHER_WS, "production"), (NONPROD_WS, "nonproduction"),
    }
    assert {(row["id"], row["profile"]["environment"]) for row in profiles["capacities"]} == {
        (PROD_CAP, "production"), (NONPROD_CAP, "nonproduction"),
    }
    assert all(row["inherit_rules"] is True for row in profiles["capacities"])
    assert "inherit_rules" not in profiles["workspaces"][0]
    assert "rule_profiles" not in profiles


def test_environment_follows_the_workspace_list_before_the_capacity_list():
    config = configuration(scope={
        "production_workspace_ids": PROD_WS,
        "nonproduction_workspace_ids": NONPROD_WS,
        "production_capacity_ids": PROD_CAP,
        "nonproduction_capacity_ids": NONPROD_CAP,
    })
    assert environment(config, OTHER_WS, PROD_CAP) == "production"
    assert environment(config, OTHER_WS, NONPROD_CAP) == "nonproduction"
    assert environment(config, OTHER_WS, None) == "unknown"
    assert environment(config, OTHER_WS, "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee") == "unknown"
    assert environment(config, NONPROD_WS, PROD_CAP) == "nonproduction"
    assert environment(config, PROD_WS, NONPROD_CAP) == "production"
    assert environment(config, PROD_WS.upper(), None) == "production"


def test_workspace_is_production_on_a_production_capacity_only():
    config = configuration(scope={"production_capacity_ids": PROD_CAP})
    assert environment(config, OTHER_WS, PROD_CAP) == "production"
    assert environment(config, OTHER_WS, NONPROD_CAP) == "unknown"
    assert environment(config, OTHER_WS, None) == "unknown"


def test_capacity_arm_resource_ids_are_recorded_per_capacity():
    profiles = declared_profiles(declarations(scope={
        "production_capacity_ids": PROD_CAP,
        "capacity_arm_resource_ids": f"{PROD_CAP.upper()}={ARM}\n{NONPROD_CAP}={ARM}2",
    }))
    capacities = {row["id"]: row for row in profiles["capacities"]}
    assert capacities[PROD_CAP]["arm_resource_id"] == ARM.lower()
    assert capacities[PROD_CAP]["profile"]["environment"] == "production"
    assert capacities[NONPROD_CAP]["arm_resource_id"] == ARM.lower() + "2"
    assert "profile" not in capacities[NONPROD_CAP]
    assert environment(configuration(scope={"capacity_arm_resource_ids": f"{PROD_CAP}={ARM}"}), OTHER_WS, PROD_CAP) == "unknown"


@pytest.mark.parametrize("scope", [
    {"production_workspace_ids": PROD_WS, "nonproduction_workspace_ids": PROD_WS},
    {"production_workspace_ids": PROD_WS.upper(), "nonproduction_workspace_ids": PROD_WS},
    {"production_workspace_ids": f"{PROD_WS} {PROD_WS}"},
    {"production_capacity_ids": PROD_CAP, "nonproduction_capacity_ids": PROD_CAP},
    {"production_workspace_ids": PROD_WS[:-1]},
    {"nonproduction_workspace_ids": "not-a-guid"},
    {"production_capacity_ids": f"{PROD_CAP}x"},
    {"capacity_arm_resource_ids": ARM},
    {"capacity_arm_resource_ids": f"{PROD_CAP}="},
    {"capacity_arm_resource_ids": f"{PROD_CAP}x={ARM}"},
    {"capacity_arm_resource_ids": f"{PROD_CAP}=/subscriptions/not-a-resource"},
    {"capacity_arm_resource_ids": f"{PROD_CAP}={ARM} {PROD_CAP}={ARM}2"},
])
def test_invalid_scope_declarations_are_rejected(scope):
    with pytest.raises(ValueError):
        declared_profiles(declarations(scope=scope))


@pytest.mark.parametrize("policy", [
    {"allowed_roles": "Admin, Owner"},
    {"allowed_roles": "admin"},
    {"allowed_storage_modes": "Import, Lake"},
    {"disabled_rules": "SEC-005, NOPE-001"},
    {"allowed_capacity_ids": "not-a-guid"},
    {"eligible_admin_ids": "someone@example.com"},
    {"allowed_principal_ids": f"{USER} {USER.upper()}"},
    {"allowed_item_types": "Lakehouse, Lakehouse"},
])
def test_invalid_policy_declarations_are_rejected(policy):
    with pytest.raises(ValueError):
        declared_profiles(declarations(policy=policy))


def test_declarations_must_keep_the_fixed_shape():
    complete = declarations()
    with pytest.raises(ValueError):
        declared_profiles({"scope": complete["scope"]})
    missing = copy.deepcopy(complete)
    del missing["policy"]["allowed_roles"]
    with pytest.raises(ValueError):
        declared_profiles(missing)
    extra = copy.deepcopy(complete)
    extra["scope"]["unknown"] = ""
    with pytest.raises(ValueError):
        declared_profiles(extra)
    with pytest.raises(ValueError, match="must be text"):
        declared_profiles(declarations(policy={"allowed_roles": ["Admin"]}))


@pytest.mark.parametrize("rule_id, key, text, expected", [
    ("ARCH-001", "allowed_item_types", "Lakehouse, Notebook;DataPipeline\nWarehouse",
     ["Lakehouse", "Notebook", "DataPipeline", "Warehouse"]),
    ("ARCH-002", "allowed_capacity_ids", f"{PROD_CAP.upper()} {NONPROD_CAP}", [PROD_CAP, NONPROD_CAP]),
    ("ARCH-003", "allowed_storage_modes", "DirectLake, Import", ["DirectLake", "Import"]),
    ("GOV-001", "eligible_admin_ids", f"{USER},{OTHER_USER}", [USER, OTHER_USER]),
    ("SEC-001", "allowed_principal_ids", USER.upper(), [USER]),
    ("SEC-002", "allowed_roles", "Admin, Member, Contributor", ["Admin", "Member", "Contributor"]),
])
def test_each_policy_list_creates_its_rule_override(rule_id, key, text, expected):
    config = configuration(policy={key: text})
    resolved = resolve_rule(config, rule_id, {"id": OTHER_WS})
    expected_policy = {"required": True, key: expected}
    if rule_id == "ARCH-002":
        expected_policy.update(production_only=True, require_shared_workspace=True)
    assert resolved["policy"] == expected_policy
    assert resolved["applicability"] == "applicable"
    assert resolved["origins"] == ["policy_variables"]
    assert set(config["profiles"]["rule_profiles"]["declared"]["rule_overrides"]) == {rule_id}
    assert config["profiles"]["default_rule_profile"] == "declared"


def test_undeclared_lists_leave_the_built_in_requirements():
    resolved = resolve_rule(configuration(policy={"allowed_roles": "Admin"}), "ARCH-002", {"id": OTHER_WS})
    assert resolved["policy"] == {"required": True, "production_only": True, "require_shared_workspace": True}
    assert resolved["origins"] == []
    assert resolve_rule(configuration(), "SEC-002", {"id": OTHER_WS})["policy"] == {}


def test_disabled_rules_switch_off_workspace_and_tenant_rules_case_insensitively():
    config = configuration(policy={"disabled_rules": "sec-005, GOV-004;arch-001"})
    for rule_id, workspace in (("SEC-005", {}), ("GOV-004", {"id": OTHER_WS}), ("ARCH-001", {"id": PROD_WS})):
        assert resolve_rule(config, rule_id, workspace)["applicability"] == "disabled"
    assert resolve_rule(config, "SEC-003", {})["applicability"] == "applicable"
    assert resolve_rule(config, "GOV-001", {"id": OTHER_WS})["applicability"] == "applicable"
    overrides = config["profiles"]["rule_profiles"]["declared"]["rule_overrides"]
    assert sorted(overrides) == ["ARCH-001", "GOV-004", "SEC-005"]
    assert set(overrides) <= ACTIVE_RULE_IDS


class Library:
    """Stands in for a Fabric Variable Library: unset variables fall back to the shipped defaults."""

    def __init__(self, defaults, overrides=None, missing=()):
        self.values = {f"{group}__{key}": value for group, entries in defaults.items() for key, value in entries.items()}
        self.values.update(overrides or {})
        for name in missing:
            del self.values[name]

    def getVariable(self, name):
        return self.values[name]


def fabric(thresholds_library, connections_library=None):
    sources = read_yaml(ROOT / "config" / "sources.yaml")
    libraries = {
        "thresholds": thresholds_library,
        "connections": connections_library or Library(sources),
    }
    return fabric_configuration(libraries.__getitem__, "thresholds", "connections")


def shipped_defaults():
    return {**read_yaml(ROOT / "config" / "thresholds.yaml"), **read_yaml(ROOT / "config" / "policy.yaml")}


def test_fabric_configuration_reads_declarations_from_the_thresholds_library():
    overrides = {
        "scope__production_workspace_ids": f"{PROD_WS}, {OTHER_WS}",
        "scope__nonproduction_capacity_ids": NONPROD_CAP,
        "scope__capacity_arm_resource_ids": f"{PROD_CAP}={ARM}",
        "policy__allowed_roles": "Admin, Member",
        "policy__eligible_admin_ids": USER,
        "policy__disabled_rules": "SEC-005",
    }
    actual = fabric(Library(shipped_defaults(), overrides))
    validate_configuration(actual)
    values = declarations()
    for name, value in overrides.items():
        group, key = name.split("__")
        values[group][key] = value
    assert actual["profiles"] == declared_profiles(values)
    assert resolve_rule(actual, "SEC-005", {})["applicability"] == "disabled"
    assert resolve_rule(actual, "SEC-002", {"id": PROD_WS})["policy"]["allowed_roles"] == ["Admin", "Member"]
    assert environment(actual, PROD_WS) == "production"


def test_fabric_configuration_with_default_variables_matches_local_configuration():
    actual = fabric(Library(shipped_defaults()))
    assert actual["profiles"] == local_configuration()["profiles"] == {"workspaces": [], "capacities": []}
    assert actual["thresholds"] == local_configuration()["thresholds"]


@pytest.mark.parametrize("name", ["scope__production_workspace_ids", "policy__disabled_rules"])
def test_fabric_configuration_rejects_a_library_missing_a_policy_variable(name):
    with pytest.raises(KeyError):
        fabric(Library(shipped_defaults(), missing=[name]))


def test_fabric_configuration_rejects_invalid_declared_values():
    with pytest.raises(ValueError):
        fabric(Library(shipped_defaults(), {"scope__production_workspace_ids": "typo"}))
    with pytest.raises(ValueError, match="must be text"):
        fabric(Library(shipped_defaults(), {"policy__allowed_roles": None}))


def test_thresholds_library_carries_scope_and_policy_variables_as_text():
    definition = library_definition(shipped_defaults())
    variables = json.loads(base64.b64decode(definition["parts"][0]["payload"]))["variables"]
    by_name = {item["name"]: item for item in variables}
    assert by_name["scope__production_workspace_ids"] == {
        "name": "scope__production_workspace_ids", "type": "String", "value": "",
    }
    assert by_name["policy__disabled_rules"] == {"name": "policy__disabled_rules", "type": "String", "value": ""}
    policy_keys = {f"{group}__{key}" for group, entries in read_yaml(ROOT / "config" / "policy.yaml").items()
                   for key in entries}
    assert policy_keys <= by_name.keys()
    assert all(by_name[name]["type"] == "String" for name in policy_keys)

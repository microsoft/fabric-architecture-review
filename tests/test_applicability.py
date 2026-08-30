# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from analyzers.applicability import (
    classify_workspace,
    classify_workspaces,
    load_capacity_overrides,
    load_workspace_overrides,
    production_scope,
    rule_applicability,
)
from analyzers.cost_review import _capacity_environment


def _workspace(name: str, *item_types: str, workspace_type: str = "Workspace") -> dict:
    return {
        "id": name,
        "name": name,
        "type": workspace_type,
        "items": [{"id": f"{name}-{index}", "type": item_type}
                  for index, item_type in enumerate(item_types)],
    }


def test_classifies_primary_fabric_workloads() -> None:
    cases = [
        (_workspace("sales-prod", "Lakehouse", "DataPipeline", "SemanticModel"), "batch_engineering"),
        (_workspace("erp-prod", "MirroredDatabase", "SemanticModel", "Report"), "mirrored_zero_etl"),
        (_workspace("telemetry-prod", "Eventhouse", "Eventstream", "Report"), "realtime"),
        (_workspace("executive-prod", "SemanticModel", "Report"), "bi_serving"),
        (_workspace("finance-prod", "Warehouse", "SemanticModel"), "warehouse_analytics"),
        (_workspace("forecast-dev", "MLExperiment", "MLModel"), "data_science"),
    ]
    for workspace, expected in cases:
        assert classify_workspace(workspace)["archetype"] == expected


def test_new_or_mixed_workloads_are_not_silently_classified() -> None:
    assert classify_workspace(_workspace("new-prod", "FutureFabricItem"))["archetype"] == "unknown"
    assert classify_workspace(_workspace("mixed-prod", "Lakehouse", "Eventhouse"))["archetype"] == "mixed"


def test_explicit_profile_and_rule_override_win() -> None:
    profile = classify_workspace(
        _workspace("ambiguous", "FutureFabricItem"),
        {"profile": {"archetype": "batch_engineering", "environment": "production",
                 "usage_pattern": "monthly",
                     "reason": "Reviewed by platform owner."},
         "rule_overrides": {"ARCH-001": {"applicability": "not_applicable",
                                           "reason": "Landing-only workspace."}}},
    )
    assert profile["classification"] == "explicit"
    assert profile["environment"] == "production"
    assert profile["usagePattern"] == "monthly"
    assert rule_applicability(profile, "ARCH-001", ["batch_engineering"]) == {
        "status": "not_applicable", "reason": "Landing-only workspace."
    }


def test_medallion_applicability_distinguishes_exempt_and_unknown() -> None:
    engineering = classify_workspace(_workspace("sales-prod", "Lakehouse", "Notebook"))
    mirrored = classify_workspace(_workspace("erp-prod", "MirroredDatabase"))
    mixed = classify_workspace(_workspace("mixed-prod", "Lakehouse", "Eventhouse"))
    assert rule_applicability(engineering, "ARCH-001", ["batch_engineering"])["status"] == "applicable"
    assert rule_applicability(mirrored, "ARCH-001", ["batch_engineering"])["status"] == "not_applicable"
    assert rule_applicability(mixed, "ARCH-001", ["batch_engineering"])["status"] == "unknown"


def test_production_scope_uses_explicit_environment_and_preserves_unknown() -> None:
    explicit = _workspace("finance", "SemanticModel")
    unknown = _workspace("shared", "Report")
    nonprod = _workspace("sales-dev", "Lakehouse")
    profiles = classify_workspaces([explicit, unknown, nonprod], {
        "finance": {"profile": {"archetype": "bi_serving", "environment": "production"}}
    })
    scope = production_scope([explicit, unknown, nonprod], profiles)
    assert [workspace["name"] for workspace in scope["applicable"]] == ["finance"]
    assert [workspace["name"] for workspace in scope["unknown"]] == ["shared"]
    assert [workspace["name"] for workspace in scope["not_applicable"]] == ["sales-dev"]


def test_loads_workspace_and_capacity_overrides_by_immutable_id(tmp_path) -> None:
    config = tmp_path / "workspaces.yaml"
    config.write_text(
        "workspaces:\n"
        "  - id: ABCDEFAB-1234-1234-1234-ABCDEFABCDEF\n"
        "    profile:\n"
        "      environment: production\n"
        "capacities:\n"
        "  - id: FEDCBA98-1234-1234-1234-FEDCBA987654\n"
        "    profile:\n"
        "      environment: nonproduction\n",
        encoding="utf-8",
    )

    workspace = load_workspace_overrides(config)["abcdefab-1234-1234-1234-abcdefabcdef"]
    capacity = load_capacity_overrides(config)["fedcba98-1234-1234-1234-fedcba987654"]
    assert workspace["profile"]["environment"] == "production"
    assert capacity["profile"]["environment"] == "nonproduction"


def test_capacity_environment_override_wins_over_workspace_and_name_inference() -> None:
    capacity = {"id": "capacity-1", "displayName": "Shared Prod Capacity"}

    assert _capacity_environment(
        capacity,
        {"production"},
        {"profile": {"environment": "nonproduction"}},
    ) == "nonproduction"
    assert _capacity_environment(
        capacity,
        {"nonproduction"},
        {"profile": {"environment": "production"}},
    ) == "production"

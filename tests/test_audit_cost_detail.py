# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_activity_log_window_is_clamped_to_28_days(tmp_path: Path, monkeypatch) -> None:
    from collectors import activity_logs

    class Provider:
        def headers(self, *, scope: str) -> dict[str, str]:
            return {"Authorization": scope}

    fetched_days = []
    monkeypatch.setattr(activity_logs, "get_default_provider", lambda: Provider())
    monkeypatch.setattr(
        activity_logs,
        "_fetch_day",
        lambda _headers, day: fetched_days.append(day) or [],
    )

    target = activity_logs.collect(tmp_path, days=99)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert payload["windowDays"] == 28
    assert len(fetched_days) == 28


def test_cost_005_requires_tenant_wide_workspace_scope(tmp_path: Path) -> None:
    from analyzers.cost_review import analyze

    _write(tmp_path / "capacity_metrics.json", {
        "workspaceScopeLimited": True,
        "capacities": [{
            "id": "cap-1",
            "displayName": "Production F64",
            "sku": "F64",
            "assignedWorkspaceCount": 1,
        }],
    })
    _write(tmp_path / "scanner.json", {"workspaces": []})

    finding = next(item for item in analyze(tmp_path) if item["rule_id"] == "COST-005")

    assert finding["status"] == "missing_evidence"
    assert finding["evidence"]["requiredScope"] == "tenant-wide"


def test_gold_materializes_capacity_items_and_tenant_setting_changes(tmp_path: Path) -> None:
    from reports.gold_layer import build_gold

    _write(tmp_path / "capacity_metrics.json", {
        "workspaceScopeLimited": False,
        "capacities": [{
            "id": "cap-1",
            "displayName": "Production F64",
            "sku": "F64",
            "assignedWorkspaceCount": 1,
        }],
    })
    _write(tmp_path / "scanner.json", {"workspaces": [{
        "id": "ws-1",
        "name": "Finance",
        "capacityId": "cap-1",
        "datasets": [{"id": "model-1", "name": "Finance Model"}],
    }]})
    _write(tmp_path / "activity_logs.json", {
        "windowDays": 28,
        "fetchedAt": "2026-09-01T11:00:00Z",
        "events": [{
            "Id": "event-1",
            "CreationTime": "2026-09-01T10:00:00Z",
            "Activity": "UpdatedAdminFeatureSwitch",
            "UserId": "admin@contoso.com",
            "ModifiedProperties": [
                {"Name": "FeatureSwitchName", "NewValue": "ExportData"},
                {"Name": "Enabled", "OldValue": "false", "NewValue": "true"},
            ],
        }],
    })

    tables = build_gold(
        [], tmp_path, run_id="run-1", run_timestamp="2026-09-01T12:00:00Z",
        check_remote=False,
    )

    assert tables["gold_capacity_items"][0] == {
        "run_id": "run-1",
        "run_timestamp": "2026-09-01T12:00:00Z",
        "capacity_id": "cap-1",
        "capacity_name": "Production F64",
        "sku": "F64",
        "workspace_id": "ws-1",
        "workspace_name": "Finance",
        "item_id": "model-1",
        "item_name": "Finance Model",
        "item_type": "SemanticModel",
        "workspace_scope_limited": False,
    }
    assert tables["gold_tenant_setting_changes"][0]["setting_name"] == "ExportData"
    assert tables["gold_tenant_setting_changes"][0]["audit_window_days"] == 28


def test_gold_cost_impacts_identify_objects_and_bridge_to_capacity_items(tmp_path: Path) -> None:
    from reports.gold_layer import build_gold

    _write(tmp_path / "capacity_metrics.json", {
        "capacities": [{
            "id": "cap-1", "displayName": "Production F64", "sku": "F64",
            "assignedWorkspaceCount": 1,
        }],
    })
    _write(tmp_path / "scanner.json", {"workspaces": [{
        "id": "ws-1", "name": "Finance", "capacityId": "cap-1",
        "datasets": [{"id": "model-1", "name": "Finance Model"}],
    }]})
    findings = [
        {
            "rule_id": "COST-004", "dimension": "cost", "severity": "medium", "status": "fail",
            "title": "Workspace placement", "recommendation": "Move it",
            "evidence": {"examples": [{"workspace": "Finance", "capacityId": "cap-1"}]},
        },
        {
            "rule_id": "COST-005", "dimension": "cost", "severity": "medium", "status": "fail",
            "title": "Large capacity", "recommendation": "Right-size it",
            "evidence": {"capacities": [{
                "id": "cap-1", "name": "Production F64", "sku": "F64",
                "workspaceCount": 1, "itemCount": 1,
            }]},
        },
        {
            "rule_id": "COST-008", "dimension": "cost", "severity": "high", "status": "pass",
            "title": "Empty capacity", "recommendation": "None",
            "evidence": {"count": 0, "capacities": []},
        },
    ]

    tables = build_gold(
        findings, tmp_path, run_id="run-1", run_timestamp="2026-09-01T12:00:00Z",
        check_remote=False,
    )

    visible = [row for row in tables["gold_cost_finding_impacts"] if row["show_in_findings"]]
    assert {row["rule_id"] for row in visible} == {"COST-004", "COST-005", "COST-008"}
    cost_004 = next(row for row in visible if row["rule_id"] == "COST-004")
    assert (cost_004["affected_type"], cost_004["affected_name"]) == ("Workspace", "Finance")
    cost_005 = next(row for row in visible if row["rule_id"] == "COST-005")
    assert (cost_005["capacity_name"], cost_005["workspace_count"], cost_005["item_count"]) == (
        "Production F64", 1, 1,
    )
    cost_008 = next(row for row in visible if row["rule_id"] == "COST-008")
    assert cost_008["affected_name"] == "No affected object"
    bridged = [
        row for row in tables["gold_cost_impact_items"]
        if row["impact_key"] == cost_005["impact_key"]
    ]
    assert [(row["workspace_name"], row["item_name"]) for row in bridged] == [
        ("Finance", "Finance Model"),
    ]
    mapping_keys = [
        (row["impact_key"], row["workspace_id"], row["item_id"])
        for row in tables["gold_cost_impact_items"]
    ]
    assert len(mapping_keys) == len(set(mapping_keys))
    assert {row["run_id"] for row in tables["gold_cost_impact_items"]} == {"run-1"}


def test_cost_report_uses_selectable_impacts_to_filter_capacity_contents() -> None:
    from reports.powerbi.report import _pages
    from reports.powerbi.semantic_model import build_bim

    model = build_bim("FAR", "endpoint", "database")
    relationship = next(
        item for item in model["model"]["relationships"]
        if item["fromTable"] == "gold_cost_impact_items"
    )
    assert relationship["toTable"] == "gold_cost_finding_impacts"
    assert relationship["fromColumn"] == relationship["toColumn"] == "impact_key"
    assert relationship["crossFilteringBehavior"] == "oneDirection"
    assert not any(
        item["fromTable"] == "gold_cost_impact_items" and item["fromColumn"] == "run_id"
        for item in model["model"]["relationships"]
    )

    cost_page = next(page for page in _pages() if page["display"] == "Cost")
    page_json = json.dumps(cost_page)
    assert "gold_cost_finding_impacts" in page_json
    assert "gold_cost_impact_items" in page_json
    assert "show_in_findings" in page_json
    assert "affected_name" in page_json
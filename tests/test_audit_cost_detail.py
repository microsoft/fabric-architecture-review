# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from pathlib import Path


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_gold_materializes_static_capacity_items_not_audit_history(tmp_path: Path) -> None:
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

    expected = {
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
    assert {key: tables["gold_capacity_items"][0][key] for key in expected} == expected
    assert "gold_tenant_setting_changes" not in tables
    assert "UpdatedAdminFeatureSwitch" not in json.dumps(tables)
    assert tables["gold_capacities"][0]["assigned_workspace_count"] == 1


def test_model_and_report_cannot_query_financial_drillthrough() -> None:
    from reports.powerbi.report import _pages
    from reports.powerbi.semantic_model import build_bim

    model = build_bim("FAR", "endpoint", "database")
    assert not any("cost" in table["name"] for table in model["model"]["tables"])
    serialized = json.dumps([model, _pages()]).lower()
    assert "gold_cost_finding_impacts" not in serialized
    assert "gold_cost_impact_items" not in serialized
    assert not any(page["display"] == "Cost" for page in _pages())
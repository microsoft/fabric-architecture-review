# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Monitoring apps never consume review slots or become review subjects."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from collectors import _http, scanner_api, workspace_inventory, workspace_scope
from collectors._common import filter_workspaces_by_scope
from orchestration import sources
from orchestration.deployment import DEFAULTS
from orchestration.runtime import select_workspaces

METRICS = "11111111-1111-4111-8111-111111111111"
HOST = "22222222-2222-4222-8222-222222222222"
FUAM = "33333333-3333-4333-8333-333333333333"
FIRST = "44444444-4444-4444-8444-444444444444"
SECOND = "55555555-5555-4555-8555-555555555555"
RUN = "66666666-6666-4666-8666-666666666666"


@pytest.fixture
def metrics():
    return {
        "id": METRICS, "name": "Renamed monitoring workspace", "type": "Workspace",
        "datasets": [{"id": "model", "name": "Fabric Capacity Metrics"}],
        "reports": [
            {"name": "Fabric Capacity Metrics", "datasetId": "model"},
            {"name": "[App] Fabric Capacity Metrics", "datasetId": "model", "appId": "app"},
        ],
    }


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    monkeypatch.delenv("FAR_REVIEW_WORKSPACE_ID", raising=False)


def test_metrics_app_is_excluded_even_when_explicitly_requested(metrics, monkeypatch):
    monkeypatch.setenv("WORKSPACE_IDS", f"{METRICS},{FIRST}")
    assert filter_workspaces_by_scope([metrics, {"id": FIRST}]) == [{"id": FIRST}]
    assert not workspace_scope.is_excluded_workspace(metrics, operational=True)


def test_workspace_name_or_unrelated_reports_do_not_exclude_customer_workspaces(metrics):
    assert not workspace_scope.is_excluded_workspace({"id": FIRST, "name": "Fabric Capacity Metrics"})
    for change in ("no_report", "different_model", "different_report", "different_dataset"):
        row = deepcopy(metrics)
        row["reports"] = row["reports"][1:]
        if change == "no_report":
            row["reports"] = []
        elif change == "different_model":
            row["datasets"][0]["name"] = "Customer model"
        elif change == "different_report":
            row["reports"][0]["name"] = "[App] Customer report"
        else:
            row["reports"][0]["datasetId"] = "other"
        assert not workspace_scope.is_excluded_workspace(row)


def test_exclusion_survives_metadata_persistence_and_filters_legacy_findings(metrics, tmp_path):
    workspace_scope._persist_scope(tmp_path, [metrics, {"id": FIRST}], [])
    assert workspace_scope.excluded_workspace_ids(tmp_path) == {METRICS}
    assert workspace_scope.excluded_workspace_ids(tmp_path, operational=True) == set()
    persisted = json.loads((tmp_path / "review_scope.json").read_text())
    assert persisted["workspaces"][0]["isCapacityMetricsWorkspace"] is True
    assert "reports" not in persisted["workspaces"][0]
    assert workspace_scope.excluded_workspace_identities([metrics]) == [
        {"id": METRICS, "type": "Workspace", "isCapacityMetricsWorkspace": True},
    ]
    payload = {
        "workspaces": [{"id": METRICS}, {"id": FIRST}],
        "findings": [{"evidence": {"workspace_id": METRICS}}, {"evidence": {"workspace_id": FIRST}}],
        "models": [{"id": "model", "workspaceId": METRICS}],
    }
    filtered = workspace_scope.filter_review_payload(payload, tmp_path)
    assert filtered["workspaces"] == [{"id": FIRST}]
    assert filtered["findings"] == [{"evidence": {"workspace_id": FIRST}}]
    assert filtered["models"] == []


def test_current_metadata_can_remove_a_historical_metrics_app_exclusion(metrics, tmp_path):
    workspace_scope._persist_scope(tmp_path, [metrics], [])
    old = json.loads((tmp_path / "review_scope.json").read_text())["workspaces"]
    (tmp_path / "workspace_inventory.json").write_text(json.dumps({"excludedWorkspaces": old}))
    workspace_scope._persist_scope(tmp_path, [*old, {"id": METRICS, "datasets": [], "reports": []}], [])
    assert workspace_scope.excluded_workspace_ids(tmp_path) == set()


def test_host_is_excluded_from_historical_findings_without_inventory(monkeypatch, tmp_path):
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", HOST)
    monkeypatch.setenv("WORKSPACE_IDS", HOST)
    payload = {"findings": [{"evidence": {"workspace_id": HOST}}]}
    assert workspace_scope.filter_review_payload(payload, tmp_path)["findings"] == []


@pytest.mark.parametrize("scoped", [False, True])
def test_standard_inventory_never_probes_metrics_app_items_or_roles(metrics, monkeypatch, tmp_path, scoped):
    if scoped:
        monkeypatch.setenv("WORKSPACE_IDS", f"{METRICS},{FIRST}")
    monkeypatch.setattr(workspace_inventory, "get_default_provider", Mock())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", Mock(return_value=([metrics, {"id": FIRST}], True)))
    items, roles = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(workspace_inventory, "_list_items", items)
    monkeypatch.setattr(workspace_inventory, "_list_users", roles)
    result = json.loads(workspace_inventory.collect(tmp_path).read_text())
    assert [w["id"] for w in result["workspaces"]] == [FIRST]
    assert [call.args[1] for call in items.call_args_list] == [FIRST]
    assert [call.args[1] for call in roles.call_args_list] == [FIRST]
    assert result["excludedWorkspaces"][0]["isCapacityMetricsWorkspace"] is True


def test_scanner_does_not_submit_metrics_app_workspace(metrics, monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_IDS", METRICS)
    monkeypatch.setattr(scanner_api, "get_default_provider", Mock())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", Mock(return_value=[METRICS]))
    monkeypatch.setattr(_http, "collect_workspace_groups", Mock(return_value=[metrics]))
    scan = Mock(side_effect=AssertionError("Capacity Metrics must not be scanned"))
    monkeypatch.setattr(scanner_api, "_start_scan", scan)
    result = json.loads(scanner_api.collect(tmp_path).read_text())
    assert result["collectionComplete"] is True
    assert result["_meta"]["workspaces_eligible"] == 0
    scan.assert_not_called()


def test_all_candidates_excluded_safely_skips_the_review(metrics, monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "collect_workspace_groups", Mock(return_value=[metrics, {"id": HOST}]))
    monkeypatch.setattr(sources, "query_rows", Mock(return_value=[
        {"workspace_id": METRICS, "metric_value": 100, "invalid_values": 0},
        {"workspace_id": HOST, "metric_value": 90, "invalid_values": 0},
    ]))
    client = Mock()
    client.get_json.return_value = {"id": FUAM, "displayName": "FUAM"}
    config = {
        **DEFAULTS, "CHILD_WORKSPACE_ID": HOST, "CHILD_PIPELINE_ID": RUN,
        "FUAM_WORKSPACE_ID": FUAM, "FUAM_LAKEHOUSE_ID": FUAM, "WORKSPACE_IDS": f"{METRICS},{HOST}",
    }
    result = select_workspaces(
        config, RUN, read_source=lambda c: sources.read_source(c, client), state_dir=tmp_path,
    )
    assert result["workspace_count"] == 0
    assert result["workspace_ids"] == ""
    assert result["status"] == "skipped_no_candidates"


@pytest.mark.parametrize("metric", ["cu_seconds", "recorded_throttling_minutes"])
@pytest.mark.parametrize("allowlist", ["", f"{HOST},{METRICS},{FUAM},{FIRST},{SECOND}"])
def test_targeted_selection_refills_slots_in_order(metrics, monkeypatch, tmp_path, metric, allowlist):
    groups = [metrics, *({"id": wid} for wid in (HOST, FUAM, FIRST, SECOND))]
    monkeypatch.setattr(sources, "collect_workspace_groups", Mock(return_value=groups))
    rows = [
        {"workspace_id": wid, "workspace_name": wid, "metric_value": value, "invalid_values": 0}
        for wid, value in ((METRICS, 100), (HOST, 90), (FUAM, 80), (FIRST, 70), (SECOND, 60))
    ]
    monkeypatch.setattr(sources, "query_rows", Mock(return_value=rows))
    client = Mock()
    client.get_json.return_value = {"id": FUAM, "displayName": "FUAM"}
    config = {
        **DEFAULTS, "CHILD_WORKSPACE_ID": HOST, "CHILD_PIPELINE_ID": RUN,
        "FUAM_WORKSPACE_ID": FUAM, "FUAM_LAKEHOUSE_ID": FUAM,
        "TOP_N": "2", "WORKSPACE_IDS": allowlist, "RANKING_METRIC": metric,
    }
    selected = select_workspaces(
        config, RUN, read_source=lambda c: sources.read_source(c, client), state_dir=tmp_path,
    )
    assert selected["workspace_ids"] == f"{FIRST},{SECOND}"
    assert selected["workspace_count"] == 2
    audit = json.loads((tmp_path / f"{RUN}.json").read_text())
    assert audit["workspace_inventory"]["unsupported_ids"] == [METRICS]


def test_admin_group_discovery_requests_app_identity_metadata(monkeypatch, metrics):
    get = Mock(return_value={"value": [metrics]})
    monkeypatch.setattr(_http, "get_json", get)
    assert _http.collect_workspace_groups("https://api.powerbi.com/v1.0/myorg/admin/groups", {}) == [metrics]
    assert get.call_args.kwargs["params"]["$expand"] == "datasets,reports"


def test_member_group_discovery_reads_app_identity_metadata(monkeypatch, metrics):
    monkeypatch.setattr(_http, "get_json", Mock(return_value={"value": [{"id": METRICS}]}))
    items = Mock(side_effect=[metrics["datasets"], metrics["reports"][:1]])
    monkeypatch.setattr(_http, "collect_value", items)
    groups = _http.collect_workspace_groups("https://api.powerbi.com/v1.0/myorg/groups", {})
    assert workspace_scope.is_excluded_workspace(groups[0])
    assert [call.args[0].rsplit("/", 1)[-1] for call in items.call_args_list] == ["datasets", "reports"]


def test_unreadable_app_metadata_is_not_silently_eligible(monkeypatch):
    monkeypatch.setattr(_http, "get_json", Mock(return_value={"value": [{"id": METRICS}]}))
    monkeypatch.setattr(_http, "collect_value", Mock(side_effect=_http.HttpError("metadata denied")))
    with pytest.raises(_http.HttpError, match="metadata denied"):
        _http.collect_workspace_groups("https://api.powerbi.com/v1.0/myorg/groups", {})

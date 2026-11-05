# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Connected source workspaces supply evidence but are never review targets."""
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from collectors import far_collection, far_evidence, scanner_api, workspace_inventory, workspace_scope
from collectors._common import filter_workspaces_by_scope
from orchestration.deployment import DEFAULTS
from orchestration.review_config import activate, capture, local_configuration, targeting_source
from orchestration.runtime import select_workspaces
from orchestration import sources
from tests.test_access_grant import M1, WS, Settings, definitions, make, model

FUAM = WS
FCA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BUSINESS = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
BUSINESS2 = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
HOST = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ITEM = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
ROWS = [{"id": wid, "name": name} for wid, name in (
    (FUAM, "Renamed monitoring"), (FCA, "Financial source"), (BUSINESS, "FCA"),
)]


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", HOST)
    config = local_configuration()
    for name, wid in (("fuam", FUAM), ("fca", FCA)):
        config["sources"][name].update(enabled=True, workspace_id=wid.upper(), lakehouse_id=ITEM)
    path = capture(tmp_path / "config", config)
    return config, activate(path)


def test_sources_excluded_by_id_even_when_allowlisted(configured, monkeypatch):
    monkeypatch.setenv("WORKSPACE_IDS", f"{FUAM},{FCA},{BUSINESS}")
    assert filter_workspaces_by_scope(ROWS) == [ROWS[2]]
    monkeypatch.setenv("WORKSPACE_IDS", f"{FUAM},{FCA}")
    assert filter_workspaces_by_scope(ROWS) == []
    assert not workspace_scope.is_excluded_workspace(ROWS[0], operational=True)
    assert not workspace_scope.is_excluded_workspace(ROWS[1], operational=True)


def test_disabled_and_shared_sources_refresh_scope(configured, tmp_path):
    config, _ = configured
    config["sources"]["fca"]["workspace_id"] = FUAM
    activate(capture(tmp_path / "shared", config))
    assert workspace_scope.source_workspace_ids() == {FUAM}
    config["sources"]["fuam"]["enabled"] = False
    config["sources"]["fca"]["enabled"] = False
    activate(capture(tmp_path / "disabled", config))
    assert workspace_scope.source_workspace_ids() == set()
    assert filter_workspaces_by_scope(ROWS) == ROWS


def test_source_exclusions_survive_id_only_downstream_evidence(configured, tmp_path, monkeypatch):
    workspace_scope._persist_scope(tmp_path, ROWS, [])
    monkeypatch.delenv(workspace_scope.SOURCE_WORKSPACES_ENV)
    assert workspace_scope.excluded_workspace_ids(tmp_path) == {HOST, FUAM, FCA}
    assert workspace_scope.excluded_workspace_ids(tmp_path, operational=True) == {HOST}
    payload = {"workspaces": ROWS, "notebooks": [
        {"id": ITEM, "workspaceId": wid} for wid in (FUAM, FCA, BUSINESS)
    ]}
    filtered = workspace_scope.filter_review_payload(payload, tmp_path)
    assert filtered["workspaces"] == [ROWS[2]]
    assert filtered["notebooks"] == [{"id": ITEM, "workspaceId": BUSINESS}]


def test_plan_excludes_sources_but_keeps_both_enrichment_connections(configured):
    _, snapshot = configured
    result = far_collection.plan(snapshot, ROWS)
    assert result["workspace_ids"] == [BUSINESS]
    assert set(result["excluded_source_workspace_ids"]) == {FUAM, FCA}
    assert result["enrichment_sources"] == ["fuam", "fca"]
    assert all(not ({FUAM, FCA} & set(row["workspace_ids"])) for row in result["source_resolutions"])
    assert far_collection.plan(snapshot, ROWS[:2])["collectors"] == []


def test_both_sources_still_enrich_business_workspaces(configured, tmp_path):
    _, snapshot = configured
    capacity = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    arm = f"/subscriptions/{HOST}/resourcegroups/test/providers/microsoft.fabric/capacities/test"
    snapshot["configuration"]["profiles"]["capacities"] = [{"id": capacity, "arm_resource_id": arm}]
    now = datetime.now(timezone.utc)
    calls = []

    def query(source, statement):
        calls.append(source)
        if source == "fca":
            if "FROM dbo.focus" in statement:
                return [{"ResourceId": arm, "Tags": '{"owner": "team"}'}]
            return [{"ResourceId": arm, "ResourceName": "test", "RegionId": "westeurope", "ResourceKey": 1}]
        columns = far_evidence.CONTRACTS["workspaces"][1]
        if statement.startswith(f"SELECT {', '.join(columns)} FROM dbo.workspaces "):
            return [{
                "WorkspaceId": BUSINESS, "WorkspaceName": "Business", "CapacityId": capacity,
                "Description": "Business workspace", "fuam_modified_at": now,
            }]
        return []

    evidence = far_evidence.bind(snapshot, [{"id": BUSINESS, "capacityId": capacity}], query, now=now)
    filtered = workspace_scope.filter_review_payload(evidence, tmp_path)
    assert set(calls) == {"fuam", "fca"}
    assert filtered["tables"]["far_evidence_workspaces"][0]["source_workspace_id"] == FUAM
    assert filtered["tables"]["far_evidence_capacity_resources"][0]["source_workspace_id"] == FCA
    assert filtered["rule_facts"]["capacity_tags"]["rows"] == [{"capacity_id": capacity, "tag_names": ["owner"]}]


def test_inventory_still_uses_fuam_without_reading_source_items(configured, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_inventory, "get_default_provider", lambda: object())
    monkeypatch.setattr(workspace_inventory, "_list_workspaces", lambda _: (ROWS, True))
    monkeypatch.setattr(workspace_inventory, "resolve_workspace_scope", lambda rows, *args: rows)
    items = Mock(return_value=[])
    users = Mock(return_value=[])
    monkeypatch.setattr(workspace_inventory, "_list_items", items)
    monkeypatch.setattr(workspace_inventory, "_list_users", users)
    reader = Mock(return_value=None)
    path = workspace_inventory.collect(tmp_path, fuam_reader=reader)
    reader.assert_called_once_with([BUSINESS])
    assert [row["id"] for row in json.loads(path.read_text())["workspaces"]] == [BUSINESS]
    assert all(call.args[1] == BUSINESS for call in items.call_args_list)
    assert items.call_count == 1


def test_scanner_never_submits_source_workspaces(configured, tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_api, "get_default_provider", lambda: object())
    monkeypatch.setattr(scanner_api, "_modified_workspaces", lambda _: [FUAM, FCA])
    monkeypatch.setattr(scanner_api._http, "collect_workspace_groups", lambda *args: ROWS[:2])
    monkeypatch.setattr(scanner_api, "resolve_workspace_scope", lambda rows, *args: rows)
    request = Mock(side_effect=AssertionError("Source workspace was scanned"))
    monkeypatch.setattr(scanner_api._http, "request", request)
    path = scanner_api.collect(tmp_path)
    assert json.loads(path.read_text())["workspaces"] == []
    request.assert_not_called()


@pytest.mark.parametrize("scope", ["declared_models", "required_evidence"])
def test_sources_never_receive_review_grants(configured, tmp_path, scope):
    grant, holder, sleeps, _ = make(tmp_path, settings=Settings(True, "auto", scope))
    path = definitions(tmp_path, model(M1, workspace=FUAM))
    refetch = Mock(side_effect=AssertionError("Source model was retried"))
    if scope == "declared_models":
        grant.remediate(path, frozenset({M1}), [{"id": FUAM}], refetch)
    else:
        grant.retry_collection(lambda: path, {FUAM}, "semantic_model_definitions")
    assert not holder
    assert sleeps == []
    refetch.assert_not_called()


@pytest.mark.parametrize("fca_enabled", [True, False])
@pytest.mark.parametrize("top_n", [1, 2])
@pytest.mark.parametrize("metric", ["cu_seconds", "recorded_throttling_minutes"])
def test_targeting_excludes_live_sources_before_top_n(configured, tmp_path, monkeypatch, fca_enabled, top_n, metric):
    config, _ = configured
    config["sources"]["fca"]["enabled"] = fca_enabled
    values = {f"{name}__{key}": value for name, source in config["sources"].items() for key, value in source.items()}
    connection = targeting_source(lambda _: SimpleNamespace(getVariable=values.__getitem__), "connections")
    options = {
        **DEFAULTS, **connection, "CHILD_WORKSPACE_ID": HOST, "CHILD_PIPELINE_ID": ITEM,
        "WORKSPACE_IDS": f"{FUAM},{FCA},{BUSINESS},{BUSINESS2}", "TOP_N": str(top_n),
        "RANKING_METRIC": metric,
    }
    rows = [
        {"workspace_id": FUAM, "metric_value": 300},
        {"workspace_id": FCA, "metric_value": 200},
        {"workspace_id": BUSINESS, "metric_value": 100},
        {"workspace_id": BUSINESS2, "metric_value": 50},
    ]
    monkeypatch.setattr(sources, "collect_workspace_groups", lambda *args: [*ROWS, {"id": BUSINESS2}])

    def current_rows(options):
        matched, _ = sources.match_current_workspaces(rows, SimpleNamespace(token=lambda: "synthetic"))
        return "fuam", matched

    read = Mock(side_effect=current_rows)
    result = select_workspaces(options, ITEM, read_source=read, state_dir=tmp_path / "selection")
    expected = ([BUSINESS, BUSINESS2] if fca_enabled else [FCA, BUSINESS])[:top_n]
    assert result["workspace_ids"] == ",".join(expected)
    assert result["workspace_count"] == top_n
    read.assert_called_once_with(options)
    assert workspace_scope.source_workspace_ids() == {FUAM, FCA}
    audit = json.loads((tmp_path / "selection" / f"{ITEM}.json").read_text())
    assert set(audit["excluded_source_workspace_ids"]) == ({FUAM, FCA} if fca_enabled else {FUAM})


def test_targeting_skips_when_only_source_candidates_remain(configured, tmp_path):
    options = {
        **DEFAULTS, "FUAM_WORKSPACE_ID": FUAM, "SOURCE_WORKSPACE_IDS": f"{FUAM},{FCA}",
        "CHILD_WORKSPACE_ID": HOST, "CHILD_PIPELINE_ID": ITEM, "TOP_N": "2",
    }
    result = select_workspaces(options, ITEM, read_source=lambda _: ("fuam", [
        {"workspace_id": FUAM, "metric_value": 300}, {"workspace_id": FCA, "metric_value": 200},
    ]), state_dir=tmp_path / "selection")
    assert result["status"] == "skipped_no_candidates"
    assert result["workspace_count"] == 0
    assert result["workspace_ids"] == ""


def test_source_scope_is_restored_after_a_selection_failure(configured, tmp_path):
    options = {
        **DEFAULTS, "FUAM_WORKSPACE_ID": FUAM, "CHILD_WORKSPACE_ID": HOST, "CHILD_PIPELINE_ID": ITEM,
    }
    with pytest.raises(RuntimeError, match="source unavailable"):
        select_workspaces(options, ITEM, read_source=Mock(side_effect=RuntimeError("source unavailable")),
                          state_dir=tmp_path / "selection")
    assert workspace_scope.source_workspace_ids() == {FUAM, FCA}

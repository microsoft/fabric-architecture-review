# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Tenant settings: FUAM first, the API for group detail, and the full API call when FUAM cannot be used."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from collectors import far_collection, fuam_sourced
from orchestration.review_config import ROOT, digest, local_configuration

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def snapshot(enabled=True, max_age=24):
    configuration = local_configuration(ROOT / "config")
    configuration["sources"]["fuam"].update(
        enabled=enabled, workspace_id=WORKSPACE, lakehouse_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        max_age_hours=max_age)
    return {"configuration": configuration, "sha256": digest(configuration), "captured_at_utc": NOW.isoformat()}


def setting(name, **extra):
    return {"settingName": name, "title": name + " title", "tenantSettingGroup": "Group", "enabled": True,
            "canSpecifySecurityGroups": True, "delegateToCapacity": None, "delegateToDomain": False,
            "delegateToWorkspace": None, "TransferDateTime": NOW - timedelta(hours=3), **extra}


def api_entry(name, enabled_groups=0, excluded=0, enabled=True):
    return {"settingName": name, "title": name + " (api)", "tenantSettingGroup": "Group", "enabled": enabled,
            "canSpecifySecurityGroups": True, "enabledSecurityGroupCount": enabled_groups,
            "excludedSecurityGroupCount": excluded}


def reader(rows=None, groups=None, fail=False):
    calls = []

    def query(source, statement):
        calls.append((source, statement))
        if fail:
            raise RuntimeError("driver text that must not be stored")
        return list(groups or []) if "tenant_settings_enabled_security_groups" in statement else list(rows or [])
    query.calls = calls
    return query


def api_returning(entries):
    def fetch():
        return list(entries)
    return fetch


def api_denied():
    raise PermissionError("403 Forbidden with details that must not be stored")


def read(tmp_path, api=None, **kwargs):
    path = fuam_sourced.collect("tenant_settings", tmp_path, snapshot(), reader(**kwargs), NOW, api_fetch=api)
    return json.loads(path.read_text(encoding="utf-8"))


def test_fuam_alone_gives_the_api_collector_shape_when_the_api_is_not_available(tmp_path):
    data = read(tmp_path, api=api_denied, rows=[setting("PublishToWeb"), setting("ExternalSharingV2", enabled=False)],
                groups=[{"settingName": "PublishToWeb", "enabledGroups": 2}])
    assert data["collectionComplete"] is True and data["dataSource"] == "fuam"
    assert data["groupDetail"] == "excluded_groups_unavailable"
    by_name = {row["settingName"]: row for row in data["tenantSettings"]}
    assert by_name["PublishToWeb"]["enabledSecurityGroupCount"] == 2
    assert by_name["ExternalSharingV2"]["enabledSecurityGroupCount"] == 0 and by_name["ExternalSharingV2"]["enabled"] is False
    assert by_name["PublishToWeb"]["excludedSecurityGroupCount"] is None
    assert "delegateToCapacity" not in by_name["PublishToWeb"] and by_name["PublishToWeb"]["delegateToDomain"] is False


def test_the_api_adds_group_detail_to_a_fresh_fuam_snapshot(tmp_path):
    data = read(tmp_path, api=api_returning([api_entry("PublishToWeb", enabled_groups=2, excluded=1)]),
                rows=[setting("PublishToWeb"), setting("OtherSetting")],
                groups=[{"settingName": "PublishToWeb", "enabledGroups": 1}])
    assert data["dataSource"] == "fuam+api" and data["collectionComplete"] is True
    by_name = {row["settingName"]: row for row in data["tenantSettings"]}
    assert by_name["PublishToWeb"]["enabledSecurityGroupCount"] == 2 and by_name["PublishToWeb"]["excludedSecurityGroupCount"] == 1
    assert by_name["OtherSetting"]["excludedSecurityGroupCount"] is None  # only in FUAM: kept
    assert len(data["tenantSettings"]) == 2


def test_a_setting_only_the_live_api_knows_is_included(tmp_path):
    data = read(tmp_path, api=api_returning([api_entry("BrandNew")]), rows=[setting("PublishToWeb")])
    assert {row["settingName"] for row in data["tenantSettings"]} == {"PublishToWeb", "BrandNew"}


@pytest.mark.parametrize("kwargs, reason", [
    (dict(rows=[setting("A", TransferDateTime=NOW - timedelta(hours=49))]), "source_data_stale"),
    (dict(rows=[]), "no_transfer_snapshot"),
    (dict(fail=True), "query_failed"),
    (dict(rows=[{"settingName": "x"}]), "incompatible_schema"),
    (dict(rows=[setting("A"), setting("A")]), "incompatible_identity"),
])
def test_when_fuam_cannot_be_used_the_full_api_call_is_the_fallback(tmp_path, kwargs, reason):
    data = read(tmp_path, api=api_returning([api_entry("PublishToWeb", excluded=3)]), **kwargs)
    assert data["dataSource"] == "api" and data["collectionComplete"] is True
    assert data["reason"] == reason and data["fallback"] == "full_api_call"
    assert data["tenantSettings"][0]["excludedSecurityGroupCount"] == 3


@pytest.mark.parametrize("kwargs, reason", [
    (dict(rows=[setting("A", TransferDateTime=NOW - timedelta(hours=49))]), "source_data_stale"),
    (dict(fail=True), "query_failed"),
])
def test_when_both_fail_the_evidence_is_missing_with_the_reason(tmp_path, kwargs, reason):
    data = read(tmp_path, api=api_denied, **kwargs)
    assert data["collectionComplete"] is False and data["reason"] == reason and data["tenantSettings"] == []
    assert "details that must not be stored" not in json.dumps(data) and "driver text" not in json.dumps(data)


@pytest.mark.parametrize("age_hours, usable", [(3, True), (47, True), (49, False)])
def test_staleness_uses_the_freshness_limit_plus_one_day_for_daily_transfers(tmp_path, age_hours, usable):
    data = read(tmp_path, api=api_denied, rows=[setting("PublishToWeb", TransferDateTime=NOW - timedelta(hours=age_hours))])
    assert data["collectionComplete"] is usable


def test_only_fuam_is_queried_and_only_for_the_latest_snapshot(tmp_path):
    query = reader(rows=[setting("PublishToWeb")])
    fuam_sourced.collect("tenant_settings", tmp_path, snapshot(), query, NOW)
    assert {source for source, _ in query.calls} == {"fuam"}
    assert all("MAX(TransferDateTime)" in statement for _, statement in query.calls)


def test_only_connected_fuam_supplies_tenant_settings():
    assert fuam_sourced.supplies(snapshot(True), "tenant_settings") is True
    assert fuam_sourced.supplies(snapshot(False), "tenant_settings") is False
    assert fuam_sourced.supplies(snapshot(True), "git_integration") is False
    with pytest.raises(ValueError):
        fuam_sourced.collect("git_integration", ".", snapshot(), reader(), NOW)


def test_the_plan_says_how_tenant_settings_are_sourced():
    def resolution(enabled):
        return next(row for row in far_collection.plan(snapshot(enabled), [{"id": WORKSPACE}])["source_resolutions"]
                    if row["collector"] == "tenant_settings")
    assert resolution(True)["resolution"] == "fuam" and "admin API" in resolution(True)["reason"]
    assert resolution(False)["resolution"] == "native_architecture"

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from reports.owner.access import (
    admin_entitlements, canonical_id, collect_entitlements, refresh_owner_model, synchronize_access,
)


WORKSPACE = "00000000-0000-4000-8000-000000000001"
SECOND = "00000000-0000-4000-8000-000000000002"
USER = "00000000-0000-4000-8000-000000000003"
MODEL = "00000000-0000-4000-8000-000000000004"
REFRESH = "00000000-0000-4000-8000-000000000005"
NOW = datetime(2026, 4, 2, 12, tzinfo=timezone.utc)


def principal(**kwargs):
    return {"principalType": "User", "groupUserAccessRight": "Admin", "graphId": USER,
            "emailAddress": "mailbox-not-upn@example.com", "identifier": "not-an-object-id", **kwargs}


def response(payload, status=200, headers=None):
    return Mock(status_code=status, json=Mock(return_value=payload), headers=headers or {})


def test_grants_use_graph_id_not_mailbox_and_expire_in_24h():
    rows = admin_entitlements(WORKSPACE.upper(), {"value": [principal(), principal()]}, NOW)
    assert rows == [{
        "workspace_id": WORKSPACE, "principal_object_id": USER,
        "refreshed_at": NOW, "expires_at": NOW + timedelta(hours=24),
    }]
    assert "example.com" not in str(rows)


@pytest.mark.parametrize("kind,role", [
    ("Group", "Admin"), ("App", "Admin"), ("None", "Admin"),
    ("User", "Member"), ("User", "Contributor"), ("User", "Viewer"), ("User", "None"),
])
def test_only_direct_user_admin_grants(kind, role):
    assert admin_entitlements(WORKSPACE, {"value": [
        principal(principalType=kind, groupUserAccessRight=role),
    ]}, NOW) == []


@pytest.mark.parametrize("value", [None, "", "mail@example.com", "00000000-0000-0000-0000-000000000000"])
def test_missing_or_invalid_graph_id_never_falls_back_to_email(value):
    with pytest.raises(ValueError):
        admin_entitlements(WORKSPACE, {"value": [principal(graphId=value)]}, NOW)


@pytest.mark.parametrize("payload", [
    {}, [], {"value": None}, {"error": {}, "value": []},
    {"value": [], "@odata.nextLink": "https://example.com"},
    {"value": [], "continuationToken": "more"}, {"value": [None]},
    {"value": [principal(principalType="future-type")]},
    {"value": [principal(groupUserAccessRight="Owner")]},
])
def test_partial_or_malformed_membership_fails(payload):
    with pytest.raises(ValueError):
        admin_entitlements(WORKSPACE, payload, NOW)


def test_utc_timestamp_required():
    with pytest.raises(ValueError, match="UTC"):
        admin_entitlements(WORKSPACE, {"value": []}, NOW.replace(tzinfo=None))


def test_refresh_all_workspaces_including_unselected_history_and_throttle():
    transport = Mock(return_value=response({"value": [principal()]}))
    sleep = Mock()
    rows = collect_entitlements(
        [SECOND, WORKSPACE, SECOND], lambda: "token",
        transport=transport, sleep=sleep, now=lambda: NOW,
    )
    assert {row["workspace_id"] for row in rows} == {WORKSPACE, SECOND}
    assert transport.call_count == 2
    sleep.assert_called_once_with(20)
    for call in transport.call_args_list:
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["timeout"] == 120


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_http_failure_never_publishes_grants(status):
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        collect_entitlements([WORKSPACE], lambda: "token",
                             transport=Mock(return_value=response({}, status)), now=lambda: NOW)


@pytest.mark.parametrize("missing", [[WORKSPACE], [WORKSPACE, SECOND]])
def test_404_denies_only_unavailable_workspaces_and_warns(missing, caplog):
    denied = []
    transport = Mock(side_effect=lambda url, **_: response(
        {"value": [principal()]}, 404 if any(workspace in url for workspace in missing) else 200,
    ))
    sleep = Mock()
    token = Mock(return_value="synthetic-token")
    rows = collect_entitlements(
        [WORKSPACE, SECOND], token, transport=transport, sleep=sleep,
        now=lambda: NOW, on_unavailable=denied.append,
    )
    assert {row["workspace_id"] for row in rows} == {WORKSPACE, SECOND} - set(missing)
    assert denied == missing
    assert token.call_count == 2
    sleep.assert_called_once_with(20)
    for workspace in missing:
        assert workspace in caplog.text
    assert "HTTP 404" in caplog.text
    assert "no entitlements" in caplog.text


def test_sync_replaces_old_grants_without_404_workspace(caplog):
    replace, refresh = Mock(), Mock()
    denied = []
    def collect(ids, token, **kwargs):
        return collect_entitlements(
            ids, token, transport=Mock(side_effect=[
                response({}, 404), response({"value": [principal()]}),
            ]), sleep=Mock(), now=lambda: NOW, **kwargs,
        )
    count = synchronize_access(
        [WORKSPACE, SECOND], lambda: "synthetic-token",
        replace=replace, refresh=refresh, collect=collect, on_unavailable=denied.append,
    )
    assert count == 1
    assert replace.call_args_list[0].args == ([],)
    assert replace.call_args_list[1].args == (
        admin_entitlements(SECOND, {"value": [principal()]}, NOW),
    )
    assert refresh.call_count == 2
    assert denied == [WORKSPACE]
    assert "HTTP 404" in caplog.text


def test_auth_failure_after_404_still_blocks_publication():
    replace, refresh = Mock(), Mock()
    def collect(ids, token):
        return collect_entitlements(
            ids, token, transport=Mock(side_effect=[response({}, 404), response({}, 403)]),
            sleep=Mock(), now=lambda: NOW,
        )
    with pytest.raises(RuntimeError, match="HTTP 403"):
        synchronize_access(
            [WORKSPACE, SECOND], lambda: "synthetic-token",
            replace=replace, refresh=refresh, collect=collect,
        )
    replace.assert_called_once_with([])
    refresh.assert_called_once_with()


def test_sync_invalidates_before_reads_and_replaces_never_appends():
    calls = []
    rows = admin_entitlements(WORKSPACE, {"value": [principal()]}, NOW)
    def collect(ids, token):
        calls.append(("collect", ids))
        return rows
    count = synchronize_access(
        [WORKSPACE], lambda: "token",
        replace=lambda values: calls.append(("replace", values)),
        refresh=lambda: calls.append(("refresh",)),
        collect=collect,
    )
    assert count == 1
    assert calls == [("replace", []), ("refresh",), ("collect", [WORKSPACE]),
                     ("replace", rows), ("refresh",)]


def test_revoked_admin_publishes_empty_snapshot():
    replace = Mock()
    refresh = Mock()
    assert synchronize_access(
        [WORKSPACE], lambda: "token", replace=replace, refresh=refresh,
        collect=lambda ids, token: [],
    ) == 0
    assert all(call.args == ([],) for call in replace.call_args_list)
    assert refresh.call_count == 2


def test_failed_sync_does_not_restore_old_grants():
    replace, refresh = Mock(), Mock()
    with pytest.raises(RuntimeError, match="lookup failed"):
        synchronize_access([WORKSPACE], lambda: "token", replace=replace, refresh=refresh,
                           collect=Mock(side_effect=RuntimeError("lookup failed")))
    replace.assert_called_once_with([])
    refresh.assert_called_once_with()


def test_invalidation_refresh_failure_stops_lookup():
    collect = Mock()
    with pytest.raises(RuntimeError, match="refresh failed"):
        synchronize_access([WORKSPACE], lambda: "token", replace=Mock(),
                           refresh=Mock(side_effect=RuntimeError("refresh failed")), collect=collect)
    collect.assert_not_called()


def test_expired_long_sync_never_returns_grants():
    clock = Mock(side_effect=[NOW, NOW + timedelta(hours=24)])
    with pytest.raises(RuntimeError, match="lifetime"):
        collect_entitlements([], lambda: "token", now=clock)


def test_refresh_waits_until_completed_without_following_arbitrary_urls():
    base = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE}/datasets/{MODEL}/refreshes"
    transport = Mock(side_effect=[
        response({}, 202, {"Location": base + "/" + REFRESH}),
        response({"status": "InProgress"}, 202), response({"status": "Completed"}),
    ])
    sleep = Mock()
    refresh_owner_model(WORKSPACE, MODEL, lambda: "token", transport=transport, sleep=sleep)
    sleep.assert_called_once_with(5)
    assert transport.call_args_list[0].kwargs["json"] == {"type": "full", "commitMode": "transactional"}
    assert all(call.kwargs["allow_redirects"] is False for call in transport.call_args_list)


@pytest.mark.parametrize("location", [
    "https://attacker.example/refreshes/" + REFRESH,
    f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE}/datasets/{MODEL}/refreshes/{REFRESH}?token=x",
    "",
])
def test_refresh_rejects_untrusted_poll_locations(location):
    transport = Mock(return_value=response({}, 202, {"Location": location}))
    with pytest.raises((ValueError, RuntimeError)):
        refresh_owner_model(WORKSPACE, MODEL, lambda: "token", transport=transport)
    assert transport.call_count == 1


@pytest.mark.parametrize("status", ["Failed", "Cancelled", "Disabled", None])
def test_refresh_failures_are_explicit(status):
    base = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE}/datasets/{MODEL}/refreshes"
    transport = Mock(side_effect=[
        response({}, 202, {"Location": base + "/" + REFRESH}), response({"status": status}),
    ])
    with pytest.raises(RuntimeError, match="did not complete"):
        refresh_owner_model(WORKSPACE, MODEL, lambda: "token", transport=transport)


def test_empty_identity_rejected():
    with pytest.raises(ValueError):
        canonical_id("00000000-0000-0000-0000-000000000000")

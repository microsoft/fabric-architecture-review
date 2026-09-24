# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Current direct-user admin entitlements; never grants report permissions.

The generated access-sync notebook embeds this dependency-light module so its
daily schedule does not download a moving repository revision.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any
from uuid import UUID

import requests


def canonical_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("An object or workspace GUID is required.")
    result = UUID(value.strip())
    if result.int == 0:
        raise ValueError("The empty GUID is not an identity.")
    return str(result)


def admin_entitlements(
    workspace_id: str, payload: Any, refreshed_at: datetime,
) -> list[dict[str, Any]]:
    """Use graphId, NOT mailbox/UPN/name, for the resource-tenant identity."""
    workspace_id = canonical_id(workspace_id)
    if refreshed_at.tzinfo is None or refreshed_at.utcoffset() != timedelta(0):
        raise ValueError("Entitlement timestamps must be timezone-aware UTC.")
    if (
        not isinstance(payload, dict) or "error" in payload
        or not isinstance(payload.get("value"), list)
        or any(payload.get(key) for key in (
            "@odata.nextLink", "continuationUri", "continuationToken",
        ))
    ):
        raise ValueError("Admin API returned incomplete or invalid membership data.")
    principals: set[str] = set()
    for principal in payload["value"]:
        if (
            not isinstance(principal, dict)
            or principal.get("principalType") not in ("User", "Group", "App", "None")
            or principal.get("groupUserAccessRight") not in (
                "Admin", "Member", "Contributor", "Viewer", "None",
            )
        ):
            raise ValueError("Admin API returned an invalid principal.")
        if principal["principalType"] == "User" and principal["groupUserAccessRight"] == "Admin":
            principals.add(canonical_id(principal.get("graphId")))
    return [{
        "workspace_id": workspace_id,
        "principal_object_id": principal,
        "refreshed_at": refreshed_at,
        "expires_at": refreshed_at + timedelta(hours=24),
    } for principal in sorted(principals)]


def collect_entitlements(
    workspace_ids: Iterable[str], token: Callable[[], str], *,
    transport: Callable[..., Any] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_unavailable: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Read ALL reviewed workspaces, with no top-N or email-recipient filter."""
    ids = sorted({canonical_id(value) for value in workspace_ids})
    observed_at = now()
    rows: list[dict[str, Any]] = []
    for index, workspace_id in enumerate(ids):
        # The admin users endpoint permits at most 200 requests/hour.
        if index:
            sleep(20)
        response = transport(
            f"https://api.powerbi.com/v1.0/myorg/admin/groups/{workspace_id}/users",
            headers={"Authorization": "Bearer " + token()},
            timeout=120, allow_redirects=False,
        )
        if response.status_code == 404:
            logging.getLogger("far.owner.access").warning(
                "Owner access lookup returned HTTP 404 for workspace %s: no entitlements "
                "will be published for this workspace; review history is retained. "
                "Check workspace existence, tenant and execution identity.",
                workspace_id,
            )
            if on_unavailable is not None:
                on_unavailable(workspace_id)
            continue
        if response.status_code != 200:
            raise RuntimeError(
                f"Owner access sync failed for {workspace_id}: HTTP {response.status_code}. "
                "No new entitlements published; inspect permissions, throttling or deleted workspaces."
            )
        try:
            payload = response.json()
        except ValueError:
            raise ValueError("Owner access sync received invalid JSON.") from None
        rows.extend(admin_entitlements(workspace_id, payload, observed_at))
    if now() >= observed_at + timedelta(hours=24):
        raise RuntimeError("Owner access sync exceeded the entitlement lifetime.")
    return rows


def synchronize_access(
    workspace_ids: Iterable[str], token: Callable[[], str], *,
    replace: Callable[[list[dict[str, Any]]], None],
    refresh: Callable[[], None],
    collect: Callable[..., list[dict[str, Any]]] = collect_entitlements,
    on_unavailable: Callable[[str], None] | None = None,
) -> int:
    """Invalidate old grants first; publish one complete replacement snapshot.

    Both replacement and model reframing must succeed. No stale-grant fallback,
    incremental grant appending, permission changes or swallowed failures.
    """
    ids = list(workspace_ids)
    replace([])
    refresh()
    rows = (collect(ids, token, on_unavailable=on_unavailable)
            if on_unavailable is not None else collect(ids, token))
    replace(rows)
    refresh()
    return len(rows)


def refresh_owner_model(
    workspace_id: str, model_id: str, token: Callable[[], str], *,
    transport: Callable[..., Any] = requests.request,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout_seconds: int = 1800,
) -> None:
    """Reframe Direct Lake and wait for the enhanced refresh to complete."""
    base = (
        "https://api.powerbi.com/v1.0/myorg/groups/"
        f"{canonical_id(workspace_id)}/datasets/{canonical_id(model_id)}/refreshes"
    )
    response = transport(
        "POST", base, headers={"Authorization": "Bearer " + token()},
        json={"type": "full", "commitMode": "transactional"},
        timeout=120, allow_redirects=False,
    )
    if response.status_code != 202:
        raise RuntimeError(f"Owner model refresh was not accepted: HTTP {response.status_code}.")
    # Do not follow a returned arbitrary URL with a bearer token.
    location = response.headers.get("Location", "")
    if not location.startswith(base + "/"):
        raise RuntimeError("Owner model refresh returned an unexpected polling location.")
    refresh_id = canonical_id(location.removeprefix(base + "/"))
    deadline = clock() + timeout_seconds
    while clock() < deadline:
        response = transport(
            "GET", base + "/" + refresh_id,
            headers={"Authorization": "Bearer " + token()},
            timeout=120, allow_redirects=False,
        )
        if response.status_code not in (200, 202):
            raise RuntimeError(f"Owner model refresh status failed: HTTP {response.status_code}.")
        status = response.json().get("status")
        if status == "Completed":
            return
        if status not in ("Unknown", "NotStarted", "InProgress"):
            raise RuntimeError(f"Owner model refresh did not complete: status {status!r}.")
        sleep(5)
    raise TimeoutError("Owner model refresh timed out; no successful sync claimed.")

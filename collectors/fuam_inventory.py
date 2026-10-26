# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace items and members that the connected FUAM already holds.

The workspace inventory reads FUAM first and asks the APIs only for what FUAM does not cover:
every workspace when the FUAM snapshot is stale, missing or unreadable, and otherwise each
workspace (or part of one) for which FUAM returns no rows. FUAM holds a daily snapshot, so an item
created or a member added since the last transfer is not in it; the snapshot time is recorded so
that is visible. A workspace with no rows in FUAM is read from the API rather than assumed empty.

DATA SAFETY: item names, types and IDs, and workspace role assignments, exactly as the API returns
them. Item bodies are never read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from collectors.enrichment_facts import DAILY_GRACE_HOURS, _chunks

Query = Callable[[str, str], list[dict[str, Any]]]

ITEM_COLUMNS = {"id", "workspaceId", "type", "name", "description", "folderId"}
USER_COLUMNS = {"WorkspaceId", "GraphId", "PrincipalType", "UserType", "Identifier", "GroupUserAccessRight"}


@dataclass
class FuamInventory:
    """What FUAM returned for a review scope. ``reason`` is set when FUAM could not be used at all."""

    items: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    users: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    observed_at: str | None = None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.reason is None


def supplies(snapshot: dict[str, Any]) -> bool:
    return bool(snapshot["configuration"]["sources"]["fuam"]["enabled"])


def _guid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except ValueError:
        return None


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": _guid(row["id"]) or str(row["id"]).lower(), "displayName": row["name"], "type": row["type"],
            "description": row["description"], "folderId": row["folderId"], "workspaceId": str(row["workspaceId"]).lower()}


def _user(row: dict[str, Any]) -> dict[str, Any]:
    kind = row["PrincipalType"]
    # The administrator API identifies an application by its application ID, which FUAM keeps in Identifier.
    graph = row["Identifier"] if kind == "App" and row["Identifier"] else row["GraphId"]
    return {"principalType": kind, "graphId": graph, "identifier": row["Identifier"], "userType": row["UserType"],
            "groupUserAccessRight": row["GroupUserAccessRight"], "displayName": None, "emailAddress": None}


def read(snapshot: dict[str, Any], query: Query, workspace_ids: list[str], now: datetime) -> FuamInventory:
    """Items and members per workspace, never raising: a failure becomes a reason code."""
    connection = snapshot["configuration"]["sources"]["fuam"]
    ids = {guid for guid in map(_guid, workspace_ids) if guid}
    try:
        newest = query("fuam", "SELECT MAX(fuam_modified_at) AS newest FROM dbo.active_items")
        if len(newest) != 1 or set(newest[0]) != {"newest"}:
            return FuamInventory(reason="incompatible_schema")
        stamp = _utc(newest[0]["newest"])
        if stamp is None:
            return FuamInventory(reason="no_transfer_snapshot")
        if (now - stamp).total_seconds() / 3600 > connection["max_age_hours"] + DAILY_GRACE_HOURS:
            return FuamInventory(reason="source_data_stale")
        result = FuamInventory(observed_at=stamp.isoformat())
        for chunk in _chunks(ids):
            for row in query("fuam", (
                "SELECT id, workspaceId, type, name, description, folderId FROM dbo.active_items "
                f"WHERE fuam_deleted = 0 AND LOWER(workspaceId) IN ({chunk})"
            )):
                if set(row) != ITEM_COLUMNS:
                    return FuamInventory(reason="incompatible_schema")
                result.items.setdefault(str(row["workspaceId"]).lower(), []).append(_item(row))
            for row in query("fuam", (
                "SELECT WorkspaceId, GraphId, PrincipalType, UserType, Identifier, GroupUserAccessRight "
                f"FROM dbo.workspaces_scanned_users WHERE LOWER(WorkspaceId) IN ({chunk})"
            )):
                if set(row) != USER_COLUMNS:
                    return FuamInventory(reason="incompatible_schema")
                result.users.setdefault(str(row["WorkspaceId"]).lower(), []).append(_user(row))
        return result
    except Exception:
        # The failure text can name tables or endpoints, so only a code is kept.
        return FuamInventory(reason="query_failed")

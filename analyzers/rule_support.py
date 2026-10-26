# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared inputs and helpers for the rule evaluators."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Context:
    """Everything a rule may read: the frozen configuration, the reviewed workspaces and evidence."""

    snapshot: dict[str, Any]
    workspaces: list[dict[str, Any]]
    raw: dict[str, dict[str, Any]]
    enrichment: dict[str, Any] | None = field(default=None)

    @property
    def configuration(self) -> dict[str, Any]:
        return self.snapshot["configuration"]


def item_inventory_complete(workspace: dict[str, Any]) -> bool:
    return workspace.get("itemsCollectionStatus") == "collected" and isinstance(workspace.get("items"), list) and all(
        isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        and isinstance(item.get("type"), str) and item["type"] for item in workspace["items"]
    )


def items(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    entries = workspace.get("items")
    return [item for item in entries if isinstance(item, dict) and item.get("id")] if isinstance(entries, list) else []


MAX_LISTED_ITEMS = 200


def listed(item_ids) -> list[str]:
    """Item IDs a finding names: distinct, sorted, and capped so one workspace cannot flood the report.

    The counts beside the list always hold the true totals.
    """
    return sorted({str(item).lower() for item in item_ids if item})[:MAX_LISTED_ITEMS]


def rows(definitions: dict[str, Any], kind: str, wid: str) -> list[dict[str, Any]]:
    entries = definitions.get(kind, [])
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise ValueError(f"Invalid architectural definition inventory: {kind}")
    return [row for row in entries if str(row.get("workspaceId") or "").lower() == wid.lower()]


def scanner_workspace(ctx: Context, workspace_id: str) -> dict[str, Any] | None:
    """The scanner's view of a workspace, or None when the scan did not complete or omitted it."""
    scan = ctx.raw.get("scanner.json") or {}
    if scan.get("collectionComplete") is not True or not isinstance(scan.get("workspaces"), list):
        return None
    return next((row for row in scan["workspaces"] if isinstance(row, dict)
                 and str(row.get("id") or "").lower() == str(workspace_id).lower()), None)


def declared_environment(ctx: Context, workspace: dict[str, Any]) -> str:
    from orchestration.rule_policy import resolve_rule
    return resolve_rule(ctx.configuration, "GOV-001", workspace)["environment"]


def optional_facts(ctx: Context, family: str) -> tuple[dict[str, Any] | None, str | None]:
    """A FUAM fact family a native rule may use to sharpen its result: (entry, None) or (None, reason).

    The rule never depends on it. When FUAM is off, stale or unreadable the rule keeps its native result
    and records the reason, so a gap is never read as an answer.
    """
    sources = (ctx.configuration.get("sources") or {}) if isinstance(ctx.configuration, dict) else {}
    if not (sources.get("fuam") or {}).get("enabled"):
        return None, "source_not_enabled"
    entry = ((ctx.enrichment or {}).get("rule_facts") or {}).get(family)
    if entry is None:
        return None, "enrichment_unavailable"
    if entry.get("status") != "collected":
        return None, entry.get("reason_code") or "family_unavailable"
    if entry.get("freshness_status") == "stale":
        return None, "source_data_stale"
    return entry, None

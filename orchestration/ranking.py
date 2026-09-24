# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Strict workspace selection; empty selection must never mean tenant-wide review."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any

from orchestration.fabric_api import guid


METRICS = ("cu_seconds", "recorded_throttling_minutes")


@dataclass(frozen=True)
class RankedWorkspace:
    workspace_id: str
    workspace_name: str
    metric_value: float


def rank_workspaces(
    rows: Iterable[Mapping[str, Any]],
    *,
    top_n: int = 5,
    allowlist: str = "",
    excluded_workspace_ids: Iterable[str] = (),
) -> list[RankedWorkspace]:
    if not 1 <= top_n <= 100:
        raise ValueError("TOP_N must be between 1 and 100.")
    allowed = {guid(value) for value in allowlist.replace(",", " ").split()}
    if allowlist.strip() and not allowed:
        raise ValueError("Nonblank WORKSPACE_IDS must contain at least one workspace GUID.")
    excluded = {guid(value) for value in excluded_workspace_ids}
    totals: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for row_number, row in enumerate(rows, 1):
        try:
            workspace_id = guid(str(row["workspace_id"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Monitoring row {row_number} has a missing or invalid WorkspaceId; "
                "expected a workspace GUID. Check the FUAM monitoring source."
            ) from exc
        try:
            value = Decimal(str(row["metric_value"]))
        except InvalidOperation as exc:
            raise ValueError(f"Invalid metric value for workspace {workspace_id}.") from exc
        if not value.is_finite() or value < 0:
            raise ValueError(f"Metric must be finite and nonnegative for workspace {workspace_id}.")
        if workspace_id in excluded or (allowed and workspace_id not in allowed):
            continue
        if value == 0:
            continue
        totals[workspace_id] = totals.get(workspace_id, Decimal(0)) + value
        names[workspace_id] = str(row.get("workspace_name") or workspace_id)
    ordered = sorted(totals, key=lambda workspace_id: (-totals[workspace_id], workspace_id))[:top_n]
    result = [RankedWorkspace(workspace_id, names[workspace_id], float(totals[workspace_id])) for workspace_id in ordered]
    if any(not math.isfinite(row.metric_value) for row in result):
        raise ValueError("Aggregated metric exceeds the supported finite numeric range.")
    return result


def selection_payload(rows: list[RankedWorkspace], source: str, metric: str) -> dict[str, Any]:
    if metric not in METRICS:
        raise ValueError(f"RANKING_METRIC must be one of {METRICS}.")
    return {
        "status": "selected" if rows else "skipped_no_candidates",
        "source": source,
        "metric": metric,
        "metric_caveat": (
            "Recorded minutes only; FUAM may omit zero-CU groups. Not a count of throttled "
            "operations or attribution of the cause of capacity overload."
            if metric == "recorded_throttling_minutes" else "Consumption is not evidence of a performance defect."
        ),
        "workspace_ids": ",".join(row.workspace_id for row in rows),
        "workspaces": [asdict(row) for row in rows],
    }

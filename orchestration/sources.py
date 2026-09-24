# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fixed FUAM monitoring queries over the supported Fabric Spark SQL connector."""
from __future__ import annotations

from datetime import date, timedelta, timezone, datetime
import json
from typing import Any, TypedDict

from collectors._http import collect_workspace_groups
from collectors.workspace_scope import is_excluded_workspace
from orchestration.fabric_api import FabricClient, guid


METRIC_COLUMNS = {"cu_seconds": "TotalCUs", "recorded_throttling_minutes": "ThrottlingInMin"}
MAX_WORKSPACES = 100_000


class ResolvedCapacity(TypedDict):
    requested: str
    capacity_id: str
    capacity_name: str


class CapacitySet(TypedDict):
    requested: str
    capacities: list[ResolvedCapacity]


CapacityScope = ResolvedCapacity | CapacitySet


def capacity_selector(value: Any = None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("CAPACITY_ID_OR_NAME must be a name/GUID, a JSON list of names/GUIDs, or blank.")
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("CAPACITY_ID_OR_NAME cannot contain control characters.")
    if value.startswith("["):
        try:
            members = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("CAPACITY_ID_OR_NAME contains an invalid JSON list.") from exc
        if not isinstance(members, list) or not members:
            raise ValueError("The capacity list must be nonempty; use blank for all capacities.")
        if any(
            not isinstance(member, str) or not member.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in member)
            for member in members
        ):
            raise ValueError("Each capacity list entry must be a nonempty name/GUID without control characters.")
        return json.dumps([member.strip() for member in members])
    return value


def resolve_capacity(
    selector: str, workspace_id: str, lakehouse: dict[str, Any],
) -> CapacityScope:
    requested = capacity_selector(selector)
    is_set = requested.startswith("[")
    members = json.loads(requested) if is_set else [requested]
    resolved: list[ResolvedCapacity] = []
    rows = None
    for member in members:
        scope: ResolvedCapacity = {"requested": member, "capacity_id": "", "capacity_name": ""}
        if member:
            try:
                scope["capacity_id"] = guid(member)
            except ValueError:
                if rows is None:
                    rows = query_rows(
                        workspace_id, lakehouse,
                        "SELECT CapacityId AS capacity_id, displayName AS capacity_name FROM dbo.capacities",
                    )
                scope["capacity_id"], scope["capacity_name"] = _match_capacity_name(member, rows)
        resolved.append(scope)
    return {"requested": requested, "capacities": resolved} if is_set else resolved[0]


def _match_capacity_name(requested: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    # Match locally: no user-supplied name is ever interpolated into SQL.
    matches = {}
    for row in rows:
        if not isinstance(row.get("capacity_name"), str) or "capacity_id" not in row:
            raise ValueError("FUAM capacities lookup returned invalid CapacityId/displayName columns.")
        if row["capacity_name"].strip().casefold() == requested.casefold():
            try:
                capacity_id = guid(row["capacity_id"])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("FUAM capacity name resolved to an invalid CapacityId.") from exc
            matches[capacity_id] = row["capacity_name"].strip()
    if not matches:
        raise ValueError("Unknown CAPACITY_ID_OR_NAME: no exact capacity name in FUAM dbo.capacities.")
    if len(matches) != 1:
        raise ValueError("Ambiguous CAPACITY_ID_OR_NAME: multiple capacity GUIDs match; use a GUID.")
    return next(iter(matches.items()))


def validate_capacity_scope(scope: Any) -> None:
    if (
        not isinstance(scope, dict)
        or not isinstance(scope.get("requested"), str)
        or capacity_selector(scope["requested"]) != scope["requested"]
    ):
        raise ValueError("Invalid recorded capacity scope.")
    if scope["requested"].startswith("["):
        members = json.loads(scope["requested"])
        resolved = scope.get("capacities")
        if (
            set(scope) != {"requested", "capacities"} or not isinstance(resolved, list)
            or len(resolved) != len(members)
        ):
            raise ValueError("Recorded capacity set does not match the requested scope.")
        for member, entry in zip(members, resolved):
            _validate_resolved_capacity(entry)
            if entry["requested"] != member or not entry["capacity_id"]:
                raise ValueError("Recorded capacity set does not match the requested scope.")
        return
    _validate_resolved_capacity(scope)


def _validate_resolved_capacity(scope: Any) -> None:
    if (
        not isinstance(scope, dict) or set(scope) != {"requested", "capacity_id", "capacity_name"}
        or any(not isinstance(value, str) for value in scope.values())
    ):
        raise ValueError("Invalid recorded capacity scope.")
    requested, capacity_id, name = scope["requested"], scope["capacity_id"], scope["capacity_name"]
    if not requested:
        if capacity_id or name:
            raise ValueError("An all-capacity selection cannot record a selected capacity.")
        return
    if not capacity_id or guid(capacity_id) != capacity_id:
        raise ValueError("A capacity-scoped selection must record a validated capacity GUID.")
    try:
        requested_id = guid(requested)
    except ValueError:
        if not name or name.strip().casefold() != requested.casefold():
            raise ValueError("Recorded capacity name does not match the requested scope.")
    else:
        if requested_id != capacity_id or name:
            raise ValueError("Recorded capacity GUID does not match the requested scope.")


class SourceResult(tuple):
    """Preserve the (source, rows) API with scope and unattributed-group counts."""

    capacity_scope: CapacityScope
    unattributed_workspace_groups: int
    workspace_inventory: dict[str, Any] | None

    def __new__(
        cls, rows: list[dict[str, Any]], capacity_scope: CapacityScope, *,
        unattributed_workspace_groups: int = 0,
        workspace_inventory: dict[str, Any] | None = None,
    ):
        result = super().__new__(cls, ("fuam", rows))
        result.capacity_scope = capacity_scope
        result.unattributed_workspace_groups = unattributed_workspace_groups
        result.workspace_inventory = workspace_inventory
        return result


def match_current_workspaces(
    rows: list[dict[str, Any]], client: FabricClient,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match FUAM identities against complete current metadata before ranking."""
    groups = collect_workspace_groups(
        "https://api.powerbi.com/v1.0/myorg/admin/groups",
        lambda: {"Authorization": "Bearer " + client.token()},
    )
    current = {guid(group["id"]): group for group in groups}
    unmatched: set[str] = set()
    unsupported: set[str] = set()
    matched = []
    for index, row in enumerate(rows, 1):
        try:
            workspace_id = guid(str(row["workspace_id"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Monitoring row {index} has a missing or invalid WorkspaceId; "
                "expected a workspace GUID. Check the FUAM monitoring source."
            ) from exc
        if workspace_id not in current:
            unmatched.add(workspace_id)
        elif is_excluded_workspace(current[workspace_id]):
            unsupported.add(workspace_id)
        else:
            matched.append(row)
    if unmatched:
        print(
            f"WARNING: FUAM excluded {len(unmatched)} unmatched workspace ID(s) before top-N ranking. "
            "They are absent from the complete current tenant workspace inventory, not proven deleted. "
            "Inspect workspace_inventory.unmatched_ids in the selection audit and verify source/tenant."
        )
    if unsupported:
        print(
            f"WARNING: FUAM excluded {len(unsupported)} workspace ID(s) with unsupported workspace "
            "metadata before top-N ranking; see workspace_inventory.unsupported_ids in the selection audit."
        )
    return (matched if unmatched or unsupported else rows), {
        "source": "powerbi_admin_groups", "complete": True, "workspace_count": len(current),
        "unmatched_count": len(unmatched), "unmatched_ids": sorted(unmatched),
        "unmatched_reason": "not_in_current_workspace_inventory",
        "unsupported_count": len(unsupported), "unsupported_ids": sorted(unsupported),
        "unsupported_reason": "unsupported_workspace_metadata",
    }


def validate_source(config: dict[str, Any]) -> None:
    capacity_selector(config.get("CAPACITY_ID_OR_NAME"))
    if config.get("SOURCE_MODE", "fuam") != "fuam":
        raise ValueError(
            "Only FUAM is supported. Direct custom Capacity Metrics "
            "model queries are unsupported by that product and are not implemented."
        )
    if not config.get("FUAM_WORKSPACE_ID"):
        raise ValueError("FUAM_WORKSPACE_ID is required; no monitoring-source fallback is performed.")
    guid(config["FUAM_WORKSPACE_ID"])
    if config.get("FUAM_LAKEHOUSE_ID"):
        guid(config["FUAM_LAKEHOUSE_ID"])
    if config["RANKING_METRIC"] not in METRIC_COLUMNS:
        raise ValueError("Choose cu_seconds or recorded_throttling_minutes, not an inferred operation count.")
    if not 1 <= int(config["LOOKBACK_DAYS"]) <= 28:
        raise ValueError("LOOKBACK_DAYS must be between 1 and 28.")


def resolve_lakehouse(config: dict[str, Any], client: FabricClient) -> dict[str, Any]:
    validate_source(config)
    workspace_id = guid(config["FUAM_WORKSPACE_ID"])
    item_id = config.get("FUAM_LAKEHOUSE_ID", "")
    if item_id:
        return client.get_json(f"/workspaces/{workspace_id}/lakehouses/{guid(item_id)}")
    items = client.list_items(workspace_id, "Lakehouse")
    if len(items) != 1:
        raise ValueError(
            f"FUAM workspace contains {len(items)} Lakehouses. Set FUAM_LAKEHOUSE_ID "
            "explicitly; no table scan or name-based source guessing is performed."
        )
    return items[0]


def fuam_query(
    metric: str, days: int, *, end_date: date,
    capacity_id: str | None = None, capacity_ids: list[str] | None = None,
) -> str:
    column = METRIC_COLUMNS[metric]
    if not 1 <= days <= 28:
        raise ValueError("Lookback must be between 1 and 28 days.")
    start = (end_date - timedelta(days=days)).isoformat()
    end = end_date.isoformat()
    capacity_id = capacity_selector(capacity_id)
    if capacity_ids is not None and (
        capacity_id or not isinstance(capacity_ids, list) or not capacity_ids
        or any(not isinstance(value, str) or not value.strip() for value in capacity_ids)
    ):
        raise ValueError("Provide one capacity GUID or a nonempty list of GUIDs, not both.")
    capacity_filter = (
        f"\n      AND UPPER(CapacityId) = '{guid(capacity_id).upper()}'" if capacity_id else ""
    )
    if capacity_ids is not None:
        ids = sorted({guid(value).upper() for value in capacity_ids})
        capacity_filter = "\n      AND UPPER(CapacityId) IN (" + ", ".join(f"'{value}'" for value in ids) + ")"
    return f"""
SELECT u.workspace_id, w.workspace_name, u.metric_value, u.invalid_values
FROM (
    SELECT UPPER(WorkspaceId) AS workspace_id,
           SUM([{column}]) AS metric_value,
           SUM(CASE WHEN [{column}] IS NULL OR [{column}] < 0 THEN 1 ELSE 0 END) AS invalid_values
    FROM dbo.capacity_metrics_by_item_by_operation_by_day
    WHERE [Date] >= CAST('{start}' AS date) AND [Date] < CAST('{end}' AS date){capacity_filter}
    GROUP BY UPPER(WorkspaceId)
) AS u
LEFT JOIN (
    SELECT UPPER(WorkspaceId) AS workspace_id, MAX(WorkspaceName) AS workspace_name
    FROM dbo.workspaces
    GROUP BY UPPER(WorkspaceId)
) AS w ON w.workspace_id = u.workspace_id
""".strip()


def query_rows(workspace_id: str, lakehouse: dict[str, Any], query: str) -> list[dict[str, Any]]:
    import com.microsoft.spark.fabric  # noqa: F401 - registers the supported connector
    from com.microsoft.spark.fabric.Constants import Constants
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    frame = (
        spark.read.option(Constants.WorkspaceId, workspace_id)
        .option(Constants.LakehouseId, guid(lakehouse["id"]))
        .option(Constants.DatabaseName, lakehouse["displayName"])
        .synapsesql(query)
    )
    rows = [row.asDict() for row in frame.limit(MAX_WORKSPACES + 1).collect()]
    if len(rows) > MAX_WORKSPACES:
        raise RuntimeError("FUAM workspace result exceeds the safety limit; selection was not truncated.")
    return rows


def read_source(config: dict[str, Any], client: FabricClient) -> SourceResult:
    lakehouse = resolve_lakehouse(config, client)
    workspace_id = guid(config["FUAM_WORKSPACE_ID"])
    scope = resolve_capacity(capacity_selector(config.get("CAPACITY_ID_OR_NAME")), workspace_id, lakehouse)
    days = int(config["LOOKBACK_DAYS"])
    end_date = datetime.now(timezone.utc).date()
    resolved = scope["capacities"] if "capacities" in scope else [scope]
    ids = sorted({entry["capacity_id"] for entry in resolved if entry["capacity_id"]})
    query = fuam_query(
        config["RANKING_METRIC"], days, end_date=end_date,
        capacity_ids=ids if len(ids) > 1 else None, capacity_id=ids[0] if len(ids) == 1 else None,
    )
    if ids:
        print(f"FUAM capacity scope: capacity_ids={','.join(ids)} (metric rows before aggregation).")
    print(
        f"FUAM source: workspace_id={workspace_id}, lakehouse_id={guid(lakehouse['id'])}, "
        f"metric={config['RANKING_METRIC']}, "
        f"window_start={(end_date - timedelta(days=days)).isoformat()}, "
        f"window_end_exclusive={end_date.isoformat()}."
    )
    rows = query_rows(workspace_id, lakehouse, query)
    if any(row["invalid_values"] for row in rows):
        raise ValueError("FUAM contains null or negative monitoring values; correct the source before ranking.")
    attributed = []
    for row in rows:
        candidate_id = row["workspace_id"]
        if candidate_id is None or (isinstance(candidate_id, str) and not candidate_id.strip()):
            continue
        attributed.append(row)
    unattributed = len(rows) - len(attributed)
    if unattributed:
        print(
            f"WARNING: FUAM excluded {unattributed} unattributed workspace metric group(s) with a null/blank "
            "WorkspaceId; their consumption cannot be assigned to a review workspace."
        )
    if config["RANKING_METRIC"] == "recorded_throttling_minutes":
        print(
            "Ranking recorded throttling minutes, not operation counts. Stock FUAM may omit "
            "zero-CU groups; these values are potentially incomplete and do not identify "
            "which workload caused capacity overload."
        )
    matched, inventory = match_current_workspaces(attributed if unattributed else rows, client)
    return SourceResult(
        matched, scope, unattributed_workspace_groups=unattributed, workspace_inventory=inventory,
    )

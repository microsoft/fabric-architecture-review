# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Aggregated FUAM and FCA facts that the enrichment rules evaluate.

Each family is one bounded, read-only query set over the connected Lakehouse. A family that
cannot be read is recorded as unavailable with a reason code; error text is never stored, and
a gap is never turned into an empty result.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from orchestration.review_config import canonical_id

CHUNK = 500
# The Lakehouse SQL endpoint compares text case-sensitively (Latin1_General_100_BIN2_UTF8) and
# FUAM stores identifiers in upper case, so every identifier predicate lowers the column and
# is matched against lower-case canonical GUIDs.
ITEM_METRICS = "capacity_metrics_by_item_by_operation_by_day"
KIND_METRICS = "capacity_metrics_by_item_kind_by_day"
ORG_LINKS = "widelyshared_organization_links"
REFRESHES = "capacity_refreshable_details"
# Daily metrics describe a finished day, so they may lag the connection's freshness limit by one day.
DAILY_GRACE_HOURS = 24


class Unavailable(Exception):
    """A family cannot be evaluated; the code explains why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Env:
    query: Callable[[str], list[dict[str, Any]]]
    connection: dict[str, Any]
    thresholds: dict[str, Any]
    workspace_ids: set[str]
    capacity_ids: set[str]
    now: datetime
    # Semantic model IDs that reports point at but the review scope does not hold; looked up tenant-wide.
    model_ids: set[str] = field(default_factory=set)

    @property
    def window_cutoff(self) -> str:
        return (self.now - timedelta(days=self.thresholds["operations"]["window_days"])).strftime("%Y-%m-%d")


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _chunks(values: set[str]) -> list[str]:
    ordered = sorted(values)
    return [", ".join(f"'{value}'" for value in ordered[start:start + CHUNK]) for start in range(0, len(ordered), CHUNK)]


def _rows(env: Env, statement: str, keys: set[str]) -> list[dict[str, Any]]:
    rows = env.query(statement)
    if any(set(row) != keys for row in rows):
        raise Unavailable("incompatible_schema")
    return rows


def _freshness(newest: datetime | None, env: Env, daily: bool = False) -> str:
    if newest is None:
        return "stale"
    limit = env.connection["max_age_hours"] + (DAILY_GRACE_HOURS if daily else 0)
    return "stale" if env.now - newest > timedelta(hours=limit) else "recent"


def _guid(value: Any) -> str:
    try:
        return canonical_id(str(value), "FUAM identity")
    except ValueError as exc:
        raise Unavailable("incompatible_identity") from exc


def _activity(env: Env) -> dict[str, Any]:
    bounds = _rows(env, "SELECT MIN(CreationDate) AS earliest, MAX(CreationDate) AS newest FROM dbo.activities",
                   {"earliest", "newest"})
    if not bounds or bounds[0]["newest"] is None:
        raise Unavailable("no_activity_history")
    earliest, newest = _utc(bounds[0]["earliest"]), _utc(bounds[0]["newest"])
    rows = []
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT WorkspaceId, MAX(CreationDate) AS last_activity FROM dbo.activities "
            f"WHERE LOWER(WorkspaceId) IN ({ids}) GROUP BY WorkspaceId"
        ), {"WorkspaceId", "last_activity"}):
            last = _utc(row["last_activity"])
            rows.append({"workspace_id": _guid(row["WorkspaceId"]), "last_activity_at": last.isoformat() if last else None})
    return {"rows": rows, "observed_at": newest.isoformat(), "earliest_at": earliest.isoformat() if earliest else None,
            "freshness_status": _freshness(newest, env, daily=True)}


STALE_ITEMS_LISTED = 200


def _item_age(env: Env) -> dict[str, Any]:
    cutoff = (env.now - timedelta(days=env.thresholds["governance"]["stale_item_days"])).strftime("%Y-%m-%d")
    rows, newest = [], None
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT workspaceId, COUNT(*) AS item_count, "
            "SUM(CASE WHEN TRY_CAST(lastUpdatedDate AS DATETIME2) IS NULL THEN 0 ELSE 1 END) AS dated_item_count, "
            f"SUM(CASE WHEN TRY_CAST(lastUpdatedDate AS DATETIME2) < '{cutoff}' THEN 1 ELSE 0 END) AS stale_item_count, "
            "MAX(fuam_modified_at) AS newest FROM dbo.active_items "
            f"WHERE LOWER(workspaceId) IN ({ids}) AND fuam_deleted = 0 GROUP BY workspaceId"
        ), {"workspaceId", "item_count", "dated_item_count", "stale_item_count", "newest"}):
            modified = _utc(row["newest"])
            newest = max(filter(None, (newest, modified)), default=None)
            rows.append({"workspace_id": _guid(row["workspaceId"]), "item_count": int(row["item_count"]),
                         "dated_item_count": int(row["dated_item_count"]),
                         "stale_item_count": int(row["stale_item_count"])})
    # The oldest items first, capped per workspace: the counts above hold the true totals.
    stale: dict[str, list[str]] = {}
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT workspaceId, id FROM (SELECT workspaceId, id, "
            "ROW_NUMBER() OVER (PARTITION BY workspaceId ORDER BY lastUpdatedDate) AS rn FROM dbo.active_items "
            f"WHERE LOWER(workspaceId) IN ({ids}) AND fuam_deleted = 0 "
            f"AND TRY_CAST(lastUpdatedDate AS DATETIME2) < '{cutoff}') AS ranked WHERE rn <= {STALE_ITEMS_LISTED}"
        ), {"workspaceId", "id"}):
            stale.setdefault(_guid(row["workspaceId"]), []).append(_guid(row["id"]))
    for row in rows:
        row["stale_item_ids"] = sorted(stale.get(row["workspace_id"], []))
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env), "stale_item_days": env.thresholds["governance"]["stale_item_days"]}


def _domains(env: Env) -> dict[str, Any]:
    rows, newest = [], None
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT WorkspaceId, DomainId, fuam_modified_at FROM dbo.workspaces "
            f"WHERE LOWER(WorkspaceId) IN ({ids}) AND fuam_deleted = 0"
        ), {"WorkspaceId", "DomainId", "fuam_modified_at"}):
            modified = _utc(row["fuam_modified_at"])
            newest = max(filter(None, (newest, modified)), default=None)
            domain = str(row["DomainId"]).lower() if row["DomainId"] else None
            rows.append({"workspace_id": _guid(row["WorkspaceId"]), "domain_id": domain})
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env)}


def _metrics_newest(env: Env, table: str) -> datetime | None:
    row = _rows(env, f"SELECT MAX([Date]) AS newest FROM dbo.{table}", {"newest"})[0]
    return _utc(row["newest"])


def _item_operations(env: Env) -> dict[str, Any]:
    rows = []
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT CapacityId, WorkspaceId, ItemId, ItemKind, SUM(OperationCount) AS operation_count, "
            "SUM(FailureOperationCount) AS failure_count, SUM(TotalCUs) AS total_cus "
            f"FROM dbo.{ITEM_METRICS} WHERE LOWER(WorkspaceId) IN ({ids}) AND [Date] >= '{env.window_cutoff}' "
            "GROUP BY CapacityId, WorkspaceId, ItemId, ItemKind"
        ), {"CapacityId", "WorkspaceId", "ItemId", "ItemKind", "operation_count", "failure_count", "total_cus"}):
            if row["ItemId"] is None:
                continue
            rows.append({
                "capacity_id": _guid(row["CapacityId"]) if row["CapacityId"] else None,
                "workspace_id": _guid(row["WorkspaceId"]), "item_id": _guid(row["ItemId"]),
                "item_kind": str(row["ItemKind"] or ""), "operation_count": int(row["operation_count"] or 0),
                "failure_count": int(row["failure_count"] or 0), "total_cus": float(row["total_cus"] or 0),
            })
    newest = _metrics_newest(env, ITEM_METRICS)
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env, daily=True),
            "window_days": env.thresholds["operations"]["window_days"]}


def _capacity_totals(env: Env) -> dict[str, Any]:
    rows = []
    for ids in _chunks(env.capacity_ids):
        for row in _rows(env, (
            f"SELECT CapacityId, SUM(TotalCUs) AS total_cus FROM dbo.{ITEM_METRICS} "
            f"WHERE LOWER(CapacityId) IN ({ids}) AND [Date] >= '{env.window_cutoff}' GROUP BY CapacityId"
        ), {"CapacityId", "total_cus"}):
            rows.append({"capacity_id": _guid(row["CapacityId"]), "total_cus": float(row["total_cus"] or 0)})
    newest = _metrics_newest(env, ITEM_METRICS)
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env, daily=True),
            "window_days": env.thresholds["operations"]["window_days"]}


def _throttling(env: Env) -> dict[str, Any]:
    rows = []
    for ids in _chunks(env.capacity_ids):
        for row in _rows(env, (
            "SELECT CapacityId, COUNT(DISTINCT CAST([Date] AS DATE)) AS observed_days, "
            "COUNT(DISTINCT CASE WHEN ThrottlingInMin > 0 THEN CAST([Date] AS DATE) END) AS throttled_days "
            f"FROM dbo.{KIND_METRICS} WHERE LOWER(CapacityId) IN ({ids}) AND [Date] >= '{env.window_cutoff}' GROUP BY CapacityId"
        ), {"CapacityId", "observed_days", "throttled_days"}):
            rows.append({"capacity_id": _guid(row["CapacityId"]), "observed_days": int(row["observed_days"]),
                         "throttled_days": int(row["throttled_days"])})
    newest = _metrics_newest(env, KIND_METRICS)
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env, daily=True),
            "window_days": env.thresholds["operations"]["window_days"]}


def _widely_shared(env: Env, table: str) -> dict[str, Any]:
    """Items in the latest transfer of one widely-shared table, tied to a workspace through active items."""
    snapshot = _rows(env, f"SELECT MAX(TransferDateTime) AS snapshot_at FROM dbo.{table}", {"snapshot_at"})
    taken = _utc(snapshot[0]["snapshot_at"]) if snapshot else None
    if taken is None:
        raise Unavailable("no_transfer_snapshot")
    rows = []
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT i.workspaceId AS WorkspaceId, p.ItemId AS ItemId, p.artifactType AS artifactType "
            f"FROM dbo.{table} p JOIN dbo.active_items i ON LOWER(i.id) = LOWER(p.ItemId) "
            f"WHERE i.fuam_deleted = 0 AND LOWER(i.workspaceId) IN ({ids}) "
            f"AND p.TransferDate = (SELECT MAX(TransferDate) FROM dbo.{table})"
        ), {"WorkspaceId", "ItemId", "artifactType"}):
            rows.append({"workspace_id": _guid(row["WorkspaceId"]), "item_id": _guid(row["ItemId"]),
                         "artifact_type": str(row["artifactType"] or "")})
    return {"rows": rows, "observed_at": taken.isoformat(), "snapshot_at": taken.isoformat(),
            "freshness_status": _freshness(taken, env)}


def _publish_to_web(env: Env) -> dict[str, Any]:
    return _widely_shared(env, "widelyshared_publishtoweb")


def _org_links(env: Env) -> dict[str, Any]:
    return _widely_shared(env, ORG_LINKS)


def _refresh_health(env: Env) -> dict[str, Any]:
    rows = []
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT WorkspaceId, ItemId, COUNT(*) AS refresh_count, "
            "SUM(CASE WHEN Status = 'Failed' THEN 1 ELSE 0 END) AS failure_count "
            f"FROM dbo.{REFRESHES} WHERE LOWER(WorkspaceId) IN ({ids}) AND (Status = 'Completed' OR Status = 'Failed') "
            f"AND LastRefreshStartDate >= '{env.window_cutoff}' GROUP BY WorkspaceId, ItemId"
        ), {"WorkspaceId", "ItemId", "refresh_count", "failure_count"}):
            rows.append({"workspace_id": _guid(row["WorkspaceId"]), "item_id": _guid(row["ItemId"]),
                         "refresh_count": int(row["refresh_count"] or 0),
                         "failure_count": int(row["failure_count"] or 0)})
    newest = _rows(env, f"SELECT MAX(LastRefreshStartTime) AS newest FROM dbo.{REFRESHES}", {"newest"})[0]["newest"]
    newest = _utc(newest)
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env, daily=True),
            "window_days": env.thresholds["operations"]["window_days"]}


# Operations a consumer of a semantic model causes, whoever it is (a report, Excel, Analyze in Excel, XMLA).
CONSUMER_OPERATIONS = ("Query", "XMLA Read Operation")


def _model_usage(env: Env) -> dict[str, Any]:
    operations = " OR ".join(f"OperationName = '{name}'" for name in CONSUMER_OPERATIONS)
    rows = []
    for ids in _chunks(env.workspace_ids):
        for row in _rows(env, (
            "SELECT ItemId, SUM(OperationCount) AS operation_count "
            f"FROM dbo.{ITEM_METRICS} WHERE LOWER(WorkspaceId) IN ({ids}) AND [Date] >= '{env.window_cutoff}' "
            f"AND ({operations}) GROUP BY ItemId"
        ), {"ItemId", "operation_count"}):
            if row["ItemId"] is None:
                continue
            rows.append({"item_id": _guid(row["ItemId"]), "query_count": int(row["operation_count"] or 0)})
    newest = _metrics_newest(env, ITEM_METRICS)
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env, daily=True),
            "window_days": env.thresholds["operations"]["window_days"]}


def _model_lookup(env: Env) -> dict[str, Any]:
    rows = []
    for ids in _chunks(env.model_ids):
        for row in _rows(env, (
            "SELECT id, workspaceId FROM dbo.active_items "
            f"WHERE fuam_deleted = 0 AND type = 'SemanticModel' AND LOWER(id) IN ({ids})"
        ), {"id", "workspaceId"}):
            rows.append({"model_id": _guid(row["id"]), "workspace_id": _guid(row["workspaceId"])})
    newest = _utc(_rows(env, "SELECT MAX(fuam_modified_at) AS newest FROM dbo.active_items", {"newest"})[0]["newest"])
    return {"rows": rows, "observed_at": newest.isoformat() if newest else None,
            "freshness_status": _freshness(newest, env)}


FUAM_FAMILIES = {
    "workspace_activity": ("activities", _activity),
    "item_age": ("active_items", _item_age),
    "domains": ("workspaces", _domains),
    "item_operations": (ITEM_METRICS, _item_operations),
    "capacity_totals": (ITEM_METRICS, _capacity_totals),
    "capacity_throttling": (KIND_METRICS, _throttling),
    "publish_to_web": ("widelyshared_publishtoweb", _publish_to_web),
    "org_links": (ORG_LINKS, _org_links),
    "refresh_health": (REFRESHES, _refresh_health),
    "model_usage": (ITEM_METRICS, _model_usage),
    "model_lookup": ("active_items", _model_lookup),
}


ENRICHMENT_FAMILIES = frozenset({*FUAM_FAMILIES, "capacity_tags"})


def _family(table: str, builder: Callable[[], dict[str, Any]], connection: dict[str, Any]) -> dict[str, Any]:
    provenance = {"source_workspace_id": connection["workspace_id"], "source_lakehouse_id": connection["lakehouse_id"]}
    try:
        return {"table": table, "status": "collected", **builder(), **provenance}
    except Unavailable as exc:
        return {"table": table, "status": "unavailable", "reason_code": exc.code, "rows": [], **provenance}
    except Exception:
        # The failure text can name tables or values, so only a code is kept.
        return {"table": table, "status": "unavailable", "reason_code": "query_failed", "rows": [], **provenance}


def fuam_facts(
    query: Callable[[str], list[dict[str, Any]]], connection: dict[str, Any], thresholds: dict[str, Any],
    workspace_ids: set[str], capacity_ids: set[str], now: datetime, model_ids: set[str] | frozenset[str] = frozenset(),
) -> dict[str, dict[str, Any]]:
    env = Env(query, connection, thresholds, workspace_ids, capacity_ids, now, set(model_ids))
    return {name: _family(table, lambda builder=builder: builder(env), connection)
            for name, (table, builder) in FUAM_FAMILIES.items()}


def _tag_names(raw: Any) -> set[str]:
    """The tag names in a FOCUS ``Tags`` value. Values are discarded and never stored."""
    if raw is None or not str(raw).strip():
        return set()
    text = str(raw).strip()
    for candidate in (text, "{" + text + "}"):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return {str(name) for name in parsed}
    raise Unavailable("incompatible_tags")


def fca_capacity_tags(
    query: Callable[[str], list[dict[str, Any]]], connection: dict[str, Any], mapping: dict[str, str],
) -> dict[str, Any]:
    """Tag names of the capacities that are mapped to an Azure resource.

    FCA keeps tags only on its FOCUS cost table, as a JSON text per row. Only the resource
    identifier and that column are selected; no cost column is read.
    """

    def build() -> dict[str, Any]:
        tags: dict[str, set[str]] = {}
        resources = sorted(mapping)
        for start in range(0, len(resources), CHUNK):
            selected = ", ".join(f"'{value}'" for value in resources[start:start + CHUNK])
            for row in query(
                "SELECT DISTINCT ResourceId, Tags FROM dbo.focus "
                f"WHERE LOWER(ResourceId) IN ({selected})"
            ):
                if set(row) != {"ResourceId", "Tags"}:
                    raise Unavailable("incompatible_schema")
                tags.setdefault(str(row["ResourceId"]).lower(), set()).update(_tag_names(row["Tags"]))
        return {"rows": [{"capacity_id": capacity, "tag_names": sorted(tags.get(resource, set()))}
                         for resource, capacity in sorted(mapping.items())],
                "observed_at": None, "freshness_status": "unknown", "mapped_capacity_count": len(mapping)}

    return _family("focus", build, connection)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pipeline + Notebook inventory and recent job-run history.

Endpoints:
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/dataPipelines
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/notebooks
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/items/{itemId}/jobs/instances

Docs: https://learn.microsoft.com/rest/api/fabric/core/job-scheduler

DATA SAFETY:
  - Run metadata only; legacy jobs retain job-instance failure diagnostics.
    New execution evidence excludes failure diagnostics.
  - DOES NOT fetch notebook cell outputs or pipeline activity payloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
from dataclasses import dataclass

from datetime import datetime, timedelta, timezone

from collectors._http import Headers, HttpError, get_json, paginate_value
from collectors._common import (
    filter_workspaces_by_scope, get_scope_workspace_ids,
    load_workspace_inventory, record_collection_failure,
)
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"
JOB_INSTANCE_MAX_PAGES = 5
JOB_INSTANCE_MAX_RECORDS = 1000
# Window (days) for the targeted failed-jobs query. Override via FAILED_JOB_WINDOW_DAYS.
FAILED_JOB_WINDOW_DAYS = int(os.environ.get("FAILED_JOB_WINDOW_DAYS", "30"))


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    return [(w["id"], w.get("name") or "")
            for w in filter_workspaces_by_scope(load_workspace_inventory(raw_dir)) if w.get("id")]


@dataclass
class _JobHistory:
    records: List[Dict[str, Any]]
    status: str
    notice: str
    status_code: int | None = None


def _job_history(headers: Headers, wsid: str, item_id: str) -> _JobHistory:
    """Read retained jobs with fixed bounds, using only documented parameters.

    Most items retain 100 completed jobs; active jobs have no API count limit.
    Follow opaque tokens on the original endpoint, never a service-supplied URL.
    Exclude explicit item-ID mismatches and mark coverage incomplete.
    """
    url = f"{FAB}/workspaces/{wsid}/items/{item_id}/jobs/instances"
    records: List[Dict[str, Any]] = []
    observed_count = 0
    mismatched_items = False
    params = None
    seen_tokens: set[str] = set()
    for _ in range(JOB_INSTANCE_MAX_PAGES):
        try:
            payload = get_json(url, headers, params=params)
        except HttpError as exc:
            status = ("forbidden" if exc.status_code in (401, 403)
                      else "not_found" if exc.status_code == 404 else "error")
            return _JobHistory(records, "partial" if records else status, "request_failed", exc.status_code)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("value"), list)
            or not all(isinstance(row, dict) for row in payload["value"])
        ):
            return _JobHistory(records, "partial" if records else "invalid_data", "invalid_response")
        room = JOB_INSTANCE_MAX_RECORDS - observed_count
        for row in payload["value"][:room]:
            if "itemId" in row and (
                not isinstance(row["itemId"], str) or row["itemId"].lower() != item_id.lower()
            ):
                mismatched_items = True
                continue
            records.append(row)
        observed_count += min(len(payload["value"]), room)
        token = payload.get("continuationToken")
        if len(payload["value"]) > room:
            return _JobHistory(records, "partial", "collection_limit")
        if token is not None and not isinstance(token, str):
            return _JobHistory(records, "partial" if records else "invalid_data", "invalid_continuation")
        if token in (None, ""):
            if payload.get("continuationUri"):
                return _JobHistory(records, "partial" if records else "invalid_data", "invalid_continuation")
            if mismatched_items:
                return _JobHistory(records, "partial" if records else "invalid_data", "mismatched_execution_item_id")
            return _JobHistory(records, "collected" if records else "empty", "retained_jobs")
        if token in seen_tokens:
            return _JobHistory(records, "partial" if records else "invalid_data", "invalid_continuation")
        if observed_count >= JOB_INSTANCE_MAX_RECORDS:
            return _JobHistory(records, "partial", "collection_limit")
        seen_tokens.add(token)
        params = {"continuationToken": token}
    return _JobHistory(records, "partial", "collection_limit")


def _job_instances(headers: Headers, wsid: str, item_id: str) -> List[Dict[str, Any]]:
    history = _job_history(headers, wsid, item_id)
    if history.status not in ("collected", "empty"):
        raise HttpError("Job history collection incomplete", status_code=history.status_code)
    return history.records


def _failures_in_window(records: List[Dict[str, Any]], window_days: int) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    failed = []
    for row in records:
        if row.get("status") != "Failed":
            continue
        start = row.get("startTimeUtc")
        if not isinstance(start, str):
            continue
        try:
            timestamp = datetime.fromisoformat(start.replace("Z", "+00:00"))
            timestamp = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp
            if timestamp >= since:
                failed.append(row)
        except (ValueError, OverflowError):
            # Raw jobs and Gold coverage still retain/evidence invalid times.
            continue
    return failed


def _failed_job_instances(
    headers: Headers,
    wsid: str,
    item_id: str,
    window_days: int = FAILED_JOB_WINDOW_DAYS,
) -> List[Dict[str, Any]]:
    """Filter the bounded retained history, not a guaranteed full time window."""
    return _failures_in_window(_job_instances(headers, wsid, item_id), window_days)


@record_collection_failure("pipelines_notebooks.json")
def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    if not workspaces:
        print("Pipelines/Notebooks: no workspace inventory found — run scanner_api or workspace_inventory first.")
        target = raw_dir / "pipelines_notebooks.json"
        target.write_text(
            json.dumps({
                "pipelines": [], "notebooks": [], "jobs": {},
                "executionEvidence": [], "workspaceScope": sorted(get_scope_workspace_ids()),
            }, indent=2),
            encoding="utf-8",
        )
        return target

    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)

    pipelines: List[Dict[str, Any]] = []
    notebooks: List[Dict[str, Any]] = []
    jobs_index: Dict[str, List[Dict[str, Any]]] = {}
    failed_jobs_index: Dict[str, List[Dict[str, Any]]] = {}
    failed_workspaces: List[str] = []
    execution_evidence: List[Dict[str, Any]] = []
    inventory_errors: List[Dict[str, Any]] = []

    print(
        f"Pipelines/Notebooks: scanning {len(workspaces)} workspace(s) "
        f"(failed-jobs window: last {FAILED_JOB_WINDOW_DAYS} day(s))..."
    )
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        for endpoint, item_type, inventory in (
            ("dataPipelines", "DataPipeline", pipelines), ("notebooks", "Notebook", notebooks),
        ):
            items: List[Dict[str, Any]] = []
            try:
                items.extend(paginate_value(f"{FAB}/workspaces/{wsid}/{endpoint}", headers))
            except HttpError as exc:
                failed_workspaces.append(wsid)
                error = {
                    "workspaceId": wsid, "workspaceName": wsname, "itemType": item_type,
                    "statusCode": exc.status_code, "noticeCode": "inventory_request_failed",
                    "collectionStatus": "partial" if items else "unavailable",
                    "observedCount": len(items),
                }
                if exc.error_code:
                    error["errorCode"] = exc.error_code
                inventory_errors.append(error)
                detail = f" ({exc.error_code})" if exc.error_code else ""
                print(
                    f"  {item_type} inventory incomplete in workspace {wsid}: "
                    f"HTTP {exc.status_code}{detail}; retained {len(items)} item(s); continuing."
                )
            for item in items:
                item["workspaceId"] = wsid
                item["workspaceName"] = wsname
                inventory.append(item)
                item_id = item.get("id")
                history = (_job_history(headers, wsid, item_id) if item_id else
                           _JobHistory([], "not_collected", "missing_item_identity"))
                execution_evidence.append({
                    "workspaceId": wsid, "workspaceName": wsname,
                    "itemId": item_id, "itemName": item.get("displayName") or item.get("name"),
                    "itemType": item_type, "source": "fabric_job_instances",
                    "historyScope": "recent_retained_observations",
                    "collectionStatus": history.status, "statusCode": history.status_code,
                    "noticeCode": history.notice,
                    "executions": [
                        {key: row[key] for key in ("id", "itemId", "status", "startTimeUtc", "endTimeUtc")
                         if key in row}
                        for row in history.records
                    ],
                })
                if history.status not in ("collected", "empty"):
                    failed_workspaces.append(wsid)
                if item_id:
                    if history.status in ("collected", "empty") or history.records:
                        jobs_index[item_id] = history.records
                    failed = _failures_in_window(history.records, FAILED_JOB_WINDOW_DAYS)
                    if failed:
                        failed_jobs_index[item_id] = failed

        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    target = raw_dir / "pipelines_notebooks.json"
    target.write_text(
        json.dumps(
            {
                "pipelines": pipelines,
                "notebooks": notebooks,
                "jobs": jobs_index,
                "failedJobsWindowDays": FAILED_JOB_WINDOW_DAYS,
                "failedJobs": failed_jobs_index,
                "failedWorkspaces": sorted(set(failed_workspaces)),
                "collectionComplete": not failed_workspaces,
                "executionEvidence": execution_evidence,
                "inventoryErrors": inventory_errors,
                "workspaceScope": sorted(get_scope_workspace_ids()),
                "failedJobsHistoryScope": "filtered_recent_retained_observations",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    total_failed = sum(len(v) for v in failed_jobs_index.values())
    skipped = f", {len(set(failed_workspaces))} workspace(s) incomplete" if failed_workspaces else ""
    print(
        f"Wrote {target} ({len(pipelines)} pipelines, {len(notebooks)} notebooks, "
        f"{total_failed} failed job(s) in last {FAILED_JOB_WINDOW_DAYS}d{skipped})."
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

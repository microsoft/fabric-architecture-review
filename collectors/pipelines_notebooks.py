# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pipeline + Notebook inventory and recent job-run history.

Endpoints:
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/dataPipelines
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/notebooks
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/items/{itemId}/jobs/instances

Docs: https://learn.microsoft.com/rest/api/fabric/core/job-scheduler

DATA SAFETY:
  - Run metadata only: start/end time, status, failure code/message at the
    job-instance level.
  - DOES NOT fetch notebook cell outputs or pipeline activity payloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from datetime import datetime, timedelta, timezone

from collectors._http import collect_value
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"
JOB_INSTANCE_TOP = 20
# Window (days) for the targeted failed-jobs query. Override via FAILED_JOB_WINDOW_DAYS.
FAILED_JOB_WINDOW_DAYS = int(os.environ.get("FAILED_JOB_WINDOW_DAYS", "30"))


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    for fname in ("scanner.json", "workspace_inventory.json"):
        p = raw_dir / fname
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        out = [(w["id"], w.get("name") or "") for w in (data.get("workspaces") or []) if w.get("id")]
        if out:
            return out
    return []


def _job_instances(headers: Dict[str, str], wsid: str, item_id: str) -> List[Dict[str, Any]]:
    url = f"{FAB}/workspaces/{wsid}/items/{item_id}/jobs/instances"
    return collect_value(url, headers, params={"maxResults": JOB_INSTANCE_TOP})


def _failed_job_instances(
    headers: Dict[str, str],
    wsid: str,
    item_id: str,
    window_days: int = FAILED_JOB_WINDOW_DAYS,
) -> List[Dict[str, Any]]:
    """Fetch only Failed job instances within the last ``window_days`` for an item.

    Uses the Fabric Job Scheduler ``$filter`` query (OData) on status and
    startTimeUtc. Returns failures with failureReason populated so analyzers
    can surface error codes / messages.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{FAB}/workspaces/{wsid}/items/{item_id}/jobs/instances"
    params = {
        "$filter": f"status eq 'Failed' and startTimeUtc ge {since}",
    }
    return collect_value(url, headers, params=params)


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    if not workspaces:
        print("Pipelines/Notebooks: no workspace inventory found — run scanner_api or workspace_inventory first.")
        target = raw_dir / "pipelines_notebooks.json"
        target.write_text(
            json.dumps({"pipelines": [], "notebooks": [], "jobs": {}}, indent=2),
            encoding="utf-8",
        )
        return target

    provider = get_default_provider()
    headers = provider.headers(scope=FABRIC_SCOPE)

    pipelines: List[Dict[str, Any]] = []
    notebooks: List[Dict[str, Any]] = []
    jobs_index: Dict[str, List[Dict[str, Any]]] = {}
    failed_jobs_index: Dict[str, List[Dict[str, Any]]] = {}
    failed_workspaces: List[str] = []

    print(
        f"Pipelines/Notebooks: scanning {len(workspaces)} workspace(s) "
        f"(failed-jobs window: last {FAILED_JOB_WINDOW_DAYS} day(s))..."
    )
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        # Isolate per-workspace failures: if _http exhausts its retries on a
        # workspace (HttpError) or the API misbehaves, skip that workspace and
        # keep the inventory already collected instead of aborting the run.
        try:
            pls = collect_value(f"{FAB}/workspaces/{wsid}/dataPipelines", headers)
            for p in pls:
                p["workspaceId"] = wsid
                p["workspaceName"] = wsname
                pipelines.append(p)
                pid = p.get("id")
                if pid:
                    jobs_index[pid] = _job_instances(headers, wsid, pid)
                    failed = _failed_job_instances(headers, wsid, pid)
                    if failed:
                        failed_jobs_index[pid] = failed

            nbs = collect_value(f"{FAB}/workspaces/{wsid}/notebooks", headers)
            for n in nbs:
                n["workspaceId"] = wsid
                n["workspaceName"] = wsname
                notebooks.append(n)
                nid = n.get("id")
                if nid:
                    jobs_index[nid] = _job_instances(headers, wsid, nid)
                    failed = _failed_job_instances(headers, wsid, nid)
                    if failed:
                        failed_jobs_index[nid] = failed
        except Exception as exc:  # noqa: BLE001 - isolate per-workspace failure
            failed_workspaces.append(wsid)
            print(f"  Workspace {wsname or wsid}: FAILED ({exc}); skipping and continuing.")
            continue

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
                "failedWorkspaces": failed_workspaces,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    total_failed = sum(len(v) for v in failed_jobs_index.values())
    skipped = f", {len(failed_workspaces)} workspace(s) skipped" if failed_workspaces else ""
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

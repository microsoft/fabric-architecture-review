# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace + item metadata via the Power BI / Fabric Admin Scanner API.

Pipeline:
  1) GET  admin/workspaces/modified                       -> list of active workspace IDs
  2) POST admin/workspaces/getInfo (batches of up to 100) -> scanId per batch
  3) Poll GET admin/workspaces/scanStatus/{scanId}        -> wait for Succeeded
  4) GET  admin/workspaces/scanResult/{scanId}            -> workspace + item metadata
  5) Merge all batches into a single output/raw/scanner.json

Docs:
  https://learn.microsoft.com/rest/api/power-bi/admin/workspace-info-get-modified-workspaces
  https://learn.microsoft.com/rest/api/power-bi/admin/workspace-info-post-workspace-info
  https://learn.microsoft.com/rest/api/power-bi/admin/workspace-info-get-scan-status
  https://learn.microsoft.com/rest/api/power-bi/admin/workspace-info-get-scan-result

Auth: runs as the signed-in user (delegated). Requires Fabric Administrator
or Power BI Administrator on the client tenant — these endpoints have no
workspace-scoped substitute.

DATA SAFETY: every "extra detail" query parameter is forced OFF so the
response contains only workspace + item METADATA. No credentials, no DAX/M
code, no column schemas, no user PII beyond owner GUIDs:
    lineage=false, datasourceDetails=false, getArtifactUsers=false,
    datasetSchema=false, datasetExpressions=false.

Rate limits (per tenant): 16 getInfo calls/hour, 500 scanStatus/scanResult/hour.
When the getInfo budget is exhausted the API returns HTTP 429 with a
``Retry-After`` header that can be up to an hour. All calls are routed through
``collectors._http.request``, which honours ``Retry-After`` and retries 429 /
5xx / transient connection errors with exponential backoff, so a throttled or
slow scan recovers on its own instead of crashing the collector.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from collectors import _http
from collectors._common import get_scope_workspace_ids
from collectors.auth import POWERBI_SCOPE, get_default_provider

BASE = "https://api.powerbi.com/v1.0/myorg/admin"
GET_INFO_PARAMS = {
    "lineage": "false",
    "datasourceDetails": "false",
    "getArtifactUsers": "false",
    "datasetSchema": "false",
    "datasetExpressions": "false",
}
BATCH_SIZE = 100
POLL_INTERVAL_SEC = 5
# A scan can stay in progress for a while on a busy tenant; combined with the
# 16-getInfo-calls/hour throttle this gives the run enough room to ride out a
# slow scan or a Retry-After back-off instead of timing out prematurely.
POLL_TIMEOUT_SEC = 3600


def _modified_workspaces(headers: Dict[str, str]) -> List[str]:
    url = f"{BASE}/workspaces/modified?excludePersonalWorkspaces=True&excludeInActiveWorkspaces=True"
    r = _http.request("GET", url, headers, timeout=60)
    r.raise_for_status()
    return [w["id"] for w in r.json() if w.get("id")]


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _start_scan(headers: Dict[str, str], workspace_ids: List[str]) -> str:
    # 429s here (the 16 getInfo calls/hour budget) are absorbed by _http.request,
    # which sleeps for the server-provided Retry-After before retrying.
    r = _http.request(
        "POST",
        f"{BASE}/workspaces/getInfo",
        headers,
        params=GET_INFO_PARAMS,
        json_body={"workspaces": workspace_ids},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["id"]


def _wait_for_scan(headers: Dict[str, str], scan_id: str) -> None:
    deadline = time.time() + POLL_TIMEOUT_SEC
    while time.time() < deadline:
        r = _http.request("GET", f"{BASE}/workspaces/scanStatus/{scan_id}", headers, timeout=60)
        r.raise_for_status()
        status = r.json().get("status")
        if status == "Succeeded":
            return
        if status in ("Failed", "Cancelled"):
            raise RuntimeError(f"Scanner API returned status={status} for scan {scan_id}")
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"Scanner API scan {scan_id} did not complete within {POLL_TIMEOUT_SEC}s")


def _fetch_scan_result(headers: Dict[str, str], scan_id: str) -> Dict[str, Any]:
    r = _http.request("GET", f"{BASE}/workspaces/scanResult/{scan_id}", headers, timeout=120)
    r.raise_for_status()
    return r.json()


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)
    headers["Content-Type"] = "application/json"

    workspace_ids = _modified_workspaces(headers)
    scope = get_scope_workspace_ids()
    if scope:
        before = len(workspace_ids)
        workspace_ids = [w for w in workspace_ids if w.lower() in scope]
        print(f"Scanner API: scoped to {len(workspace_ids)}/{before} workspace(s) via WORKSPACE_IDS.")
    print(f"Scanner API: {len(workspace_ids)} workspace(s) eligible.")

    combined: Dict[str, Any] = {
        "workspaces": [],
        "datasourceInstances": [],
        "misconfiguredDatasourceInstances": [],
    }
    batches = list(_chunks(workspace_ids, BATCH_SIZE))
    failed_batches: List[int] = []
    for i, batch in enumerate(batches, start=1):
        print(f"  Batch {i}/{len(batches)}: scanning {len(batch)} workspace(s)...")
        # Each batch is an independent scan cycle. Per-HTTP-call retry is handled
        # by _http.request; here we additionally isolate a whole-batch failure
        # (retries exhausted, scan Failed/Cancelled, non-retryable 4xx, timeout)
        # so one bad batch is logged and skipped instead of discarding every
        # batch already collected.
        try:
            scan_id = _start_scan(headers, batch)
            _wait_for_scan(headers, scan_id)
            result = _fetch_scan_result(headers, scan_id)
        except Exception as exc:  # noqa: BLE001 - isolate per-batch failure
            failed_batches.append(i)
            print(f"  Batch {i}: FAILED ({exc}); skipping and continuing.")
            continue
        combined["workspaces"].extend(result.get("workspaces") or [])
        combined["datasourceInstances"].extend(result.get("datasourceInstances") or [])
        combined["misconfiguredDatasourceInstances"].extend(
            result.get("misconfiguredDatasourceInstances") or []
        )

    # Self-describing collection metadata so downstream analyzers can warn when
    # the scan was partial (skipped batches) instead of silently treating an
    # incomplete inventory as the whole tenant. Consumers only read the
    # "workspaces" array, so this extra top-level key is non-breaking.
    combined["_meta"] = {
        "collector": "scanner_api",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scoped": bool(scope),
        "batch_size": BATCH_SIZE,
        "batches_total": len(batches),
        "batches_failed": len(failed_batches),
        "failed_batch_numbers": failed_batches,
        "workspaces_eligible": len(workspace_ids),
        "workspaces_collected": len(combined["workspaces"]),
        "complete": not failed_batches,
    }

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "scanner.json"
    target.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    if failed_batches:
        print(
            f"Wrote {target} ({len(combined['workspaces'])} workspaces) "
            f"with {len(failed_batches)}/{len(batches)} batch(es) skipped: {failed_batches}. "
            "Re-run to retry the missing workspaces."
        )
    else:
        print(f"Wrote {target} ({len(combined['workspaces'])} workspaces).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

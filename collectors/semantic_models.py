# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Semantic model inventory + refresh history via Power BI REST API.

Endpoints (admin-tenant scope):
  GET https://api.powerbi.com/v1.0/myorg/admin/groups?$top=5000
  GET https://api.powerbi.com/v1.0/myorg/admin/groups/{groupId}/datasets
  GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshes?$top=10

Falls back to per-workspace listing for non-admin users.

Docs:
  https://learn.microsoft.com/rest/api/power-bi/admin/datasets-get-datasets-as-admin
  https://learn.microsoft.com/rest/api/power-bi/datasets/get-refresh-history-in-group

DEEP-METRICS: Deeper model metrics (size on disk, mode, partition row counts)
would require XMLA + `$SYSTEM.TMSCHEMA_*` DMVs. See
docs/data-safety.md for the constraints.

DATA SAFETY: Metadata only. Refresh history contains start/end time, status,
duration, and error code. No dataset values are read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import filter_workspaces_by_scope, get_scope_workspace_ids, record_collection_failure
from collectors.workspace_scope import filter_review_payload, resolve_workspace_scope
from collectors._http import Headers, HttpError, collect_value, collect_workspace_groups, get_json
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"
REFRESH_TOP = 10


def _list_datasets_admin(headers: Headers, raw_dir: Path | None = None) -> List[Dict[str, Any]]:
    groups = collect_workspace_groups(f"{PBI}/admin/groups", headers)
    return _datasets_for_groups(headers, groups, raw_dir, admin=True)


def _list_datasets_per_workspace(headers: Headers, raw_dir: Path | None = None) -> List[Dict[str, Any]]:
    groups = collect_workspace_groups(f"{PBI}/groups", headers)
    return _datasets_for_groups(headers, groups, raw_dir, admin=False)


def _datasets_for_groups(headers: Headers, groups: list[dict], raw_dir: Path | None, *, admin: bool) -> list[dict]:
    groups = filter_workspaces_by_scope(resolve_workspace_scope(groups, headers, raw_dir))
    if raw_dir is not None:
        groups = filter_review_payload({"workspaces": groups}, raw_dir)["workspaces"]
    all_ds: List[Dict[str, Any]] = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            raise HttpError("Workspace listing returned a row without an ID")
        ds_list = collect_value(f"{PBI}/{'admin/' if admin else ''}groups/{gid}/datasets", headers)
        for ds in ds_list:
            ds["workspaceId"] = gid
            ds["workspaceName"] = g.get("name")
            all_ds.append(ds)
    return all_ds


def _refresh_history(headers: Headers, workspace_id: str, dataset_id: str) -> List[Dict[str, Any]]:
    url = f"{PBI}/groups/{workspace_id}/datasets/{dataset_id}/refreshes"
    payload = get_json(url, headers, params={"$top": REFRESH_TOP})
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("value"), list)
        or not all(isinstance(row, dict) for row in payload["value"])
        or len(payload["value"]) > REFRESH_TOP
        or any(payload.get(key) for key in ("@odata.nextLink", "nextLink", "continuationUri", "continuationToken"))
    ):
        # This endpoint documents $top, not continuation. Never follow an
        # unexpected URL with the Power BI bearer token.
        raise HttpError("Invalid refresh history response")
    return payload["value"]


@record_collection_failure("semantic_models.json")
def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=POWERBI_SCOPE)

    print("Semantic models: listing datasets...")
    scope = get_scope_workspace_ids()
    if scope:
        # Force per-workspace path when scoping, since /admin/datasets is global.
        datasets = _list_datasets_per_workspace(headers, Path(output_dir))
        before = len(datasets)
        datasets = [d for d in datasets if (d.get("workspaceId") or d.get("groupId") or "").lower() in scope]
        admin_mode = False
        print(f"  scoped to {len(datasets)}/{before} dataset(s) via WORKSPACE_IDS.")
    else:
        try:
            datasets = _list_datasets_admin(headers, Path(output_dir))
            admin_mode = True
        except HttpError as exc:
            if exc.status_code not in (401, 403) or exc.error_code == "IncompleteWorkspaceListing":
                raise
            datasets = _list_datasets_per_workspace(headers, Path(output_dir))
            admin_mode = False
    datasets = filter_review_payload({"datasets": datasets}, Path(output_dir))["datasets"]
    print(f"  {len(datasets)} dataset(s) found (admin_mode={admin_mode}).")

    refreshes: Dict[str, List[Dict[str, Any]]] = {}
    refresh_errors: Dict[str, Dict[str, Any]] = {}
    execution_evidence: List[Dict[str, Any]] = []
    for i, ds in enumerate(datasets, 1):
        wsid = ds.get("workspaceId") or ds.get("groupId")
        dsid = ds.get("id")
        evidence: Dict[str, Any] = {
            "workspaceId": wsid, "workspaceName": ds.get("workspaceName"),
            "itemId": dsid, "itemName": ds.get("name"), "itemType": "SemanticModel",
            "source": "powerbi_refresh_history",
            "historyScope": "recent_retained_observations",
            "collectionStatus": "not_collected", "statusCode": None,
            "noticeCode": "missing_item_identity", "executions": [],
        }
        execution_evidence.append(evidence)
        if not (wsid and dsid):
            continue
        try:
            refreshes[dsid] = _refresh_history(headers, wsid, dsid)
            evidence.update(
                collectionStatus="collected" if refreshes[dsid] else "empty",
                noticeCode="refresh_top_limit",
                executions=[
                    {key: row[key] for key in ("requestId", "id", "status", "startTime", "endTime")
                     if key in row}
                    for row in refreshes[dsid]
                ],
            )
        except HttpError as exc:
            refresh_errors[dsid] = {
                "statusCode": exc.status_code, "message": "Refresh history collection failed.",
            }
            evidence.update(
                collectionStatus=("forbidden" if exc.status_code in (401, 403)
                                  else "not_found" if exc.status_code == 404 else "error"),
                statusCode=exc.status_code, noticeCode="request_failed",
            )
        if i % 50 == 0:
            print(f"  refresh history: {i}/{len(datasets)}")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "semantic_models.json"
    target.write_text(
        json.dumps(
            {
                "adminMode": admin_mode,
                "datasets": datasets,
                "refreshes": refreshes,
                "refreshErrors": refresh_errors,
                "executionEvidence": execution_evidence,
                "workspaceScope": sorted(scope),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {target}.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

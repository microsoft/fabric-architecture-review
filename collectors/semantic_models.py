# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Semantic model inventory + refresh history via Power BI REST API.

Endpoints (admin-tenant scope):
  GET https://api.powerbi.com/v1.0/myorg/admin/datasets?$top=5000
  GET https://api.powerbi.com/v1.0/myorg/admin/datasets/{id}/refreshables  (optional)
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

from collectors._common import get_scope_workspace_ids
from collectors._http import collect_value, get_json
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"
REFRESH_TOP = 10


def _list_datasets_admin(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    return collect_value(f"{PBI}/admin/datasets", headers, params={"$top": 5000})


def _list_datasets_per_workspace(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    groups = collect_value(f"{PBI}/groups", headers, params={"$top": 5000})
    scope = get_scope_workspace_ids()
    if scope:
        groups = [g for g in groups if (g.get("id") or "").lower() in scope]
    all_ds: List[Dict[str, Any]] = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        ds_list = collect_value(f"{PBI}/groups/{gid}/datasets", headers)
        for ds in ds_list:
            ds["workspaceId"] = gid
            ds["workspaceName"] = g.get("name")
            all_ds.append(ds)
    return all_ds


def _refresh_history(headers: Dict[str, str], workspace_id: str, dataset_id: str) -> List[Dict[str, Any]]:
    url = f"{PBI}/groups/{workspace_id}/datasets/{dataset_id}/refreshes"
    payload = get_json(url, headers, params={"$top": REFRESH_TOP})
    if not payload:
        return []
    return payload.get("value") or []


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)

    print("Semantic models: listing datasets...")
    scope = get_scope_workspace_ids()
    if scope:
        # Force per-workspace path when scoping, since /admin/datasets is global.
        datasets = _list_datasets_per_workspace(headers)
        before = len(datasets)
        datasets = [d for d in datasets if (d.get("workspaceId") or d.get("groupId") or "").lower() in scope]
        admin_mode = False
        print(f"  scoped to {len(datasets)}/{before} dataset(s) via WORKSPACE_IDS.")
    else:
        datasets = _list_datasets_admin(headers)
        admin_mode = bool(datasets)
        if not datasets:
            datasets = _list_datasets_per_workspace(headers)
    print(f"  {len(datasets)} dataset(s) found (admin_mode={admin_mode}).")

    refreshes: Dict[str, List[Dict[str, Any]]] = {}
    for i, ds in enumerate(datasets, 1):
        wsid = ds.get("workspaceId") or ds.get("groupId")
        dsid = ds.get("id")
        if not (wsid and dsid):
            continue
        try:
            refreshes[dsid] = _refresh_history(headers, wsid, dsid)
        except Exception:
            refreshes[dsid] = []
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

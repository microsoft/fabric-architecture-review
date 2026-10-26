# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Architecture-only semantic model inventory via Power BI REST API.

Endpoints (admin-tenant scope):
  GET https://api.powerbi.com/v1.0/myorg/admin/groups?$top=5000
  GET https://api.powerbi.com/v1.0/myorg/admin/groups/{groupId}/datasets

Falls back to per-workspace listing for non-admin users.

Docs:
  https://learn.microsoft.com/rest/api/power-bi/admin/datasets-get-datasets-as-admin
DATA SAFETY: Catalog metadata only; no dataset values, refresh history or DAX.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import filter_workspaces_by_scope, get_scope_workspace_ids, record_collection_failure
from collectors.workspace_scope import filter_review_payload, resolve_workspace_scope
from collectors._http import Headers, HttpError, collect_value, collect_workspace_groups
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"


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


@record_collection_failure("semantic_models.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=POWERBI_SCOPE)

    print("Semantic models: listing datasets...")
    scope = get_scope_workspace_ids()
    try:
        # Admin discovery still filters workspace scope before listing its models.
        datasets = _list_datasets_admin(headers, Path(output_dir))
        admin_mode = True
    except HttpError as exc:
        if exc.status_code not in (401, 403) or exc.error_code == "IncompleteWorkspaceListing":
            raise
        print(f"Semantic models: admin discovery returned HTTP {exc.status_code}; trying member scope.")
        datasets = _list_datasets_per_workspace(headers, Path(output_dir))
        admin_mode = False
    if scope:
        before = len(datasets)
        datasets = [d for d in datasets if (d.get("workspaceId") or d.get("groupId") or "").lower() in scope]
        print(f"  scoped to {len(datasets)}/{before} dataset(s) via WORKSPACE_IDS.")
    datasets = filter_review_payload({"datasets": datasets}, Path(output_dir))["datasets"]
    print(f"  {len(datasets)} dataset(s) found (admin_mode={admin_mode}).")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "semantic_models.json"
    target.write_text(
        json.dumps(
            {
                "adminMode": admin_mode,
                "metadataOnly": True,
                "collectionComplete": True,
                "datasets": datasets,
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
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

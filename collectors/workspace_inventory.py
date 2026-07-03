# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace-level inventory: items, role assignments, capacity assignment.

This is the REST-API fallback / complement to ``collectors.scanner_api``. Where
the Scanner API requires Fabric/PBI Administrator and runs in batch mode, this
collector reads ``/v1/admin/groups`` per workspace and works incrementally for
both tenant admins (all workspaces) and workspace-scoped users (workspaces they
belong to).

Endpoints:
  - GET https://api.powerbi.com/v1.0/myorg/admin/groups?$top=5000  (admin)
    fallback: GET https://api.powerbi.com/v1.0/myorg/groups       (workspace member)
  - GET https://api.powerbi.com/v1.0/myorg/admin/groups/{id}/users (admin)
  - GET https://api.fabric.microsoft.com/v1/workspaces/{id}        (member)
  - GET https://api.fabric.microsoft.com/v1/workspaces/{id}/items  (member)

DATA SAFETY: Item names, types, IDs, capacity assignment, and role assignments
only. Item bodies / contents are never read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import get_scope_workspace_ids
from collectors._http import collect_value, get_json
from collectors.auth import FABRIC_SCOPE, POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"
FAB = "https://api.fabric.microsoft.com/v1"


def _list_workspaces(provider) -> List[Dict[str, Any]]:
    pbi_headers = provider.headers(scope=POWERBI_SCOPE)
    admin_url = f"{PBI}/admin/groups"
    admin = collect_value(admin_url, pbi_headers, params={"$top": 5000})
    if admin:
        return admin
    return collect_value(f"{PBI}/groups", pbi_headers, params={"$top": 5000})


def _list_users(provider, workspace_id: str, is_admin: bool) -> List[Dict[str, Any]]:
    headers = provider.headers(scope=POWERBI_SCOPE)
    if is_admin:
        return collect_value(f"{PBI}/admin/groups/{workspace_id}/users", headers)
    payload = get_json(f"{FAB}/workspaces/{workspace_id}/roleAssignments", provider.headers(scope=FABRIC_SCOPE))
    return (payload or {}).get("value") or []


def _list_items(provider, workspace_id: str) -> List[Dict[str, Any]]:
    fab_headers = provider.headers(scope=FABRIC_SCOPE)
    return collect_value(f"{FAB}/workspaces/{workspace_id}/items", fab_headers)


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    workspaces = _list_workspaces(provider)
    is_admin = any(ws.get("state") for ws in workspaces) and any("isReadOnly" in ws for ws in workspaces)
    scope = get_scope_workspace_ids()
    if scope:
        before = len(workspaces)
        workspaces = [w for w in workspaces if (w.get("id") or "").lower() in scope]
        print(f"Workspace inventory: scoped to {len(workspaces)}/{before} workspace(s) via WORKSPACE_IDS.")
    print(f"Workspace inventory: {len(workspaces)} workspace(s) visible (admin={is_admin}).")

    enriched: List[Dict[str, Any]] = []
    for i, ws in enumerate(workspaces, 1):
        wsid = ws.get("id")
        if not wsid:
            continue
        users = _list_users(provider, wsid, is_admin)
        items = _list_items(provider, wsid)
        enriched.append(
            {
                "id": wsid,
                "name": ws.get("name"),
                "type": ws.get("type"),
                "state": ws.get("state"),
                "isOnDedicatedCapacity": ws.get("isOnDedicatedCapacity"),
                "capacityId": ws.get("capacityId"),
                "description": ws.get("description"),
                "users": users,
                "items": items,
            }
        )
        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "workspace_inventory.json"
    target.write_text(
        json.dumps({"adminMode": is_admin, "workspaces": enriched}, indent=2, ensure_ascii=False),
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

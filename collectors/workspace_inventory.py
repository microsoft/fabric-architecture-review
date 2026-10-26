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
  - GET https://api.fabric.microsoft.com/v1/workspaces/{id}/roleAssignments (member)
  - GET https://api.fabric.microsoft.com/v1/workspaces/{id}/items  (member)

When the connected FUAM is passed in (``fuam_reader``), items and members are read from it first
and the endpoints above are called only for the workspaces, or the part of a workspace, that FUAM
does not cover. Each workspace records where its items and members came from.

DATA SAFETY: Item names, types, IDs, capacity assignment, and role assignments
only. Item bodies / contents are never read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

from collectors._common import filter_workspaces_by_scope, get_scope_workspace_ids, record_collection_failure
from collectors.workspace_scope import excluded_workspace_identities, resolve_workspace_scope, filter_review_payload
from collectors._http import HttpError, collect_value, collect_workspace_groups
from collectors.auth import FABRIC_SCOPE, POWERBI_SCOPE, get_default_provider
from collectors.fuam_inventory import FuamInventory

PBI = "https://api.powerbi.com/v1.0/myorg"
FAB = "https://api.fabric.microsoft.com/v1"


def _list_workspaces(provider) -> tuple[List[Dict[str, Any]], bool]:
    def pbi_headers():
        return provider.headers(scope=POWERBI_SCOPE)

    admin_url = f"{PBI}/admin/groups"
    try:
        return collect_workspace_groups(admin_url, pbi_headers), True
    except HttpError as exc:
        if exc.status_code not in (401, 403) or exc.error_code == "IncompleteWorkspaceListing":
            raise
        print(f"Workspace inventory: admin listing returned HTTP {exc.status_code}; trying member scope.")
    return collect_workspace_groups(f"{PBI}/groups", pbi_headers), False


def _list_users(provider, workspace_id: str, is_admin: bool) -> List[Dict[str, Any]]:
    if is_admin:
        return collect_value(
            f"{PBI}/admin/groups/{workspace_id}/users",
            lambda: provider.headers(scope=POWERBI_SCOPE),
        )
    assignments = collect_value(
        f"{FAB}/workspaces/{workspace_id}/roleAssignments",
        lambda: provider.headers(scope=FABRIC_SCOPE),
    )
    users = []
    for assignment in assignments:
        principal = assignment.get("principal") or {}
        if (
            not isinstance(principal, dict)
            or not isinstance(principal.get("type"), str)
            or not principal.get("id")
            or not isinstance(assignment.get("role"), str)
            or not isinstance(principal.get("userDetails") or {}, dict)
        ):
            raise HttpError(f"Workspace {workspace_id} returned an incomplete role assignment")
        users.append({
            "principalType": principal.get("type"),
            "graphId": principal.get("id"),
            "identifier": (principal.get("userDetails") or {}).get("userPrincipalName") or principal.get("id"),
            "displayName": principal.get("displayName"),
            "groupUserAccessRight": assignment.get("role"),
        })
    return users


def _list_items(provider, workspace_id: str) -> List[Dict[str, Any]]:
    return collect_value(
        f"{FAB}/workspaces/{workspace_id}/items",
        lambda: provider.headers(scope=FABRIC_SCOPE),
    )


@record_collection_failure("workspace_inventory.json")
def collect(
    output_dir: str | os.PathLike = "output/far/raw",
    fuam_reader: Callable[[List[str]], FuamInventory] | None = None,
) -> Path:
    provider = get_default_provider()
    workspaces, is_admin = _list_workspaces(provider)
    workspaces = resolve_workspace_scope(
        workspaces, lambda: provider.headers(scope=POWERBI_SCOPE), Path(output_dir),
    )
    excluded = excluded_workspace_identities(workspaces)
    scope = get_scope_workspace_ids()
    if scope:
        before = len(workspaces)
        workspaces = [w for w in workspaces if (w.get("id") or "").lower() in scope]
        print(f"Workspace inventory: scoped to {len(workspaces)}/{before} workspace(s) via WORKSPACE_IDS.")
    workspaces = filter_workspaces_by_scope(workspaces)
    workspaces = filter_review_payload({"workspaces": workspaces}, Path(output_dir))["workspaces"]
    print(f"Workspace inventory: {len(workspaces)} workspace(s) in review scope (admin={is_admin}).")

    fuam = fuam_reader([str(w["id"]) for w in workspaces if w.get("id")]) if fuam_reader else None
    if fuam is not None and not fuam.usable:
        print(f"Workspace inventory: FUAM unusable ({fuam.reason}); every workspace is read from the API.")
    from_fuam = {"items": 0, "users": 0}
    from_api = {"items": 0, "users": 0}
    enriched: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    workspace_list_complete = True
    for i, ws in enumerate(workspaces, 1):
        wsid = ws.get("id")
        if not wsid:
            workspace_list_complete = False
            errors.append({
                "component": "workspace_identity",
                "message": "Workspace listing returned a row without an ID.",
            })
            print("Workspace inventory incomplete: listing returned a row without an ID.")
            continue
        row: Dict[str, Any] = {
            "id": wsid,
            "name": ws.get("name"),
            "type": ws.get("type"),
            "state": ws.get("state"),
            "isOnDedicatedCapacity": ws.get("isOnDedicatedCapacity"),
            "isOnPremiumPerUserCapacity": ws.get("isOnPremiumPerUserCapacity"),
            "capacitySku": ws.get("capacitySku"),
            "capacityId": ws.get("capacityId"),
            "description": ws.get("description"),
        }
        for component in ("users", "items"):
            held = (fuam.users if component == "users" else fuam.items).get(str(wsid).lower()) if fuam and fuam.usable else None
            if held:
                row[component] = held
                row[f"{component}CollectionStatus"] = "collected"
                row[f"{component}DataSource"] = "fuam"
                from_fuam[component] += 1
                continue
            try:
                row[component] = (
                    _list_users(provider, wsid, is_admin) if component == "users"
                    else _list_items(provider, wsid)
                )
                row[f"{component}CollectionStatus"] = "collected"
                if fuam is not None:
                    row[f"{component}DataSource"] = "api"
                from_api[component] += 1
            except HttpError as exc:
                row[component] = None
                row[f"{component}CollectionStatus"] = "unavailable"
                errors.append({
                    "workspaceId": wsid, "component": component,
                    "statusCode": exc.status_code, "message": str(exc),
                })
                print(f"Workspace inventory incomplete: {wsid} {component}: {exc}")
        enriched.append(row)
        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    summary: Dict[str, Any] = {}
    if fuam is not None:
        used = fuam.usable and bool(from_fuam["items"] or from_fuam["users"])
        summary = {"dataSource": "fuam+api" if used else "api", "inventorySource": {
            "fuamObservedAt": fuam.observed_at, "fromFuam": from_fuam, "fromApi": from_api,
        }}
        if not used:
            summary["reason"] = fuam.reason or "no_workspace_covered"
            summary["fallback"] = "full_api_call"
        print(f"Workspace inventory: items from FUAM {from_fuam['items']} / API {from_api['items']}, "
              f"members from FUAM {from_fuam['users']} / API {from_api['users']}.")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "workspace_inventory.json"
    target.write_text(
        json.dumps({
            "adminMode": is_admin, "workspaces": enriched,
            "excludedWorkspaces": excluded,
            "workspaceListComplete": workspace_list_complete, "collectionComplete": not errors,
            "collectionErrors": errors, **summary,
        }, indent=2, ensure_ascii=False),
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

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""OneLake data access roles of each lakehouse.

Endpoint:
  GET https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{lakehouseId}/dataAccessRoles

Docs: https://learn.microsoft.com/rest/api/fabric/core/onelake-data-access-security/list-data-access-roles

DATA SAFETY: Only how many roles exist and how many are custom. Role names other than the built-in
default, members, paths and permissions are not kept.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import filter_workspaces_by_scope, load_workspace_inventory, record_collection_failure
from collectors._http import HttpError, collect_value
from collectors.auth import FABRIC_SCOPE, get_default_provider
from collectors.workspace_evidence import workspace_items_available

FAB = "https://api.fabric.microsoft.com/v1"
DEFAULT_ROLE = "defaultreader"


def _lakehouses(workspace: Dict[str, Any]) -> List[str]:
    if not workspace_items_available(workspace) or not isinstance(workspace.get("items"), list):
        return []
    return sorted(str(item["id"]) for item in workspace["items"]
                  if isinstance(item, dict) and item.get("type") == "Lakehouse" and item.get("id"))


@record_collection_failure("onelake_roles.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = [w for w in filter_workspaces_by_scope(load_workspace_inventory(raw_dir)) if w.get("id")]
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for workspace in workspaces:
        wsid = workspace["id"]
        for item_id in _lakehouses(workspace):
            try:
                roles = collect_value(f"{FAB}/workspaces/{wsid}/items/{item_id}/dataAccessRoles", headers)
            except HttpError as exc:
                results.append({"workspaceId": wsid, "id": item_id, "status": "unavailable"})
                errors.append({"workspaceId": wsid, "component": "data_access_roles",
                               "statusCode": exc.status_code, "message": "The data access roles could not be read."})
                continue
            custom = [role for role in roles if str(role.get("name") or "").casefold() != DEFAULT_ROLE]
            results.append({"workspaceId": wsid, "id": item_id, "status": "collected",
                            "roleCount": len(roles), "customRoleCount": len(custom)})
    target = raw_dir / "onelake_roles.json"
    target.write_text(json.dumps({"lakehouses": results, "collectionComplete": not errors, "collectionErrors": errors},
                                 indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(results)} lakehouse(s)).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

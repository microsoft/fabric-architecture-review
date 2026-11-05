# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Git integration configuration per workspace.

Endpoint:
  GET https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/git/connection

Docs: https://learn.microsoft.com/rest/api/fabric/core/git/get-connection

Workspace IDs are reconciled from eligible output/raw/scanner.json and
output/raw/workspace_inventory.json evidence.

DATA SAFETY: Returns Git provider, organization, repository, branch and
connection state only. No item contents are read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from collectors._http import request, response_error_code
from collectors._common import filter_workspaces_by_scope, load_workspace_inventory, record_collection_failure
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    """Return (workspace_id, workspace_name) for in-scope workspaces only."""
    return [(w["id"], w.get("name") or "")
            for w in filter_workspaces_by_scope(load_workspace_inventory(raw_dir)) if w.get("id")]


def read_connection(wsid: str, wsname: str, headers: Any) -> Dict[str, Any]:
    response = request("GET", f"{FAB}/workspaces/{wsid}/git/connection", headers)
    row: Dict[str, Any] = {"workspaceId": wsid, "workspaceName": wsname, "connected": False}
    if response.status_code == 200 and response.content:
        payload = response.json()
        state = payload.get("gitConnectionState")
        row.update(connected=state in ("Connected", "ConnectedAndInitialized") or bool(payload.get("gitProviderDetails")),
                   gitConnectionState=state, gitProviderDetails=payload.get("gitProviderDetails"),
                   gitSyncDetails=payload.get("gitSyncDetails"))
    elif response.status_code == 404:
        row["gitConnectionState"] = "NotConnected"
    else:
        category = "insufficient_permissions" if response.status_code in (401, 403) else "unexpected_status"
        row["error"] = f"{category} ({response.status_code})"
        code = response_error_code(response)
        if code:
            row["errorCode"] = code
    return row


@record_collection_failure("git_integration.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    if not workspaces:
        print("Git integration: no workspaces found in scanner.json or workspace_inventory.json — skipping.")
        target = raw_dir / "git_integration.json"
        target.write_text(json.dumps({"workspaces": []}, indent=2), encoding="utf-8")
        return target

    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)
    print(f"Git integration: probing {len(workspaces)} workspace(s)...")

    results: List[Dict[str, Any]] = []
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        results.append(read_connection(wsid, wsname, headers))
        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    target = raw_dir / "git_integration.json"
    target.write_text(
        json.dumps({"workspaces": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    connected = sum(1 for w in results if w.get("connected"))
    print(f"Wrote {target} ({connected}/{len(results)} connected).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

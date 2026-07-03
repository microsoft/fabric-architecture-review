# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Git integration configuration per workspace.

Endpoint:
  GET https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/git/connection

Docs: https://learn.microsoft.com/rest/api/fabric/core/git/get-connection

Source of workspace IDs (in this order):
  1. output/raw/scanner.json     (preferred — full tenant view)
  2. output/raw/workspace_inventory.json  (REST fallback)

DATA SAFETY: Returns Git provider, organization, repository, branch and
connection state only. No item contents are read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from collectors._http import request
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    """Return list of (workspace_id, workspace_name) from whatever inventory exists."""
    scan = raw_dir / "scanner.json"
    inv = raw_dir / "workspace_inventory.json"
    workspaces: List[Tuple[str, str]] = []
    if scan.exists():
        data = json.loads(scan.read_text(encoding="utf-8-sig"))
        for w in data.get("workspaces") or []:
            if w.get("id"):
                workspaces.append((w["id"], w.get("name") or ""))
    elif inv.exists():
        data = json.loads(inv.read_text(encoding="utf-8-sig"))
        for w in data.get("workspaces") or []:
            if w.get("id"):
                workspaces.append((w["id"], w.get("name") or ""))
    return workspaces


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    if not workspaces:
        print("Git integration: no workspaces found in scanner.json or workspace_inventory.json — skipping.")
        target = raw_dir / "git_integration.json"
        target.write_text(json.dumps({"workspaces": []}, indent=2), encoding="utf-8")
        return target

    provider = get_default_provider()
    headers = provider.headers(scope=FABRIC_SCOPE)
    print(f"Git integration: probing {len(workspaces)} workspace(s)...")

    results: List[Dict[str, Any]] = []
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        url = f"{FAB}/workspaces/{wsid}/git/connection"
        r = request("GET", url, headers)
        if r.status_code == 200 and r.content:
            payload = r.json()
            state = payload.get("gitConnectionState")
            # The Fabric API returns "Connected" for a workspace that is wired
            # to a repo but never synced, and "ConnectedAndInitialized" once
            # the first sync has happened. Both mean "under source control".
            is_connected = state in ("Connected", "ConnectedAndInitialized") or bool(
                payload.get("gitProviderDetails")
            )
            results.append(
                {
                    "workspaceId": wsid,
                    "workspaceName": wsname,
                    "connected": is_connected,
                    "gitConnectionState": state,
                    "gitProviderDetails": payload.get("gitProviderDetails"),
                    "gitSyncDetails": payload.get("gitSyncDetails"),
                }
            )
        elif r.status_code in (401, 403):
            results.append(
                {
                    "workspaceId": wsid,
                    "workspaceName": wsname,
                    "connected": False,
                    "error": f"insufficient_permissions ({r.status_code})",
                }
            )
        else:
            results.append(
                {
                    "workspaceId": wsid,
                    "workspaceName": wsname,
                    "connected": False,
                    "gitConnectionState": "NotConnected",
                }
            )
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
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

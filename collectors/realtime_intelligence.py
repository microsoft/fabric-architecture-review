# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Real-Time Intelligence + mirroring inventory per workspace.

Endpoints (Fabric REST, all workspace-scoped):
  GET https://api.fabric.microsoft.com/v1/workspaces/{id}/eventhouses
  GET https://api.fabric.microsoft.com/v1/workspaces/{id}/kqlDatabases
  GET https://api.fabric.microsoft.com/v1/workspaces/{id}/eventstreams
  GET https://api.fabric.microsoft.com/v1/workspaces/{id}/reflexes
  GET https://api.fabric.microsoft.com/v1/workspaces/{id}/mirroredDatabases

Iterates workspaces from scanner.json (preferred) or workspace_inventory.json.

Docs:
  https://learn.microsoft.com/fabric/real-time-intelligence/overview
  https://learn.microsoft.com/fabric/database/mirrored-database/overview

DATA SAFETY: Item metadata only (name, id, kind, properties block). No KQL,
no eventstream payloads, no mirrored table contents are read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._http import collect_value
from collectors.auth import FABRIC_SCOPE, get_default_provider

FABRIC = "https://api.fabric.microsoft.com/v1"

ITEM_KINDS = ("eventhouses", "kqlDatabases", "eventstreams", "reflexes", "mirroredDatabases")


def _list_workspace_ids(raw_dir: Path) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for fname in ("scanner.json", "workspace_inventory.json"):
        p = raw_dir / fname
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        for ws in data.get("workspaces") or []:
            wsid = ws.get("id")
            if not wsid:
                continue
            if ws.get("type") == "PersonalGroup":
                continue
            out.append({"id": wsid, "name": ws.get("name") or ""})
        if out:
            break
    return out


def _list_items(headers: Dict[str, str], workspace_id: str, kind: str) -> List[Dict[str, Any]]:
    try:
        return collect_value(f"{FABRIC}/workspaces/{workspace_id}/{kind}", headers)
    except Exception:
        return []


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = provider.headers(scope=FABRIC_SCOPE)

    workspaces = _list_workspace_ids(Path(output_dir))
    print(f"Real-Time Intelligence: scanning {len(workspaces)} workspace(s)...")

    by_kind: Dict[str, List[Dict[str, Any]]] = {k: [] for k in ITEM_KINDS}
    for i, ws in enumerate(workspaces, 1):
        for kind in ITEM_KINDS:
            for item in _list_items(headers, ws["id"], kind):
                item["workspaceId"] = ws["id"]
                item["workspaceName"] = ws["name"]
                by_kind[kind].append(item)
        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    summary = {k: len(v) for k, v in by_kind.items()}
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    out = target / "realtime_intelligence.json"
    out.write_text(json.dumps({
        "summary": summary,
        **by_kind,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(summary.values())
    print(f"Wrote {out} (total RTI/mirrored items: {total}; {summary}).")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

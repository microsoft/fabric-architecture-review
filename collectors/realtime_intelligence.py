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

collectionCoverage records each workspace/type attempt. Partial inventories
retain observed items, but their summary counts are lower bounds, not totals.
Personal workspaces are explicitly not applicable; HTTP failures are unknown
coverage, not evidence of absence or non-applicability.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List

from collectors._http import Headers, HttpError, paginate_value
from collectors._common import load_workspace_inventory, record_collection_failure
from collectors.auth import FABRIC_SCOPE, get_default_provider

FABRIC = "https://api.fabric.microsoft.com/v1"

ITEM_KINDS = ("eventhouses", "kqlDatabases", "eventstreams", "reflexes", "mirroredDatabases")


def _list_workspace_ids(raw_dir: Path) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for ws in load_workspace_inventory(raw_dir):
        wsid = ws.get("id")
        if not wsid:
            continue
        out.append({"id": wsid, "name": ws.get("name") or "", "type": ws.get("type") or ""})
    return out


def _list_items(headers: Headers, workspace_id: str, kind: str) -> Iterator[Dict[str, Any]]:
    return paginate_value(f"{FABRIC}/workspaces/{workspace_id}/{kind}", headers)


@record_collection_failure("realtime_intelligence.json")
def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    workspaces = _list_workspace_ids(Path(output_dir))
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)

    print(f"Real-Time Intelligence: scanning {len(workspaces)} workspace(s)...")

    by_kind: Dict[str, List[Dict[str, Any]]] = {k: [] for k in ITEM_KINDS}
    coverage: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for i, ws in enumerate(workspaces, 1):
        for kind in ITEM_KINDS:
            row: Dict[str, Any] = {
                "workspaceId": ws["id"], "component": kind,
                "collectionStatus": "collected", "observedCount": 0,
            }
            coverage.append(row)
            if ws.get("type") == "PersonalGroup":
                row.update(collectionStatus="not_applicable", reasonCode="personal_workspace")
                continue
            try:
                for item in _list_items(headers, ws["id"], kind):
                    item["workspaceId"] = ws["id"]
                    item["workspaceName"] = ws["name"]
                    by_kind[kind].append(item)
                    row["observedCount"] += 1
            except HttpError as exc:
                row["collectionStatus"] = "partial" if row["observedCount"] else "unavailable"
                error = {
                    "workspaceId": ws["id"], "component": kind,
                    "statusCode": exc.status_code, "message": str(exc),
                }
                if exc.error_code:
                    error["errorCode"] = exc.error_code
                errors.append(error)
                print(f"Real-Time Intelligence collection incomplete ({kind}): {exc}")
        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    summary = {k: len(v) for k, v in by_kind.items()}
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    out = target / "realtime_intelligence.json"
    out.write_text(json.dumps({
        "summary": summary,
        **by_kind,
        "collectionComplete": not errors,
        "collectionErrors": errors,
        "collectionCoverage": coverage,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(summary.values())
    print(f"Wrote {out} (observed RTI/mirrored items: {total}; {summary}).")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

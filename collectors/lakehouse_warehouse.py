# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Lakehouse and Warehouse inventory via Fabric REST.

Endpoints:
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/lakehouses
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/lakehouses/{id}/tables
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/warehouses

Workspace IDs come from scanner.json / workspace_inventory.json.

DEEP-METRICS: OneLake DFS recursive filesystem listing (file count + size per
table) would require the Azure Storage DataLake SDK + OAuth-on-OneLake setup.
Not in REST-only mode.

DATA SAFETY: Table NAMES and metadata only. No SELECT against Warehouse/SQL
endpoints. No file content reads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from collectors._http import collect_value, request
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    for fname in ("scanner.json", "workspace_inventory.json"):
        p = raw_dir / fname
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        out = [(w["id"], w.get("name") or "") for w in (data.get("workspaces") or []) if w.get("id")]
        if out:
            return out
    return []


def _tables(headers: Dict[str, str], wsid: str, lakehouse_id: str) -> List[Dict[str, Any]]:
    url = f"{FAB}/workspaces/{wsid}/lakehouses/{lakehouse_id}/tables"
    try:
        r = request("GET", url, headers, params={"maxResults": 100})
        if r.status_code == 200 and r.content:
            return r.json().get("data") or r.json().get("value") or []
    except Exception:
        pass
    return []


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    if not workspaces:
        print("Lakehouse/Warehouse: no workspace inventory found — run scanner_api or workspace_inventory first.")
        target = raw_dir / "lakehouse_warehouse.json"
        target.write_text(json.dumps({"lakehouses": [], "warehouses": [], "tables": {}}, indent=2), encoding="utf-8")
        return target

    provider = get_default_provider()
    headers = provider.headers(scope=FABRIC_SCOPE)

    lakehouses: List[Dict[str, Any]] = []
    warehouses: List[Dict[str, Any]] = []
    tables_index: Dict[str, List[Dict[str, Any]]] = {}

    print(f"Lakehouse/Warehouse: scanning {len(workspaces)} workspace(s)...")
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        lhs = collect_value(f"{FAB}/workspaces/{wsid}/lakehouses", headers)
        for lh in lhs:
            lh["workspaceId"] = wsid
            lh["workspaceName"] = wsname
            lakehouses.append(lh)
            lhid = lh.get("id")
            if lhid:
                tables_index[lhid] = _tables(headers, wsid, lhid)

        whs = collect_value(f"{FAB}/workspaces/{wsid}/warehouses", headers)
        for wh in whs:
            wh["workspaceId"] = wsid
            wh["workspaceName"] = wsname
            warehouses.append(wh)

        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    target = raw_dir / "lakehouse_warehouse.json"
    target.write_text(
        json.dumps(
            {"lakehouses": lakehouses, "warehouses": warehouses, "tables": tables_index},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {target} ({len(lakehouses)} lakehouses, {len(warehouses)} warehouses).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

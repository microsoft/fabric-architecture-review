# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capacity inventory + REST-accessible utilization signals.

Endpoints:
  GET https://api.powerbi.com/v1.0/myorg/admin/capacities             (all)
  GET https://api.powerbi.com/v1.0/myorg/capacities                   (assigned to me)
  GET https://api.powerbi.com/v1.0/myorg/capacities/{id}/refreshables (per capacity)
  GET https://api.powerbi.com/v1.0/myorg/admin/capacities/{id}/Workloads

Workspace-to-capacity mapping is derived from scanner.json /
workspace_inventory.json when available, so we can report capacity utilization
patterns (workspaces per capacity, refreshable density).

DEEP-METRICS: Real CU%, throttling counters, and background-rejection metrics
require either the Fabric Capacity Metrics App's semantic model (XMLA) or
Azure Monitor metrics for the Microsoft.Fabric/capacities resource. Both are
out of scope for REST-only mode; see ``docs/data-safety.md``.

DATA SAFETY: Capacity name, SKU, admin list, refreshable counts, and workload
configuration only. No dataset content is read.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from collectors._http import collect_value, get_json
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"


def _list_capacities(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    admin = collect_value(f"{PBI}/admin/capacities", headers)
    if admin:
        return admin
    return collect_value(f"{PBI}/capacities", headers)


def _refreshables(headers: Dict[str, str], capacity_id: str) -> List[Dict[str, Any]]:
    url = f"{PBI}/capacities/{capacity_id}/refreshables"
    payload = get_json(url, headers, params={"$top": 1000})
    if not payload:
        return []
    return payload.get("value") or []


def _workloads(headers: Dict[str, str], capacity_id: str) -> List[Dict[str, Any]]:
    url = f"{PBI}/admin/capacities/{capacity_id}/Workloads"
    payload = get_json(url, headers)
    if not payload:
        return []
    return payload.get("value") or payload.get("workloads") or []


def _workspaces_by_capacity(raw_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = {}
    for fname in ("scanner.json", "workspace_inventory.json"):
        p = raw_dir / fname
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        for ws in data.get("workspaces") or []:
            cap = ws.get("capacityId") or ""
            if not cap:
                continue
            out.setdefault(cap.lower(), []).append({"id": ws.get("id"), "name": ws.get("name")})
        if out:
            break
    return out


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)

    print("Capacity metrics: listing capacities...")
    capacities = _list_capacities(headers)
    print(f"  {len(capacities)} capacity(ies) visible.")

    workspaces_by_cap = _workspaces_by_capacity(Path(output_dir))

    enriched: List[Dict[str, Any]] = []
    for i, cap in enumerate(capacities, 1):
        cid = cap.get("id") or cap.get("capacityId")
        if not cid:
            continue
        refs = _refreshables(headers, cid)
        wls = _workloads(headers, cid)
        ws_list = workspaces_by_cap.get(cid.lower(), [])
        enriched.append(
            {
                "id": cid,
                "displayName": cap.get("displayName") or cap.get("name"),
                "sku": cap.get("sku"),
                "state": cap.get("state"),
                "region": cap.get("region"),
                "admins": cap.get("admins"),
                "tenantKeyId": cap.get("tenantKeyId"),
                "refreshableCount": len(refs),
                "refreshables": refs,
                "workloads": wls,
                "assignedWorkspaceCount": len(ws_list),
                "assignedWorkspaces": ws_list,
            }
        )
        if i % 10 == 0:
            print(f"  ... {i}/{len(capacities)}")

    sku_counter = Counter([c.get("sku") for c in enriched if c.get("sku")])
    summary = {
        "capacityCount": len(enriched),
        "skuDistribution": dict(sku_counter),
        "emptyCapacities": [c["displayName"] for c in enriched if c["assignedWorkspaceCount"] == 0],
    }

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "capacity_metrics.json"
    target.write_text(
        json.dumps(
            {"summary": summary, "capacities": enriched, "deepMetricsAvailable": False},
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

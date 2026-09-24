# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capacity inventory + REST-accessible utilization signals.

Endpoints:
  GET https://api.powerbi.com/v1.0/myorg/admin/capacities             (all)
  GET https://api.powerbi.com/v1.0/myorg/capacities                   (assigned to me)
  GET https://api.powerbi.com/v1.0/myorg/capacities/{id}/refreshables (per capacity)
  GET https://api.powerbi.com/v1.0/myorg/capacities/{id}/Workloads (legacy)

Workload configuration is not relevant for Gen2 capacities; an unavailable
workload endpoint does not invalidate capacity inventory or other probes.

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

from collectors._common import get_scope_workspace_ids, load_workspace_inventory, record_collection_failure
from collectors._http import Headers, HttpError, collect_value
from collectors.auth import POWERBI_SCOPE, get_default_provider
from collectors.workspace_scope import is_excluded_capacity, filter_review_payload

PBI = "https://api.powerbi.com/v1.0/myorg"


def _list_capacities(headers: Headers) -> List[Dict[str, Any]]:
    try:
        return collect_value(f"{PBI}/admin/capacities", headers)
    except HttpError as exc:
        if exc.status_code not in (401, 403):
            raise
        return collect_value(f"{PBI}/capacities", headers)


def _refreshables(headers: Headers, capacity_id: str) -> List[Dict[str, Any]]:
    url = f"{PBI}/capacities/{capacity_id}/refreshables"
    return collect_value(url, headers, params={"$top": 1000})


def _workloads(headers: Headers, capacity_id: str) -> List[Dict[str, Any]]:
    return collect_value(f"{PBI}/capacities/{capacity_id}/Workloads", headers)


def _workspaces_by_capacity(raw_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = {}
    for ws in load_workspace_inventory(raw_dir):
        cap = ws.get("capacityId") or ""
        if not cap:
            continue
        out.setdefault(cap.lower(), []).append({"id": ws.get("id"), "name": ws.get("name")})
    return out


@record_collection_failure("capacity_metrics.json")
def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=POWERBI_SCOPE)

    print("Capacity metrics: listing capacities...")
    capacities = _list_capacities(headers)
    excluded = [cap for cap in capacities if is_excluded_capacity(cap)]
    capacities = [cap for cap in capacities if not is_excluded_capacity(cap)]
    print(f"  {len(capacities)} capacity(ies) visible.")

    errors: List[Dict[str, Any]] = []
    try:
        workspaces_by_cap = _workspaces_by_capacity(Path(output_dir))
    except HttpError as exc:
        workspaces_by_cap = None
        errors.append({"component": "workspace_mapping", "statusCode": exc.status_code, "message": str(exc)})
        print(f"Capacity metrics incomplete: workspace mapping: {exc}")
    workspace_scope_limited = bool(get_scope_workspace_ids())

    enriched: List[Dict[str, Any]] = []
    capacity_list_complete = True
    for i, cap in enumerate(capacities, 1):
        cid = cap.get("id") or cap.get("capacityId")
        if not cid:
            capacity_list_complete = False
            errors.append({"component": "capacity_identity", "message": "Capacity listing returned a row without an ID."})
            print("Capacity metrics incomplete: listing returned a row without an ID.")
            continue
        ws_list = workspaces_by_cap.get(cid.lower(), []) if workspaces_by_cap is not None else None
        row: Dict[str, Any] = {
            "id": cid,
            "displayName": cap.get("displayName") or cap.get("name"),
            "sku": cap.get("sku"),
            "state": cap.get("state"),
            "region": cap.get("region"),
            "admins": cap.get("admins"),
            "tenantKeyId": cap.get("tenantKeyId"),
            "assignedWorkspaceCount": len(ws_list) if ws_list is not None else None,
            "assignedWorkspaces": ws_list,
            "workspaceScopeLimited": workspace_scope_limited,
        }
        for component, probe in (("refreshables", _refreshables), ("workloads", _workloads)):
            try:
                row[component] = filter_review_payload(
                    {component: probe(headers, cid)}, Path(output_dir),
                )[component]
                row[f"{component}CollectionStatus"] = "collected"
            except HttpError as exc:
                row[component] = None
                row[f"{component}CollectionStatus"] = "unavailable"
                errors.append({
                    "capacityId": cid, "component": component,
                    "statusCode": exc.status_code, "message": str(exc),
                })
                print(f"Capacity metrics incomplete: {cid} {component}: {exc}")
        row["refreshableCount"] = len(row["refreshables"]) if row["refreshables"] is not None else None
        enriched.append(row)
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
            {
                "summary": summary,
                "capacities": enriched,
                "excludedCapacities": excluded,
                "capacityListComplete": capacity_list_complete,
                "workspaceMappingComplete": workspaces_by_cap is not None,
                "collectionComplete": not errors,
                "collectionErrors": errors,
                "deepMetricsAvailable": False,
                "workspaceScopeLimited": workspace_scope_limited,
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
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

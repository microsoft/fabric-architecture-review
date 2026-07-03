# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Query the Fabric Capacity Metrics App semantic model via Power BI REST.

This collector runs as the signed-in user (same `TokenProvider` chain the
other collectors use), so deep CU% / throttling signals can be fetched
without provisioning a service principal or installing the .NET ADOMD stack
that XMLA normally requires.

Flow:
  1. Locate the Metrics App dataset:
     - explicit override via ``METRICS_APP_WORKSPACE_ID`` + ``METRICS_APP_DATASET_ID``,
     - else scan ``/v1.0/myorg/admin/datasets`` for one whose name contains
       "Capacity Metrics".
  2. POST a small set of DAX probes to
     ``/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/executeQueries``.
  3. Persist results (and any per-query errors) to
     ``output/raw/capacity_metrics_app.json``.

The probes are written defensively because the Metrics App schema is owned
by Microsoft and evolves; if a probe fails (unknown measure / table) the
error is captured and the rest of the run continues.

DATA SAFETY: Only the Metrics App's own metrics tables are queried (CU%,
throttling minutes, capacity inventory). No customer business data is read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from collectors._http import collect_value, request
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"
DATASET_NAME_HINT = os.environ.get("METRICS_APP_DATASET_NAME_HINT", "Capacity Metrics").lower()


def _find_dataset(headers: Dict[str, str]) -> Optional[Dict[str, str]]:
    ws_id = os.environ.get("METRICS_APP_WORKSPACE_ID")
    ds_id = os.environ.get("METRICS_APP_DATASET_ID")
    if ws_id and ds_id:
        return {"workspaceId": ws_id, "datasetId": ds_id, "name": "(env override)", "source": "env"}

    print("  Scanning admin/datasets for the Capacity Metrics App...")
    datasets = collect_value(f"{PBI}/admin/datasets", headers)
    for d in datasets:
        name = (d.get("name") or "").lower()
        if DATASET_NAME_HINT in name:
            return {
                "workspaceId": d.get("workspaceId") or d.get("groupId") or "",
                "datasetId": d.get("id"),
                "name": d.get("name"),
                "source": "admin/datasets",
            }
    return None


def _execute_dax(
    headers: Dict[str, str], workspace_id: str, dataset_id: str, dax: str
) -> Dict[str, Any]:
    url = f"{PBI}/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
    body = {
        "queries": [{"query": dax}],
        "serializerSettings": {"includeNulls": True},
    }
    r = request("POST", url, headers, json_body=body, timeout=120)
    if r.status_code == 200:
        payload = r.json()
        tables = (payload.get("results") or [{}])[0].get("tables") or []
        rows = tables[0].get("rows") if tables else []
        return {"ok": True, "rowCount": len(rows or []), "rows": rows}
    return {
        "ok": False,
        "status": r.status_code,
        "error": (r.text or "")[:800],
    }


# DAX probes ----------------------------------------------------------------
# Each probe is (name, dax). Names are stable keys consumed by the analyzer.
#
# The Microsoft Fabric Capacity Metrics App ships with pre-aggregated per-
# capacity tables ("Usage Summary By Capacities (Last X)") that already have
# Average CU %, P95 throttling deltas, and a Health flag baked in - querying
# them directly avoids fighting with the model's date-context measures.
#
# We also pull the schema info tables so the raw output is still useful for
# debugging if Microsoft renames a column in a future Metrics App version.
PROBES: List[Dict[str, str]] = [
    {"name": "info_tables",            "dax": "EVALUATE INFO.VIEW.TABLES()"},
    {"name": "info_measures",          "dax": "EVALUATE INFO.VIEW.MEASURES()"},
    {"name": "capacities_sample",      "dax": "EVALUATE TOPN(1, Capacities)"},
    {"name": "usage_summary_7d",       "dax": "EVALUATE 'Usage Summary By Capacities (Last 7 days)'"},
    {"name": "usage_summary_24h",      "dax": "EVALUATE 'Usage Summary By Capacities (Last 24 hours)'"},
    {"name": "usage_summary_1h",       "dax": "EVALUATE 'Usage Summary By Capacities (Last 1 hour)'"},
    {"name": "items_throttled",        "dax": "EVALUATE 'Items Throttled'"},
    {"name": "surge_protection_by_day","dax": "EVALUATE 'Surge Protection By Day'"},
]


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    from dotenv import load_dotenv
    load_dotenv()
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "capacity_metrics_app.json"

    # Honor the reviewer's explicit opt-out. When CAPACITY_METRICS_APP_INSTALLED
    # is falsy we MUST NOT issue any DAX / executeQueries call - the entire
    # purpose of the flag is to attest that the Metrics App is not present.
    flag_raw = (os.environ.get("CAPACITY_METRICS_APP_INSTALLED") or "").strip().lower()
    if flag_raw in ("0", "false", "no", "n", "off"):
        out = {
            "datasetLocated": False,
            "skipped": True,
            "notes": [
                "CAPACITY_METRICS_APP_INSTALLED is set to a falsy value; no "
                "executeQueries / DAX calls were issued against any semantic model."
            ],
        }
        target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Capacity metrics app: skipped (CAPACITY_METRICS_APP_INSTALLED=false). Wrote {target}.")
        return target

    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)

    out: Dict[str, Any] = {
        "datasetLocated": False,
        "dataset": None,
        "queries": {},
        "notes": [],
    }

    print("Capacity metrics app: locating dataset...")
    ds = _find_dataset(headers)
    if not ds:
        out["notes"].append(
            "Capacity Metrics App dataset not found via admin/datasets. "
            "Install it from AppSource and re-run, or set METRICS_APP_WORKSPACE_ID "
            "and METRICS_APP_DATASET_ID in .env to point at it explicitly."
        )
        target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Dataset not found. Wrote {target}.")
        return target

    out["datasetLocated"] = True
    out["dataset"] = ds
    print(f"  Using dataset '{ds.get('name')}' in workspace {ds.get('workspaceId')}.")

    if not ds.get("workspaceId"):
        out["notes"].append(
            "Dataset workspaceId is unknown (admin/datasets returned no group). "
            "Set METRICS_APP_WORKSPACE_ID in .env."
        )
        target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Workspace id missing. Wrote {target}.")
        return target

    for probe in PROBES:
        name = probe["name"]
        print(f"  Running probe: {name} ...")
        result = _execute_dax(headers, ds["workspaceId"], ds["datasetId"], probe["dax"])
        result["dax"] = probe["dax"]
        out["queries"][name] = result
        if result["ok"]:
            print(f"    -> {result['rowCount']} row(s).")
        else:
            print(f"    -> failed (status {result.get('status')}).")

    target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target}.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

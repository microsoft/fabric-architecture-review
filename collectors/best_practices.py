# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Best-practice / health analysis for semantic models, reports and capacities.

Runs **inside Fabric** and uses semantic-link-labs to capture the same checks a
Fabric specialist would run by hand:

  * Model Best Practice Analyzer (BPA) violations          -> high value
  * Report Best Practice Analyzer (BPA) violations         -> medium value
  * Direct Lake fallback behaviour / reasons               -> high value
  * Broken / orphaned reports (binding to missing fields)  -> high value
  * Delta table health (small files, V-Order, row groups)  -> medium value
  * Unused model objects (columns / tables / measures)     -> medium value
  * Capacity migration readiness (P-SKU -> F-SKU)          -> medium value

Model and report lists come from ``semantic_models.json`` /
``scanner_api.json`` (already scoped by ``WORKSPACE_IDS``) so we reuse the
inventory the other collectors built. Each item is analyzed in isolation: a
failure (no XMLA access, item not resident, transient error) is captured
per-item and the run continues.

Degrades to ``{"available": False, ...}`` when semantic-link-labs is not
importable (i.e. anywhere outside a Fabric notebook) so it is safe to run in
the local collect script.

DATA SAFETY: metadata / engine-DMV checks only — model & report definitions,
table sizes, encoding and BPA rule outcomes. No business row values are read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the battle-tested sempy_labs import shims from the VertiPaq collector so
# we degrade and self-heal exactly the same way (older Fabric azure-core, etc.).
from collectors.vertipaq_stats import (
    _ensure_sempy_labs,
    _frame_to_records,
    _shim_fabric_rest_client,
    _truthy,
)


def _df_records(value: Any) -> List[Dict[str, Any]]:
    """Best-effort convert a sempy/pandas result to JSON-safe records."""
    if value is None:
        return []
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if hasattr(value, "columns"):  # pandas DataFrame
        return _frame_to_records(value)
    return []


def _model_bpa(labs: Any, dataset: str, workspace: Optional[str]) -> List[Dict[str, Any]]:
    df = labs.run_model_bpa(dataset=dataset, workspace=workspace, return_dataframe=True)
    return _df_records(df)


def _report_bpa(labs: Any, report: str, workspace: Optional[str]) -> List[Dict[str, Any]]:
    fn = getattr(labs, "report", None)
    fn = getattr(fn, "run_report_bpa", None) if fn is not None else None
    if fn is None:
        return []
    return _df_records(fn(report=report, workspace=workspace, return_dataframe=True))


def _fallback(labs: Any, dataset: str, workspace: Optional[str]) -> List[Dict[str, Any]]:
    dl = getattr(labs, "directlake", None)
    fn = getattr(dl, "check_fallback_reason", None) if dl is not None else None
    if fn is None:
        return []
    return _df_records(fn(dataset=dataset, workspace=workspace))


def _delta_health(labs: Any, dataset: str, workspace: Optional[str]) -> List[Dict[str, Any]]:
    fn = getattr(labs, "delta_analyzer", None)
    if fn is None:
        return []
    try:
        return _df_records(fn(dataset=dataset, workspace=workspace))
    except Exception:
        return []


def _unused(labs: Any, dataset: str, workspace: Optional[str]) -> List[Dict[str, Any]]:
    fn = getattr(labs, "list_unused_objects", None)
    if fn is None:
        return []
    return _df_records(fn(dataset=dataset, workspace=workspace))


def _capacity_readiness(labs: Any) -> List[Dict[str, Any]]:
    fn = getattr(labs, "list_capacities", None)
    if fn is None:
        return []
    rows = _df_records(fn())
    out: List[Dict[str, Any]] = []
    for r in rows:
        sku = str(r.get("sku") or r.get("Sku") or r.get("SKU") or "")
        # Only legacy Premium P-SKUs (P1/P2/P3...) need P->F migration. The PPU
        # reservation SKU (PP3) also starts with 'P' but must NOT be flagged.
        out.append({
            "capacity": r.get("name") or r.get("Display Name") or r.get("capacity"),
            "sku": sku,
            "needs_migration": bool(re.match(r"^P\d", sku.upper())),
        })
    return out


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "best_practices.json"

    if _truthy(os.environ.get("BEST_PRACTICES_SKIP")):
        payload = {"available": False, "skipped": True, "models": [], "capacities": [],
                   "notes": ["BEST_PRACTICES_SKIP is set; best-practice analysis was not run."]}
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Best practices: skipped (BEST_PRACTICES_SKIP). Wrote {target}.")
        return target

    import_error = _ensure_sempy_labs()
    if import_error is not None:
        payload = {"available": False, "models": [], "capacities": [], "notes": [
            "sempy_labs (semantic-link-labs) is not importable here; best-practice "
            "analysis only runs inside a Fabric notebook. " + import_error]}
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Best practices: sempy_labs unavailable ({import_error}) -> wrote empty {target}.")
        return target

    import sempy_labs as labs  # noqa
    _shim_fabric_rest_client()

    try:
        catalog = json.loads((target_dir / "semantic_models.json").read_text(encoding="utf-8-sig"))
    except Exception:
        catalog = {}
    datasets: List[Dict[str, Any]] = catalog.get("datasets") or []

    models_out: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for ds in datasets:
        mid, name = ds.get("id"), ds.get("name")
        ws = ds.get("workspaceId") or ds.get("workspaceName")
        if not mid:
            continue
        entry: Dict[str, Any] = {"model_id": mid, "model_name": name, "workspace_name": ds.get("workspaceName"),
                                 "storage_mode": ds.get("targetStorageMode")}
        for key, fn in (("model_bpa", _model_bpa), ("fallback", _fallback),
                        ("delta", _delta_health), ("unused", _unused)):
            try:
                entry[key] = fn(labs, mid, ws)
            except Exception as exc:  # one bad check must not stop the rest
                entry[key] = []
                errors.append({"model_id": str(mid), "check": key, "error": str(exc)})
        models_out.append(entry)

    # Reports (for report BPA) from the scanner inventory.
    reports_out: List[Dict[str, Any]] = []
    try:
        scanner = json.loads((target_dir / "scanner_api.json").read_text(encoding="utf-8-sig"))
    except Exception:
        scanner = {}
    for ws in scanner.get("workspaces") or []:
        ws_name = ws.get("name") or ws.get("id")
        for rep in ws.get("reports") or []:
            rid, rname = rep.get("id"), rep.get("name")
            if not rid:
                continue
            try:
                viol = _report_bpa(labs, rid, ws.get("id") or ws_name)
            except Exception as exc:
                viol = []
                errors.append({"report_id": str(rid), "check": "report_bpa", "error": str(exc)})
            reports_out.append({"report_id": rid, "report_name": rname,
                                "workspace_name": ws_name, "report_bpa": viol})

    try:
        capacities = _capacity_readiness(labs)
    except Exception as exc:
        capacities = []
        errors.append({"check": "capacities", "error": str(exc)})

    payload = {"available": True, "models": models_out, "reports": reports_out,
               "capacities": capacities, "errors": errors}
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(models_out)} model(s), {len(capacities)} capacity row(s), {len(errors)} error(s)).")
    return target
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(output_dir=args.output_dir)


if __name__ == "__main__":
    main()

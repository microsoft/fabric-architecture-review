# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Best-practice / health review (BPA, Direct Lake fallback, Delta, unused, capacity).

Reads ``best_practices.json`` (produced by ``collectors.best_practices`` inside
Fabric via semantic-link-labs) and emits one rolled-up finding per check across
the estate:

  BPA-001  Model Best Practice Analyzer violations           (high)
  BPA-002  Broken / orphaned reports                         (high)
  BPA-003  Direct Lake fallback to DirectQuery               (high)
  BPA-004  Report Best Practice Analyzer violations          (medium)
  BPA-005  Delta table health (small files / V-Order)        (medium)
  BPA-006  Unused model objects (columns/tables/measures)    (medium)
  BPA-007  Capacity migration readiness (P-SKU -> F-SKU)     (medium)

If the raw file is missing or semantic-link-labs was unavailable, each rule is
reported as ``info`` so the dimension always appears in the report.

DATA SAFETY: BPA outcomes + engine metadata only. No customer data.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from analyzers._common import load_raw, load_rules, make_finding, write_findings

DIMENSION = "best_practices"


def _rule(rules: Dict[str, Any], rid: str, sev: str) -> Dict[str, Any]:
    return rules.get(rid) or {"id": rid, "severity": sev}


def _sum(models: List[Dict[str, Any]], key: str) -> int:
    return sum(len(m.get(key) or []) for m in models)


def _examples(models: List[Dict[str, Any]], key: str, limit: int = 10) -> List[str]:
    out = [m.get("model_name") for m in models if (m.get(key) or [])]
    return out[:limit]


def analyze(raw_dir: str | Path, checklist_path: str | Path) -> List[Dict[str, Any]]:
    rules = load_rules(checklist_path)
    raw = load_raw(Path(raw_dir) / "best_practices.json")
    ids = ["BPA-001", "BPA-002", "BPA-003", "BPA-004", "BPA-005", "BPA-006", "BPA-007"]
    sev = {"BPA-001": "high", "BPA-002": "high", "BPA-003": "high",
           "BPA-004": "medium", "BPA-005": "medium", "BPA-006": "medium", "BPA-007": "medium"}

    if not raw or not raw.get("available"):
        note = (raw or {}).get("notes")
        out = []
        for rid in ids:
            out.append(make_finding(
                _rule(rules, rid, sev[rid]), dimension=DIMENSION, status="info",
                title=f"{rid}: best-practice analysis not available this run",
                evidence={"available": False, "notes": note},
                recommendation=("Run the collector inside Fabric (semantic-link-labs) to populate "
                                "Best Practice Analyzer, Direct Lake fallback, Delta and capacity checks."),
            ))
        return out

    models: List[Dict[str, Any]] = raw.get("models") or []
    reports: List[Dict[str, Any]] = raw.get("reports") or []
    caps: List[Dict[str, Any]] = raw.get("capacities") or []
    out: List[Dict[str, Any]] = []

    # BPA-001 model BPA
    mbpa = _sum(models, "model_bpa")
    out.append(make_finding(_rule(rules, "BPA-001", "high"), dimension=DIMENSION,
        status="fail" if mbpa else "pass",
        title=f"BPA-001: {mbpa} model best-practice violation(s) across {len(models)} model(s)",
        evidence={"violations": mbpa, "models": _examples(models, "model_bpa")},
        recommendation="Clear high-impact BPA rule violations (storage, formatting, perf) in the flagged models."))

    # BPA-002 broken reports
    rbpa = sum(len(r.get("report_bpa") or []) for r in reports)
    out.append(make_finding(_rule(rules, "BPA-002", "high"), dimension=DIMENSION,
        status="fail" if rbpa else "pass",
        title=f"BPA-002: {rbpa} report issue(s) across {len(reports)} report(s)",
        evidence={"issues": rbpa, "reports": [r.get("report_name") for r in reports if r.get("report_bpa")][:10]},
        recommendation="Fix reports binding to missing fields or with orphaned visuals before publishing."))

    # BPA-003 Direct Lake fallback
    fb = _sum(models, "fallback")
    out.append(make_finding(_rule(rules, "BPA-003", "high"), dimension=DIMENSION,
        status="fail" if fb else "pass",
        title=f"BPA-003: {fb} Direct Lake fallback reason(s) detected",
        evidence={"fallbackReasons": fb, "models": _examples(models, "fallback")},
        recommendation="Resolve fallback causes (guardrails, unsupported types) so models stay on Direct Lake."))

    # BPA-004 report BPA (volume, medium)
    out.append(make_finding(_rule(rules, "BPA-004", "medium"), dimension=DIMENSION,
        status="info", title=f"BPA-004: report BPA evaluated for {len(reports)} report(s)",
        evidence={"reports": len(reports), "issues": rbpa},
        recommendation="Address medium report BPA items (visual count, slow visuals, layout) over time."))

    # BPA-005 delta health
    delta = _sum(models, "delta")
    out.append(make_finding(_rule(rules, "BPA-005", "medium"), dimension=DIMENSION,
        status="fail" if delta else "info",
        title=f"BPA-005: {delta} Delta table health concern(s)",
        evidence={"concerns": delta, "models": _examples(models, "delta")},
        recommendation="Run OPTIMIZE/V-Order and consolidate small files for the flagged Delta tables."))

    # BPA-006 unused objects
    unused = _sum(models, "unused")
    out.append(make_finding(_rule(rules, "BPA-006", "medium"), dimension=DIMENSION,
        status="fail" if unused else "pass",
        title=f"BPA-006: {unused} unused model object(s)",
        evidence={"unused": unused, "models": _examples(models, "unused")},
        recommendation="Remove unused columns/tables/measures to shrink models and refresh time."))

    # BPA-007 capacity migration readiness
    pskus = [c for c in caps if c.get("needs_migration")]
    out.append(make_finding(_rule(rules, "BPA-007", "medium"), dimension=DIMENSION,
        status="fail" if pskus else "pass",
        title=f"BPA-007: {len(pskus)} capacity(ies) on legacy P-SKU need F-SKU migration",
        evidence={"pSkus": [c.get("capacity") for c in pskus][:10], "total": len(caps)},
        recommendation="Plan migration from P-SKU Premium capacities to Fabric F-SKUs before EOL."))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_best_practices.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Best practices: {len(findings)} finding(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

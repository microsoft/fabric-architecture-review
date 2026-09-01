# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Review metadata-only DAX analysis for static performance-risk patterns."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, write_findings


def analyze(raw_dir: str | Path, checklist_path: str | Path) -> List[Dict[str, Any]]:
    rules = load_rules(checklist_path)
    payload = load_raw(Path(raw_dir) / "dax_analysis.json")
    if payload is None:
        return [missing_raw_finding(rules[rule_id], "performance", "dax_analysis.json") for rule_id in ("DAX-001", "DAX-002")]

    measures = payload.get("measures") or []
    risky = [measure for measure in measures if measure.get("risk_level") in ("medium", "high")]
    high = [measure for measure in risky if measure.get("risk_level") == "high"]
    models_scanned = int(payload.get("models_scanned") or 0)
    errors = int(payload.get("definition_errors") or 0)
    available = bool(payload.get("available"))
    if risky:
        risk_status = "fail"
    elif not available:
        risk_status = "missing_evidence"
    elif errors:
        risk_status = "unknown"
    else:
        risk_status = "pass"
    findings = [make_finding(
        rules["DAX-001"],
        dimension="performance",
        status=risk_status,
        title=(
            f"{len(risky)} measure(s) contain potentially expensive static DAX patterns"
            if risky else
            "Static DAX risk could not be concluded because definition coverage is incomplete"
            if risk_status in ("unknown", "missing_evidence") else
            "No potentially expensive static DAX patterns detected"
        ),
        evidence={
            "metadata_only": True,
            "measures_analyzed": len(measures),
            "medium_or_high_risk_count": len(risky),
            "high_risk_count": len(high),
            "measures": [{
                "model_id": measure.get("model_id"),
                "model_name": measure.get("model_name"),
                "workspace_id": measure.get("workspace_id"),
                "workspace_name": measure.get("workspace_name"),
                "table_name": measure.get("table_name"),
                "measure_name": measure.get("measure_name"),
                "risk_score": measure.get("risk_score"),
                "risk_level": measure.get("risk_level"),
                "signal_codes": [signal.get("code") for signal in measure.get("signals") or []],
            } for measure in risky],
        },
        recommendation=(
            "Review the flagged iterator, cardinality, filtering, materialization, and complexity patterns. "
            "Confirm impact with approved runtime tooling before claiming measured performance or cost."
            if risky else
            "Complete semantic-model definition collection before concluding that no static DAX risks exist."
            if risk_status in ("unknown", "missing_evidence") else
            "Continue metadata analysis after semantic-model changes and validate runtime behavior separately."
        ),
    )]

    if not available:
        coverage_status = "missing_evidence"
        coverage_title = "Semantic-model definitions were not available for DAX analysis"
    elif errors:
        coverage_status = "unknown"
        coverage_title = f"DAX definition coverage is incomplete for {errors} semantic model(s)"
    else:
        coverage_status = "pass"
        coverage_title = f"DAX definitions were scanned for {models_scanned} semantic model(s)"
    findings.append(make_finding(
        rules["DAX-002"],
        dimension="performance",
        status=coverage_status,
        title=coverage_title,
        evidence={
            "metadata_only": True,
            "models_scanned": models_scanned,
            "definition_errors": errors,
            "measures_extracted": len(measures),
            "notes": payload.get("notes") or [],
        },
        recommendation=(
            "Collect semantic-model definitions for all in-scope models and rerun the DAX collector."
            if coverage_status != "pass" else
            "Keep definition collection enabled so DAX coverage remains current."
        ),
    ))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_dax.json")
    args = parser.parse_args()
    write_findings(analyze(args.raw_dir, args.checklist), args.out)


if __name__ == "__main__":
    main()
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Operational Excellence / ALM review.

Rule coverage:
  OPS-001 Production workspaces covered by a deployment pipeline
  OPS-002 Production workspaces under Git source control
  OPS-003 A dev -> prod promotion path exists (multi-stage deployment pipeline)

These rules are deliberately scoped to *production* workspaces (name markers) so
dev / personal / sandbox workspaces are never flagged for lacking ALM controls -
they don't need them. All boundaries are tunable in config/thresholds.yaml.

Inputs: scanner.json (or workspace_inventory.json), deployment_pipelines.json,
git_integration.json.

DATA SAFETY: Metadata only - pipeline/Git configuration and workspace inventory.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings

DIMENSION = "operational_excellence"

# A production workspace (stronger ALM expectations). Dev/test/sandbox are excluded.
PROD_PATTERN = re.compile(r"(prod|production)", re.IGNORECASE)

PROD_PIPELINE_MIN_RATIO = threshold("operational_excellence", "prod_pipeline_min_ratio", 0.8, env="OPS_PROD_PIPELINE_MIN_RATIO", cast=float)
PROD_GIT_MIN_RATIO = threshold("operational_excellence", "prod_git_min_ratio", 0.8, env="OPS_PROD_GIT_MIN_RATIO", cast=float)


def _workspaces(raw_dir: Path) -> List[Dict[str, Any]]:
    scan = load_raw(raw_dir / "scanner.json")
    if scan and scan.get("workspaces"):
        return scan["workspaces"]
    inv = load_raw(raw_dir / "workspace_inventory.json")
    if inv and inv.get("workspaces"):
        return inv["workspaces"]
    return []


def _items(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(ws.get("items"), list):
        return ws["items"]
    bucket: List[Dict[str, Any]] = []
    for key in ("datasets", "reports", "dashboards", "dataflows", "lakehouses",
                "warehouses", "notebooks", "pipelines", "kqlDatabases", "mlModels"):
        bucket.extend(ws.get(key) or [])
    return bucket


def _production_workspaces(workspaces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shared, non-empty workspaces whose name marks them as production."""
    out = []
    for w in workspaces:
        if (w.get("type") or "") == "PersonalGroup":
            continue
        if not _items(w):
            continue
        if PROD_PATTERN.search(w.get("name") or ""):
            out.append(w)
    return out


def _pipeline_workspace_ids(raw_dir: Path) -> set:
    """Every workspace id that is a stage in some deployment pipeline."""
    dp = load_raw(raw_dir / "deployment_pipelines.json") or {}
    ids: set = set()
    for p in dp.get("pipelines") or []:
        for wid in p.get("assignedWorkspaceIds") or []:
            if wid:
                ids.add(str(wid).lower())
        for st in p.get("stages") or []:
            wid = st.get("workspaceId")
            if wid:
                ids.add(str(wid).lower())
    return ids


def _git_connected_ids(raw_dir: Path) -> set:
    git = load_raw(raw_dir / "git_integration.json") or {}
    ids: set = set()
    for w in git.get("workspaces") or []:
        if w.get("connected"):
            wid = w.get("workspaceId") or w.get("id")
            if wid:
                ids.add(str(wid).lower())
    return ids


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    workspaces = _workspaces(raw_dir)
    prod = _production_workspaces(workspaces)

    # --- OPS-001 production workspaces covered by a deployment pipeline ---
    rule = rules.get("OPS-001")
    if rule:
        dp = load_raw(raw_dir / "deployment_pipelines.json")
        if dp is None:
            findings.append(missing_raw_finding(rule, DIMENSION, "deployment_pipelines.json"))
        elif not prod:
            findings.append(make_finding(
                rule, dimension=DIMENSION, status="pass",
                title="Production workspaces covered by a deployment pipeline",
                evidence={"productionWorkspaces": 0,
                          "note": "No production-named, non-empty workspaces to evaluate."},
                recommendation="Adopt deployment pipelines to promote content dev -> test -> prod."
            ))
        else:
            in_pipe = _pipeline_workspace_ids(raw_dir)
            uncovered = [w.get("name") for w in prod if (w.get("id") or "").lower() not in in_pipe]
            ratio = 1 - (len(uncovered) / len(prod))
            status = "pass" if ratio >= PROD_PIPELINE_MIN_RATIO else "fail"
            findings.append(make_finding(
                rule, dimension=DIMENSION, status=status,
                title="Production workspaces covered by a deployment pipeline",
                evidence={"productionWorkspaces": len(prod), "coveredRatio": round(ratio, 2),
                          "minRatio": PROD_PIPELINE_MIN_RATIO,
                          "uncoveredCount": len(uncovered), "examples": uncovered[:20]},
                recommendation=("Attach every production workspace to a deployment pipeline so content is promoted "
                                "from validated lower stages instead of edited directly in production.")
            ))

    # --- OPS-002 production workspaces under Git source control ---
    rule = rules.get("OPS-002")
    if rule:
        git = load_raw(raw_dir / "git_integration.json")
        if git is None:
            findings.append(missing_raw_finding(rule, DIMENSION, "git_integration.json"))
        elif not prod:
            findings.append(make_finding(
                rule, dimension=DIMENSION, status="pass",
                title="Production workspaces under Git source control",
                evidence={"productionWorkspaces": 0,
                          "note": "No production-named, non-empty workspaces to evaluate."},
                recommendation="Connect production workspaces to Git for versioning and rollback."
            ))
        else:
            connected = _git_connected_ids(raw_dir)
            disconnected = [w.get("name") for w in prod if (w.get("id") or "").lower() not in connected]
            ratio = 1 - (len(disconnected) / len(prod))
            status = "pass" if ratio >= PROD_GIT_MIN_RATIO else "fail"
            findings.append(make_finding(
                rule, dimension=DIMENSION, status=status,
                title="Production workspaces under Git source control",
                evidence={"productionWorkspaces": len(prod), "connectedRatio": round(ratio, 2),
                          "minRatio": PROD_GIT_MIN_RATIO,
                          "disconnectedCount": len(disconnected), "examples": disconnected[:20]},
                recommendation=("Connect production workspaces to Git (Azure DevOps / GitHub) for version history, "
                                "code review and rollback - the backbone of Fabric ALM.")
            ))

    # --- OPS-003 a dev -> prod promotion path exists ---
    rule = rules.get("OPS-003")
    if rule:
        dp = load_raw(raw_dir / "deployment_pipelines.json")
        if dp is None:
            findings.append(missing_raw_finding(rule, DIMENSION, "deployment_pipelines.json"))
        else:
            pipelines = dp.get("pipelines") or []
            multi_stage = [p for p in pipelines if (p.get("stageCount") or len(p.get("stages") or [])) >= 2]
            # Advisory: absence of any multi-stage pipeline is an INFO nudge, not a hard fail.
            status = "pass" if multi_stage else "info"
            findings.append(make_finding(
                rule, dimension=DIMENSION, status=status,
                title="A dev -> prod promotion path (multi-stage deployment pipeline) exists",
                evidence={"pipelineCount": len(pipelines), "multiStagePipelines": len(multi_stage)},
                recommendation=("Create at least one multi-stage deployment pipeline (Development -> Test -> "
                                "Production) so changes are validated before reaching production.")
            ))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_operational_excellence.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Operational Excellence: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

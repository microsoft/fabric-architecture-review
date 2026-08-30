# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Governance review.

Rule coverage:
    GOV-001 Production workspaces with at least 2 admins
    GOV-002 Inactive items in the activity-log review window
    GOV-003 Sensitivity-label coverage on governed items
    GOV-004 Workspace naming convention (env/layer markers)
  GOV-005 Sharing activity volume
  GOV-006 Orphaned workspaces (content but no recent activity)
  GOV-007 Fabric Capacity Metrics app installed
  GOV-008 Endorsement (Certified / Promoted) coverage of content (advisory)
  GOV-009 Uncertified semantic models in production workspaces

Inputs: scanner.json (or workspace_inventory.json), activity_logs.json.

DATA SAFETY: Metadata + audit metadata only.
"""
from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings
from analyzers.applicability import classify_workspaces, load_workspace_overrides, production_scope

NAMING_PATTERN = re.compile(
    r"(bronze|silver|gold|raw|stg|staging|curated|landing|dev|test|qa|uat|prod|production|sbx|sandbox)",
    re.IGNORECASE,
)
SHARE_ACTIONS = {"ShareReport", "SharePermissions", "ShareDashboard", "ShareDataset", "UpdateSharePermissions"}
SHARE_VOLUME_THRESHOLD = threshold("governance", "share_volume_warn", 100, env="GOV_SHARE_VOLUME_THRESHOLD", cast=int)
MIN_ADMINS = threshold("governance", "min_admins", 2, cast=int)
LABEL_COVERAGE_MIN_RATIO = threshold("governance", "label_coverage_min_ratio", 0.5, cast=float)
NAMING_COVERAGE_MIN_RATIO = threshold("governance", "naming_coverage_min_ratio", 0.5, cast=float)
ENDORSEMENT_MIN_RATIO = threshold("governance", "endorsement_min_ratio", 0.3, env="GOV_ENDORSEMENT_MIN_RATIO", cast=float)
PROD_ENDORSEMENT_MIN_RATIO = threshold("governance", "prod_endorsement_min_ratio", 0.5, env="GOV_PROD_ENDORSEMENT_MIN_RATIO", cast=float)

# Production workspaces (name markers) carry a stronger expectation of endorsed content.
PROD_PATTERN = re.compile(r"(prod|production)", re.IGNORECASE)
# Item kinds that can be endorsed (Certified / Promoted) in Fabric / Power BI.
_ENDORSABLE_KINDS = ("datasets", "reports", "dataflows", "lakehouses", "warehouses")
_LABELLED_KINDS = ("datasets", "reports", "lakehouses", "warehouses")
_ACTIVITY_ITEM_ID_KEYS = (
    "ArtifactId", "ArtifactID", "artifactId", "ItemId", "ItemID", "itemId",
    "DatasetId", "DatasetID", "datasetId", "ReportId", "ReportID", "reportId",
    "DataflowId", "DataflowID", "dataflowId", "ObjectId", "ObjectID", "objectId",
)


def _endorsement(item: Dict[str, Any]) -> str:
    """The endorsement label of a scanner item ('Certified' | 'Promoted' | '')."""
    det = item.get("endorsementDetails") or {}
    return (det.get("endorsement") or "").strip()


def _endorsable_items(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for kind in _ENDORSABLE_KINDS:
        out.extend(ws.get(kind) or [])
    return out


def _labelled_items(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return only item types covered by the sensitivity-label contract."""
    out: List[Dict[str, Any]] = []
    for kind in _LABELLED_KINDS:
        out.extend(ws.get(kind) or [])
    if out or not isinstance(ws.get("items"), list):
        return out
    supported = {"semanticmodel", "dataset", "report", "lakehouse", "warehouse"}
    return [item for item in ws["items"]
            if (item.get("type") or item.get("itemType") or "").lower().replace(" ", "") in supported]


def _activity_item_ids(events: List[Dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for event in events:
        for key in _ACTIVITY_ITEM_ID_KEYS:
            value = event.get(key)
            if value:
                ids.add(str(value).lower())
                break
    return ids


def _workspaces(raw_dir: Path) -> List[Dict[str, Any]]:
    scan = load_raw(raw_dir / "scanner.json")
    if scan and scan.get("workspaces"):
        return scan["workspaces"]
    inv = load_raw(raw_dir / "workspace_inventory.json")
    if inv and inv.get("workspaces"):
        return inv["workspaces"]
    return []


def _admins(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
    # scanner: users with groupUserAccessRight == 'Admin'
    out: List[Dict[str, Any]] = []
    for u in ws.get("users") or []:
        right = (u.get("groupUserAccessRight") or u.get("role") or "").lower()
        if right == "admin":
            out.append(u)
    return out


def _items(ws: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(ws.get("items"), list):
        return ws["items"]
    bucket: List[Dict[str, Any]] = []
    for key in ("datasets", "reports", "dashboards", "dataflows", "lakehouses",
                "warehouses", "notebooks", "pipelines", "kqlDatabases", "mlModels"):
        bucket.extend(ws.get(key) or [])
    return bucket


def _is_shared_content_workspace(ws: Dict[str, Any]) -> bool:
    """A real, shared workspace that holds content.

    Personal ("My workspace" / PersonalGroup) and empty workspaces are excluded:
    a personal workspace structurally always has a single admin and no second
    owner is possible, so admin/ownership governance rules don't apply to it.
    """
    if (ws.get("type") or "") == "PersonalGroup":
        return False
    return len(_items(ws)) > 0


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    workspaces = _workspaces(raw_dir)
    workspace_config = os.environ.get("WORKSPACES_CONFIG") or str(Path(checklist_path).parent / "workspaces.yaml")
    workspace_profiles = classify_workspaces(workspaces, load_workspace_overrides(workspace_config))

    # --- GOV-001 production workspaces have ≥2 admins ---
    rule = rules.get("GOV-001")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "governance", "scanner.json or workspace_inventory.json"))
        else:
            scope = production_scope(workspaces, workspace_profiles)
            relevant = [w for w in scope["applicable"] if _is_shared_content_workspace(w)]
            if not relevant:
                findings.append(make_finding(
                    rule, dimension="governance",
                    status="unknown" if scope["unknown"] else "not_applicable",
                    title="Workspaces with fewer than 2 admins",
                    evidence={"evaluatedWorkspaces": 0, "minAdmins": MIN_ADMINS,
                              "unknownEnvironmentWorkspaces": [w.get("name") for w in scope["unknown"]],
                              "note": "No classified production, shared, non-empty workspaces to evaluate."},
                    recommendation="Assign at least two workspace admins (preferably via a security group) to avoid orphan risk."
                ))
            else:
                under_admin = [w.get("name") for w in relevant if len(_admins(w)) < MIN_ADMINS]
                findings.append(make_finding(
                    rule, dimension="governance",
                    status="pass" if not under_admin else "fail",
                    title="Workspaces with fewer than 2 admins",
                    evidence={"evaluatedWorkspaces": len(relevant), "minAdmins": MIN_ADMINS,
                              "underAdminCount": len(under_admin),
                              "examples": under_admin[:20]},
                    recommendation="Assign at least two workspace admins (preferably via a security group) to avoid orphan risk."
                ))

    # --- GOV-002 inactive items ---
    rule = rules.get("GOV-002")
    if rule:
        logs = load_raw(raw_dir / "activity_logs.json")
        if not workspaces or not logs:
            findings.append(missing_raw_finding(
                rule, "governance", "scanner.json + activity_logs.json"
            ))
        else:
            inventoried = [
                {"id": str(item.get("id") or "").lower(), "name": item.get("name"),
                 "workspace": workspace.get("name")}
                for workspace in workspaces if workspace.get("type") != "PersonalGroup"
                for item in _items(workspace) if item.get("id")
            ]
            active_ids = _activity_item_ids(logs.get("events") or [])
            inactive = [item for item in inventoried if item["id"] not in active_ids]
            evidence_available = bool(active_ids) or not inventoried
            status = ("pass" if not inactive else "fail") if evidence_available else "missing_evidence"
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title=(f"Items with no activity in the last {logs.get('windowDays', '?')} day(s)"
                       if evidence_available else "Item inactivity could not be evaluated"),
                evidence={"windowDays": logs.get("windowDays"),
                          "inventoriedItems": len(inventoried),
                          "eventsWithArtifactId": len(active_ids),
                          "inactiveCount": len(inactive) if evidence_available else None,
                          "examples": inactive[:20] if evidence_available else [],
                          "missingEvidence": None if evidence_available else
                          "Activity-log events contain no artifact/item identifiers."},
                recommendation=("Archive or delete confirmed inactive items after validating their business "
                                "retention requirements." if evidence_available else
                                "Collect activity events with ArtifactId, ItemId, DatasetId, or ReportId fields "
                                "before drawing an item-level inactivity conclusion.")
            ))

    # --- GOV-003 sensitivity labels ---
    rule = rules.get("GOV-003")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "governance", "scanner.json or workspace_inventory.json"))
        else:
            governed = [item for workspace in workspaces for item in _labelled_items(workspace)]
            labelled = [item for item in governed
                        if item.get("sensitivityLabel") or item.get("informationProtectionLabel")]
            ratio = (len(labelled) / len(governed)) if governed else 0
            status = "pass" if governed and ratio >= LABEL_COVERAGE_MIN_RATIO else (
                "not_applicable" if not governed else "fail"
            )
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title="Sensitivity label coverage",
                evidence={"evaluatedItems": len(governed), "labelledItems": len(labelled),
                          "ratio": round(ratio, 2)},
                recommendation="Apply sensitivity labels to semantic models, reports, lakehouses, and warehouses; "
                               "enforce via tenant setting and Purview integration."
            ))

    # --- GOV-004 naming convention ---
    rule = rules.get("GOV-004")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "governance", "scanner.json or workspace_inventory.json"))
        else:
            shared = [w for w in workspaces if w.get("type") != "PersonalGroup"]
            matching = [w for w in shared if NAMING_PATTERN.search(w.get("name") or "")]
            ratio = (len(matching) / len(shared)) if shared else 0
            status = "pass" if shared and ratio >= NAMING_COVERAGE_MIN_RATIO else (
                "not_applicable" if not shared else "fail"
            )
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title="Workspaces following documented naming convention",
                evidence={"workspaceCount": len(shared), "matchingCount": len(matching),
                          "ratio": round(ratio, 2)},
                recommendation="Document and enforce a naming convention combining domain, environment, and "
                               "purpose (for example, `<domain>-<env>-<purpose>`)."
            ))

    # --- GOV-005 sharing volume ---
    rule = rules.get("GOV-005")
    if rule:
        logs = load_raw(raw_dir / "activity_logs.json")
        if not logs:
            findings.append(missing_raw_finding(rule, "governance", "activity_logs.json"))
        else:
            events = logs.get("events") or []
            share_events = [e for e in events if (e.get("Activity") or e.get("Operation") or "") in SHARE_ACTIONS]
            top_users = Counter(
                (e.get("UserId") or e.get("UserEmail") or e.get("UserKey") or "unknown") for e in share_events
            ).most_common(5)
            status = "pass" if len(share_events) < SHARE_VOLUME_THRESHOLD else "fail"
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title=f"Sharing events in last {logs.get('windowDays', '?')} day(s)",
                evidence={"shareEventCount": len(share_events),
                          "threshold": SHARE_VOLUME_THRESHOLD,
                          "topSharers": [{"principal": u, "count": c} for u, c in top_users]},
                recommendation="Review high-volume sharers; enforce sharing via Apps + security groups rather than "
                               "ad-hoc per-report sharing."
            ))

    # --- GOV-006 orphaned workspaces (no recent activity) ---
    rule = rules.get("GOV-006")
    if rule:
        logs = load_raw(raw_dir / "activity_logs.json")
        if not workspaces or not logs:
            findings.append(missing_raw_finding(rule, "governance",
                                                "scanner.json + activity_logs.json"))
        else:
            events = logs.get("events") or []
            seen_ws: set = set()
            for e in events:
                wsid = (e.get("WorkspaceId") or e.get("WorkSpaceId") or
                        e.get("WorkspaceID") or e.get("workspaceId") or "")
                if wsid:
                    seen_ws.add(wsid.lower())
            orphans = []
            expected_inactive = []
            for w in workspaces:
                if w.get("type") == "PersonalGroup":
                    continue
                if (w.get("id") or "").lower() in seen_ws:
                    continue
                # Skip empty workspaces — ARCH-007 covers those.
                if not _items(w):
                    continue
                profile = workspace_profiles.get((w.get("id") or "").lower(), {})
                row = {"name": w.get("name"), "items": len(_items(w)),
                       "usagePattern": profile.get("usagePattern", "unknown")}
                if profile.get("usagePattern") in ("monthly", "quarterly", "on_demand"):
                    expected_inactive.append(row)
                else:
                    orphans.append(row)
            status = "fail" if orphans else "info" if expected_inactive else "pass"
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title=f"Workspaces with content but no activity in last {logs.get('windowDays', '?')} day(s)",
                evidence={"windowDays": logs.get("windowDays"),
                          "orphanedCount": len(orphans),
                          "examples": orphans[:20],
                          "expectedLowFrequencyCount": len(expected_inactive),
                          "expectedLowFrequency": expected_inactive[:20]},
                recommendation=("Confirm the owner; if the workspace is no longer used, archive it to reduce "
                                "attack surface and capacity load.")
            ))

    # --- GOV-007 Fabric Capacity Metrics app installed ---
    rule = rules.get("GOV-007")
    if rule:
        installed = (os.environ.get("CAPACITY_METRICS_APP_INSTALLED") or "").strip().lower() in (
            "1", "true", "yes", "y", "on")
        source = "env:CAPACITY_METRICS_APP_INSTALLED" if installed else None
        if not installed:
            scan = load_raw(raw_dir / "scanner.json") or {}
            for ws in scan.get("workspaces") or []:
                for kind in ("datasets", "reports"):
                    for item in ws.get(kind) or []:
                        name = (item.get("name") or "").lower()
                        if "capacity metrics" in name:
                            installed = True
                            source = f"scanner:{ws.get('name')}/{item.get('name')}"
                            break
                    if installed:
                        break
                if installed:
                    break
        findings.append(make_finding(
            rule, dimension="governance",
            status="pass" if installed else "fail",
            title=("Fabric Capacity Metrics app is installed"
                   if installed else "Fabric Capacity Metrics app not detected"),
            evidence={"installed": installed, "source": source},
            recommendation=("Install the Microsoft Fabric Capacity Metrics app so capacity CU, throttling, and "
                            "overload events can be monitored over time - it is the primary tool for diagnosing "
                            "capacity health and right-sizing.")
        ))

    # --- GOV-008 endorsement coverage (advisory) ---
    rule = rules.get("GOV-008")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "governance", "scanner.json"))
        else:
            total = 0
            endorsed = 0
            for w in workspaces:
                if w.get("type") == "PersonalGroup":
                    continue
                for item in _endorsable_items(w):
                    total += 1
                    if _endorsement(item) in ("Certified", "Promoted"):
                        endorsed += 1
            ratio = (endorsed / total) if total else 0.0
            # Advisory maturity signal: PASS when adopted, otherwise INFO (never a hard fail).
            status = "pass" if (total and ratio >= ENDORSEMENT_MIN_RATIO) else ("info" if total else "info")
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title="Endorsement (Certified / Promoted) coverage of content",
                evidence={"endorsableItems": total, "endorsedItems": endorsed,
                          "ratio": round(ratio, 2), "minRatio": ENDORSEMENT_MIN_RATIO},
                recommendation=("Endorse trusted datasets/reports as Promoted, and the authoritative ones as "
                                "Certified, so consumers can tell governed content from ad-hoc content.")
            ))

    # --- GOV-009 uncertified content in production workspaces ---
    rule = rules.get("GOV-009")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "governance", "scanner.json"))
        else:
            prod_ws = [w for w in workspaces
                       if w.get("type") != "PersonalGroup" and PROD_PATTERN.search(w.get("name") or "")]
            offenders: List[Dict[str, Any]] = []
            evaluated = 0
            for w in prod_ws:
                datasets = w.get("datasets") or []
                if not datasets:
                    continue
                evaluated += 1
                endorsed = sum(1 for d in datasets if _endorsement(d) in ("Certified", "Promoted"))
                ratio = endorsed / len(datasets)
                if ratio < PROD_ENDORSEMENT_MIN_RATIO:
                    offenders.append({"name": w.get("name"), "datasets": len(datasets),
                                      "endorsed": endorsed, "ratio": round(ratio, 2)})
            status = "pass" if (not evaluated or not offenders) else "fail"
            findings.append(make_finding(
                rule, dimension="governance", status=status,
                title="Production workspaces without endorsed semantic models",
                evidence={"productionWorkspaces": evaluated, "minRatio": PROD_ENDORSEMENT_MIN_RATIO,
                          "offenderCount": len(offenders), "examples": offenders[:20]},
                recommendation=("Certify the authoritative semantic models in production workspaces so downstream "
                                "reports build on governed, trusted data.")
            ))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_governance.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Governance: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

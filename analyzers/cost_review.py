# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Cost review.

Rule coverage:
  COST-001 Capacities with zero assigned workspaces (empty SKU spend)
  COST-002 Non-production capacities should use Pause/Resume        -> info (REST cannot read schedules)
  COST-003 Dev/test/sandbox capacities flagged for pause schedule
  COST-004 Workspaces with prod-like names on PPU / personal capacities
  COST-005 Large capacities (F64+) hosting few workspaces

Inputs: capacity_metrics.json, scanner.json (or workspace_inventory.json).
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings, is_dedicated_capacity

NONPROD_PATTERN = re.compile(r"(dev|test|qa|uat|sbx|sandbox|poc|demo)", re.IGNORECASE)
PROD_PATTERN = re.compile(r"(prod|production|live)", re.IGNORECASE)
LARGE_SKU_PATTERN = re.compile(r"^F(64|128|256|512|1024|2048)$", re.IGNORECASE)
SMALL_WORKSPACE_THRESHOLD = threshold("cost", "large_sku_min_workspaces", 5, env="COST_SMALL_WORKSPACE_THRESHOLD", cast=int)


def _workspaces(raw_dir: Path) -> List[Dict[str, Any]]:
    scan = load_raw(raw_dir / "scanner.json")
    if scan and scan.get("workspaces"):
        return scan["workspaces"]
    inv = load_raw(raw_dir / "workspace_inventory.json")
    if inv and inv.get("workspaces"):
        return inv["workspaces"]
    return []


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    caps_raw = load_raw(raw_dir / "capacity_metrics.json")
    capacities = (caps_raw or {}).get("capacities") or []

    # --- COST-001 empty capacities ---
    rule = rules.get("COST-001")
    if rule:
        if not capacities:
            findings.append(missing_raw_finding(rule, "cost", "capacity_metrics.json"))
        else:
            empties = [c for c in capacities if c.get("assignedWorkspaceCount", 0) == 0
                       and (c.get("state") or "").lower() == "active"
                       and is_dedicated_capacity(c.get("sku"))]
            status = "pass" if not empties else "fail"
            findings.append(make_finding(
                rule, dimension="cost", status=status,
                title="Active capacities with no assigned workspaces",
                evidence={"count": len(empties),
                          "capacities": [{"name": c.get("displayName"), "sku": c.get("sku")} for c in empties]},
                recommendation="Pause or delete empty capacities; they incur SKU charges with zero utilization."
            ))

    # --- COST-002 info marker about pause/resume detectability ---
    rule = rules.get("COST-002")
    if rule:
        auto_pause = (os.environ.get("CAPACITY_AUTO_PAUSE_CONFIGURED") or "").strip().lower() in (
            "1", "true", "yes", "y", "on", "auto", "detect"
        )
        cap_states = [{"name": c.get("displayName"), "sku": c.get("sku"),
                       "state": c.get("state"), "region": c.get("region")}
                      for c in capacities]
        paused = [c for c in cap_states if (c.get("state") or "").lower() == "paused"]

        azure_auto = load_raw(raw_dir / "azure_capacity_automation.json") or {}
        azure_hits = azure_auto.get("pauseAutomations") or []
        azure_candidates = azure_auto.get("pauseCandidates") or []
        azure_skipped = bool(azure_auto.get("skipped"))

        if azure_hits:
            findings.append(make_finding(
                rule, dimension="cost", status="pass",
                title=f"Pause/Resume automation verified in Azure ({len(azure_hits)} hit(s))",
                evidence={"reason": ("Found one or more Azure Automation runbook(s) or Logic App(s) "
                                     "whose content references the Fabric capacity resource id / name "
                                     "and the suspend/resume verbs. The capacity state at scan time is "
                                     "included below for cross-check."),
                          "subscriptionsScanned": azure_auto.get("subscriptionsScanned"),
                          "automations": azure_hits,
                          "candidates": azure_candidates,
                          "currentlyPaused": len(paused),
                          "capacitiesAtScan": cap_states},
                recommendation=("Confirm the listed runbooks / workflows target the intended capacities and "
                                "that their managed identity / service principal still holds Capacity "
                                "Contributor (or equivalent) on those resources. Validate the linked "
                                "schedules still match the actual quiet hours.")
            ))
        elif azure_candidates:
            findings.append(make_finding(
                rule, dimension="cost", status="pass",
                title=f"Pause/Resume automation candidate(s) detected ({len(azure_candidates)})",
                evidence={"reason": ("Found Azure Automation runbook(s) or Logic App(s) whose content "
                                     "references Fabric capacity suspend/resume verbs but does not name "
                                     "the capacity literally in the script body. This is typical when the "
                                     "capacity name / resource id is passed in as a runtime parameter or "
                                     "a webhook payload. Treat as the pause/resume schedule pending "
                                     "manual confirmation of the parameter values and the schedule "
                                     "binding."),
                          "subscriptionsScanned": azure_auto.get("subscriptionsScanned"),
                          "candidates": azure_candidates,
                          "currentlyPaused": len(paused),
                          "capacitiesAtScan": cap_states},
                recommendation=("Open each candidate runbook / Logic App and verify (a) the capacity "
                                "resource id passed as a parameter or stored in an Automation variable, "
                                "(b) the schedule binding, and (c) that the executing identity still "
                                "holds Capacity Contributor on the target. If a runbook is unrelated, "
                                "rename it or annotate it so future reviews can ignore it.")
            ))
        elif auto_pause and not azure_skipped and azure_auto.get("subscriptionsScanned") is not None:
            findings.append(make_finding(
                rule, dimension="cost", status="fail",
                title="Reviewer attested Pause/Resume but no Azure automation matched the capacity",
                evidence={"reason": ("CAPACITY_AUTO_PAUSE_CONFIGURED is set, and the Azure ARM scan ran "
                                     "successfully, but no runbook or Logic App content referenced this "
                                     "tenant's Fabric capacities together with suspend/resume verbs. "
                                     "Either the automation lives in a subscription this user cannot "
                                     "read, the runbook content endpoint returned 403, or the "
                                     "attestation is stale."),
                          "subscriptionsScanned": azure_auto.get("subscriptionsScanned"),
                          "currentlyPaused": len(paused),
                          "capacitiesAtScan": cap_states},
                recommendation=("Either grant the signed-in user Reader on the subscription that hosts "
                                "the automation account / Logic App and re-run, or unset "
                                "CAPACITY_AUTO_PAUSE_CONFIGURED if the schedule no longer exists.")
            ))
        elif auto_pause:
            findings.append(make_finding(
                rule, dimension="cost", status="info",
                title="Pause/Resume automation reported by reviewer (Azure scan disabled)",
                evidence={"reason": ("CAPACITY_AUTO_PAUSE_CONFIGURED=true was set in the environment by "
                                     "the reviewer. The Azure ARM auto-detection collector "
                                     "(collectors.azure_capacity_automation) did not run in this session, "
                                     "so this finding records only the attestation plus the current "
                                     "capacity state at scan time."),
                          "currentlyPaused": len(paused),
                          "capacitiesAtScan": cap_states},
                recommendation=("Run `python -m collectors.azure_capacity_automation` (or include it in "
                                "scripts/powershell/01_collect.ps1) to auto-detect the runbooks / Logic Apps that "
                                "implement the pause schedule. Re-validate periodically that the "
                                "automation still runs and its identity still has Capacity Contributor "
                                "on the target resources.")
            ))
        else:
            findings.append(make_finding(
                rule, dimension="cost", status="info",
                title="Pause/Resume schedule not visible via Fabric REST",
                evidence={"reason": ("The Power BI / Fabric REST capacities endpoint returns only the "
                                     "current `state` (Active / Paused) per capacity, not any pause "
                                     "schedule or Azure Automation runbook. Set "
                                     "CAPACITY_AUTO_PAUSE_CONFIGURED=true in .env to enable the Azure "
                                     "ARM auto-detection collector."),
                          "currentlyPaused": len(paused),
                          "capacitiesAtScan": cap_states},
                recommendation=("If a runbook / Logic App pauses these capacities, set "
                                "CAPACITY_AUTO_PAUSE_CONFIGURED=true and re-run; the collector will "
                                "verify it from Azure ARM and downgrade this finding to PASS with the "
                                "matching runbook(s).")
            ))

    # --- COST-003 non-prod capacities ---
    rule = rules.get("COST-003")
    if rule:
        if not capacities:
            findings.append(missing_raw_finding(rule, "cost", "capacity_metrics.json"))
        else:
            nonprod = [c for c in capacities if NONPROD_PATTERN.search(c.get("displayName") or "")]
            status = "pass" if not nonprod else "info"
            findings.append(make_finding(
                rule, dimension="cost", status=status,
                title="Non-production capacities (candidates for Pause/Resume)",
                evidence={"count": len(nonprod),
                          "capacities": [{"name": c.get("displayName"), "sku": c.get("sku")} for c in nonprod]},
                recommendation="Configure an Azure Automation runbook or schedule to pause non-production "
                               "capacities outside business hours."
            ))

    # --- COST-004 prod workspaces on PPU/personal ---
    rule = rules.get("COST-004")
    if rule:
        workspaces = _workspaces(raw_dir)
        if not workspaces:
            findings.append(missing_raw_finding(rule, "cost", "scanner.json or workspace_inventory.json"))
        else:
            offenders = []
            for w in workspaces:
                name = w.get("name") or ""
                if PROD_PATTERN.search(name):
                    if w.get("type") in ("PersonalGroup", "Personal") or not (w.get("capacityId") or w.get("isOnDedicatedCapacity")):
                        offenders.append({"workspace": name, "type": w.get("type"),
                                          "capacityId": w.get("capacityId")})
            status = "pass" if not offenders else "fail"
            findings.append(make_finding(
                rule, dimension="cost", status=status,
                title="Production-like workspaces not on a shared Fabric capacity",
                evidence={"count": len(offenders), "examples": offenders[:20]},
                recommendation="Move production content to a Fabric capacity; PPU and personal workspaces "
                               "do not scale and are tied to one user's license."
            ))

    # --- COST-005 large capacities under-utilized by workspace count ---
    rule = rules.get("COST-005")
    if rule:
        if not capacities:
            findings.append(missing_raw_finding(rule, "cost", "capacity_metrics.json"))
        else:
            under = [c for c in capacities
                     if LARGE_SKU_PATTERN.match(c.get("sku") or "")
                     and c.get("assignedWorkspaceCount", 0) < SMALL_WORKSPACE_THRESHOLD]
            status = "pass" if not under else "fail"
            findings.append(make_finding(
                rule, dimension="cost", status=status,
                title=f"Large capacities (F64+) hosting fewer than {SMALL_WORKSPACE_THRESHOLD} workspaces",
                evidence={"count": len(under),
                          "capacities": [{"name": c.get("displayName"), "sku": c.get("sku"),
                                          "workspaces": c.get("assignedWorkspaceCount")} for c in under]},
                recommendation="Right-size: either consolidate workspaces onto this capacity or downgrade the SKU."
            ))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_cost.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Cost: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Cost review.

Rule coverage:
    COST-001 Capacity SKU right-sizing from sustained average CU
  COST-002 Non-production capacities should use Pause/Resume        -> info (REST cannot read schedules)
  COST-003 Dev/test/sandbox capacities flagged for pause schedule
  COST-004 Workspaces with prod-like names on PPU / personal capacities
  COST-005 Large capacities (F64+) hosting few workspaces
  COST-006 Trial / Embedded capacities hosting workspaces
  COST-007 Small-capacity sprawl (consolidation opportunity)             -> info
    COST-008 Capacities with zero assigned workspaces (empty SKU spend)

Inputs: capacity_metrics.json, scanner.json (or workspace_inventory.json).
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings, is_dedicated_capacity, capacity_kind
from analyzers.applicability import classify_workspaces, load_capacity_overrides, load_workspace_overrides

NONPROD_PATTERN = re.compile(r"(dev|test|qa|uat|sbx|sandbox|poc|demo)", re.IGNORECASE)
PROD_PATTERN = re.compile(r"(prod|production|live)", re.IGNORECASE)
LARGE_SKU_PATTERN = re.compile(r"^F(64|128|256|512|1024|2048)$", re.IGNORECASE)
SMALL_SKU_PATTERN = re.compile(r"^F(1|2|4|8|16)$", re.IGNORECASE)
SMALL_WORKSPACE_THRESHOLD = threshold("cost", "large_sku_min_workspaces", 5, env="COST_SMALL_WORKSPACE_THRESHOLD", cast=int)
CONSOLIDATION_MIN_SMALL_CAPS = threshold("cost", "consolidation_min_small_capacities", 3, env="COST_CONSOLIDATION_MIN_SMALL_CAPS", cast=int)
CU_AVG_IDLE_PCT = threshold("cost", "cu_avg_idle_pct", 20, cast=float)
CU_AVG_SATURATED_PCT = threshold("cost", "cu_avg_saturated_pct", 85, cast=float)


def _column(row: Dict[str, Any], name: str) -> Any:
    target = name.lower()
    for key, value in row.items():
        lowered = key.lower()
        if lowered == target or lowered.endswith(f"[{target}]"):
            return value
    return None


def _capacity_cu_7d(raw_dir: Path) -> List[Dict[str, Any]]:
    payload = load_raw(raw_dir / "capacity_metrics_app.json") or {}
    probe = (payload.get("queries") or {}).get("usage_summary_7d") or {}
    if not probe.get("ok"):
        return []
    rows: List[Dict[str, Any]] = []
    for row in probe.get("rows") or []:
        capacity_id = _column(row, "Capacity Id")
        average = _column(row, "Average CU %")
        if capacity_id is None or average is None:
            continue
        try:
            rows.append({"capacityId": str(capacity_id), "avgCU7d": float(average)})
        except (TypeError, ValueError):
            continue
    return rows


def _workspaces(raw_dir: Path) -> List[Dict[str, Any]]:
    scan = load_raw(raw_dir / "scanner.json")
    if scan and scan.get("workspaces"):
        return scan["workspaces"]
    inv = load_raw(raw_dir / "workspace_inventory.json")
    if inv and inv.get("workspaces"):
        return inv["workspaces"]
    return []


def _capacity_environment(
    capacity: Dict[str, Any], derived_environments: set[str], override: Dict[str, Any] | None = None,
) -> str:
    explicit = str(((override or {}).get("profile") or {}).get("environment") or "").lower()
    if explicit:
        return explicit if explicit in ("production", "nonproduction") else "unknown"
    if derived_environments == {"nonproduction"}:
        return "nonproduction"
    if "unknown" in derived_environments:
        return "unknown"
    if not derived_environments and NONPROD_PATTERN.search(capacity.get("displayName") or ""):
        return "nonproduction"
    return "production"


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    caps_raw = load_raw(raw_dir / "capacity_metrics.json")
    capacities = (caps_raw or {}).get("capacities") or []
    workspaces = _workspaces(raw_dir)
    workspace_config = os.environ.get("WORKSPACES_CONFIG") or str(Path(checklist_path).parent / "workspaces.yaml")
    workspace_profiles = classify_workspaces(workspaces, load_workspace_overrides(workspace_config))
    capacity_overrides = load_capacity_overrides(workspace_config)
    capacity_environments: Dict[str, set[str]] = {}
    for workspace in workspaces:
        capacity_id = str(workspace.get("capacityId") or "").lower()
        if capacity_id:
            profile = workspace_profiles.get(str(workspace.get("id") or "").lower(), {})
            capacity_environments.setdefault(capacity_id, set()).add(str(profile.get("environment") or "unknown"))

    # --- COST-001 sustained CU right-sizing ---
    rule = rules.get("COST-001")
    if rule:
        utilization = _capacity_cu_7d(raw_dir)
        if not utilization:
            findings.append(make_finding(
                rule, dimension="cost", status="missing_evidence",
                title="Sustained capacity CU% was not collected",
                evidence={"requiredInput": "capacity_metrics_app.json:usage_summary_7d",
                          "idleBelowPct": CU_AVG_IDLE_PCT,
                          "saturatedAbovePct": CU_AVG_SATURATED_PCT},
                recommendation=("Install or configure the Fabric Capacity Metrics app and grant Build "
                                "permission so 7-day Average CU % can be evaluated."),
            ))
        else:
            names = {(c.get("id") or "").lower(): c for c in capacities}
            outside = []
            for sample in utilization:
                average = sample["avgCU7d"]
                if average < CU_AVG_IDLE_PCT or average > CU_AVG_SATURATED_PCT:
                    capacity = names.get(sample["capacityId"].lower(), {})
                    outside.append({**sample, "name": capacity.get("displayName"),
                                    "sku": capacity.get("sku"),
                                    "classification": "idle" if average < CU_AVG_IDLE_PCT else "saturated"})
            findings.append(make_finding(
                rule, dimension="cost", status="fail" if outside else "pass",
                title=f"Capacity right-sizing from sustained CU% ({len(outside)} outside target band)",
                evidence={"idleBelowPct": CU_AVG_IDLE_PCT,
                          "saturatedAbovePct": CU_AVG_SATURATED_PCT,
                          "capacitiesEvaluated": len(utilization), "outsideBand": outside},
                recommendation=("Downsize or consolidate chronically idle capacities; optimize workloads, "
                                "enable autoscale, or upsize chronically saturated capacities."),
            ))

    # --- COST-002 info marker about pause/resume detectability ---
    rule = rules.get("COST-002")
    if rule:
        nonprod_capacities = []
        unknown_capacities = []
        for capacity in capacities:
            capacity_id = str(capacity.get("id") or "").lower()
            environments = capacity_environments.get(capacity_id, set())
            environment = _capacity_environment(capacity, environments, capacity_overrides.get(capacity_id))
            if environment == "nonproduction":
                nonprod_capacities.append(capacity)
            elif environment == "unknown":
                unknown_capacities.append(capacity)
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

        if not nonprod_capacities and unknown_capacities:
            findings.append(make_finding(
                rule, dimension="cost", status="unknown",
                title="Non-production capacity scope could not be established",
                evidence={"unknownCapacities": [c.get("displayName") for c in unknown_capacities]},
                recommendation="Set profile.environment for workspaces so pause/resume applicability can be evaluated.",
            ))
        elif not nonprod_capacities:
            findings.append(make_finding(
                rule, dimension="cost", status="not_applicable",
                title="No dedicated non-production capacities detected",
                evidence={"nonProductionCapacityCount": 0},
                recommendation="No pause/resume action is required for the current capacity scope.",
            ))
        elif azure_hits:
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
                          "nonProductionCapacities": [c.get("displayName") for c in nonprod_capacities],
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
                          "nonProductionCapacities": [c.get("displayName") for c in nonprod_capacities],
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

    # --- COST-006 trial / embedded capacities hosting content ---
    rule = rules.get("COST-006")
    if rule:
        if not capacities:
            findings.append(missing_raw_finding(rule, "cost", "capacity_metrics.json"))
        else:
            interim = [c for c in capacities
                       if capacity_kind(c.get("sku")) in ("Trial", "Embedded")
                       and (c.get("assignedWorkspaceCount", 0) or 0) > 0]
            status = "pass" if not interim else "fail"
            findings.append(make_finding(
                rule, dimension="cost", status=status,
                title="Trial / Embedded capacities hosting workspaces",
                evidence={"count": len(interim),
                          "capacities": [{"name": c.get("displayName"), "sku": c.get("sku"),
                                          "kind": capacity_kind(c.get("sku")),
                                          "workspaces": c.get("assignedWorkspaceCount")} for c in interim]},
                recommendation=("Move real content off Trial capacities (they expire - content and settings are "
                                "lost) and off Embedded capacities (sized for app embedding, not interactive use) "
                                "onto a right-sized Fabric (F) capacity.")
            ))

    # --- COST-007 small-capacity sprawl (consolidation opportunity) ---
    rule = rules.get("COST-007")
    if rule:
        if not capacities:
            findings.append(missing_raw_finding(rule, "cost", "capacity_metrics.json"))
        else:
            small = [c for c in capacities
                     if is_dedicated_capacity(c.get("sku"))
                     and SMALL_SKU_PATTERN.match((c.get("sku") or "").strip())
                     and (c.get("state") or "").lower() == "active"]
            # Advisory: many small capacities often cost more (and are harder to govern)
            # than one right-sized capacity with bursting/smoothing across workloads.
            status = "pass" if len(small) < CONSOLIDATION_MIN_SMALL_CAPS else "info"
            findings.append(make_finding(
                rule, dimension="cost", status=status,
                title="Small capacities that may be candidates for consolidation",
                evidence={"smallCapacityCount": len(small),
                          "threshold": CONSOLIDATION_MIN_SMALL_CAPS,
                          "capacities": [{"name": c.get("displayName"), "sku": c.get("sku"),
                                          "workspaces": c.get("assignedWorkspaceCount")} for c in small]},
                recommendation=("Consider consolidating several small F-SKUs into one right-sized capacity: "
                                "CU smoothing and bursting are shared across workloads, often improving both cost "
                                "efficiency and headroom versus many isolated small capacities.")
            ))

    # --- COST-008 empty active capacities ---
    rule = rules.get("COST-008")
    if rule:
        if not capacities:
            findings.append(missing_raw_finding(rule, "cost", "capacity_metrics.json"))
        else:
            empties = [c for c in capacities if c.get("assignedWorkspaceCount", 0) == 0
                       and (c.get("state") or "").lower() == "active"
                       and is_dedicated_capacity(c.get("sku"))]
            findings.append(make_finding(
                rule, dimension="cost", status="fail" if empties else "pass",
                title="Active capacities with no assigned workspaces",
                evidence={"count": len(empties),
                          "capacities": [{"name": c.get("displayName"), "sku": c.get("sku")}
                                         for c in empties]},
                recommendation="Pause or delete empty capacities; they incur SKU charges with zero utilization.",
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

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Performance review.

Reads semantic_models.json + capacity_metrics.json and emits findings on
refresh health, scheduling, and capacity headroom signals reachable via REST.

Rule coverage:
  PERF-001  Capacity throttling          -> graded vs future-smoothing thresholds
                                            (fail at/near 100%, info below)
  PERF-002  Average CU%                  -> info (deep metric)
    PERF-003  Semantic model size threshold
  PERF-004  Refresh failure rate
  PERF-005  Stale models (no recent refresh)
  PERF-006  Refreshable models without schedule / recent run
  PERF-007  Long average refresh duration (> 2h)

DATA SAFETY: Metadata + refresh history only. No model query is issued.
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings

STALE_DAYS = threshold("performance", "stale_model_days", 30, env="PERF_STALE_DAYS", cast=int)
LONG_REFRESH_HOURS = threshold("performance", "long_refresh_hours", 2.0, env="PERF_LONG_REFRESH_HOURS", cast=float)
FAIL_RATIO_THRESHOLD = threshold("performance", "refresh_failure_ratio", 0.2, env="PERF_FAIL_RATIO_THRESHOLD", cast=float)
MODEL_SIZE_WARN_MB = threshold("performance", "model_size_warn_mb", 2048, env="PERF_MODEL_SIZE_WARN_MB", cast=float)
MODEL_SIZE_CRITICAL_MB = threshold("performance", "model_size_critical_mb", 8192, env="PERF_MODEL_SIZE_CRITICAL_MB", cast=float)
REFRESH_OVERLAP_MIN = threshold("performance", "refresh_overlap_minutes", 2, env="PERF_REFRESH_OVERLAP_MIN", cast=int)

# PERF-001 throttling thresholds, expressed as a percent of the Capacity
# Metrics App "future smoothing" window. The raw P95 percentages only measure
# how full each future window got at the busiest 10-minute point; they become
# real, customer-facing throttling (Throttling (s) > 0) only once a bucket
# reaches 100%. So we grade rejection buckets against these levels rather than
# failing on any non-zero value.
THROTTLE_CRITICAL_PCT = threshold("performance", "throttle_critical_pct", 100, env="PERF_THROTTLE_CRITICAL_PCT", cast=float)
THROTTLE_WARN_PCT = threshold("performance", "throttle_warn_pct", 70, env="PERF_THROTTLE_WARN_PCT", cast=float)

# PERF-002 / PERF-011 — 7-day average CU% grading bands.
CU_AVG_WARN_PCT = threshold("performance", "cu_avg_warn_pct", 70, cast=float)
CU_AVG_CRITICAL_PCT = threshold("performance", "cu_avg_critical_pct", 80, cast=float)


def _model_size_mb(dataset: Dict[str, Any]) -> float | None:
    """Return semantic model size in MB from known Power BI/Fabric metadata keys."""
    byte_keys = (
        "sizeInBytes", "SizeInBytes", "storageSizeInBytes", "StorageSizeInBytes",
        "modelSizeInBytes", "ModelSizeInBytes", "estimatedSizeInBytes",
    )
    mb_keys = ("sizeInMB", "SizeInMB", "sizeMb", "SizeMb", "modelSizeMb", "ModelSizeMb")
    for key in byte_keys:
        value = dataset.get(key)
        if value is None:
            continue
        try:
            return float(value) / (1024 * 1024)
        except (TypeError, ValueError):
            continue
    for key in mb_keys:
        value = dataset.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _capacity_metrics_app_signal(raw_dir: Path) -> Dict[str, Any]:
    """Detect whether the Fabric Capacity Metrics App is installed.

    Order: explicit env flag wins; otherwise look for a workspace that contains
    a 'Fabric Capacity Metrics' semantic model / report in scanner output.
    """
    if _truthy(os.environ.get("CAPACITY_METRICS_APP_INSTALLED")):
        return {"installed": True, "source": "env:CAPACITY_METRICS_APP_INSTALLED"}
    scan = load_raw(raw_dir / "scanner.json") or {}
    for ws in scan.get("workspaces", []):
        for kind in ("datasets", "reports"):
            for item in (ws.get(kind) or []):
                name = (item.get("name") or "").lower()
                if "fabric capacity metrics" in name or "capacity metrics" in name:
                    return {"installed": True, "source": f"scanner:{ws.get('name')}/{item.get('name')}"}
    return {"installed": False, "source": None}


def _row_get(row: Dict[str, Any], *suffixes: str) -> Any:
    """Lookup a value in an executeQueries row regardless of whether the column
    came back as ``Table[Column]`` or just ``Column``."""
    for key, value in row.items():
        key_l = key.lower()
        for s in suffixes:
            if key_l == s.lower() or key_l.endswith(f"[{s.lower()}]"):
                return value
    return None


def _metrics_app_data(raw_dir: Path) -> Dict[str, Any]:
    """Parse capacity_metrics_app.json into per-capacity facts.

    Returns a dict with:
      - ``available``: dataset was located
      - ``reached``: at least one DAX probe succeeded
      - ``dataset``: dataset identification block
      - ``perCapacity``: ``{capacityId: {capacityId, health, avgCU7d, avgCU24h,
        p95BgRejection7d, p95InteractiveRejection7d, p95InteractiveDelay7d,
        processedOverage7d}}``
      - ``throttledItems``: list of items currently throttled (from
        ``Items Throttled`` table; one row per item)
      - ``schema``: introspection summary
      - ``errors``: per-probe error list
    """
    payload = load_raw(raw_dir / "capacity_metrics_app.json") or {}
    queries = payload.get("queries") or {}
    out: Dict[str, Any] = {
        "available": bool(payload.get("datasetLocated")),
        "reached": False,
        "dataset": payload.get("dataset"),
        "perCapacity": {},
        "throttledItems": [],
        "schema": {},
        "errors": [],
    }
    if not out["available"]:
        return out

    for probe_key, schema_key in (
        ("info_tables", "tableCount"),
        ("info_measures", "measureCount"),
    ):
        probe = queries.get(probe_key) or {}
        if probe.get("ok"):
            out["reached"] = True
            out["schema"][schema_key] = probe.get("rowCount")
        elif probe:
            out["errors"].append({"probe": probe_key, "status": probe.get("status"),
                                  "error": (probe.get("error") or "")[:300]})

    def _strip_table(col: str) -> str:
        return col.split("[", 1)[1][:-1] if "[" in col and col.endswith("]") else col

    def _read_rows(probe_key: str) -> List[Dict[str, Any]]:
        probe = queries.get(probe_key) or {}
        if not probe.get("ok"):
            if probe:
                out["errors"].append({"probe": probe_key, "status": probe.get("status"),
                                      "error": (probe.get("error") or "")[:300]})
            return []
        out["reached"] = True
        cleaned: List[Dict[str, Any]] = []
        for row in probe.get("rows") or []:
            cleaned.append({_strip_table(k): v for k, v in row.items()})
        return cleaned

    per: Dict[str, Dict[str, Any]] = {}

    for row in _read_rows("usage_summary_7d"):
        cid = (row.get("Capacity Id") or "").lower()
        if not cid:
            continue
        entry = per.setdefault(cid, {"capacityId": row.get("Capacity Id")})
        entry["health"] = row.get("Health")
        entry["avgCU7d"] = row.get("Average CU %")
        entry["p95InteractiveDelay7d"] = row.get("P95 interactive delay")
        entry["p95InteractiveRejection7d"] = row.get("P95 interactive rejection")
        entry["p95BgRejection7d"] = row.get("P95 background rejection")
        entry["processedOverage7d"] = row.get("Processed overage")
        entry["overageBillingLimit"] = row.get("Overage billing limit")

    for row in _read_rows("usage_summary_24h"):
        cid = (row.get("Capacity Id") or "").lower()
        if not cid:
            continue
        entry = per.setdefault(cid, {"capacityId": row.get("Capacity Id")})
        entry["avgCU24h"] = row.get("Average CU %")
        entry["p95BgRejection24h"] = row.get("P95 background rejection")

    for row in _read_rows("usage_summary_1h"):
        cid = (row.get("Capacity Id") or "").lower()
        if not cid:
            continue
        entry = per.setdefault(cid, {"capacityId": row.get("Capacity Id")})
        entry["avgCU1h"] = row.get("Average CU %")

    out["perCapacity"] = per

    for row in _read_rows("items_throttled"):
        out["throttledItems"].append({
            "capacityId": row.get("Capacity Id"),
            "workspace": row.get("Workspace name"),
            "itemKind": row.get("Item kind"),
            "itemName": row.get("Item name"),
            "timestamp": row.get("Timestamp"),
            "billable": row.get("Billable type"),
        })

    return out


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    # --- Capacity-level metrics gap (PERF-001/002) ---
    metrics_signal = _capacity_metrics_app_signal(raw_dir)
    metrics_data = _metrics_app_data(raw_dir)

    def _emit_perf001() -> None:
        """Throttling (background rejection / interactive delay / interactive
        rejection), graded against the Metrics App future-smoothing thresholds.

        The raw percentages are the P95 of how full each "future" window got at
        the busiest 10-minute point. They become real, customer-facing
        throttling (Throttling (s) > 0) only when a bucket reaches 100%, so we
        grade rather than fail on any non-zero value:

          * rejection bucket >= THROTTLE_CRITICAL_PCT (100%) -> active rejection (fail)
          * rejection bucket >= THROTTLE_WARN_PCT            -> approaching rejection (fail)
          * any bucket > 0 but below warn                    -> peaks eating headroom (info)
          * nothing                                         -> pass
        """
        rule = rules.get("PERF-001")
        if not rule:
            return

        per = metrics_data.get("perCapacity") or {}
        throttled = metrics_data.get("throttledItems") or []
        if per or throttled:
            # Aggregate throttled items by capacity for the evidence block.
            by_cap: Dict[str, List[Dict[str, Any]]] = {}
            for item in throttled:
                by_cap.setdefault((item.get("capacityId") or "").lower(), []).append(item)

            offenders: List[Dict[str, Any]] = []
            for cid, e in per.items():
                p95bg = e.get("p95BgRejection7d") or 0
                p95ir = e.get("p95InteractiveRejection7d") or 0
                p95id = e.get("p95InteractiveDelay7d") or 0
                items_n = len(by_cap.get(cid, []))
                worst_rejection = max(p95bg, p95ir)
                worst_any = max(p95bg, p95ir, p95id)
                if worst_rejection >= THROTTLE_CRITICAL_PCT or p95id >= THROTTLE_CRITICAL_PCT:
                    tier = "critical"   # a bucket maxed out - Fabric is actively throttling
                elif worst_rejection >= THROTTLE_WARN_PCT:
                    tier = "high"       # rejection bucket approaching the 100% ceiling
                elif worst_any > 0 or items_n:
                    tier = "watch"      # peaks consuming headroom, no rejection yet
                else:
                    tier = "ok"
                if tier != "ok":
                    offenders.append({
                        "capacityId": e.get("capacityId"),
                        "health": e.get("health"),
                        "tier": tier,
                        "p95BgRejection7d": p95bg,
                        "p95InteractiveRejection7d": p95ir,
                        "p95InteractiveDelay7d": p95id,
                        "worstRejectionPct": worst_rejection,
                        "itemsThrottled": items_n,
                    })

            critical = [o for o in offenders if o["tier"] == "critical"]
            high = [o for o in offenders if o["tier"] == "high"]
            watch = [o for o in offenders if o["tier"] == "watch"]
            actionable = critical + high
            thresholds_block = {
                "criticalPct": THROTTLE_CRITICAL_PCT,
                "warnPct": THROTTLE_WARN_PCT,
                "note": "P95 of the Metrics App future-smoothing window; "
                        "100% = active throttling (Throttling (s) > 0).",
            }

            if actionable:
                items_preview = [
                    f"{i.get('itemKind')}/{i.get('itemName')} (ws: {i.get('workspace')})"
                    for i in throttled[:10]
                ]
                worst_pct = max(o["worstRejectionPct"] for o in actionable)
                state = "active rejection" if critical else "approaching rejection"
                findings.append(make_finding(
                    rule, dimension="performance", status="fail",
                    title=f"Throttling risk on {len(actionable)} capacity(ies) ({state}); "
                          f"worst P95 rejection {worst_pct:.0f}%",
                    evidence={
                        "source": "Capacity Metrics App (Usage Summary By Capacities + Items Throttled)",
                        "dataset": metrics_data.get("dataset"),
                        "thresholds": thresholds_block,
                        "offenders": actionable,
                        "watch": watch,
                        "throttledItemsPreview": items_preview,
                        "throttledItemsTotal": len(throttled),
                    },
                    recommendation=(
                        "A throttling bucket has reached or is approaching 100% - the point "
                        "where Fabric actually delays or rejects operations. In the Metrics "
                        "App, open the named capacity's Throttling page, click the tallest "
                        "peaks, drill to Timepoint Detail, and sort items by '% of Base "
                        "capacity' / Total CU (s). Tune the top CU consumers (stagger semantic-"
                        "model refreshes, reduce query/refresh cost, scope Spark jobs), then "
                        "enable autoscale or scale-out for the peak windows, or upsize the SKU."
                    ),
                ))
            elif watch:
                worst_pct = max(
                    max(o["p95BgRejection7d"], o["p95InteractiveRejection7d"], o["p95InteractiveDelay7d"])
                    for o in watch
                )
                findings.append(make_finding(
                    rule, dimension="performance", status="info",
                    title=f"Peaks consuming capacity headroom on {len(watch)} capacity(ies); "
                          f"no rejection yet (worst P95 {worst_pct:.0f}%)",
                    evidence={
                        "source": "Capacity Metrics App (Usage Summary By Capacities + Items Throttled)",
                        "dataset": metrics_data.get("dataset"),
                        "thresholds": thresholds_block,
                        "offenders": watch,
                        "throttledItemsTotal": len(throttled),
                    },
                    recommendation=(
                        "No operations are being delayed or rejected yet (Throttling (s) = 0); "
                        "these percentages only show how much future headroom the busiest "
                        "peaks consumed. Treat it as an early-warning gauge: in the Metrics "
                        "App, drill the tallest peaks to Timepoint Detail and note the top "
                        "CU-consuming items so you can tune or reschedule them before the next "
                        "workload increase pushes a bucket toward 100%."
                    ),
                ))
            else:
                findings.append(make_finding(
                    rule, dimension="performance", status="pass",
                    title="No throttling or rejection observed",
                    evidence={
                        "source": "Capacity Metrics App (Usage Summary By Capacities + Items Throttled)",
                        "dataset": metrics_data.get("dataset"),
                        "thresholds": thresholds_block,
                        "capacitiesObserved": len(per),
                    },
                    recommendation=(
                        "Keep monitoring; throttling can appear quickly when heavy workloads "
                        "are onboarded."
                    ),
                ))
            return

        if metrics_signal["installed"]:
            findings.append(make_finding(
                rule, dimension="performance", status="info",
                title="Capacity Metrics App present; throttling tables not reachable",
                evidence={
                    "reason": "Dataset located but Usage Summary / Items Throttled queries "
                              "returned no rows or failed. Likely the signed-in user lacks "
                              "Build permission on the Metrics App dataset.",
                    "detectedVia": metrics_signal["source"],
                    "probeErrors": metrics_data.get("errors"),
                },
                recommendation=(
                    "Grant the signed-in user Build permission on the Capacity Metrics App "
                    "dataset and re-run. Until then, open the Metrics App Throttling page "
                    "manually."
                ),
            ))
        else:
            findings.append(make_finding(
                rule, dimension="performance", status="info",
                title="Throttling counters not reachable",
                evidence={"reason": "Capacity Metrics App not detected in this tenant."},
                recommendation=(
                    "Install the Fabric Capacity Metrics App from AppSource, grant the signed-in "
                    "user Build permission on its dataset, then re-run."
                ),
            ))

    def _emit_perf002() -> None:
        """Average CU% trend - 7-day average from the Metrics App; threshold-based
        status."""
        rule = rules.get("PERF-002")
        if not rule:
            return
        per = metrics_data.get("perCapacity") or {}
        rows_with_cu = [(cid, e) for cid, e in per.items() if e.get("avgCU7d") is not None]
        if rows_with_cu:
            def _avg(e: Dict[str, Any]) -> float:
                return float(e.get("avgCU7d") or 0)
            worst_cid, worst_entry = max(rows_with_cu, key=lambda kv: _avg(kv[1]))
            worst = _avg(worst_entry)
            samples = [
                {
                    "capacityId": e.get("capacityId"),
                    "health": e.get("health"),
                    "avgCU7d": e.get("avgCU7d"),
                    "avgCU24h": e.get("avgCU24h"),
                    "avgCU1h": e.get("avgCU1h"),
                }
                for _cid, e in rows_with_cu
            ]
            if worst >= CU_AVG_CRITICAL_PCT:
                status = "fail"
                title = f"7-day average CU% high on '{worst_entry.get('capacityId')}' ({worst:.1f}%)"
                reco = (
                    "Sustained average CU% at or above 80% leaves no headroom for spikes and "
                    "directly precedes throttling. Plan SKU upsize, enable capacity autoscale, "
                    "or reduce the heaviest workloads (PERF-004..PERF-009)."
                )
            elif worst >= CU_AVG_WARN_PCT:
                status = "info"
                title = f"7-day average CU% elevated on '{worst_entry.get('capacityId')}' ({worst:.1f}%)"
                reco = (
                    "Average CU% between 70% and 80% is close to throttling threshold. "
                    "Tune the heaviest workloads now or plan autoscale before the next "
                    "workload increase."
                )
            else:
                status = "pass"
                title = f"7-day average CU% healthy (worst capacity {worst:.1f}%)"
                reco = "Monitor regularly; re-run if workloads change materially."
            findings.append(make_finding(
                rule, dimension="performance", status=status,
                title=title,
                evidence={
                    "source": "Capacity Metrics App (Usage Summary By Capacities, 7d/24h/1h)",
                    "dataset": metrics_data.get("dataset"),
                    "capacities": samples,
                },
                recommendation=reco,
            ))
            return
        if metrics_signal["installed"]:
            findings.append(make_finding(
                rule, dimension="performance", status="info",
                title="Capacity Metrics App present; CU% tables not reachable",
                evidence={
                    "reason": "Usage Summary tables did not return rows. Likely missing Build "
                              "permission on the Metrics App dataset.",
                    "detectedVia": metrics_signal["source"],
                    "probeErrors": metrics_data.get("errors"),
                },
                recommendation=(
                    "Grant the signed-in user Build permission on the Capacity Metrics App "
                    "dataset and re-run."
                ),
            ))
        else:
            findings.append(make_finding(
                rule, dimension="performance", status="info",
                title="Average CU% trend not reachable",
                evidence={"reason": "Capacity Metrics App not detected in this tenant."},
                recommendation=(
                    "Install the Fabric Capacity Metrics App, grant Build permission, re-run."
                ),
            ))

    _emit_perf001()
    _emit_perf002()

    sm = load_raw(raw_dir / "semantic_models.json")
    if not sm:
        for rid in ("PERF-003", "PERF-004", "PERF-005", "PERF-006", "PERF-007"):
            if rid in rules:
                findings.append(missing_raw_finding(rules[rid], "performance", "semantic_models.json"))
        return findings

    datasets = sm.get("datasets") or []
    refreshes = sm.get("refreshes") or {}
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_DAYS)

    failed_recent: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    no_schedule: List[Dict[str, Any]] = []
    long_running: List[Dict[str, Any]] = []
    oversized_models: List[Dict[str, Any]] = []
    sized_models: List[Dict[str, Any]] = []

    for ds in datasets:
        dsid = ds.get("id")
        if not dsid:
            continue
        size_mb = _model_size_mb(ds)
        if size_mb is not None:
            row = {"name": ds.get("name"), "workspace": ds.get("workspaceName"),
                   "sizeMb": round(size_mb, 1),
                   "storageMode": ds.get("targetStorageMode") or ds.get("defaultMode")}
            sized_models.append(row)
            if size_mb >= MODEL_SIZE_WARN_MB:
                oversized_models.append(row)
        history = refreshes.get(dsid) or []
        successes = [h for h in history if (h.get("status") or "").lower() == "completed"]
        fails = [h for h in history if (h.get("status") or "").lower() in ("failed", "disabled")]
        last = history[0] if history else None
        last_success = successes[0] if successes else None

        if history and len(fails) / max(1, len(history)) >= FAIL_RATIO_THRESHOLD:
            failed_recent.append({"name": ds.get("name"), "workspace": ds.get("workspaceName"),
                                  "failureRatio": round(len(fails) / len(history), 2),
                                  "lastStatus": (last or {}).get("status")})

        last_success_dt = _parse_dt((last_success or {}).get("endTime"))
        if ds.get("isRefreshable") and (not last_success_dt or last_success_dt < stale_cutoff):
            stale.append({"name": ds.get("name"), "workspace": ds.get("workspaceName"),
                          "lastSuccessfulRefresh": (last_success or {}).get("endTime")})

        # PERF-006: refreshable without any scheduled-triggered run
        scheduled_runs = [h for h in history if (h.get("refreshType") or "").lower() in ("scheduled", "scheduledrefresh")]
        if ds.get("isRefreshable") and not scheduled_runs:
            no_schedule.append({"name": ds.get("name"), "workspace": ds.get("workspaceName")})

        # PERF-007: long average refresh
        durations = []
        for h in successes:
            start = _parse_dt(h.get("startTime"))
            end = _parse_dt(h.get("endTime"))
            if start and end and end > start:
                durations.append((end - start).total_seconds() / 3600.0)
        if durations:
            avg_h = sum(durations) / len(durations)
            if avg_h > LONG_REFRESH_HOURS:
                long_running.append({"name": ds.get("name"), "workspace": ds.get("workspaceName"),
                                     "avgHours": round(avg_h, 2), "sampleCount": len(durations)})

    rule = rules.get("PERF-003")
    if rule:
        if not sized_models:
            findings.append(make_finding(
                rule, dimension="performance", status="info",
                title="Semantic model size metadata not present in collected REST payload",
                evidence={"datasetCount": len(datasets),
                          "knownSizeKeys": ["sizeInBytes", "storageSizeInBytes", "modelSizeInBytes", "sizeInMB"]},
                recommendation=("The REST dataset inventory did not include model-size fields. If large Import "
                                "models are suspected, collect XMLA/TMSCHEMA storage metadata or check model "
                                "size in the Fabric/Power BI UI, then evaluate Direct Lake or aggregations.")
            ))
        else:
            critical = [m for m in oversized_models if m["sizeMb"] >= MODEL_SIZE_CRITICAL_MB]
            status = "fail" if oversized_models else "pass"
            findings.append(make_finding(
                rule, dimension="performance", status=status,
                title=(f"Semantic models over size threshold ({len(oversized_models)} over "
                       f"{MODEL_SIZE_WARN_MB:.0f} MB)" if oversized_models
                       else "Semantic model sizes below threshold"),
                evidence={
                    "warnThresholdMb": MODEL_SIZE_WARN_MB,
                    "criticalThresholdMb": MODEL_SIZE_CRITICAL_MB,
                    "modelsWithSizeMetadata": len(sized_models),
                    "oversizedModels": oversized_models[:20],
                    "criticalModels": critical[:20],
                },
                recommendation=("For large Import models, reduce cardinality and unused columns, add aggregations "
                                "or incremental refresh, and evaluate Direct Lake where source tables live in "
                                "OneLake Delta.")
            ))

    rule = rules.get("PERF-004")
    if rule:
        findings.append(make_finding(
            rule, dimension="performance",
            status="pass" if not failed_recent else "fail",
            title="Datasets with high refresh-failure ratio",
            evidence={"threshold": FAIL_RATIO_THRESHOLD, "count": len(failed_recent),
                      "datasets": failed_recent[:20]},
            recommendation="Investigate failing datasets — common causes are expired gateway credentials, "
                           "source schema drift, or out-of-memory in Import models."
        ))

    rule = rules.get("PERF-005")
    if rule:
        findings.append(make_finding(
            rule, dimension="performance",
            status="pass" if not stale else "fail",
            title=f"Datasets with no successful refresh in {STALE_DAYS} days",
            evidence={"thresholdDays": STALE_DAYS, "count": len(stale), "datasets": stale[:20]},
            recommendation="Confirm whether these models are still in use; archive or fix the refresh schedule."
        ))

    rule = rules.get("PERF-006")
    if rule:
        findings.append(make_finding(
            rule, dimension="performance",
            status="pass" if not no_schedule else "fail",
            title="Refreshable datasets without a scheduled refresh history",
            evidence={"count": len(no_schedule), "datasets": no_schedule[:20]},
            recommendation="Configure scheduled refresh (or document the manual-only contract) so data stays current."
        ))

    rule = rules.get("PERF-007")
    if rule:
        findings.append(make_finding(
            rule, dimension="performance",
            status="pass" if not long_running else "fail",
            title=f"Datasets with average refresh > {LONG_REFRESH_HOURS}h",
            evidence={"thresholdHours": LONG_REFRESH_HOURS, "count": len(long_running),
                      "datasets": long_running[:20]},
            recommendation="Enable incremental refresh or move to Direct Lake; long refreshes contend for capacity CU."
        ))

    # --- PERF-010 Consecutive refresh failures ---
    rule = rules.get("PERF-010")
    if rule:
        consecutive: List[Dict[str, Any]] = []
        for ds in datasets:
            dsid = ds.get("id")
            if not dsid:
                continue
            history = refreshes.get(dsid) or []
            # history is sorted most-recent-first by the collector.
            streak = 0
            for h in history:
                if (h.get("status") or "").lower() == "failed":
                    streak += 1
                else:
                    break
            if streak >= 2:
                consecutive.append({"name": ds.get("name"),
                                    "workspace": ds.get("workspaceName"),
                                    "consecutiveFailures": streak,
                                    "lastStatus": (history[0] if history else {}).get("status")})
        findings.append(make_finding(
            rule, dimension="performance",
            status="pass" if not consecutive else "fail",
            title="Semantic models with consecutive refresh failures",
            evidence={"count": len(consecutive), "datasets": consecutive[:20]},
            recommendation=("Two or more failures in a row means the data product is offline for users. "
                            "Fix the root cause (gateway credentials, schema drift, OOM) before adding "
                            "more retry attempts.")
        ))

    # --- PERF-011 Capacity autoscale configuration ---
    rule = rules.get("PERF-011")
    if rule:
        cm = load_raw(raw_dir / "capacity_metrics.json")
        per_metrics = metrics_data.get("perCapacity") or {}
        if not cm:
            findings.append(missing_raw_finding(rule, "performance", "capacity_metrics.json"))
        else:
            caps = cm.get("capacities") or cm.get("value") or []
            hot_no_autoscale: List[Dict[str, Any]] = []
            inventory: List[Dict[str, Any]] = []
            for c in caps:
                cid = (c.get("id") or "").lower()
                sku = c.get("sku") or c.get("skuName") or ""
                workloads = c.get("workloads") or {}
                autoscale = workloads.get("AutoScaleEnabled") if isinstance(workloads, dict) else None
                if autoscale is None:
                    # azure_capacity / fabric capacity props
                    autoscale = c.get("autoScaleEnabled") or bool(c.get("autoScaleSettings"))
                metrics_entry = per_metrics.get(cid) or {}
                avg7d = metrics_entry.get("avgCU7d")
                row = {"name": c.get("displayName") or c.get("name"),
                       "sku": sku,
                       "autoscaleEnabled": bool(autoscale),
                       "avgCU7d": avg7d}
                inventory.append(row)
                if avg7d is not None and avg7d >= CU_AVG_WARN_PCT and not autoscale:
                    hot_no_autoscale.append(row)
            if not caps:
                findings.append(make_finding(
                    rule, dimension="performance", status="info",
                    title="No Fabric capacities inventoried",
                    evidence={"reason": "capacity_metrics.json has no capacities."},
                    recommendation="Re-run the capacity_metrics collector with Fabric Admin scope."
                ))
            else:
                status = "fail" if hot_no_autoscale else "pass"
                findings.append(make_finding(
                    rule, dimension="performance", status=status,
                    title=("Capacities running hot (>=70% avg CU 7d) without autoscale"
                           if hot_no_autoscale else "Capacity autoscale configuration"),
                    evidence={"capacities": inventory,
                              "hotWithoutAutoscale": hot_no_autoscale},
                    recommendation=("Enable autoscale (or schedule a manual scale-out window) on capacities "
                                    "that regularly exceed 70% average CU - otherwise the next workload "
                                    "increase will throttle.")
                ))

    # --- PERF-008 / PERF-009: pipeline & notebook job health ---
    findings.extend(_analyze_jobs(raw_dir, rules))

    # --- PERF-014 Overlapping refresh windows in the same workspace ---
    rule = rules.get("PERF-014")
    if rule:
        by_ws: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = defaultdict(list)
        for ds in datasets:
            dsid = ds.get("id")
            if not dsid:
                continue
            windows = []
            for h in refreshes.get(dsid) or []:
                if (h.get("status") or "").lower() != "completed":
                    continue
                start = _parse_dt(h.get("startTime"))
                end = _parse_dt(h.get("endTime"))
                if start and end and end > start:
                    windows.append((start, end))
            if not windows:
                continue
            windows.sort(key=lambda w: w[0], reverse=True)
            start, end = windows[0]
            by_ws[(ds.get("workspaceId"), ds.get("workspaceName"))].append(
                {"name": ds.get("name"), "start": start, "end": end})

        overlaps: List[Dict[str, Any]] = []
        workspaces_with_timing = 0
        for (wsid, wsname), items in by_ws.items():
            workspaces_with_timing += 1
            if len(items) < REFRESH_OVERLAP_MIN:
                continue
            items.sort(key=lambda x: x["start"])
            cluster = [items[0]]
            cluster_end = items[0]["end"]
            groups: List[List[Dict[str, Any]]] = []
            for it in items[1:]:
                if it["start"] < cluster_end:
                    cluster.append(it)
                    cluster_end = max(cluster_end, it["end"])
                else:
                    if len(cluster) >= REFRESH_OVERLAP_MIN:
                        groups.append(cluster)
                    cluster = [it]
                    cluster_end = it["end"]
            if len(cluster) >= REFRESH_OVERLAP_MIN:
                groups.append(cluster)
            for g in groups:
                overlaps.append({
                    "workspace": wsname,
                    "concurrentModels": [x["name"] for x in g],
                    "window": {"start": g[0]["start"].isoformat(),
                               "end": max(x["end"] for x in g).isoformat()},
                })

        if workspaces_with_timing == 0:
            findings.append(make_finding(
                rule, dimension="performance", status="info",
                title="PERF-014: no completed refresh timing available to assess concurrency",
                evidence={"datasetsWithTiming": 0},
                recommendation="This check activates once datasets have completed refresh history with start/end times."
            ))
        else:
            findings.append(make_finding(
                rule, dimension="performance",
                status="fail" if overlaps else "pass",
                title=("PERF-014: concurrent refresh windows detected in the same workspace"
                       if overlaps else "PERF-014: refresh windows do not overlap within workspaces"),
                evidence={"overlapThreshold": REFRESH_OVERLAP_MIN,
                          "workspacesEvaluated": workspaces_with_timing,
                          "overlapGroups": overlaps[:20]},
                recommendation=("Stagger scheduled refreshes so models in the same workspace/capacity do not run "
                                "simultaneously - overlapping refreshes spike CU usage and can trigger throttling "
                                "or interactive-query slowdowns.")
            ))

    return findings


JOB_FAIL_RATIO_THRESHOLD = threshold("performance", "job_failure_ratio", 0.2, env="PERF_JOB_FAIL_RATIO", cast=float)
JOB_LONG_HOURS = threshold("performance", "job_long_hours", 1.0, env="PERF_JOB_LONG_HOURS", cast=float)


def _summarise_runs(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    terminal = [h for h in history if (h.get("status") or "").lower() in
                ("completed", "failed", "cancelled", "deduped")]
    fails = [h for h in terminal if (h.get("status") or "").lower() == "failed"]
    successes = [h for h in terminal if (h.get("status") or "").lower() == "completed"]
    durations = []
    for h in successes:
        s = _parse_dt(h.get("startTimeUtc") or h.get("startTime"))
        e = _parse_dt(h.get("endTimeUtc") or h.get("endTime"))
        if s and e and e > s:
            durations.append((e - s).total_seconds() / 3600.0)
    avg_h = round(sum(durations) / len(durations), 2) if durations else None
    ratio = round(len(fails) / len(terminal), 2) if terminal else 0.0
    return {"runs": len(terminal), "failures": len(fails), "failureRatio": ratio,
            "avgHours": avg_h, "sampleCount": len(durations)}


def _analyze_jobs(raw_dir: Path, rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    data = load_raw(raw_dir / "pipelines_notebooks.json")
    if not data:
        for rid in ("PERF-008", "PERF-009"):
            if rid in rules:
                out.append(missing_raw_finding(rules[rid], "performance", "pipelines_notebooks.json"))
        return out

    jobs_by_item: Dict[str, List[Dict[str, Any]]] = data.get("jobs") or {}

    def _examine(items: List[Dict[str, Any]], kind: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
        failing: List[Dict[str, Any]] = []
        slow: List[Dict[str, Any]] = []
        observed = 0
        for it in items:
            iid = it.get("id")
            if not iid:
                continue
            stats = _summarise_runs(jobs_by_item.get(iid) or [])
            if stats["runs"] == 0:
                continue
            observed += 1
            row = {"name": it.get("displayName") or it.get("name"),
                   "workspace": it.get("workspaceName"),
                   "runs": stats["runs"], "failureRatio": stats["failureRatio"]}
            if stats["failureRatio"] >= JOB_FAIL_RATIO_THRESHOLD and stats["failures"] >= 2:
                failing.append(row)
            if stats["avgHours"] is not None and stats["avgHours"] > JOB_LONG_HOURS:
                slow.append({**row, "avgHours": stats["avgHours"], "sampleCount": stats["sampleCount"]})
        return failing, slow, observed

    pipelines = data.get("pipelines") or []
    notebooks = data.get("notebooks") or []
    pl_fail, pl_slow, pl_obs = _examine(pipelines, "pipeline")
    nb_fail, nb_slow, nb_obs = _examine(notebooks, "notebook")

    rule = rules.get("PERF-008")
    if rule:
        if pl_obs == 0:
            out.append(make_finding(
                rule, dimension="performance", status="info",
                title="No pipeline run history observed",
                evidence={"pipelinesInventoried": len(pipelines),
                          "reason": "No data pipelines had any job-instance history in the review window. "
                                    "Either no pipelines have ever run, or they were created after the "
                                    "collector window."},
                recommendation="If pipelines exist but were never triggered, decide whether they should be "
                               "scheduled or removed; orphaned pipelines still appear in capacity governance."
            ))
        else:
            out.append(make_finding(
                rule, dimension="performance",
                status="pass" if not pl_fail and not pl_slow else "fail",
                title="Data pipeline run health",
                evidence={"pipelinesWithRuns": pl_obs,
                          "failingPipelines": len(pl_fail),
                          "longRunningPipelines": len(pl_slow),
                          "failThresholdRatio": JOB_FAIL_RATIO_THRESHOLD,
                          "longThresholdHours": JOB_LONG_HOURS,
                          "failing": pl_fail[:15], "slow": pl_slow[:15]},
                recommendation="Investigate failing pipelines (activity-level errors, expired gateway creds, "
                               "upstream schema). For long-running pipelines, parallelise activities, push "
                               "filters upstream, or split into smaller pipelines triggered by event."
            ))

    rule = rules.get("PERF-009")
    if rule:
        if nb_obs == 0:
            out.append(make_finding(
                rule, dimension="performance", status="info",
                title="No notebook run history observed",
                evidence={"notebooksInventoried": len(notebooks),
                          "reason": "No notebooks had any job-instance history in the review window."},
                recommendation="If notebooks are intended for production, schedule them via a pipeline and "
                               "re-run this review to capture run health."
            ))
        else:
            out.append(make_finding(
                rule, dimension="performance",
                status="pass" if not nb_fail and not nb_slow else "fail",
                title="Spark notebook run health",
                evidence={"notebooksWithRuns": nb_obs,
                          "failingNotebooks": len(nb_fail),
                          "longRunningNotebooks": len(nb_slow),
                          "failThresholdRatio": JOB_FAIL_RATIO_THRESHOLD,
                          "longThresholdHours": JOB_LONG_HOURS,
                          "failing": nb_fail[:15], "slow": nb_slow[:15]},
                recommendation="Long-running notebooks often signal missing OPTIMIZE / V-Order on lakehouse "
                               "tables or oversized Spark sessions. Failing notebooks usually point to "
                               "unhandled exceptions or missing dependencies - bake them into the CI smoke "
                               "test before promoting."
            ))

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_performance.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Performance: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

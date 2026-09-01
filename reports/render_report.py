# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Render findings JSON to a single Markdown report using Jinja2 templates.

Inputs:
  - findings JSON (list of finding dicts produced by analyzers)
  - templates under reports/templates/

Output: a merged Markdown document combining executive summary, detailed
findings and recommendations.

DATA SAFETY: This module formats already-analyzed findings only. No data access.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from reports.diagrams import build_diagrams

try:
    from reports.version import __version__ as _far_version
except Exception:  # pragma: no cover - version file should always be present
    _far_version = "unknown"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
DIMENSIONS = ["architecture", "performance", "governance", "operational_excellence", "security", "cost", "tenant_settings", "notebook_code"]

DIMENSION_TITLES = {
    "architecture": "Architecture",
    "performance": "Performance",
    "governance": "Governance",
    "security": "Security",
    "cost": "Cost",
    "operational_excellence": "Operational Excellence",
    "tenant_settings": "Tenant Settings",
    "notebook_code": "Notebook Code Review (heuristic)",
}

STATUS_BADGE = {
    "pass": "PASS",
    "fail": "FAIL",
    "info": "INFO",
    "not_applicable": "N/A",
    "unknown": "UNKNOWN",
    "missing_evidence": "MISSING EVIDENCE",
}

SEVERITY_BADGE = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
    "info": "INFO",
}


def _format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:g}" if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _md_cell(value: Any) -> str:
    return _format_value(value).replace("|", "\\|").replace("\n", "<br>")


def _markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    if not rows:
        return ""
    header = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join(_md_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _format_list_of_dicts(items: List[Dict[str, Any]], max_rows: int = 15) -> str:
    if not items:
        return ""
    # Pick the most useful columns based on first item, capped to 4 columns.
    preferred = ("name", "workspace", "notebook", "displayName", "sku", "type", "status",
                 "failureRatio", "avgHours", "sampleCount", "count", "id",
                 "capacityId", "health", "itemsThrottled",
                 "p95BgRejection7d", "p95InteractiveRejection7d", "p95InteractiveDelay7d",
                 "avgCU7d", "autoscaleEnabled",
                 "lastSuccessfulRefresh", "lastStatus",
                 "workspaces", "thresholdDays", "thresholdHours",
                 "gatewayType", "memberCount", "datasourceCount",
                 "stageCount", "storageMode", "consecutiveFailures",
                 "items", "cellIndexes")
    first_keys = list(items[0].keys()) if isinstance(items[0], dict) else []
    cols = [k for k in preferred if k in first_keys][:4]
    if not cols:
        cols = first_keys[:4]
    if not cols:
        return ", ".join(_format_value(x) for x in items[:max_rows])

    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    rows = [header, sep]
    for it in items[:max_rows]:
        if not isinstance(it, dict):
            continue
        rows.append("| " + " | ".join(_format_value(it.get(c, "")) for c in cols) + " |")
    if len(items) > max_rows:
        rows.append(f"| _\u2026 {len(items) - max_rows} more_ |" + " |" * (len(cols) - 1))
    return "\n".join(rows)


def _join_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_format_value(x) for x in value)
    return _format_value(value) if value not in (None, "") else ""


def _source_summary(source_hints: Dict[str, Any]) -> str:
    if not isinstance(source_hints, dict) or not source_hints:
        return ""
    pieces: List[str] = []
    if source_hints.get("connectors"):
        pieces.append("connectors: " + _join_list(source_hints["connectors"]))
    if source_hints.get("sqlServers"):
        pieces.append("servers: " + _join_list(source_hints["sqlServers"]))
    if source_hints.get("schemaItems"):
        refs = [
            f"{x.get('schema')}.{x.get('item')}"
            for x in source_hints["schemaItems"]
            if isinstance(x, dict)
        ]
        if refs:
            pieces.append("objects: " + ", ".join(refs))
    if source_hints.get("referencedQueries"):
        pieces.append("uses another model query/table: " + _join_list(source_hints["referencedQueries"]))
    if source_hints.get("parameterOrQueryRefs"):
        pieces.append("uses model expression/parameter: " + _join_list(source_hints["parameterOrQueryRefs"]))
    if source_hints.get("inlineTable"):
        pieces.append("inline table")
    if source_hints.get("files"):
        pieces.append("files: " + _join_list(source_hints["files"]))
    return "; ".join(pieces)


def _directlake_audit_md(audit: Dict[str, Any]) -> str:
    """Render PERF-012 audit evidence as readable report sections."""
    if not isinstance(audit, dict):
        return ""
    details = audit.get("details") or {}
    parts: List[str] = []

    summary = [
        f"storage partitions are `{_join_list(audit.get('partitionModes') or []) or 'not detected'}`",
        f"Power Query/M partitions: {'yes' if audit.get('hasM') else 'no'}",
        f"source connectors: {_join_list(audit.get('mConnectors') or []) or 'none detected'}",
        f"calculated columns: {audit.get('calculatedColumns', 0)}",
        f"calculated tables: {audit.get('calculatedTables', 0)}",
    ]
    parts.append("- **Direct Lake audit summary:** " + "; ".join(summary))

    blockers = audit.get("blockers") or []
    if blockers:
        rows = []
        for b in blockers:
            if not isinstance(b, dict):
                continue
            evidence = _join_list(b.get("connectors") or [])
            if b.get("count") not in (None, ""):
                evidence = f"{b.get('count')} object(s)"
            rows.append([
                (b.get("kind") or "").replace("_", " "),
                b.get("detail") or "",
                evidence,
            ])
        table = _markdown_table(["Blocker", "Why it matters", "Evidence"], rows)
        if table:
            parts.append(f"\n**Direct Lake blockers ({len(rows)})**\n\n{table}")

    calc_cols = details.get("calculatedColumnsDetail") or []
    if calc_cols:
        rows = [
            [item.get("table"), item.get("column")]
            for item in calc_cols
            if isinstance(item, dict)
        ]
        table = _markdown_table(["Table", "Calculated column"], rows[:40])
        if table:
            parts.append(f"\n**Calculated columns ({len(calc_cols)})**\n\nThese columns are defined by DAX expressions in the semantic model. Direct Lake does not support them as model-side calculated columns; move the logic upstream into Delta tables or replace it with measures where appropriate.\n\n{table}")

    calc_tables = details.get("calculatedTablesDetail") or []
    if calc_tables:
        rows = [
            [item.get("table"), item.get("partition")]
            for item in calc_tables
            if isinstance(item, dict)
        ]
        table = _markdown_table(["Calculated table", "Partition"], rows[:40])
        if table:
            parts.append(f"\n**Calculated tables ({len(calc_tables)})**\n\nThese tables are generated inside the semantic model rather than bound directly to OneLake Delta tables. Recreate them upstream in the lakehouse/warehouse before moving to Direct Lake.\n\n{table}")

    partitions = details.get("partitions") or []
    if partitions:
        rows = []
        for p in partitions:
            if not isinstance(p, dict):
                continue
            kind = p.get("kind") or ""
            kind_label = "calculated table" if kind == "calculated" else ("Power Query/M" if kind == "m" else kind)
            rows.append([
                p.get("table"),
                kind_label,
                _source_summary(p.get("sourceHints") or {}),
            ])
        table = _markdown_table(["Model table", "Partition/source type", "Source hint"], rows[:40])
        if table:
            parts.append(f"\n**Model table sources ({len(rows)})**\n\nThis table shows where each model table appears to come from. `Power Query/M` means the model uses an import query; `calculated table` means the table is produced by a DAX calculated partition. Source hints are metadata-only and may point to SQL objects, files, inline tables, or another model query/expression.\n\n{table}")

    expressions = details.get("expressions") or []
    if expressions:
        rows = [
            [item.get("name"), item.get("value")]
            for item in expressions
            if isinstance(item, dict)
        ]
        table = _markdown_table(["Expression / parameter", "Value or default"], rows[:20])
        if table:
            parts.append(f"\n**Model expressions / parameters ({len(expressions)})**\n\nThese are model-level expressions or parameters referenced by partitions. They are shown because a table source may depend on one of these values rather than directly naming the source object.\n\n{table}")

    counts = details.get("counts") or {}
    if counts:
        source_columns = counts.get("sourceColumnsListed", 0)
        parts.append(
            f"- **Metadata coverage note:** the analyzer found {source_columns} source-column mapping(s) "
            "in the model definition. They are retained in `findings_storage_mode.json` for traceability, "
            "but the PDF focuses on Direct Lake blockers, calculated objects, and table sources."
        )

    return "\n".join(parts)


def fmt_evidence(evidence: Any) -> str:
    """Render evidence dict as customer-readable markdown (bullets + tables)."""
    if not isinstance(evidence, dict) or not evidence:
        return ""
    parts: List[str] = []
    # Pull out summary scalars first.
    scalar_keys = []
    list_keys = []
    for k, v in evidence.items():
        if isinstance(v, list):
            list_keys.append(k)
        else:
            scalar_keys.append(k)
    for k in scalar_keys:
        v = evidence[k]
        if v in ("", None, [], {}):
            continue
        if k == "audit" and isinstance(v, dict):
            audit = _directlake_audit_md(v)
            if audit:
                parts.append(audit)
            continue
        label = k.replace("_", " ")
        if isinstance(v, (dict,)):
            parts.append(f"- **{label}:** `{json.dumps(v, ensure_ascii=False)}`")
        else:
            parts.append(f"- **{label}:** {_format_value(v)}")
    for k in list_keys:
        items = evidence[k]
        if not items:
            continue
        label = k.replace("_", " ").capitalize()
        if all(isinstance(x, dict) for x in items):
            table = _format_list_of_dicts(items)
            if table:
                parts.append(f"\n**{label} ({len(items)})**\n\n{table}")
        else:
            # List of scalars: keep short lists inline, render long lists as a
            # bulleted block so the PDF stays readable instead of producing a
            # comma-joined wall of text.
            if len(items) <= 3:
                sample = ", ".join(_format_value(x) for x in items)
                parts.append(f"- **{label} ({len(items)}):** {sample}")
            else:
                shown = items[:15]
                bullets = "\n".join(f"  - {_format_value(x)}" for x in shown)
                more = "" if len(items) <= 15 else f"\n  - _\u2026 {len(items) - 15} more_"
                parts.append(f"\n**{label} ({len(items)})**\n\n{bullets}{more}")
    return "\n".join(parts)


def sev_badge(sev: str) -> str:
    return SEVERITY_BADGE.get((sev or "").lower(), (sev or "").upper())


def status_badge(status: str) -> str:
    return STATUS_BADGE.get((status or "").lower(), (status or "").upper())


def dim_title(dim: str) -> str:
    return DIMENSION_TITLES.get(dim, (dim or "").replace("_", " ").title())


def _summary(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, Dict[str, int]] = {
        d: {"dimension": d, "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for d in DIMENSIONS
    }
    for f in findings:
        if f.get("status") != "fail":
            continue
        rid = (f.get("rule_id") or "")
        dim = "notebook_code" if rid.startswith("NBCODE-") else f.get("dimension", "other")
        sev = f.get("severity", "info")
        if dim in counts and sev in counts[dim]:
            counts[dim][sev] += 1
    return list(counts.values())


def _scope_counts(raw_dir: Path) -> Dict[str, Any]:
    counts: Dict[str, Any] = {"workspaces": "(see inventory)", "capacities": "(see inventory)"}
    try:
        inv_path = raw_dir / "workspace_inventory.json"
        if inv_path.exists():
            inv = json.loads(inv_path.read_text(encoding="utf-8-sig"))
            wss = inv.get("workspaces") or inv.get("value") or []
            counts["workspaces"] = len(wss)
        cap_path = raw_dir / "capacity_metrics.json"
        if cap_path.exists():
            cap = json.loads(cap_path.read_text(encoding="utf-8-sig"))
            counts["capacities"] = len(cap.get("capacities") or [])
    except (json.JSONDecodeError, OSError):
        pass
    return counts


# ---- Environment Overview (FUAM-style metric cards) ----------------------

def _env_card(num: Any, label: str, sub: str = "", tone: str = "") -> str:
    """One metric card. ``tone`` adds a coloured left accent (info/good/warn/bad)."""
    cls = "env-card" + (f" env-{tone}" if tone else "")
    sub_html = f'<div class="env-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="{cls}">'
        f'<div class="env-num">{num}</div>'
        f'<div class="env-label">{label}</div>'
        f"{sub_html}"
        "</div>"
    )


def _env_group(title: str, cards: List[str]) -> str:
    cards = [c for c in cards if c]
    if not cards:
        return ""
    return (
        '<div class="env-group">'
        f'<div class="env-group-title">{title}</div>'
        f'<div class="env-cards">{"".join(cards)}</div>'
        "</div>"
    )


def _environment_overview(raw_dir: Path, findings: List[Dict[str, Any]]) -> str:
    """Build a FUAM-style at-a-glance overview of the Fabric estate.

    A grid of metric cards summarising workspaces, items, capacities, access,
    governance and activity — built strictly from the collected metadata. Cards
    only render when their backing raw file is present, so the section scales
    from a single workspace to a whole tenant without crashing.
    """
    from reports.diagrams import _count_items, _filter_workspaces, _is_personal_workspace

    heading = "# Environment Overview"
    intro = (
        "An at-a-glance map of the Fabric estate captured by the collectors — the "
        "workspaces, items, capacities, access and activity this review is based on. "
        "Every number is metadata only; no customer data is read."
    )

    scan = _load_json(raw_dir / "scanner.json") or {}
    inv = _load_json(raw_dir / "workspace_inventory.json") or {}
    cap = _load_json(raw_dir / "capacity_metrics.json") or {}
    models = _load_json(raw_dir / "semantic_models.json") or {}
    ts = _load_json(raw_dir / "tenant_settings.json") or {}
    gw = _load_json(raw_dir / "gateways.json") or {}
    git = _load_json(raw_dir / "git_integration.json") or {}
    pipes = _load_json(raw_dir / "deployment_pipelines.json") or {}
    acts = _load_json(raw_dir / "activity_logs.json") or {}

    groups: List[str] = []

    # --- Estate -----------------------------------------------------------
    scan_ws = scan.get("workspaces") or []
    inv_ws = inv.get("workspaces") or inv.get("value") or []
    all_ws = scan_ws or inv_ws
    real_ws = _filter_workspaces(all_ws) if all_ws else []
    personal = sum(1 for w in all_ws if _is_personal_workspace(w))

    capacities = cap.get("capacities") or []
    skus = sorted({(c.get("sku") or "").upper() for c in capacities if c.get("sku")})
    datasets = models.get("datasets") or []

    item_kinds = ("lakehouses", "warehouses", "datasets", "reports",
                  "dataflows", "notebooks", "pipelines")
    item_totals = {k: 0 for k in item_kinds}
    if scan_ws:
        for w in _filter_workspaces(scan_ws):
            for k in item_kinds:
                item_totals[k] += _count_items(w, k)
    total_items = sum(item_totals.values())

    estate_cards = []
    if all_ws:
        estate_cards.append(_env_card(
            len(real_ws), "Workspaces",
            f"+{personal} personal" if personal else "", "info"))
    if capacities:
        estate_cards.append(_env_card(
            len(capacities), "Capacities",
            ", ".join(skus) if skus else "", "info"))
    if scan_ws:
        estate_cards.append(_env_card(total_items, "Fabric items", "across workspaces", "info"))
    if datasets:
        dl = sum(1 for d in datasets if (d.get("targetStorageMode") or "").lower().startswith("directlake"))
        estate_cards.append(_env_card(
            len(datasets), "Semantic models",
            f"{dl} Direct Lake" if dl else "", "info"))
    groups.append(_env_group("Estate", estate_cards))

    # --- Items breakdown --------------------------------------------------
    if scan_ws and total_items:
        breakdown = [
            _env_card(item_totals["lakehouses"], "Lakehouses"),
            _env_card(item_totals["warehouses"], "Warehouses"),
            _env_card(item_totals["reports"], "Reports"),
            _env_card(item_totals["notebooks"], "Notebooks"),
            _env_card(item_totals["pipelines"], "Data pipelines"),
            _env_card(item_totals["dataflows"], "Dataflows"),
        ]
        groups.append(_env_group("Items by type", breakdown))

    # --- Governance & access ---------------------------------------------
    gov_cards = []
    if scan_ws:
        principals: set[str] = set()
        for w in scan_ws:
            for u in (w.get("users") or []):
                ident = (u.get("identifier") or u.get("displayName")
                         or u.get("graphId") or u.get("emailAddress"))
                if ident:
                    principals.add(str(ident).lower())
        if principals:
            gov_cards.append(_env_card(len(principals), "Principals with access",
                                       "users + groups", "info"))
    settings = ts.get("tenantSettings") or ts.get("value") or []
    if settings:
        enabled = sum(1 for s in settings if s.get("enabled"))
        gov_cards.append(_env_card(f"{enabled}/{len(settings)}", "Tenant settings on",
                                   "enabled vs reviewed", "warn"))
    git_ws = git.get("workspaces") or git.get("value") or []
    if git_ws:
        connected = sum(
            1 for w in git_ws
            if (w.get("gitProviderDetails") or w.get("repository") or {}).get("repositoryName")
            or w.get("connected")
        )
        tone = "good" if connected else "warn"
        gov_cards.append(_env_card(f"{connected}/{len(git_ws)}", "Git-connected", "source control", tone))
    if pipes.get("pipelines") is not None:
        n_pipes = len(pipes.get("pipelines") or [])
        gov_cards.append(_env_card(n_pipes, "Deployment pipelines",
                                   "release management", "good" if n_pipes else "warn"))
    if gw.get("gateways") is not None:
        n_gw = len(gw.get("gateways") or [])
        gov_cards.append(_env_card(n_gw, "Gateways", "data connectivity", "info"))
    groups.append(_env_group("Governance & access", gov_cards))

    # --- Activity & refresh ----------------------------------------------
    act_cards = []
    events = acts.get("events") or []
    if acts:
        window = acts.get("windowDays") or os.environ.get("ACTIVITY_DAYS_LOG", os.environ.get("ACTIVITY_LOG_DAYS", "7"))
        act_cards.append(_env_card(acts.get("eventCount", len(events)), "Activity events",
                                   f"last {window} days", "info"))
        active = {str(e.get("UserId") or e.get("UserKey")).lower()
                  for e in events if e.get("UserId") or e.get("UserKey")}
        if active:
            act_cards.append(_env_card(len(active), "Active users", "in the window", "info"))
    refreshes = models.get("refreshes") or {}
    if datasets:
        refreshable = sum(1 for d in datasets if d.get("isRefreshable"))
        act_cards.append(_env_card(refreshable, "Refreshable models", "scheduled refresh", "info"))
    if refreshes:
        failed_models = 0
        for hist in refreshes.values():
            if any((r.get("status") or "").lower() == "failed" for r in (hist or [])):
                failed_models += 1
        act_cards.append(_env_card(failed_models, "Models with refresh failures",
                                   "in recent history", "bad" if failed_models else "good"))
    groups.append(_env_group("Activity & refresh", act_cards))

    # --- Review result ----------------------------------------------------
    fails = sum(1 for f in findings if f.get("status") == "fail")
    passes = sum(1 for f in findings if f.get("status") == "pass")
    infos = sum(1 for f in findings if f.get("status") == "info")
    not_applicable = sum(1 for f in findings if f.get("status") == "not_applicable")
    unknown = sum(1 for f in findings if f.get("status") == "unknown")
    missing = sum(1 for f in findings if f.get("status") == "missing_evidence")
    result_cards = [
        _env_card(fails, "Failing checks", "need attention", "bad" if fails else "good"),
        _env_card(passes, "Passing checks", "aligned to checklist", "good"),
        _env_card(infos, "Informational", "context / not scored", "info"),
        _env_card(not_applicable, "Not applicable", "excluded from score", "info"),
        _env_card(unknown + missing, "Needs evidence", "unknown or incomplete", "warn" if unknown + missing else "good"),
    ]
    groups.append(_env_group("Review result", result_cards))

    groups = [g for g in groups if g]
    if not groups:
        return ""
    overview = '<div class="env-overview">' + "".join(groups) + "</div>"
    return f"{heading}\n\n{intro}\n\n{overview}\n"


# ---- Semantic Models / VertiPaq footprint section ------------------------

def _vp_first(rec: Dict[str, Any], *candidates: str) -> Any:
    """First present, non-null value among ``candidates`` keys.

    VertiPaq Analyzer column headers vary by sempy-labs version (the collector
    normalizes them to snake_case), so every lookup tries a few spellings.
    """
    for key in candidates:
        if key in rec and rec[key] is not None:
            return rec[key]
    return None


def _vp_num(rec: Dict[str, Any], *candidates: str) -> float:
    val = _vp_first(rec, *candidates)
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _vp_text(rec: Dict[str, Any], *candidates: str) -> str:
    val = _vp_first(rec, *candidates)
    return "" if val is None else str(val)


def _mb(num_bytes: float) -> str:
    return f"{num_bytes / 1048576:.1f}" if num_bytes else "0.0"


def _vertipaq_section(raw_dir: Path) -> str:
    """Render the VertiPaq footprint of each semantic model as markdown.

    Reads ``output/raw/vertipaq_stats.json`` (produced by the
    ``collectors.vertipaq_stats`` collector, which only yields real data when
    run inside a Fabric notebook). Degrades to a short explanatory note when the
    file is missing or empty so the local report always renders.
    """
    heading = "# Semantic Models — VertiPaq Footprint"
    intro = (
        "In-memory storage-engine statistics for each Import / Direct Lake semantic "
        "model: total size, column counts and the most expensive tables and columns — "
        "the same numbers DAX Studio's VertiPaq Analyzer reports. Metadata only; no "
        "customer rows are read."
    )

    vp = _load_json(raw_dir / "vertipaq_stats.json")
    if not vp:
        return (
            f"{heading}\n\n{intro}\n\n"
            "> _No VertiPaq statistics available — `output/raw/vertipaq_stats.json` "
            "was not produced. This collector only returns data when the pipeline "
            "runs inside a Microsoft Fabric notebook (it uses the in-engine VertiPaq "
            "Analyzer via `semantic-link-labs`)._\n"
        )
    if not vp.get("available"):
        notes = vp.get("notes") or []
        note = (" " + notes[0]) if notes else ""
        return (
            f"{heading}\n\n{intro}\n\n"
            "> _VertiPaq analysis did not run in this collection."
            f"{note} Run the pipeline inside a Microsoft Fabric notebook to populate "
            "model sizes, cardinality and encoding._\n"
        )

    models = vp.get("models") or []
    if not models:
        return (
            f"{heading}\n\n{intro}\n\n"
            "> _No semantic models were analyzed in this run._\n"
        )

    parts: List[str] = [heading, "", intro, ""]

    # Per-model summary table.
    summary_rows: List[List[Any]] = []
    for m in models:
        cols = m.get("columns") or []
        tbls = m.get("tables") or []
        model_frame = m.get("model") or []
        total = _vp_num(model_frame[0], "total_size", "model_size", "size") if model_frame else 0.0
        if not total and tbls:
            total = sum(_vp_num(t, "total_size", "size") for t in tbls)
        calc = sum(1 for c in cols if str(_vp_first(c, "is_calculated", "calculated") or "").lower() in ("true", "1", "yes"))
        summary_rows.append([
            m.get("model_name") or "(unknown)",
            m.get("workspace_name") or "",
            m.get("storage_mode") or "",
            _mb(total),
            len(tbls),
            len(cols),
            calc,
        ])
    summary = _markdown_table(
        ["Model", "Workspace", "Storage mode", "Size (MB)", "Tables", "Columns", "Calc cols"],
        summary_rows,
    )
    if summary:
        parts.append("## Model summary\n")
        parts.append(summary)
        parts.append("")

    # Per-model detail: largest tables + largest columns.
    for m in models:
        name = m.get("model_name") or "(unknown)"
        tbls = m.get("tables") or []
        cols = m.get("columns") or []
        if not tbls and not cols and not (m.get("partitions") or m.get("relationships") or m.get("hierarchies")):
            continue
        parts.append(f"\n## {name}\n")

        if tbls:
            top_t = sorted(tbls, key=lambda t: _vp_num(t, "total_size", "size"), reverse=True)[:15]
            rows = [[
                _vp_text(t, "table_name", "table", "name"),
                f"{int(_vp_num(t, 'row_count', 'rows', 'cardinality')):,}",
                _mb(_vp_num(t, "total_size", "size")),
                f"{_vp_num(t, 'pct_db', 'pct_database'):.1f}",
            ] for t in top_t]
            table = _markdown_table(["Table", "Rows", "Size (MB)", "% of model"], rows)
            if table:
                parts.append("**Largest tables**\n")
                parts.append(table)
                parts.append("")

        if cols:
            top_c = sorted(cols, key=lambda c: _vp_num(c, "total_size", "size"), reverse=True)[:20]
            rows = [[
                _vp_text(c, "table_name", "table"),
                _vp_text(c, "column_name", "column", "name"),
                _vp_text(c, "data_type", "type"),
                _vp_text(c, "encoding", "column_encoding"),
                f"{int(_vp_num(c, 'cardinality', 'column_cardinality')):,}",
                _mb(_vp_num(c, "total_size", "size")),
                f"{_vp_num(c, 'pct_table'):.1f}",
            ] for c in top_c]
            table = _markdown_table(
                ["Table", "Column", "Data type", "Encoding", "Cardinality", "Size (MB)", "% of table"],
                rows,
            )
            if table:
                parts.append("**Largest columns**\n")
                parts.append(table)
                parts.append("")

        partitions = m.get("partitions") or []
        if partitions:
            top_p = sorted(partitions, key=lambda p: _vp_num(p, "record_count", "records", "row_count", "rows"), reverse=True)[:15]
            rows = [[
                _vp_text(p, "table_name", "table"),
                _vp_text(p, "partition_name", "partition", "name"),
                _vp_text(p, "mode", "partition_mode"),
                f"{int(_vp_num(p, 'record_count', 'records', 'row_count', 'rows')):,}",
                f"{int(_vp_num(p, 'segment_count', 'segments')):,}",
                f"{_vp_num(p, 'records_per_segment', 'rows_per_segment'):,.0f}",
            ] for p in top_p]
            table = _markdown_table(
                ["Table", "Partition", "Mode", "Records", "Segments", "Records / segment"],
                rows,
            )
            if table:
                parts.append("**Partitions**\n")
                parts.append(table)
                parts.append("")

        relationships = m.get("relationships") or []
        if relationships:
            rows = [[
                _vp_text(r, "from_object", "from", "from_table"),
                _vp_text(r, "to_object", "to", "to_table"),
                _vp_text(r, "multiplicity", "cardinality_type"),
                _mb(_vp_num(r, "used_size", "relationship_size", "size")),
                f"{int(_vp_num(r, 'missing_rows', 'missing_keys')):,}",
            ] for r in relationships]
            table = _markdown_table(
                ["From", "To", "Multiplicity", "Used size (MB)", "Missing rows"],
                rows,
            )
            if table:
                parts.append("**Relationships**\n")
                parts.append(table)
                parts.append("")

        hierarchies = m.get("hierarchies") or []
        if hierarchies:
            rows = [[
                _vp_text(h, "table_name", "table"),
                _vp_text(h, "hierarchy_name", "hierarchy", "name"),
                _mb(_vp_num(h, "used_size", "hierarchy_size", "size")),
            ] for h in hierarchies]
            table = _markdown_table(
                ["Table", "Hierarchy", "Used size (MB)"],
                rows,
            )
            if table:
                parts.append("**User hierarchies**\n")
                parts.append(table)
                parts.append("")

    errors = vp.get("errors") or []
    if errors:
        parts.append("\n> _Some models could not be analyzed: "
                     + "; ".join(str(e) for e in errors[:5]) + "._\n")

    return "\n".join(parts).rstrip() + "\n"


def _dax_section(raw_dir: Path) -> str:
    """Render metadata-only DAX coverage and static-risk details."""
    heading = "# DAX Analyzer - Metadata-only static risk"
    disclaimer = (
        "> **Interpretation:** These signals come from measure definitions only. "
        "They identify patterns that may be expensive; they are not measured duration, "
        "capacity usage, query-plan evidence, or proof that a measure is slow."
    )
    analysis = _load_json(raw_dir / "dax_analysis.json")
    if not isinstance(analysis, dict):
        return (
            f"{heading}\n\n{disclaimer}\n\n"
            "> _DAX metadata was not collected. Run the DAX collector after semantic-model "
            "definition collection to populate this page._\n"
        )

    measures = [row for row in (analysis.get("measures") or []) if isinstance(row, dict)]
    risky = sorted(
        (row for row in measures if int(row.get("risk_score") or 0) > 0),
        key=lambda row: (-int(row.get("risk_score") or 0), str(row.get("measure_name") or "")),
    )
    parts = [heading, "", disclaimer, ""]
    parts.append(
        f"- **Coverage:** {int(analysis.get('models_scanned') or 0)} semantic model(s), "
        f"{len(measures)} measure definition(s), {len(risky)} measure(s) with static signals."
    )
    errors = int(analysis.get("definition_errors") or 0)
    if errors:
        parts.append(
            f"- **Evidence gap:** {errors} semantic model definition(s) could not be analyzed; "
            "results are incomplete for those models."
        )

    if not measures:
        parts.extend(["", "> _No measure definitions were available for static DAX analysis._"])
        return "\n".join(parts).rstrip() + "\n"
    if not risky:
        parts.extend(["", "No configured static-risk patterns were detected in the collected measures."])
        return "\n".join(parts).rstrip() + "\n"

    rows: List[List[Any]] = []
    for row in risky[:25]:
        signals = row.get("signals") or []
        codes = ", ".join(
            str(signal.get("code")) for signal in signals
            if isinstance(signal, dict) and signal.get("code")
        )
        expression = " ".join(str(row.get("expression") or "").split())
        if len(expression) > 180:
            expression = expression[:177] + "..."
        rows.append([
            row.get("workspace_name") or "",
            row.get("model_name") or "",
            row.get("measure_name") or "",
            row.get("risk_level") or "none",
            int(row.get("risk_score") or 0),
            codes,
            expression,
        ])
    parts.extend([
        "",
        "## Measures with static-risk signals",
        "",
        _markdown_table(
            ["Workspace", "Semantic model", "Measure", "Risk", "Score", "Signals", "Expression preview"],
            rows,
        ),
    ])
    if len(risky) > len(rows):
        parts.extend(["", f"_{len(risky) - len(rows)} additional signalled measure(s) are available in Gold._"])
    return "\n".join(parts).rstrip() + "\n"


def _load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def render(findings_path: Path, out_path: Path, templates_dir: Path, raw_dir: Path | None = None) -> Path:
    load_dotenv()
    findings: List[Dict[str, Any]] = json.loads(findings_path.read_text(encoding="utf-8-sig"))

    fails = [f for f in findings if f.get("status") == "fail"]
    fails.sort(key=lambda x: SEVERITY_ORDER.get(x.get("severity", "info"), 99))

    by_dim: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in findings:
        # Route NBCODE-* findings to the dedicated "notebook_code" section
        # rather than their original dimension (security / architecture /
        # performance), so they're presented together as a heuristic
        # source-scan block.
        rid = (f.get("rule_id") or "")
        dim = "notebook_code" if rid.startswith("NBCODE-") else f.get("dimension", "other")
        by_dim[dim].append(f)

    scope = _scope_counts(raw_dir or Path("output/raw"))

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(disabled_extensions=("md", "j2"), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["fmt_evidence"] = fmt_evidence
    env.filters["sev_badge"] = sev_badge
    env.filters["status_badge"] = status_badge
    env.filters["dim_title"] = dim_title

    exec_tpl = env.get_template("exec_summary.md.j2")
    find_tpl = env.get_template("findings.md.j2")
    rec_tpl = env.get_template("recommendations.md.j2")

    total_unknown = sum(1 for f in findings if f.get("status") == "unknown")
    total_missing = sum(1 for f in findings if f.get("status") == "missing_evidence")
    conclusive = len(findings) - total_unknown - total_missing
    assessment_coverage = round(conclusive / len(findings) * 100, 1) if findings else 0.0
    exec_md = exec_tpl.render(
        engagement_name=os.environ.get("ENGAGEMENT_NAME", "Fabric Architecture Review"),
        brand=os.environ.get("REPORT_BRAND", ""),
        client_name=os.environ.get("CLIENT_NAME", ""),
        reviewer_name=os.environ.get("REVIEWER_NAME", ""),
        review_date=os.environ.get("REVIEW_DATE") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        tenant_id=os.environ.get("TENANT_ID", ""),
        far_version=_far_version,
        workspace_count=scope["workspaces"],
        capacity_count=scope["capacities"],
        activity_log_days=os.environ.get("ACTIVITY_LOG_DAYS", "30"),
        summary_by_dimension=_summary(findings),
        top_findings=fails[:10],
        total_rules=len(findings),
        total_fails=len(fails),
        total_pass=sum(1 for f in findings if f.get("status") == "pass"),
        total_info=sum(1 for f in findings if f.get("status") == "info"),
        total_not_applicable=sum(1 for f in findings if f.get("status") == "not_applicable"),
        total_unknown=total_unknown,
        total_missing_evidence=total_missing,
        assessment_coverage=assessment_coverage,
    )

    # Sort each dimension's findings: fail first, then info, then pass; within group by severity.
    status_order = {"fail": 0, "missing_evidence": 1, "unknown": 2,
                    "info": 3, "not_applicable": 4, "pass": 5}
    sorted_by_dim: Dict[str, List[Dict[str, Any]]] = {}
    for dim in DIMENSIONS + sorted(set(by_dim) - set(DIMENSIONS)):
        if dim in by_dim:
            sorted_by_dim[dim] = sorted(
                by_dim[dim],
                key=lambda x: (status_order.get(x.get("status"), 9),
                               SEVERITY_ORDER.get(x.get("severity", "info"), 99),
                               x.get("rule_id", "")),
            )

    find_md = find_tpl.render(findings_by_dimension=sorted_by_dim)

    rec_md = rec_tpl.render(
        recs_30=[f for f in fails if f.get("severity") in ("critical", "high")],
        recs_90=[f for f in fails if f.get("severity") == "medium"],
        recs_backlog=[f for f in fails if f.get("severity") in ("low", "info")],
    )

    overview_md = _environment_overview(raw_dir or Path("output/raw"), findings)

    diagrams_md = build_diagrams(raw_dir or Path("output/raw"), findings)

    vertipaq_md = _vertipaq_section(raw_dir or Path("output/raw"))

    dax_md = _dax_section(raw_dir or Path("output/raw"))

    merged = "\n\n<div class=\"page-break\"></div>\n\n".join(
        [s for s in [exec_md, overview_md, diagrams_md, vertipaq_md, dax_md, find_md, rec_md] if s]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(merged, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", default="output/findings.json")
    parser.add_argument("--out", default="output/report.md")
    parser.add_argument("--templates", default="reports/templates")
    parser.add_argument("--raw-dir", default="output/raw")
    args = parser.parse_args()
    path = render(Path(args.findings), Path(args.out), Path(args.templates), Path(args.raw_dir))
    print(f"Markdown report written to: {path}")


if __name__ == "__main__":
    main()

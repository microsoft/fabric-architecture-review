"""Build the gold-layer records that back the Direct Lake governance report.

Pure Python (no Spark, no pandas) so it can be unit-tested on a workstation.
The Fabric ``04_Gold`` notebook calls :func:`build_gold`, then writes each
returned table to a Delta table (append mode) in the Lakehouse ``Tables/``
folder using the schema in :mod:`reports.powerbi.schema`.

Every row carries ``run_id`` + ``run_timestamp`` so the Delta tables
accumulate one partition of history per pipeline run.

DATA SAFETY: Reads already-collected metadata / already-analyzed findings
JSON only. No live data access.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from reports.powerbi.schema import GOLD_TABLES_BY_NAME, SEVERITY_RANK
from reports.version import build_release_record

DIMENSIONS = [
    "architecture",
    "performance",
    "cost",
    "governance",
    "security",
    "tenant_settings",
    "best_practices",
]

# Base URL used to build clickable deep-links to a Fabric notebook. Fabric does
# not support reliable URL anchors to an individual cell, so we link to the
# notebook and surface the offending cell number(s) in a separate column.
_FABRIC_BASE_URL = "https://app.fabric.microsoft.com"

# Capacity SKU classification (kept local so the in-Fabric gold notebook has no
# analyzers dependency). The admin/capacities API also returns the per-user
# "Premium Per User - Reserved" (PP3) system reservation; it is real but is NOT
# a dedicated capacity you provision/pause, so we label it so the report can
# distinguish it from F-SKU/P-SKU dedicated capacities.
_PPU_SKU_RE = re.compile(r"^PP\d", re.IGNORECASE)
_FABRIC_SKU_RE = re.compile(r"^F\d", re.IGNORECASE)
_PREMIUM_SKU_RE = re.compile(r"^P\d", re.IGNORECASE)
_EMBEDDED_SKU_RE = re.compile(r"^A\d", re.IGNORECASE)


def _capacity_kind(sku: Optional[str]) -> str:
    s = (sku or "").strip()
    if _PPU_SKU_RE.match(s) or "premiumperuser" in s.lower().replace(" ", ""):
        return "Premium Per User"
    if _FABRIC_SKU_RE.match(s):
        return "Fabric"
    if _PREMIUM_SKU_RE.match(s):
        return "Premium"
    if _EMBEDDED_SKU_RE.match(s):
        return "Embedded"
    if "trial" in s.lower():
        return "Trial"
    return "Other"


def _is_dedicated_capacity(sku: Optional[str]) -> bool:
    return _capacity_kind(sku) in ("Fabric", "Premium", "Embedded")


def _rule_descriptions() -> Dict[str, str]:
    """Map ``rule_id -> plain-language description`` from the review checklist.

    The checklist lives at ``config/review-checklist.yaml`` (two levels up from
    this module). Folded YAML scalars span multiple lines, so whitespace is
    collapsed to a single readable line. Returns ``{}`` if the file or PyYAML
    is unavailable so the gold build never fails on a missing description.
    """
    try:
        import yaml  # local import: keep core gold build dependency-light
        path = Path(__file__).resolve().parents[1] / "config" / "review-checklist.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for r in (raw or {}).get("rules") or []:
        rid = r.get("id")
        desc = r.get("description")
        if rid and desc:
            out[str(rid)] = " ".join(str(desc).split())
    return out


def _notebook_index(scanner: Dict[str, Any]) -> Dict[str, tuple]:
    """Index ``(workspace_name, notebook_name) -> (workspace_id, notebook_id)``.

    The scanner payload groups items under each workspace by type; notebooks are
    under the ``Notebook`` key. A name-only fallback key is also added so a
    finding that knows the notebook but not the workspace can still resolve.
    """
    idx: Dict[str, tuple] = {}
    for ws in (scanner or {}).get("workspaces") or []:
        ws_id = ws.get("id") or ws.get("objectId")
        ws_name = (ws.get("name") or "").strip().lower()
        for nb in ws.get("Notebook") or []:
            nb_id = nb.get("id")
            nb_name = (nb.get("name") or "").strip().lower()
            if not nb_id:
                continue
            idx[f"{ws_name}|{nb_name}"] = (ws_id, nb_id)
            idx.setdefault(f"|{nb_name}", (ws_id, nb_id))
    return idx


def _notebook_url(idx: Dict[str, tuple], workspace_name: str, notebook_name: str) -> str:
    ws = (workspace_name or "").strip().lower()
    nb = (notebook_name or "").strip().lower()
    hit = idx.get(f"{ws}|{nb}") or idx.get(f"|{nb}")
    if not hit or not hit[0] or not hit[1]:
        return ""
    ws_id, nb_id = hit
    return f"{_FABRIC_BASE_URL}/groups/{ws_id}/synapsenotebooks/{nb_id}"


def _load(raw_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    p = raw_dir / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _coerce_row(table_name: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a row containing exactly the table's columns, with safe defaults
    and the right Python types so Spark can build a typed DataFrame."""
    table = GOLD_TABLES_BY_NAME[table_name]
    out: Dict[str, Any] = {}
    for col in table.columns:
        val = row.get(col.name)
        if col.kind == "int64":
            out[col.name] = int(val) if val is not None else 0
        elif col.kind == "double":
            out[col.name] = float(val) if val is not None else 0.0
        elif col.kind == "boolean":
            out[col.name] = bool(val) if val is not None else False
        elif col.kind == "dateTime":
            out[col.name] = val  # ISO-8601 string; notebook casts to timestamp
        else:
            out[col.name] = "" if val is None else str(val)
    return out


def _vp_get(rec: Dict[str, Any], *candidates: str) -> Any:
    """Return the first present value among ``candidates`` keys.

    VertiPaq Analyzer column headers vary by sempy-labs version; the collector
    normalizes them to snake_case but the exact spelling is not guaranteed, so
    every lookup tries a few plausible names before giving up.
    """
    for key in candidates:
        if key in rec and rec[key] is not None:
            return rec[key]
    return None


def _vp_int(rec: Dict[str, Any], *candidates: str) -> int:
    val = _vp_get(rec, *candidates)
    try:
        return int(float(val)) if val is not None else 0
    except (TypeError, ValueError):
        return 0


def _vp_float(rec: Dict[str, Any], *candidates: str) -> float:
    val = _vp_get(rec, *candidates)
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _vp_str(rec: Dict[str, Any], *candidates: str) -> str:
    val = _vp_get(rec, *candidates)
    return "" if val is None else str(val)


def _vp_is_calc(rec: Dict[str, Any]) -> bool:
    """Best-effort detection of a calculated column from a VertiPaq column row."""
    flag = _vp_get(rec, "is_calculated", "calculated")
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "1"):
        return True
    kind = _vp_str(rec, "type", "column_type", "kind").lower()
    return "calc" in kind


def _score(pass_count: int, fail_count: int) -> float:
    evaluated = pass_count + fail_count
    if evaluated == 0:
        return 100.0
    return round(pass_count / evaluated * 100.0, 1)


# Status colour buckets - kept consistent across every page of the report so a
# red node, a red matrix cell and a red bubble all mean the same thing.
_STATUS_RANK: Dict[str, int] = {"red": 3, "amber": 2, "green": 1, "blue": 0, "grey": -1}

# Weight each failing finding contributes to a 0-100 risk score by severity.
_SEV_WEIGHT: Dict[str, int] = {"critical": 25, "high": 12, "medium": 5, "low": 2, "info": 0}


def _status_for(value: Optional[float], kind: str) -> str:
    """Map a numeric metric to a red / amber / green status by rule type.

    ``None`` -> ``grey`` (unevaluated). Thresholds are the single set used
    everywhere so colours never drift between visuals.
    """
    if value is None:
        return "grey"
    v = float(value)
    if kind == "issue":
        return "red" if v >= 8 else "amber" if v >= 3 else "green"
    if kind == "risk":
        return "red" if v >= 75 else "amber" if v >= 40 else "green"
    if kind == "orphaned":
        return "red" if v >= 3 else "amber" if v >= 1 else "green"
    if kind == "refresh":
        return "red" if v >= 15 else "amber" if v >= 5 else "green"
    if kind == "utilization":
        return "red" if v >= 90 else "amber" if v >= 70 else "green"
    return "grey"


def _is_personal_workspace(ws: Dict[str, Any]) -> bool:
    """True for a per-user personal ("My workspace") workspace.

    Personal workspaces are never collected as part of the estate and must be
    excluded from every gold table so they never appear in the report. Detected
    by type (``PersonalGroup``/``Personal``) or name (``My workspace`` /
    ``PersonalWorkspace <UPN>``).
    """
    if (ws.get("type") or "").lower() in ("personalgroup", "personal"):
        return True
    name = (ws.get("name") or ws.get("workspaceName") or "").strip().lower()
    return name == "my workspace" or name.startswith("personalworkspace ")


def _finding_workspace_names(ev: Any) -> set:
    """Return the set of workspace names (lowercased) a finding references.

    Findings tie to workspaces by *name* through several evidence shapes, never
    by id, so we collect every name-bearing field and let the caller reverse-map
    to a workspace id via the inventory.
    """
    names: set = set()
    if not isinstance(ev, dict):
        return names
    for key in ("workspaceExamples", "workspacesWithAllLayersInside",
                "uncoveredProductionWorkspaces"):
        for n in ev.get(key) or []:
            if isinstance(n, str) and n.strip():
                names.add(n.strip().lower())
    lbw = ev.get("lakehouseLayersByWorkspace")
    if isinstance(lbw, dict):
        for n in lbw.keys():
            if isinstance(n, str) and n.strip():
                names.add(n.strip().lower())
    for key in ("workspace", "workspaceName"):
        v = ev.get(key)
        if isinstance(v, str) and v.strip():
            names.add(v.strip().lower())
    for ex in ev.get("examples") or []:
        if isinstance(ex, dict):
            v = ex.get("workspace") or ex.get("workspaceName")
            if isinstance(v, str) and v.strip():
                names.add(v.strip().lower())
    return names


def _affected_summary(ev: Any) -> str:
    """A short, human-readable "where" for a finding: the count of affected
    objects plus the first few names, e.g. ``174 affected: WS A, WS B, WS C
    +171 more``. Pulled from the varied evidence shapes the analyzers emit so
    every finding points at the workspaces/items that triggered it."""
    if not isinstance(ev, dict):
        return ""
    names: List[str] = []
    for k in ("workspaceExamples", "unassignedNames", "examples",
              "uncoveredProductionWorkspaces", "workspacesWithAllLayersInside"):
        for n in ev.get(k) or []:
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
    for k in ("monoliths", "models", "items"):
        for it in ev.get(k) or []:
            if isinstance(it, dict) and it.get("name"):
                names.append(str(it["name"]))
    cnt = None
    for k, v in ev.items():
        if isinstance(v, int) and "count" in k.lower():
            cnt = v
            break
    head = names[:3]
    if not head and cnt is None:
        return ""
    total = cnt if cnt is not None else len(names)
    base = ", ".join(head)
    extra = total - len(head)
    if extra > 0:
        base = f"{base} +{extra} more" if base else f"{total} item(s)"
    return f"{total} affected: {base}" if base else f"{total} affected"


def build_gold(
    findings: List[Dict[str, Any]],
    raw_dir: str | Path,
    *,
    run_id: str,
    run_timestamp: str,
    client_name: str = "",
    engagement_name: str = "",
    reviewer_name: str = "",
    deployed_version: str | None = None,
    repo_url: str = "",
    branch: str = "main",
    check_remote: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Build every gold table as a list of column-aligned row dicts."""
    raw = Path(raw_dir)
    meta = {
        "run_id": run_id,
        "run_timestamp": run_timestamp,
        "client_name": client_name,
        "engagement_name": engagement_name,
        "reviewer_name": reviewer_name,
    }
    tables: Dict[str, List[Dict[str, Any]]] = {name: [] for name in GOLD_TABLES_BY_NAME}
    rule_desc = _rule_descriptions()

    # ---- gold_findings -------------------------------------------------
    for f in findings:
        severity = (f.get("severity") or "medium").lower()
        status = (f.get("status") or "info").lower()
        rid = f.get("rule_id") or ""
        tables["gold_findings"].append(_coerce_row("gold_findings", {
            **meta,
            "rule_id": rid,
            "dimension": f.get("dimension"),
            "severity": severity,
            "severity_rank": SEVERITY_RANK.get(severity, 2),
            "status": status,
            "is_fail": 1 if status == "fail" else 0,
            "title": f.get("title"),
            "recommendation": f.get("recommendation"),
            "rule_description": rule_desc.get(rid, ""),
            "microsoft_learn_url": f.get("microsoft_learn_url"),
            "affected": _affected_summary(f.get("evidence") or {}),
            "evidence_json": json.dumps(f.get("evidence") or {}, ensure_ascii=False),
        }))

    # ---- gold_run_summary + gold_dimension_summary ---------------------
    def _bucket(items: List[Dict[str, Any]]) -> Dict[str, int]:
        b = {"pass": 0, "fail": 0, "info": 0,
             "critical_fail": 0, "high_fail": 0, "medium_fail": 0, "low_fail": 0}
        for it in items:
            st = (it.get("status") or "info").lower()
            b[st if st in ("pass", "fail", "info") else "info"] += 1
            if st == "fail":
                sev = (it.get("severity") or "medium").lower()
                key = f"{sev}_fail"
                if key in b:
                    b[key] += 1
        return b

    rb = _bucket(findings)
    tables["gold_run_summary"].append(_coerce_row("gold_run_summary", {
        **meta,
        "total_findings": len(findings),
        "pass_count": rb["pass"], "fail_count": rb["fail"], "info_count": rb["info"],
        "critical_fail": rb["critical_fail"], "high_fail": rb["high_fail"],
        "medium_fail": rb["medium_fail"], "low_fail": rb["low_fail"],
        "score": _score(rb["pass"], rb["fail"]),
        "is_latest": True,
    }))

    # ---- gold_release (deployed version vs latest published) -----------
    try:
        release = build_release_record(
            deployed_version, repo_url, branch, check_remote=check_remote
        )
    except Exception:
        release = build_release_record(
            deployed_version, repo_url, branch, check_remote=False
        )
    tables["gold_release"].append(_coerce_row("gold_release", {**meta, **release}))

    for dim in DIMENSIONS:
        items = [f for f in findings if (f.get("dimension") or "") == dim]
        if not items:
            continue
        db = _bucket(items)
        fails = [i for i in items if (i.get("status") or "").lower() == "fail"]
        worst = "none"
        if fails:
            worst = max(fails, key=lambda i: SEVERITY_RANK.get((i.get("severity") or "medium").lower(), 2))
            worst = (worst.get("severity") or "medium").lower()
        tables["gold_dimension_summary"].append(_coerce_row("gold_dimension_summary", {
            **meta,
            "dimension": dim,
            "total": len(items),
            "pass_count": db["pass"], "fail_count": db["fail"], "info_count": db["info"],
            "score": _score(db["pass"], db["fail"]),
            "worst_severity": worst,
        }))

    # ---- gold_capacities ----------------------------------------------
    cap = _load(raw, "capacity_metrics.json") or {}
    for c in cap.get("capacities") or []:
        sku = c.get("sku")
        tables["gold_capacities"].append(_coerce_row("gold_capacities", {
            **meta,
            "capacity_id": c.get("id"),
            "capacity_name": c.get("displayName") or c.get("name"),
            "sku": sku,
            "kind": _capacity_kind(sku),
            "is_dedicated": _is_dedicated_capacity(sku),
            "state": c.get("state"),
            "region": c.get("region"),
        }))

    # ---- gold_workspaces ----------------------------------------------
    wsi = _load(raw, "workspace_inventory.json") or {}
    scanner = _load(raw, "scanner.json") or {}
    # Drop personal ("My workspace") workspaces from every source list so they
    # never reach any gold table. Track their names so model/notebook/finding
    # rows that reference them by name are filtered out too.
    _personal_names = {
        (w.get("name") or "").strip().lower()
        for src in (wsi.get("workspaces") or [], scanner.get("workspaces") or [])
        for w in src if _is_personal_workspace(w)
    }
    _personal_names.discard("")
    wsi["workspaces"] = [w for w in (wsi.get("workspaces") or []) if not _is_personal_workspace(w)]
    scanner["workspaces"] = [w for w in (scanner.get("workspaces") or []) if not _is_personal_workspace(w)]
    item_counts: Dict[str, int] = {}
    for ws in scanner.get("workspaces") or []:
        wid = ws.get("id") or ws.get("objectId")
        if wid:
            item_counts[str(wid).lower()] = len(ws.get("items") or []) or sum(
                len(ws.get(k) or []) for k in ("reports", "datasets", "dashboards", "dataflows", "lakehouses")
            )
    # Most-recent activity-log event per workspace (ISO-8601 UTC strings sort
    # chronologically), so we can flag workspaces with no activity in the window.
    _acts = _load(raw, "activity_logs.json") or {}
    last_activity_by_wid: Dict[str, str] = {}
    for ev in _acts.get("events") or []:
        _wl = str(ev.get("WorkspaceId") or "").lower()
        _ct = ev.get("CreationTime")
        if _wl and _ct and _ct > last_activity_by_wid.get(_wl, ""):
            last_activity_by_wid[_wl] = _ct
    ws_name_by_id: Dict[str, str] = {}
    for ws in wsi.get("workspaces") or []:
        wid = ws.get("id")
        _widl = str(wid).lower() if wid else ""
        _admins = sum(
            1 for u in (ws.get("users") or [])
            if (u.get("groupUserAccessRight") or "") == "Admin"
        )
        tables["gold_workspaces"].append(_coerce_row("gold_workspaces", {
            **meta,
            "workspace_id": wid,
            "workspace_name": ws.get("name"),
            "capacity_id": ws.get("capacityId"),
            "on_capacity": ws.get("isOnDedicatedCapacity"),
            "item_count": item_counts.get(_widl, 0),
            "admin_count": _admins,
            "last_activity": last_activity_by_wid.get(_widl),
            "is_inactive": bool(_widl) and _widl not in last_activity_by_wid,
            "description": ws.get("description"),
        }))
        if wid:
            ws_name_by_id[str(wid).lower()] = ws.get("name")

    # ---- gold_semantic_models + gold_model_tables + gold_model_columns -
    sm = _load(raw, "semantic_models.json") or {}
    vp = _load(raw, "vertipaq_stats.json") or {}

    # Index VertiPaq results by model_id (preferred) and lowercased name.
    vp_by_id: Dict[str, Dict[str, Any]] = {}
    vp_by_name: Dict[str, Dict[str, Any]] = {}
    if vp.get("available"):
        for m in vp.get("models") or []:
            mid = str(m.get("model_id") or "")
            mname = str(m.get("model_name") or "").strip().lower()
            if mid:
                vp_by_id[mid] = m
            if mname:
                vp_by_name.setdefault(mname, m)

    for d in sm.get("datasets") or []:
        model_id = d.get("id")
        model_name = d.get("name")
        workspace_name = d.get("workspaceName")
        ws_id = d.get("workspaceId") or d.get("groupId")
        if not workspace_name and ws_id:
            workspace_name = ws_name_by_id.get(str(ws_id).lower())
        if (workspace_name or "").strip().lower() in _personal_names:
            continue  # skip models that live in a personal workspace
        vpm = vp_by_id.get(str(model_id or "")) or vp_by_name.get(str(model_name or "").strip().lower())

        vp_tables = (vpm or {}).get("tables") or []
        vp_columns = (vpm or {}).get("columns") or []
        vp_model = (vpm or {}).get("model") or []

        # Total model size: prefer the Model frame's own figure, else sum tables.
        total_size = 0
        if vp_model:
            total_size = _vp_int(vp_model[0], "total_size", "model_size", "size")
        if not total_size and vp_tables:
            total_size = sum(_vp_int(t, "total_size", "size") for t in vp_tables)
        calc_count = sum(1 for c in vp_columns if _vp_is_calc(c))

        tables["gold_semantic_models"].append(_coerce_row("gold_semantic_models", {
            **meta,
            "model_id": model_id,
            "model_name": model_name,
            "workspace_name": workspace_name,
            "storage_mode": d.get("targetStorageMode"),
            "is_refreshable": d.get("isRefreshable"),
            "total_size": total_size,
            "table_count": len(vp_tables),
            "column_count": len(vp_columns),
            "calc_column_count": calc_count,
            "max_refresh_seconds": 0.0,
        }))

        # Per-table footprint.
        for t in vp_tables:
            tname = _vp_str(t, "table_name", "table", "name")
            if not tname:
                continue
            tables["gold_model_tables"].append(_coerce_row("gold_model_tables", {
                **meta,
                "model_id": model_id,
                "model_name": model_name,
                "workspace_name": workspace_name,
                "table_name": tname,
                "row_count": _vp_int(t, "row_count", "rows", "cardinality"),
                "total_size": _vp_int(t, "total_size", "size"),
                "data_size": _vp_int(t, "data_size"),
                "dictionary_size": _vp_int(t, "dictionary_size", "dict_size"),
                "hierarchy_size": _vp_int(t, "hierarchy_size", "hier_size", "user_hierarchies_size"),
                "column_count": _vp_int(t, "columns", "column_count", "columns_count"),
                "pct_db": _vp_float(t, "pct_db", "pct_database"),
            }))

        # Per-column statistics.
        for c in vp_columns:
            cname = _vp_str(c, "column_name", "column", "name")
            if not cname:
                continue
            tname = _vp_str(c, "table_name", "table")
            tables["gold_model_columns"].append(_coerce_row("gold_model_columns", {
                **meta,
                "model_id": model_id,
                "model_name": model_name,
                "workspace_name": workspace_name,
                "table_name": tname,
                "column_name": cname,
                "qualified_column": f"{cname} ({tname})" if tname else cname,
                "data_type": _vp_str(c, "data_type", "type"),
                "encoding": _vp_str(c, "encoding", "column_encoding", "encoding_hint"),
                "cardinality": _vp_int(c, "cardinality", "column_cardinality"),
                "total_size": _vp_int(c, "total_size", "size"),
                "data_size": _vp_int(c, "data_size"),
                "dictionary_size": _vp_int(c, "dictionary_size", "dict_size"),
                "hierarchy_size": _vp_int(c, "hierarchy_size", "hier_size"),
                "pct_table": _vp_float(c, "pct_table"),
                "pct_db": _vp_float(c, "pct_db", "pct_database"),
                "is_calculated": _vp_is_calc(c),
            }))

        # Per-partition footprint.
        for p in (vpm or {}).get("partitions") or []:
            pname = _vp_str(p, "partition_name", "partition", "name")
            tname = _vp_str(p, "table_name", "table")
            if not pname and not tname:
                continue
            tables["gold_model_partitions"].append(_coerce_row("gold_model_partitions", {
                **meta,
                "model_id": model_id,
                "model_name": model_name,
                "workspace_name": workspace_name,
                "table_name": tname,
                "partition_name": pname,
                "mode": _vp_str(p, "mode", "partition_mode"),
                "record_count": _vp_int(p, "record_count", "records", "row_count", "rows", "cardinality"),
                "segment_count": _vp_int(p, "segment_count", "segments"),
                "records_per_segment": _vp_float(p, "records_per_segment", "rows_per_segment"),
            }))

        # Relationships.
        for r in (vpm or {}).get("relationships") or []:
            frm = _vp_str(r, "from_object", "from", "from_table")
            to = _vp_str(r, "to_object", "to", "to_table")
            if not frm and not to:
                continue
            tables["gold_model_relationships"].append(_coerce_row("gold_model_relationships", {
                **meta,
                "model_id": model_id,
                "model_name": model_name,
                "workspace_name": workspace_name,
                "from_object": frm,
                "to_object": to,
                "multiplicity": _vp_str(r, "multiplicity", "cardinality_type"),
                "used_size": _vp_int(r, "used_size", "relationship_size", "size"),
                "max_from_cardinality": _vp_int(r, "max_from_cardinality", "from_cardinality"),
                "max_to_cardinality": _vp_int(r, "max_to_cardinality", "to_cardinality"),
                "missing_rows": _vp_int(r, "missing_rows", "missing_keys"),
            }))

        # User hierarchies.
        for h in (vpm or {}).get("hierarchies") or []:
            hname = _vp_str(h, "hierarchy_name", "hierarchy", "name")
            if not hname:
                continue
            tables["gold_model_hierarchies"].append(_coerce_row("gold_model_hierarchies", {
                **meta,
                "model_id": model_id,
                "model_name": model_name,
                "workspace_name": workspace_name,
                "table_name": _vp_str(h, "table_name", "table"),
                "hierarchy_name": hname,
                "used_size": _vp_int(h, "used_size", "hierarchy_size", "size"),
            }))

    # ---- gold_notebook_smells -----------------------------------------
    nb_idx = _notebook_index(scanner)
    for f in findings:
        rid = f.get("rule_id") or ""
        if not rid.startswith("NBCODE") or (f.get("status") or "").lower() != "fail":
            continue
        desc = rule_desc.get(rid) or f.get("title") or ""
        examples = (f.get("evidence") or {}).get("examples") or []
        if not examples:
            tables["gold_notebook_smells"].append(_coerce_row("gold_notebook_smells", {
                **meta, "rule_id": rid, "rule_description": desc,
                "severity": (f.get("severity") or "medium").lower(),
                "dimension": f.get("dimension"), "notebook_name": "(multiple)",
                "workspace_name": "", "cells": "", "notebook_url": "",
            }))
            continue
        for ex in examples:
            cells = ex.get("cellIndexes") or ex.get("cells") or []
            nb_name = ex.get("notebook")
            ws_name = ex.get("workspace")
            if (ws_name or "").strip().lower() in _personal_names:
                continue  # skip notebooks in personal workspaces
            tables["gold_notebook_smells"].append(_coerce_row("gold_notebook_smells", {
                **meta, "rule_id": rid, "rule_description": desc,
                "severity": (f.get("severity") or "medium").lower(),
                "dimension": f.get("dimension"),
                "notebook_name": nb_name,
                "workspace_name": ws_name,
                "cells": ", ".join(str(x) for x in cells),
                "notebook_url": _notebook_url(nb_idx, ws_name, nb_name),
            }))

    # ---- estate graph: workspace risk, severity matrix, nodes + edges --
    # Capacity id -> friendly name (case-insensitive lookups throughout).
    cap_name_by_id: Dict[str, str] = {}
    for c in cap.get("capacities") or []:
        cid = str(c.get("id") or "").lower()
        if cid:
            cap_name_by_id[cid] = c.get("displayName") or c.get("name") or cid

    # Workspace inventory: owner + capacity + a name -> id reverse map.
    ws_meta: Dict[str, Dict[str, Any]] = {}
    name_to_wid: Dict[str, str] = {}
    for ws in wsi.get("workspaces") or []:
        wid = ws.get("id")
        if not wid:
            continue
        widl = str(wid).lower()
        owner = ""
        for u in ws.get("users") or []:
            if (u.get("groupUserAccessRight") or "") == "Admin":
                owner = u.get("displayName") or u.get("emailAddress") or ""
                if (u.get("principalType") or "") == "User":
                    break  # prefer a named human admin over a group
        cap_id = ws.get("capacityId") or ""
        ws_meta[widl] = {
            "id": wid,
            "name": ws.get("name") or "",
            "capacity_id": cap_id,
            "capacity_name": cap_name_by_id.get(str(cap_id).lower(), ""),
            "owner": owner,
        }
        if ws.get("name"):
            name_to_wid[ws["name"].strip().lower()] = widl

    # Scanner: per-workspace item mix.
    scan_ws: Dict[str, Dict[str, Any]] = {}
    for ws in scanner.get("workspaces") or []:
        wid = ws.get("id") or ws.get("objectId")
        if wid:
            scan_ws[str(wid).lower()] = ws

    # Attribute failing findings to workspaces by name.
    ws_issue: Dict[str, Dict[str, int]] = {}
    for f in findings:
        if (f.get("status") or "").lower() != "fail":
            continue
        sev = (f.get("severity") or "medium").lower()
        for nm in _finding_workspace_names(f.get("evidence") or {}):
            widl = name_to_wid.get(nm)
            if not widl:
                continue
            agg = ws_issue.setdefault(widl, {"issue": 0, "critical": 0, "high": 0})
            agg["issue"] += 1
            if sev == "critical":
                agg["critical"] += 1
            elif sev == "high":
                agg["high"] += 1

    # gold_workspace_risk: one roll-up row per workspace.
    for widl in sorted(set(ws_meta) | set(scan_ws)):
        m = ws_meta.get(widl, {})
        s = scan_ws.get(widl, {})
        sm_c = len(s.get("datasets") or [])
        rp_c = len(s.get("reports") or [])
        nb_c = len(s.get("Notebook") or [])
        pl_c = len(s.get("DataPipeline") or [])
        lh_c = len(s.get("Lakehouse") or [])
        item_c = (sm_c + rp_c + nb_c + pl_c + lh_c
                  + len(s.get("dashboards") or []) + len(s.get("dataflows") or [])
                  + len(s.get("datamarts") or []))
        agg = ws_issue.get(widl, {"issue": 0, "critical": 0, "high": 0})
        others = max(0, agg["issue"] - agg["critical"] - agg["high"])
        risk = min(100.0, 25 * agg["critical"] + 12 * agg["high"] + 4 * others)
        status = _status_for(risk, "risk")
        cap_id = m.get("capacity_id") or s.get("capacityId") or ""
        tables["gold_workspace_risk"].append(_coerce_row("gold_workspace_risk", {
            **meta,
            "workspace_id": m.get("id") or s.get("id") or s.get("objectId"),
            "workspace_name": m.get("name") or s.get("name") or "",
            "capacity_id": cap_id,
            "capacity_name": m.get("capacity_name") or cap_name_by_id.get(str(cap_id).lower(), ""),
            "owner": m.get("owner") or "",
            "item_count": item_c,
            "semantic_model_count": sm_c,
            "report_count": rp_c,
            "notebook_count": nb_c,
            "pipeline_count": pl_c,
            "lakehouse_count": lh_c,
            "issue_count": agg["issue"],
            "critical_count": agg["critical"],
            "high_count": agg["high"],
            "risk_score": round(risk, 1),
            "status": status,
            "status_rank": _STATUS_RANK[status],
        }))

    # gold_severity_matrix: dimension x severity grid of failing findings.
    sev_grid: Dict[tuple, int] = {}
    for f in findings:
        if (f.get("status") or "").lower() != "fail":
            continue
        dim = f.get("dimension") or "other"
        sev = (f.get("severity") or "medium").lower()
        sev_grid[(dim, sev)] = sev_grid.get((dim, sev), 0) + 1
    for (dim, sev), cnt in sorted(sev_grid.items()):
        status = _status_for(cnt, "issue")
        tables["gold_severity_matrix"].append(_coerce_row("gold_severity_matrix", {
            **meta,
            "dimension": dim,
            "severity": sev,
            "severity_rank": SEVERITY_RANK.get(sev, 2),
            "status": status,
            "issue_count": cnt,
            "weighted_risk": float(cnt * _SEV_WEIGHT.get(sev, 5)),
        }))

    # gold_bpa_violations: one row per individual BPA / health violation.
    bpa = _load(raw, "best_practices.json") or {}
    # BPA models/reports usually arrive without a workspace; backfill it from the
    # scanner inventory by item name so the violation table shows "where".
    _ws_by_item: Dict[str, str] = {}
    for ws in scanner.get("workspaces") or []:
        wn = (ws.get("name") or "").strip()
        if not wn:
            continue
        for it in (ws.get("items") or []):
            nm = (it.get("displayName") or it.get("name") or "").strip().lower()
            if nm:
                _ws_by_item.setdefault(nm, wn)
        for k in ("datasets", "reports", "dashboards", "lakehouses"):
            for it in (ws.get(k) or []):
                nm = (it.get("name") or it.get("displayName") or "").strip().lower()
                if nm:
                    _ws_by_item.setdefault(nm, wn)
    for m in (bpa.get("models") or []):
        mname = m.get("model_name") or "(model)"
        mws = (m.get("workspace_name") or m.get("workspace")
               or _ws_by_item.get(str(mname).strip().lower()) or "")
        for v in (m.get("model_bpa") or []):
            tables["gold_bpa_violations"].append(_coerce_row("gold_bpa_violations", {
                **meta, "object_type": "Model", "object_name": mname, "workspace_name": mws,
                "area": "Model BPA", "rule": v.get("rule") or v.get("name") or "rule",
                "severity": "high", "severity_rank": SEVERITY_RANK.get("high", 3),
            }))
        for v in (m.get("fallback") or []):
            tables["gold_bpa_violations"].append(_coerce_row("gold_bpa_violations", {
                **meta, "object_type": "Model", "object_name": mname, "workspace_name": mws,
                "area": "Direct Lake fallback", "rule": v.get("reason") or "fallback",
                "severity": "high", "severity_rank": SEVERITY_RANK.get("high", 3),
            }))
        for v in (m.get("delta") or []):
            tables["gold_bpa_violations"].append(_coerce_row("gold_bpa_violations", {
                **meta, "object_type": "Model", "object_name": mname, "workspace_name": mws,
                "area": "Delta health", "rule": str(v.get("table") or "table"),
                "severity": "medium", "severity_rank": SEVERITY_RANK.get("medium", 2),
            }))
        for v in (m.get("unused") or []):
            tables["gold_bpa_violations"].append(_coerce_row("gold_bpa_violations", {
                **meta, "object_type": "Model", "object_name": mname, "workspace_name": mws,
                "area": "Unused object", "rule": str(v.get("object") or "object"),
                "severity": "low", "severity_rank": SEVERITY_RANK.get("low", 1),
            }))
    for r in (bpa.get("reports") or []):
        rname = r.get("report_name") or "(report)"
        for v in (r.get("report_bpa") or []):
            tables["gold_bpa_violations"].append(_coerce_row("gold_bpa_violations", {
                **meta, "object_type": "Report", "object_name": rname,
                "workspace_name": (r.get("workspace_name") or r.get("workspace")
                                   or _ws_by_item.get(str(rname).strip().lower()) or ""),
                "area": "Report BPA", "rule": v.get("rule") or v.get("name") or "rule",
                "severity": "medium", "severity_rank": SEVERITY_RANK.get("medium", 2),
            }))
    for c in (bpa.get("capacities") or []):
        if c.get("needs_migration"):
            tables["gold_bpa_violations"].append(_coerce_row("gold_bpa_violations", {
                **meta, "object_type": "Capacity", "object_name": c.get("capacity") or "(capacity)",
                "workspace_name": "", "area": "Capacity migration",
                "rule": f"{c.get('sku') or 'P-SKU'} needs migration to Fabric SKU",
                "severity": "high", "severity_rank": SEVERITY_RANK.get("high", 3),
            }))

    # gold_graph_nodes + gold_graph_edges -------------------------------
    node_seen: set = set()
    edge_seen: set = set()

    def _add_node(node_id, node_type, node_name, *, workspace_id="", workspace_name="",
                  capacity_id="", capacity_name="", owner="", issue=0, critical=0,
                  risk=0.0, importance=1.0, status=None, kpi_label="", kpi_value=""):
        nid = str(node_id or f"{node_type}:{node_name}")
        key = f"{node_type}|{nid}".lower()
        if key in node_seen:
            return
        node_seen.add(key)
        st = status or _status_for(risk, "risk")
        tables["gold_graph_nodes"].append(_coerce_row("gold_graph_nodes", {
            **meta,
            "node_id": nid, "node_type": node_type, "node_name": node_name,
            "workspace_id": workspace_id, "workspace_name": workspace_name,
            "capacity_id": capacity_id, "capacity_name": capacity_name, "owner": owner,
            "status": st, "status_rank": _STATUS_RANK.get(st, -1),
            "issue_count": issue, "critical_count": critical,
            "risk_score": round(float(risk), 1), "importance": float(importance),
            "kpi_label": kpi_label, "kpi_value": str(kpi_value),
        }))

    def _add_edge(source_id, source_name, source_type, target_id, target_name,
                  target_type, rel):
        sid, tid = str(source_id or source_name), str(target_id or target_name)
        eid = f"{sid}->{tid}:{rel}"
        if eid in edge_seen:
            return
        edge_seen.add(eid)
        # Curated data-lineage chain for the Estate Map Sankey:
        #   Capacity --hosts--> Workspace --contains--> SemanticModel --feeds--> Report.
        # Structural 'contains' edges to notebooks/pipelines/lakehouses and the
        # owner 'administers' edges are excluded so the flow stays readable; they
        # remain available in the node-inventory bar and the edge table.
        is_lineage = (
            rel == "hosts"
            or rel == "feeds"
            or (rel == "contains" and target_type == "SemanticModel")
        )
        tables["gold_graph_edges"].append(_coerce_row("gold_graph_edges", {
            **meta, "edge_id": eid,
            "source_id": sid, "source_name": source_name, "source_type": source_type,
            "target_id": tid, "target_name": target_name, "target_type": target_type,
            "relationship": rel, "is_lineage": is_lineage,
        }))

    # Capacity nodes (sized by how many workspaces they host).
    ws_per_cap: Dict[str, int] = {}
    for r in tables["gold_workspace_risk"]:
        cid = str(r["capacity_id"]).lower()
        if cid:
            ws_per_cap[cid] = ws_per_cap.get(cid, 0) + 1
    for c in cap.get("capacities") or []:
        cid = c.get("id")
        cname = c.get("displayName") or c.get("name") or cid
        _add_node(cid, "Capacity", cname, capacity_id=cid, capacity_name=cname,
                  importance=ws_per_cap.get(str(cid).lower(), 1) + 1, status="blue",
                  kpi_label="SKU", kpi_value=c.get("sku") or "")

    # Workspace nodes + their child items + the relationship edges.
    for r in tables["gold_workspace_risk"]:
        widl = str(r["workspace_id"]).lower()
        s = scan_ws.get(widl, {})
        _add_node(r["workspace_id"], "Workspace", r["workspace_name"],
                  workspace_id=r["workspace_id"], workspace_name=r["workspace_name"],
                  capacity_id=r["capacity_id"], capacity_name=r["capacity_name"],
                  owner=r["owner"], issue=r["issue_count"], critical=r["critical_count"],
                  risk=r["risk_score"], importance=r["item_count"] + 1, status=r["status"],
                  kpi_label="Issues", kpi_value=r["issue_count"])
        if r["capacity_id"]:
            _add_edge(r["capacity_id"], r["capacity_name"], "Capacity",
                      r["workspace_id"], r["workspace_name"], "Workspace", "hosts")
        if r["owner"]:
            oid = f"owner:{r['owner']}".lower()
            _add_node(oid, "Owner", r["owner"], status="blue", importance=2.0,
                      kpi_label="Role", kpi_value="Admin")
            _add_edge(oid, r["owner"], "Owner",
                      r["workspace_id"], r["workspace_name"], "Workspace", "administers")
        ds_by_id: Dict[str, str] = {}
        for ds in s.get("datasets") or []:
            did, dname = ds.get("id"), ds.get("name")
            if not did:
                continue
            dname = dname or did
            ds_by_id[str(did)] = dname
            _add_node(did, "SemanticModel", dname, workspace_id=r["workspace_id"],
                      workspace_name=r["workspace_name"], status="grey", importance=2.0,
                      kpi_label="Workspace", kpi_value=r["workspace_name"])
            _add_edge(r["workspace_id"], r["workspace_name"], "Workspace",
                      did, dname, "SemanticModel", "contains")
        for rp in s.get("reports") or []:
            rid, rname = rp.get("id"), rp.get("name")
            if not rid:
                continue
            rname = rname or rid
            _add_node(rid, "Report", rname, workspace_id=r["workspace_id"],
                      workspace_name=r["workspace_name"], status="grey", importance=1.5,
                      kpi_label="Workspace", kpi_value=r["workspace_name"])
            _add_edge(r["workspace_id"], r["workspace_name"], "Workspace",
                      rid, rname, "Report", "contains")
            dsid = rp.get("datasetId")
            if dsid and str(dsid) in ds_by_id:
                _add_edge(dsid, ds_by_id[str(dsid)], "SemanticModel",
                          rid, rname, "Report", "feeds")
        for nb in s.get("Notebook") or []:
            nid, nname = nb.get("id"), nb.get("name")
            if not nid:
                continue
            nname = nname or nid
            _add_node(nid, "Notebook", nname, workspace_id=r["workspace_id"],
                      workspace_name=r["workspace_name"], status="grey", importance=1.5,
                      kpi_label="Workspace", kpi_value=r["workspace_name"])
            _add_edge(r["workspace_id"], r["workspace_name"], "Workspace",
                      nid, nname, "Notebook", "contains")
        for pl in s.get("DataPipeline") or []:
            pid, pname = pl.get("id"), pl.get("name")
            if not pid:
                continue
            pname = pname or pid
            _add_node(pid, "Pipeline", pname, workspace_id=r["workspace_id"],
                      workspace_name=r["workspace_name"], status="grey", importance=1.5,
                      kpi_label="Workspace", kpi_value=r["workspace_name"])
            _add_edge(r["workspace_id"], r["workspace_name"], "Workspace",
                      pid, pname, "Pipeline", "contains")
        for lh in s.get("Lakehouse") or []:
            lid, lname = lh.get("id"), lh.get("name")
            if not lid:
                continue
            lname = lname or lid
            _add_node(lid, "Lakehouse", lname, workspace_id=r["workspace_id"],
                      workspace_name=r["workspace_name"], status="grey", importance=1.5,
                      kpi_label="Workspace", kpi_value=r["workspace_name"])
            _add_edge(r["workspace_id"], r["workspace_name"], "Workspace",
                      lid, lname, "Lakehouse", "contains")

    return tables


def build_gold_from_dir(
    out_dir: str | Path,
    *,
    run_id: str,
    run_timestamp: str,
    client_name: str = "",
    engagement_name: str = "",
    reviewer_name: str = "",
    deployed_version: str | None = None,
    repo_url: str = "",
    branch: str = "main",
    check_remote: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Convenience wrapper: read ``findings.json`` + ``raw/`` from a run folder."""
    out = Path(out_dir)
    findings = json.loads((out / "findings.json").read_text(encoding="utf-8-sig"))
    return build_gold(
        findings, out / "raw",
        run_id=run_id, run_timestamp=run_timestamp,
        client_name=client_name, engagement_name=engagement_name, reviewer_name=reviewer_name,
        deployed_version=deployed_version, repo_url=repo_url, branch=branch,
        check_remote=check_remote,
    )

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Build Mermaid diagrams + architecture tables describing the client's Fabric estate.

Each builder is independent: it inspects ``output/raw/`` for the JSON file it
needs and either returns a markdown block or a short note explaining which
collector still has to run. Builders that don't apply yet are skipped gracefully
so the report always renders.

Diagram design rules (so the PDF stays readable):
  * Mermaid labels never contain HTML (the renderer is configured with
    ``htmlLabels: false``); use plain text + ``\\n`` for line breaks.
  * Each mermaid block must fit on one Letter page - cap nodes at ~12 per block
    and emit multiple smaller blocks instead of one giant one.
  * Prefer ``flowchart TD`` (top-down) for short lists, ``LR`` only for
    capacity --> workspace fan-outs with <= 8 children.

DATA SAFETY: Diagrams are built strictly from the metadata files in output/raw/.
No new API calls happen here and no customer data is read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

# --- Helpers ---------------------------------------------------------------

_MERMAID_ID_FORBIDDEN = str.maketrans({c: "_" for c in " -.:/\\()[]{}<>@#?!\"'&"})


def _node_id(prefix: str, name: str) -> str:
    return f"{prefix}_{name.translate(_MERMAID_ID_FORBIDDEN)}"[:60]


def _label(text: str, max_len: int = 38) -> str:
    text = (text or "?").replace("\"", "'").replace("[", "(").replace("]", ")")
    if len(text) > max_len:
        text = text[: max_len - 1] + "\u2026"
    return text


def _load(raw_dir: Path, name: str) -> Any | None:
    p = raw_dir / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _skip(title: str, missing: str, hint: str) -> str:
    return (
        f"### {title}\n\n"
        f"> _Diagram unavailable - `output/raw/{missing}` not produced yet. "
        f"{hint}_\n"
    )


def _chunks(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _is_personal_workspace(ws: Dict[str, Any]) -> bool:
    """A 'My workspace' / personal workspace - one per user, not part of the architecture.

    Fabric exposes one of these per signed-in user (named `PersonalWorkspace <UPN>`
    or simply `My workspace`). They share a synthetic 'Unassigned' capacity bucket
    and should not be counted in the architecture topology.
    """
    if (ws.get("type") or "").lower() in ("personalgroup", "personal"):
        return True
    name = (ws.get("name") or ws.get("workspaceName") or "").strip().lower()
    if name == "my workspace" or name.startswith("personalworkspace "):
        return True
    return False


def _filter_workspaces(workspaces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [w for w in workspaces if not _is_personal_workspace(w)]


# Fabric Scanner API returns Power BI legacy items under lowercase plural keys
# (`datasets`, `reports`, `dashboards`, `dataflows`) and Fabric-native items
# under PascalCase singular keys (`Lakehouse`, `Warehouse`, `Notebook`,
# `DataPipeline`, `KQLDatabase`, ...). Count both forms.
_ITEM_KIND_ALIASES: Dict[str, tuple] = {
    "lakehouses":  ("lakehouses", "Lakehouse"),
    "warehouses":  ("warehouses", "Warehouse"),
    "datasets":    ("datasets", "SemanticModel"),
    "reports":     ("reports", "Report"),
    "dataflows":   ("dataflows", "Dataflow", "Dataflow2"),
    "notebooks":   ("notebooks", "Notebook"),
    "pipelines":   ("pipelines", "DataPipeline"),
}


def _count_items(ws: Dict[str, Any], kind: str) -> int:
    return sum(len(ws.get(k) or []) for k in _ITEM_KIND_ALIASES.get(kind, (kind,)))


# --- Builders --------------------------------------------------------------

def _wrap_label(text: str, width: int = 28) -> str:
    """Wrap text into ~width-char lines for use inside a mermaid node label.

    Mermaid (with htmlLabels:false) renders a literal ``\n`` inside a
    double-quoted node label as a line break, so we hand-wrap on word
    boundaries to avoid the 38-char ellipsis truncation we used to apply.
    """
    text = (text or "?").replace("\"", "'").replace("[", "(").replace("]", ")")
    words = text.split()
    if not words:
        return text
    lines: List[str] = []
    current = words[0]
    for w in words[1:]:
        if len(current) + 1 + len(w) <= width:
            current = f"{current} {w}"
        else:
            lines.append(current)
            current = w
    lines.append(current)
    return "\\n".join(lines)


def _tenant_security_posture(raw_dir: Path, findings: List[Dict[str, Any]]) -> str:
    """Status flowchart of tenant-wide settings (one node per rule).

    Labels are hand-wrapped (see :func:`_wrap_label`) so the full check title
    is visible without ellipsis truncation.
    """
    payload = _load(raw_dir, "tenant_settings.json")
    if payload is None:
        return _skip(
            "Tenant security posture",
            "tenant_settings.json",
            "Run `python -m collectors.tenant_settings`.",
        )

    ts_findings = [f for f in findings if f.get("dimension") == "tenant_settings"]
    if not ts_findings:
        return ""

    lines = [
        "### Tenant security posture",
        "",
        "Status of the tenant-wide settings evaluated against the checklist.",
        "",
        "```mermaid",
        "flowchart TD",
        "    classDef pass fill:#DFF6DD,stroke:#107C10,color:#0B6A0B;",
        "    classDef fail fill:#FDE7E9,stroke:#A4262C,color:#A4262C;",
        "    classDef info fill:#FFF4CE,stroke:#8A6914,color:#8A6914;",
        "    tenant([\"Fabric tenant\"])",
    ]
    order = {"fail": 0, "info": 1, "pass": 2}
    for f in sorted(ts_findings, key=lambda x: (order.get(x.get("status"), 3), x.get("rule_id", ""))):
        rid = f.get("rule_id", "unknown")
        nid = _node_id("s", rid)
        wrapped = _wrap_label(f.get("title", ""), width=28)
        label = f"{rid}\\n{wrapped}"
        lines.append(f"    tenant --> {nid}[\"{label}\"]")
        cls = {"pass": "pass", "fail": "fail"}.get(f.get("status"), "info")
        lines.append(f"    class {nid} {cls};")
    lines.append("```")
    return "\n".join(lines) + "\n"


def _capacity_workspace_topology(raw_dir: Path) -> str:
    """Capacity -> workspace topology, split into one mermaid block per capacity.

    Each block holds at most 8 workspaces so it fits on a single PDF page.
    Capacities with more workspaces get multiple consecutive blocks.
    """
    inv = _load(raw_dir, "workspace_inventory.json") or _load(raw_dir, "scanner.json")
    if inv is None:
        return _skip(
            "Capacity \u2192 Workspace topology",
            "workspace_inventory.json",
            "Implement and run `collectors.workspace_inventory` or `collectors.scanner_api`.",
        )

    # Cross-reference scanner.json: workspaces that returned items from the
    # admin scanner are "populated" - the rest are empty default experiences
    # (auto-created Data Engineering / Data Science / Data Analytics buckets,
    # untouched workspaces, etc.) and only add noise to the topology view.
    scan = _load(raw_dir, "scanner.json") or {}
    populated_ids = {
        (w.get("id") or "").lower()
        for w in (scan.get("workspaces") or [])
        if any(_count_items(w, k) for k in
               ("lakehouses", "warehouses", "datasets", "reports",
                "dataflows", "notebooks", "pipelines"))
    }

    # capacity_metrics.json carries the Azure friendly name (displayName) for
    # each capacity GUID. Build an id->name map so the topology shows the real
    # capacity name instead of the bare GUID returned by the scanner workspace
    # record (which only has capacityId).
    cap_meta = _load(raw_dir, "capacity_metrics.json") or {}
    cap_name_by_id: Dict[str, str] = {}
    for c in (cap_meta.get("capacities") or []):
        cid = (c.get("id") or "").lower()
        cname = c.get("displayName") or c.get("name")
        if cid and cname:
            cap_name_by_id[cid] = cname

    workspaces = inv.get("workspaces") or inv.get("value") or []
    total = len(workspaces)
    workspaces = _filter_workspaces(workspaces)
    personal_excluded = total - len(workspaces)

    if populated_ids:
        before_empty = len(workspaces)
        workspaces = [w for w in workspaces if (w.get("id") or "").lower() in populated_ids]
        empty_excluded = before_empty - len(workspaces)
    else:
        empty_excluded = 0

    if not workspaces:
        return ""

    by_capacity: Dict[str, List[Dict[str, Any]]] = {}
    for ws in workspaces:
        cap_id = (ws.get("capacityId") or "").lower()
        cap = (cap_name_by_id.get(cap_id)
               or ws.get("capacityName")
               or ws.get("capacityId")
               or "Unassigned (shared)")
        by_capacity.setdefault(str(cap), []).append(ws)

    parts = ["### Capacity \u2192 Workspace topology", ""]
    intro = (f"Across **{len(by_capacity)} capacity bucket(s)** and **{len(workspaces)} populated "
             f"workspace(s)**.")
    notes = []
    if personal_excluded:
        notes.append(f"{personal_excluded} personal / `My workspace` entries")
    if empty_excluded:
        notes.append(f"{empty_excluded} empty workspace(s) (no Fabric items per the admin scanner)")
    if notes:
        intro += " Excluded from the architecture view: " + "; ".join(notes) + "."
    parts.append(intro)
    parts.append("")

    # Summary table first - easier to read than 25 nodes in one diagram.
    parts.append("| Capacity | Workspaces |")
    parts.append("|---|---:|")
    for cap, items in sorted(by_capacity.items(), key=lambda x: -len(x[1])):
        cap_label = _label(cap, max_len=44)
        parts.append(f"| `{cap_label}` | {len(items)} |")
    parts.append("")

    # Then one compact diagram per capacity, chunked to 8 workspaces per block.
    for cap, items in sorted(by_capacity.items(), key=lambda x: -len(x[1])):
        cap_label = _label(cap, max_len=36)
        items_sorted = sorted(items, key=lambda w: (w.get("name") or "").lower())
        chunks = _chunks(items_sorted, 8)
        for ci, chunk in enumerate(chunks):
            suffix = f" (part {ci + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            parts.append(f"**Capacity `{cap_label}`{suffix} - {len(items_sorted)} workspace(s)**")
            parts.append("")
            parts.append("```mermaid")
            parts.append("flowchart LR")
            cap_id = _node_id("cap", cap + f"_{ci}")
            parts.append(f"    {cap_id}[(\"{cap_label}\")]")
            for ws in chunk:
                wsid = _node_id("ws", (ws.get("id") or ws.get("name") or "?") + f"_{ci}")
                parts.append(f"    {wsid}[\"{_label(ws.get('name', '?'))}\"]")
                parts.append(f"    {cap_id} --> {wsid}")
            parts.append("```")
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _workspace_items_table(raw_dir: Path) -> str:
    """Inventory table: items per workspace (much more report-friendly than a mermaid grid)."""
    scan = _load(raw_dir, "scanner.json")
    if scan is None:
        return _skip(
            "Workspace items inventory",
            "scanner.json",
            "Implement and run `collectors.scanner_api`.",
        )

    workspaces = scan.get("workspaces") or []
    workspaces = _filter_workspaces(workspaces)
    if not workspaces:
        return ""

    item_kinds = ("lakehouses", "warehouses", "datasets", "reports", "dataflows", "notebooks", "pipelines")
    headers = ["Workspace", "Lakehouses", "Warehouses", "Datasets", "Reports", "Dataflows", "Notebooks", "Pipelines"]

    parts = ["### Workspace items inventory", "",
             "Counts of items per workspace returned by the Fabric Scanner API.", ""]
    parts.append("| " + " | ".join(headers) + " |")
    parts.append("|" + "|".join(["---"] + ["---:"] * (len(headers) - 1)) + "|")
    for ws in sorted(workspaces, key=lambda w: (w.get("name") or "").lower()):
        row = [_label(ws.get("name", "?"), max_len=40)]
        for kind in item_kinds:
            row.append(str(_count_items(ws, kind)))
        parts.append("| " + " | ".join(row) + " |")
    return "\n".join(parts) + "\n"


_BLOCKER_LABEL = {
    "no_lakehouse_binding": "no lakehouse binding",
    "m_partitions": "M partitions",
    "calculated_columns": "calc columns",
    "calculated_tables": "calc tables",
    "unsupported_types": "unsupported types",
    "composite_mode": "composite mode",
}


def _semantic_model_storage_modes(raw_dir: Path) -> str:
    """Per-model storage mode + DirectLake feasibility audit.

    Renders only when both ``semantic_models.json`` and
    ``semantic_model_definitions.json`` are present, so the section appears
    automatically once the new collector/analyzer have run.
    """
    catalog = _load(raw_dir, "semantic_models.json")
    defs_payload = _load(raw_dir, "semantic_model_definitions.json")
    if catalog is None or defs_payload is None:
        return ""

    datasets = catalog.get("datasets") or []
    if not datasets:
        return ""

    # Import lazily to avoid a top-level dependency cycle on the analyzers
    # package when diagrams.py is used from a smoke test that doesn't have
    # the analyzer environment loaded.
    from analyzers.semantic_model_storage_review import _is_directlake, _scan_model

    defs_by_id: Dict[str, Dict[str, Any]] = {
        m.get("id"): m for m in (defs_payload.get("models") or []) if m.get("id")
    }

    rows: List[List[str]] = []
    n_dl = n_candidate = n_blocked = n_import = 0
    for ds in sorted(
        datasets,
        key=lambda d: ((d.get("workspaceName") or "").lower(), (d.get("name") or "").lower()),
    ):
        storage_mode = (ds.get("targetStorageMode") or "").strip() or "?"
        ws = _label(ds.get("workspaceName") or "?", max_len=28)
        name = _label(ds.get("name") or "?", max_len=40)

        if _is_directlake(storage_mode):
            n_dl += 1
            dl_status = "Already DirectLake"
            detail = "&mdash;"
        else:
            n_import += 1
            model_def = defs_by_id.get(ds.get("id"))
            if not model_def:
                dl_status = "Not audited"
                detail = "definition not collected"
            elif model_def.get("error"):
                dl_status = "Audit failed"
                detail = str(model_def.get("error"))[:80]
            else:
                audit = _scan_model(model_def)
                if audit["blockers"]:
                    n_blocked += 1
                    dl_status = "Blocked"
                    labels = [_BLOCKER_LABEL.get(b["kind"], b["kind"]) for b in audit["blockers"]]
                    detail = ", ".join(labels)
                else:
                    n_candidate += 1
                    dl_status = "Candidate"
                    detail = "no structural blockers"
        rows.append([ws, name, storage_mode, dl_status, detail])

    parts = [
        "### Semantic-model storage mode & DirectLake feasibility",
        "",
        (
            f"**{len(datasets)} semantic model(s)** in scope: "
            f"**{n_dl}** already DirectLake, **{n_import}** Import "
            f"(**{n_candidate}** DirectLake candidate(s), **{n_blocked}** blocked by refactor needs). "
            "Per-model blockers come from the PERF-012 TMDL audit; **Candidate** means "
            "no structural DirectLake blockers were detected in the model definition."
        ),
        "",
        "| Workspace | Model | Storage mode | DirectLake | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in rows[:60]:
        parts.append("| " + " | ".join(r) + " |")
    if len(rows) > 60:
        parts.append(f"| _\u2026 {len(rows) - 60} more_ |  |  |  |  |")
    return "\n".join(parts) + "\n"


def _git_integration_summary(raw_dir: Path) -> str:
    """Table summary of Git source-control coverage."""
    git = _load(raw_dir, "git_integration.json")
    if git is None:
        return _skip(
            "Git source-control coverage",
            "git_integration.json",
            "Implement and run `collectors.git_integration`.",
        )
    items = git.get("workspaces") or git.get("value") or []
    if not items:
        return ""

    connected = [w for w in items if (w.get("gitProviderDetails") or w.get("repository") or {}).get("repositoryName")]
    parts = ["### Git source-control coverage", "",
             f"**{len(connected)} of {len(items)} workspace(s)** are connected to Git source control."]

    if connected:
        parts += ["", "| Workspace | Repository | Branch |", "|---|---|---|"]
        for ws in connected[:40]:
            repo = ws.get("gitProviderDetails") or ws.get("repository") or {}
            name = _label(ws.get("workspaceName") or ws.get("name") or "?", max_len=40)
            repo_label = _label(
                f"{repo.get('organizationName','?')}/{repo.get('repositoryName','?')}", max_len=44
            )
            branch = _label(repo.get("branchName", "?"), max_len=24)
            parts.append(f"| {name} | `{repo_label}` | `{branch}` |")

    if len(connected) < len(items):
        unconnected = [w for w in items if w not in connected]
        parts += ["", f"**Workspaces without Git** ({len(unconnected)}):"]
        names = ", ".join(_label(w.get("workspaceName") or w.get("name") or "?", 32) for w in unconnected[:20])
        parts.append(names + (" \u2026" if len(unconnected) > 20 else ""))

    return "\n".join(parts) + "\n"


# --- Public API ------------------------------------------------------------

BUILDERS = [
    _tenant_security_posture,
    _capacity_workspace_topology,
    _workspace_items_table,
    _semantic_model_storage_modes,
    _git_integration_summary,
]


def build_diagrams(raw_dir: Path, findings: List[Dict[str, Any]]) -> str:
    """Build the full diagrams + architecture-overview section."""
    raw_dir = Path(raw_dir)
    parts: List[str] = ["# Architecture Overview", "",
                        "Visual and tabular view of the Fabric estate, built strictly from metadata "
                        "captured by the collectors. No customer data is read.", ""]
    rendered = 0
    for builder in BUILDERS:
        try:
            block = builder(raw_dir, findings) if builder.__name__ == "_tenant_security_posture" else builder(raw_dir)
        except Exception as exc:  # never let a diagram bug crash the report
            block = f"### {builder.__name__}\n\n> _Skipped - builder error: {exc}_\n"
        block = block.strip()
        if block:
            parts.append(block)
            parts.append("")
            rendered += 1
    if rendered == 0:
        parts.append("> _No diagrams to render yet - implement more collectors._")
    return "\n".join(parts).rstrip() + "\n"

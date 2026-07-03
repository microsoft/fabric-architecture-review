# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Heuristic notebook source-code review.

Reads decoded notebooks from ``output/raw/pipeline_definitions.json`` and
flags common Fabric / Spark anti-patterns by regex-scanning each code cell.

Rule coverage (see config/review-checklist.yaml):
  NBCODE-001  Hard-coded secrets / tokens / keys
  NBCODE-002  Inline %pip / !pip installs
  NBCODE-003  .collect() / .toPandas() / display() on large DataFrames
  NBCODE-004  Databricks-only APIs (dbutils, /dbfs, databricks mlflow)
  NBCODE-005  Hard-coded abfss:// paths or workspace/lakehouse GUIDs
  NBCODE-006  Non-Delta writes (.format("parquet" | "csv"))

DATA SAFETY:
  - Reads notebook source already on disk (collected by pipeline_definitions).
  - Findings reference the notebook displayName + cell index ONLY. The cell
    body is never copied into the finding, so no source code or potential
    secret value leaves this file.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, write_findings

MAX_EXAMPLES = 25  # cap evidence list length to keep findings readable

# --- Pattern library ----------------------------------------------------------
# Each pattern is matched against a single code cell's joined source.
# Keep patterns conservative; analyzers report per-cell hits, not per-line.

_SECRET_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"AccountKey\s*=\s*[\"'][^\"'\s]{20,}", re.IGNORECASE),
    re.compile(r"SharedAccessSignature\s*=\s*[\"']?sv=", re.IGNORECASE),
    re.compile(r"\bsig=[A-Za-z0-9%\-_]{20,}", re.IGNORECASE),
    re.compile(r"\b(client_secret|clientSecret)\s*[:=]\s*[\"'][^\"'\s]{8,}", re.IGNORECASE),
    re.compile(r"\b(password|passwd|pwd)\s*[:=]\s*[\"'][^\"']{4,}[\"']", re.IGNORECASE),
    re.compile(r"\b(api[_-]?key|apikey)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
    # Long base64-looking strings assigned to suspicious names
    re.compile(r"\b(secret|token)\s*=\s*[\"'][A-Za-z0-9+/=]{32,}[\"']", re.IGNORECASE),
]

_PIP_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^\s*%pip\s+install\b", re.MULTILINE),
    re.compile(r"^\s*!pip\s+install\b", re.MULTILINE),
    re.compile(r"^\s*%conda\s+install\b", re.MULTILINE),
]

_COLLECT_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\.collect\s*\(\s*\)"),
    re.compile(r"\.toPandas\s*\(\s*\)"),
]

# Method calls that bound the result to a small/single-row DataFrame; if any
# of these appears upstream in the same chain, the .collect()/.toPandas()
# is considered safe.
_BOUNDED_GUARD_TOKENS = (
    ".limit(",
    ".head(",
    ".take(",
    ".first(",
    ".agg(",
    ".count(",
)

# Collapses a multi-line method chain to a single line: ")\n    .collect()"
# becomes ").collect()" so the guard check is line-local.
_CHAIN_JOIN_RE = re.compile(r"\)\s*\n\s*\.")

_DATABRICKS_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\bdbutils\."),
    re.compile(r"[\"']/dbfs/"),
    re.compile(r"databricks\.com/mlflow", re.IGNORECASE),
    re.compile(r"set_tracking_uri\s*\(\s*[\"']databricks", re.IGNORECASE),
]

_HARDCODED_PATH_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"abfss://[^\s\"']+@[^\s\"']+\.dfs\.fabric\.microsoft\.com", re.IGNORECASE),
    re.compile(r"abfss://[^\s\"']+@[^\s\"']+\.dfs\.core\.windows\.net", re.IGNORECASE),
    # GUID literal inline
    re.compile(r"[\"'][0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[\"']"),
]

_NON_DELTA_WRITE_PATTERNS: List[re.Pattern[str]] = [
    # .write[.mode(...)].format("parquet"|"csv"|...)
    re.compile(
        r"\.write(?:\s*\.[A-Za-z_]+\s*\([^)]*\))*\s*\.format\s*\(\s*[\"'](parquet|csv|json|orc|avro)[\"']",
        re.IGNORECASE,
    ),
    # .write.parquet( / .write.csv( / etc. shortcuts
    re.compile(r"\.write(?:\s*\.[A-Za-z_]+\s*\([^)]*\))*\s*\.(parquet|csv|json|orc|avro)\s*\(", re.IGNORECASE),
    # saveAsTable in non-delta format: .format("parquet").saveAsTable(... )
    re.compile(
        r"\.format\s*\(\s*[\"'](parquet|csv|json|orc|avro)[\"']\s*\)\s*\.(?:save|saveAsTable|insertInto)\s*\(",
        re.IGNORECASE,
    ),
]


def _ipynb_from_parts(parts: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for part in parts or []:
        path = (part.get("path") or "").lower()
        decoded = part.get("decoded")
        if isinstance(decoded, dict) and path.endswith(".ipynb"):
            return decoded
    return None


def _py_source_from_parts(parts: List[Dict[str, Any]]) -> str | None:
    """Decode a Fabric ``notebook-content.py`` part into raw source text.

    Fabric exports Python notebooks as a single .py file with synapse-style
    ``# CELL ********************`` separators and magic markers. The
    pipeline_definitions collector leaves these as base64 in ``payload``
    because it only auto-decodes JSON/ipynb.
    """
    for part in parts or []:
        path = (part.get("path") or "").lower()
        if not path.endswith(".py"):
            continue
        payload = part.get("payload")
        if not isinstance(payload, str) or (part.get("payloadType") or "").lower() != "inlinebase64":
            continue
        try:
            raw = base64.b64decode(payload, validate=False)
        except (binascii.Error, ValueError):
            continue
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                continue
    return None


# Fabric/Synapse cell delimiter inside notebook-content.py
_PY_CELL_SPLIT = re.compile(r"^#\s*CELL\s*\*+\s*$", re.MULTILINE)


def _iter_py_code_cells(src: str) -> Iterable[str]:
    """Yield code-cell bodies from a Fabric notebook-content.py file.

    Skips the file-level header and any markdown cells (``# MARKDOWN ********``).
    """
    if not src:
        return
    chunks = _PY_CELL_SPLIT.split(src)
    # First chunk is the synapse header; everything before any CELL marker.
    for chunk in chunks[1:]:
        # Inside a chunk, the first line may be "# MARKDOWN ********" — skip those.
        head = chunk.lstrip().splitlines()[:1]
        head_str = head[0] if head else ""
        if "MARKDOWN" in head_str.upper():
            continue
        yield chunk


def _cell_source(cell: Dict[str, Any]) -> str:
    src = cell.get("source")
    if isinstance(src, list):
        return "".join(s for s in src if isinstance(s, str))
    if isinstance(src, str):
        return src
    return ""


def _strip_noise(src: str) -> str:
    """Drop comment-only lines and trailing inline comments so patterns
    don't fire on commented-out code or hashed-out examples."""
    out_lines: List[str] = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Trim trailing inline comments (best-effort; ignores hashes inside
        # strings, but that's acceptable for heuristic scanning).
        if "#" in line:
            # naive split: only drop the comment if a # appears outside quotes
            in_s = False
            quote = ""
            cut = -1
            for i, ch in enumerate(line):
                if in_s:
                    if ch == quote and line[i - 1] != "\\":
                        in_s = False
                elif ch in ("'", '"'):
                    in_s = True
                    quote = ch
                elif ch == "#":
                    cut = i
                    break
            if cut >= 0:
                line = line[:cut]
        out_lines.append(line)
    return "\n".join(out_lines)


def _has_collect_action(src: str) -> bool:
    """NBCODE-003: flag .collect()/.toPandas() only when the chain is not
    bounded by an upstream .limit/.head/.take/.first/.agg/.count call.

    We first collapse multi-line method chains (``)\\n    .collect()``) into
    one line, then for each ``.collect()``/``.toPandas()`` occurrence we
    check whether any guard token appears before it on the same line. This
    handles arbitrary nested parens like ``.agg(F.max(col("x")))`` that a
    bounded regex with ``[^)]*`` would miss.
    """
    joined = _CHAIN_JOIN_RE.sub(").", src)
    for line in joined.splitlines():
        for pat in _COLLECT_PATTERNS:
            for m in pat.finditer(line):
                prefix = line[: m.start()]
                if not any(tok in prefix for tok in _BOUNDED_GUARD_TOKENS):
                    return True
    return False


def _scan_cell(src: str) -> Dict[str, bool]:
    """Return a map of rule_id -> matched flag for one code cell."""
    cleaned = _strip_noise(src)
    return {
        "NBCODE-001": any(p.search(cleaned) for p in _SECRET_PATTERNS),
        "NBCODE-002": any(p.search(cleaned) for p in _PIP_PATTERNS),
        "NBCODE-003": _has_collect_action(cleaned),
        "NBCODE-004": any(p.search(cleaned) for p in _DATABRICKS_PATTERNS),
        "NBCODE-005": any(p.search(cleaned) for p in _HARDCODED_PATH_PATTERNS),
        "NBCODE-006": any(p.search(cleaned) for p in _NON_DELTA_WRITE_PATTERNS),
    }


def _collect_hits(defs: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], int, int]:
    """Walk every notebook in ``pipeline_definitions.json`` and return
    ``(hits_by_rule, notebooks_scanned, code_cells_scanned)``.

    Each hit is ``{notebook, workspace, cellIndex}`` — no source content.
    """
    notebooks = defs.get("notebooks") or []
    hits: Dict[str, List[Dict[str, Any]]] = {
        rid: [] for rid in (
            "NBCODE-001", "NBCODE-002", "NBCODE-003",
            "NBCODE-004", "NBCODE-005", "NBCODE-006",
        )
    }
    nb_scanned = 0
    cells_scanned = 0
    for nb in notebooks:
        ipynb = _ipynb_from_parts(nb.get("parts") or [])
        cells_iter: List[Tuple[int, str]] = []
        if ipynb:
            for idx, cell in enumerate(ipynb.get("cells") or []):
                if isinstance(cell, dict) and cell.get("cell_type") == "code":
                    cells_iter.append((idx, _cell_source(cell)))
        else:
            py_src = _py_source_from_parts(nb.get("parts") or [])
            if py_src:
                for idx, body in enumerate(_iter_py_code_cells(py_src)):
                    cells_iter.append((idx, body))
        if not cells_iter:
            continue
        nb_scanned += 1
        for idx, src in cells_iter:
            cells_scanned += 1
            if not src.strip():
                continue
            matches = _scan_cell(src)
            for rid, matched in matches.items():
                if matched:
                    hits[rid].append({
                        "notebook": nb.get("displayName"),
                        "workspace": nb.get("workspaceName"),
                        "cellIndex": idx,
                    })
    return hits, nb_scanned, cells_scanned


_RULE_TITLES: Dict[str, Tuple[str, str, str]] = {
    # rid: (dimension, title-when-fail, recommendation)
    "NBCODE-001": (
        "security",
        "Notebook cells contain potential hard-coded secrets",
        "Replace literal keys / SAS / tokens / passwords with Key Vault lookups via "
        "notebookutils.credentials.getSecret(...) or a workspace-managed-identity flow.",
    ),
    "NBCODE-002": (
        "architecture",
        "Notebook cells install packages inline (%pip / !pip / %conda)",
        "Move dependencies to a Fabric environment so installs are baked in once, not "
        "repeated on every run, and so library versions are pinned across promotions.",
    ),
    "NBCODE-003": (
        "performance",
        "Notebook cells call unbounded .collect() / .toPandas() on Spark DataFrames",
        "Bare .collect()/.toPandas() pulls every row to the driver and is a common "
        "OOM cause. Bound the result first (.limit(N), .first(), .head(N), .take(N), "
        "or aggregate to a single row), or persist with .write.format(\"delta\") "
        "instead of materialising on the driver. Note: Fabric's display() is safe — "
        "it auto-truncates and is not flagged by this rule.",
    ),
    "NBCODE-004": (
        "architecture",
        "Notebook cells use Databricks-only APIs",
        "Replace dbutils.* with notebookutils.*, /dbfs paths with abfss:// or attached "
        "lakehouse references, and Databricks MLflow URIs with the Fabric MLflow integration.",
    ),
    "NBCODE-005": (
        "architecture",
        "Notebook cells embed hard-coded abfss:// paths or workspace / lakehouse GUIDs",
        "Parameterise the lakehouse / path, or rely on the notebook's attached default "
        "lakehouse. Hard-coded GUIDs break dev->test->prod promotion via deployment "
        "pipelines and Git integration.",
    ),
    "NBCODE-006": (
        "performance",
        "Notebook cells write data in non-Delta formats (parquet / csv / json / orc / avro)",
        "Prefer .format(\"delta\"). Delta gives ACID, time travel, V-Order and is required "
        "for Direct Lake. Non-Delta tables silently lose those capabilities in OneLake.",
    ),
}


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    # All NBCODE rules share the same input file, so check it once.
    nbcode_ids = [rid for rid in _RULE_TITLES if rid in rules]
    if not nbcode_ids:
        return findings

    defs = load_raw(raw_dir / "pipeline_definitions.json")
    if not defs:
        for rid in nbcode_ids:
            findings.append(missing_raw_finding(rules[rid], _RULE_TITLES[rid][0],
                                                "pipeline_definitions.json"))
        return findings

    hits, nb_scanned, cells_scanned = _collect_hits(defs)

    if nb_scanned == 0:
        # Catalog had no notebooks, OR getDefinition failed for all of them.
        for rid in nbcode_ids:
            rule = rules[rid]
            dim = _RULE_TITLES[rid][0]
            findings.append(make_finding(
                rule, dimension=dim, status="info",
                title="No notebook source available to scan",
                evidence={"notebooksScanned": 0, "codeCellsScanned": 0,
                          "hint": "Check pipeline_definitions.json — notebooks list "
                                  "or getDefinition errors."},
                recommendation="Run the pipeline_definitions collector and re-run this analyzer.",
            ))
        return findings

    for rid in nbcode_ids:
        rule = rules[rid]
        dim, fail_title, reco = _RULE_TITLES[rid]
        rule_hits = hits.get(rid, [])
        if not rule_hits:
            findings.append(make_finding(
                rule, dimension=dim, status="pass",
                title=f"No matches across {nb_scanned} notebook(s) / "
                      f"{cells_scanned} code cell(s)",
                evidence={"notebooksScanned": nb_scanned,
                          "codeCellsScanned": cells_scanned},
                recommendation="Heuristic check passed — no action required.",
            ))
            continue

        # Aggregate per-notebook for cleaner reporting
        per_nb: Dict[str, Dict[str, Any]] = {}
        for h in rule_hits:
            key = f"{h.get('workspace') or '?'} / {h.get('notebook') or '?'}"
            entry = per_nb.setdefault(key, {
                "notebook": h.get("notebook"),
                "workspace": h.get("workspace"),
                "cellIndexes": [],
            })
            entry["cellIndexes"].append(h["cellIndex"])

        examples = list(per_nb.values())[:MAX_EXAMPLES]

        findings.append(make_finding(
            rule, dimension=dim, status="fail",
            title=f"{fail_title} ({len(rule_hits)} cell(s) across "
                  f"{len(per_nb)} notebook(s))",
            evidence={
                "notebooksScanned": nb_scanned,
                "codeCellsScanned": cells_scanned,
                "matchedCellCount": len(rule_hits),
                "matchedNotebookCount": len(per_nb),
                "heuristic": True,
                "note": "Pattern-based scan — review each match before acting; "
                        "false positives are possible.",
                "examples": examples,
            },
            recommendation=reco,
        ))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_notebook_code.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Notebook code review: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

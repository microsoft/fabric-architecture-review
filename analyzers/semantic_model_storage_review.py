# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Storage-mode review for semantic models — DirectLake feasibility audit.

Reads ``semantic_models.json`` (catalog + storage mode) and
``semantic_model_definitions.json`` (decoded TMDL/BIM parts) and emits one
finding per Import-mode model classifying it against the DirectLake hard
prerequisites:

    Blockers detected per model (any one of these prevents DirectLake):
    - M (Power Query) partition with a non-lakehouse connector
    - Calculated columns
    - Calculated tables
    - Column data types unsupported by DirectLake (binary / variant / interval)
    - Composite mode (mixes Import + DirectLake)
    - No lakehouse-table binding (no ``entityName`` reference)

  Outcome status:
    - pass   -> model is already DirectLake (no audit needed)
    - fail   -> Import model with one or more blockers (refactor needed
                before migration is possible)
    - info   -> Import model with no blockers detected (good DL candidate)

Rule coverage:
  PERF-012  DirectLake feasibility audit for Import-mode models
  PERF-013  Direct Lake fallback behaviour (directLakeBehavior)

DATA SAFETY: TMDL/BIM metadata only. No row data is read.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, write_findings

DL_UNSUPPORTED_TYPES = {"binary", "variant", "interval"}

# M connectors that are *compatible* with DirectLake (Fabric lakehouse / OneLake).
DL_FRIENDLY_CONNECTORS = {"Lakehouse.Contents", "Fabric.Warehouse", "Sql.Databases"}

# All M connectors we care to surface (DL-incompatible unless in the friendly set).
M_CONNECTOR_PATTERN = re.compile(
    r"(Web\.Contents|Csv\.Document|Excel\.Workbook|Sql\.Database|"
    r"AzureStorage\.\w+|SharePoint\.\w+|Lakehouse\.\w+|Fabric\.\w+|"
    r"OData\.Feed|Json\.Document|Xml\.Tables|File\.Contents|Folder\.Files)"
)


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value or None


def _limited(values: List[Dict[str, Any]] | List[str], limit: int = 40) -> List[Any]:
    return values[:limit]


def _source_hints(source: str) -> Dict[str, Any]:
    """Extract metadata-level source hints without storing full M/DAX bodies."""
    hints: Dict[str, Any] = {}
    connectors = sorted(set(M_CONNECTOR_PATTERN.findall(source)))
    if connectors:
        hints["connectors"] = connectors

    sql_servers = sorted(set(re.findall(r"Sql\.Databases?\(\s*\"([^\"]+)\"", source)))
    if sql_servers:
        hints["sqlServers"] = sql_servers[:10]
    schemas_items = [
        {"schema": schema, "item": item}
        for schema, item in re.findall(r"\[\s*Schema\s*=\s*\"([^\"]+)\"\s*,\s*Item\s*=\s*\"([^\"]+)\"\s*\]", source)
    ]
    if schemas_items:
        hints["schemaItems"] = schemas_items[:20]
    referenced_queries = sorted(set(re.findall(r"Source\s*=\s*#\"([^\"]+)\"", source)))
    if referenced_queries:
        hints["referencedQueries"] = referenced_queries[:20]
    parameter_refs = sorted(set(re.findall(r"\bSource\s*=\s*([A-Za-z_][\w]*)\b", source)))
    parameter_refs = [x for x in parameter_refs if x not in {"let", "in", "Table"}]
    if parameter_refs:
        hints["parameterOrQueryRefs"] = parameter_refs[:20]
    inline_tables = bool(re.search(r"\bTable\.FromRows\b|\b#table\b", source))
    if inline_tables:
        hints["inlineTable"] = True
    files = sorted(set(re.findall(r"(?:File\.Contents|Excel\.Workbook|Csv\.Document)\(\s*\"([^\"]+)\"", source)))
    if files:
        hints["files"] = files[:10]
    return hints


def _extract_source_block(text: str, partition_start: int) -> str:
    source_match = re.search(r"^\s*source\s*=\s*\n", text[partition_start:], flags=re.M)
    if not source_match:
        return ""
    source_start = partition_start + source_match.end()
    next_partition = re.search(r"^\s*partition\s+", text[source_start:], flags=re.M)
    next_annotation = re.search(r"^\s*annotation\s+", text[source_start:], flags=re.M)
    candidates = [m.start() for m in (next_partition, next_annotation) if m]
    source_end = source_start + min(candidates) if candidates else len(text)
    return text[source_start:source_end]


def _model_details(model: Dict[str, Any]) -> Dict[str, Any]:
    calculated_columns: List[Dict[str, Any]] = []
    calculated_tables: List[Dict[str, Any]] = []
    partitions: List[Dict[str, Any]] = []
    source_columns: List[Dict[str, Any]] = []
    expressions: List[Dict[str, Any]] = []

    for part in model.get("parts") or []:
        path = part.get("path") or ""
        text = part.get("text")
        if not isinstance(text, str):
            continue

        table_match = re.search(r"^table\s+(.+?)\s*$", text, flags=re.M)
        table_name = _clean_name(table_match.group(1)) if table_match else None

        for match in re.finditer(r"^\s+column\s+((?:'[^']+'|[^\s=]+))\s*=\s*", text, flags=re.M):
            calculated_columns.append({
                "table": table_name,
                "column": _clean_name(match.group(1)),
            })

        current_column: str | None = None
        for line in text.splitlines():
            col_match = re.match(r"^\s+column\s+((?:'[^']+'|[^\s=]+))(?:\s*=\s*)?", line)
            if col_match:
                current_column = _clean_name(col_match.group(1))
                continue
            source_col_match = re.match(r"^\s+sourceColumn:\s*(.+?)\s*$", line)
            if source_col_match and current_column:
                source_columns.append({
                    "table": table_name,
                    "column": current_column,
                    "sourceColumn": _clean_name(source_col_match.group(1)),
                })

        for match in re.finditer(r"^\s*partition\s+(.+?)\s*=\s*(\w+)\b", text, flags=re.M):
            partition_name = _clean_name(match.group(1))
            partition_kind = match.group(2).lower()
            source_block = _extract_source_block(text, match.start())
            row = {
                "table": table_name,
                "partition": partition_name,
                "kind": partition_kind,
            }
            hints = _source_hints(source_block)
            if hints:
                row["sourceHints"] = hints
            partitions.append(row)
            if partition_kind == "calculated":
                calculated_tables.append({
                    "table": table_name,
                    "partition": partition_name,
                })

        if path.lower().endswith("expressions.tmdl"):
            for expr_name, expr_value in re.findall(r"^expression\s+((?:'[^']+'|[^=]+?))\s*=\s*(.+?)(?:\s+meta\s+|$)", text, flags=re.M):
                expressions.append({
                    "name": _clean_name(expr_name),
                    "value": expr_value.strip().strip('"'),
                })

    return {
        "calculatedColumnsDetail": _limited(calculated_columns),
        "calculatedTablesDetail": _limited(calculated_tables),
        "partitions": _limited(partitions),
        "sourceColumns": _limited(source_columns),
        "expressions": _limited(expressions),
        "counts": {
            "calculatedColumnsListed": len(calculated_columns),
            "calculatedTablesListed": len(calculated_tables),
            "partitionsListed": len(partitions),
            "sourceColumnsListed": len(source_columns),
            "expressionsListed": len(expressions),
        },
    }


def _decoded_blob(model: Dict[str, Any]) -> str:
    """Concatenate every decoded TMDL/BIM part into a single text blob for
    regex-style scanning. Skips parts that couldn't be decoded."""
    parts = model.get("parts") or []
    chunks: List[str] = []
    for p in parts:
        text = p.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "\n".join(chunks)


def _scan_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """Return a per-model audit dict: blockers + DL-friendly facts."""
    blob = _decoded_blob(model)
    details = _model_details(model)

    partition_modes: Set[str] = {
        m.lower() for m in re.findall(r"\bmode:\s*(\w+)", blob, flags=re.I)
    }

    has_m = bool(re.search(r"\blet\b\s+\w+\s*=", blob))
    m_connectors = {
        c for c in M_CONNECTOR_PATTERN.findall(blob)
    }

    has_lakehouse_binding = bool(re.search(r"\bentityName\s*:", blob, flags=re.I))
    has_directlake_refs = bool(
        re.search(r"directLake|DirectLakeOnly|DirectQueryToOneLake", blob, flags=re.I)
    )

    # TMDL: calculated columns have `column NAME = EXPR`; calculated tables
    # surface as partitions with `= calculated`. Use the detailed parser so
    # names with spaces/quotes are counted the same way they are reported.
    calc_columns = details["counts"]["calculatedColumnsListed"]
    calc_tables = details["counts"]["calculatedTablesListed"]

    types_used = {t.lower() for t in re.findall(r"dataType:\s*(\w+)", blob)}
    unsupported_types = sorted(types_used & DL_UNSUPPORTED_TYPES)

    composite = "import" in partition_modes and "directlake" in partition_modes

    incompatible_connectors = sorted(c for c in m_connectors if c not in DL_FRIENDLY_CONNECTORS)

    blockers: List[Dict[str, Any]] = []
    if not has_lakehouse_binding and not has_directlake_refs:
        blockers.append({
            "kind": "no_lakehouse_binding",
            "detail": "No entityName / DirectLake table binding found; data is materialised via M.",
        })
    if has_m and incompatible_connectors:
        blockers.append({
            "kind": "m_partitions",
            "detail": "Power Query (M) partitions present; DirectLake disallows M shaping.",
            "connectors": incompatible_connectors,
        })
    if calc_columns:
        blockers.append({
            "kind": "calculated_columns",
            "detail": f"{calc_columns} calculated column(s) detected; DirectLake-on-Lakehouse disallows them.",
            "count": calc_columns,
        })
    if calc_tables:
        blockers.append({
            "kind": "calculated_tables",
            "detail": f"{calc_tables} calculated table(s) detected; DirectLake disallows them.",
            "count": calc_tables,
        })
    if unsupported_types:
        blockers.append({
            "kind": "unsupported_types",
            "detail": "Column data types not supported by DirectLake.",
            "types": unsupported_types,
        })
    if composite:
        blockers.append({
            "kind": "composite_mode",
            "detail": "Model mixes Import + DirectLake partitions (composite); migrate fully or leave as-is.",
        })

    return {
        "partitionModes": sorted(partition_modes),
        "hasM": has_m,
        "mConnectors": sorted(m_connectors),
        "incompatibleConnectors": incompatible_connectors,
        "hasLakehouseBinding": has_lakehouse_binding,
        "hasDirectLakeRefs": has_directlake_refs,
        "calculatedColumns": calc_columns,
        "calculatedTables": calc_tables,
        "dataTypes": sorted(types_used),
        "unsupportedTypes": unsupported_types,
        "composite": composite,
        "blockers": blockers,
        "details": details,
    }


def _is_directlake(storage_mode: str | None) -> bool:
    return (storage_mode or "").strip().lower() in {"directlake", "directlakeonly"}


def _direct_lake_behavior(model_def: Dict[str, Any] | None) -> str | None:
    """Return the declared directLakeBehavior (lowercased) from the model TMDL,
    or None when the property is absent (Power BI defaults to Automatic)."""
    if not model_def:
        return None
    blob = _decoded_blob(model_def)
    m = re.search(r"directLakeBehavior\s*[:=]\s*\"?([A-Za-z]+)", blob, flags=re.I)
    return m.group(1).lower() if m else None


def analyze(raw_dir: str | Path, checklist_path: str | Path) -> List[Dict[str, Any]]:
    raw = Path(raw_dir)
    rules = load_rules(checklist_path)
    rule = rules.get("PERF-012") or {"id": "PERF-012", "severity": "medium"}
    rule13 = rules.get("PERF-013")

    catalog = load_raw(raw / "semantic_models.json")
    if not catalog:
        out = [missing_raw_finding(rule, "performance", "semantic_models.json")]
        if rule13:
            out.append(missing_raw_finding(rule13, "performance", "semantic_models.json"))
        return out
    defs_payload = load_raw(raw / "semantic_model_definitions.json")
    if not defs_payload:
        out = [missing_raw_finding(rule, "performance", "semantic_model_definitions.json")]
        if rule13:
            out.append(missing_raw_finding(rule13, "performance", "semantic_model_definitions.json"))
        return out

    datasets: List[Dict[str, Any]] = catalog.get("datasets") or []
    defs_by_id: Dict[str, Dict[str, Any]] = {
        m.get("id"): m for m in (defs_payload.get("models") or []) if m.get("id")
    }

    out: List[Dict[str, Any]] = []

    # Per-model findings (only meaningful for non-DirectLake models).
    audited = 0
    blocked = 0
    candidates = 0
    for ds in datasets:
        storage_mode = ds.get("targetStorageMode")
        if _is_directlake(storage_mode):
            continue
        model_def = defs_by_id.get(ds.get("id"))
        if not model_def:
            continue
        if model_def.get("error"):
            out.append(make_finding(
                rule,
                dimension="performance",
                status="info",
                title=f"PERF-012: {ds.get('name')} — definition fetch failed ({model_def['error']})",
                evidence={
                    "workspace": ds.get("workspaceName"),
                    "workspaceId": ds.get("workspaceId"),
                    "datasetId": ds.get("id"),
                    "storageMode": storage_mode,
                    "error": model_def.get("error"),
                },
                recommendation=(
                    "Re-run the semantic_model_definitions collector once the service "
                    "principal has Fabric workspace access, or fetch the definition "
                    "manually from the Power BI service to complete the audit."
                ),
            ))
            continue

        audit = _scan_model(model_def)
        audited += 1
        if audit["blockers"]:
            blocked += 1
            blocker_kinds = [b["kind"] for b in audit["blockers"]]
            out.append(make_finding(
                rule,
                dimension="performance",
                status="fail",
                title=(
                    f"PERF-012: {ds.get('name')} — Import-mode model blocks DirectLake "
                    f"migration ({', '.join(blocker_kinds)})"
                ),
                evidence={
                    "workspace": ds.get("workspaceName"),
                    "workspaceId": ds.get("workspaceId"),
                    "datasetId": ds.get("id"),
                    "storageMode": storage_mode,
                    "audit": audit,
                },
                recommendation=(
                    "DirectLake is not reachable for this model without a refactor: "
                    "replace M-based partitions with a direct lakehouse-table binding "
                    "(entityName), push calculated columns/tables into the upstream "
                    "Delta tables (silver/gold), and re-cast any unsupported data "
                    "types. Until then, keep the model in Import and ensure a "
                    "scheduled refresh is configured (PERF-006)."
                ),
            ))
        else:
            candidates += 1
            out.append(make_finding(
                rule,
                dimension="performance",
                status="info",
                title=f"PERF-012: {ds.get('name')} — Import-mode model is a DirectLake migration candidate",
                evidence={
                    "workspace": ds.get("workspaceName"),
                    "workspaceId": ds.get("workspaceId"),
                    "datasetId": ds.get("id"),
                    "storageMode": storage_mode,
                    "audit": audit,
                },
                recommendation=(
                    "No structural DirectLake blockers detected. Validate the source "
                    "tables live as Delta in a Fabric lakehouse, rebuild the model "
                    "with DirectLake bindings, and decommission the Import refresh "
                    "schedule once the migration is verified."
                ),
            ))

    # Roll-up info finding so the rule always shows up even when nothing is
    # in scope (e.g. all models are DirectLake already).
    out.append(make_finding(
        rule,
        dimension="performance",
        status="info" if blocked == 0 else "fail",
        title=(
            f"PERF-012: DirectLake feasibility — {audited} Import-mode model(s) audited, "
            f"{blocked} blocked, {candidates} candidate(s)"
        ),
        evidence={
            "auditedModels": audited,
            "blockedModels": blocked,
            "candidateModels": candidates,
            "directLakeModels": sum(1 for d in datasets if _is_directlake(d.get("targetStorageMode"))),
            "totalDatasets": len(datasets),
        },
        recommendation=(
            "Use the per-model PERF-012 findings to plan the storage-mode roadmap. "
            "Models flagged 'candidate' can be migrated to DirectLake with little "
            "rework; 'blocked' models need the listed blockers cleared first."
        ),
    ))

    # --- PERF-013 Direct Lake fallback behaviour ---
    rule13 = rules.get("PERF-013")
    if rule13:
        dl_models = [d for d in datasets if _is_directlake(d.get("targetStorageMode"))]
        pinned_dq: List[Dict[str, Any]] = []
        implicit_fallback: List[Dict[str, Any]] = []
        no_fallback = 0
        for ds in dl_models:
            behavior = _direct_lake_behavior(defs_by_id.get(ds.get("id")))
            row = {"name": ds.get("name"), "workspace": ds.get("workspaceName"),
                   "datasetId": ds.get("id"), "directLakeBehavior": behavior or "unspecified (defaults to automatic)"}
            if behavior == "directqueryonly":
                pinned_dq.append(row)
            elif behavior in (None, "automatic"):
                implicit_fallback.append(row)
            else:  # directlakeonly
                no_fallback += 1
        if not dl_models:
            out.append(make_finding(
                rule13, dimension="performance", status="info",
                title="PERF-013: no Direct Lake semantic models to evaluate for fallback behaviour",
                evidence={"directLakeModels": 0, "totalDatasets": len(datasets)},
                recommendation="This check activates once Direct Lake models exist in scope."
            ))
        else:
            status = "fail" if pinned_dq else ("fail" if implicit_fallback else "pass")
            out.append(make_finding(
                rule13, dimension="performance", status=status,
                title=(f"PERF-013: {len(pinned_dq)} Direct Lake model(s) pinned to DirectQuery, "
                       f"{len(implicit_fallback)} relying on implicit (automatic) fallback"),
                evidence={"directLakeModels": len(dl_models),
                          "explicitNoFallback": no_fallback,
                          "implicitAutomaticFallback": implicit_fallback[:20],
                          "pinnedDirectQueryOnly": pinned_dq[:20]},
                recommendation=("Set directLakeBehavior deliberately. Use DirectLakeOnly to fail fast (so "
                                "guardrail breaches are visible) or Automatic only when a monitored DirectQuery "
                                "fallback is acceptable. Investigate any model pinned to DirectQueryOnly - it is "
                                "not getting Direct Lake performance.")
            ))

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_storage_mode.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Storage mode: {len(findings)} finding(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

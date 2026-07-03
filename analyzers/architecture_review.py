# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Architecture review.

Evaluates workspace layering, capacity assignment, Git integration, workspace
size and shape using the Scanner API output, workspace inventory, and Git
integration probe.

Rule coverage:
  ARCH-001 medallion / layer naming convention
  ARCH-002 capacity assignment (no orphaned production workspaces)
    ARCH-003 OneLake shortcut usage vs duplicated lakehouse tables
  ARCH-004 Git integration coverage
  ARCH-005 monolithic workspace (item count > threshold)
  ARCH-006 workspace description present
  ARCH-007 empty workspaces
  ARCH-008 personal / PersonalGroup workspaces
  ARCH-014 deployment-pipeline stage staleness / out-of-sync

DATA SAFETY: Metadata only.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings

LAYER_PATTERN = re.compile(r"(bronze|silver|gold|raw|stg|staging|curated|landing)", re.IGNORECASE)
ENV_PATTERN = re.compile(r"\b(dev|test|qa|uat|prod|production|sbx|sandbox)\b", re.IGNORECASE)
MONOLITH_THRESHOLD = threshold("architecture", "monolith_max_items", 50, env="ARCH_MONOLITH_THRESHOLD", cast=int)
PIPELINE_STALE_DAYS = threshold("architecture", "pipeline_stale_days", 30, env="ARCH_PIPELINE_STALE_DAYS", cast=int)
LAYER_NAMING_MIN_RATIO = threshold("architecture", "layer_naming_min_ratio", 0.5, cast=float)
GIT_COVERAGE_MIN_RATIO = threshold("architecture", "git_coverage_min_ratio", 0.5, cast=float)
DESCRIPTION_COVERAGE_MIN_RATIO = threshold("architecture", "description_coverage_min_ratio", 0.5, cast=float)
IMPORT_DOMINANCE_RATIO = threshold("architecture", "import_dominance_ratio", 0.7, cast=float)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None



def _workspaces_from_scanner_or_inventory(raw_dir: Path) -> List[Dict[str, Any]]:
    scan = load_raw(raw_dir / "scanner.json")
    if scan and scan.get("workspaces"):
        return scan["workspaces"]
    inv = load_raw(raw_dir / "workspace_inventory.json")
    if inv and inv.get("workspaces"):
        return inv["workspaces"]
    return []


def _item_count(ws: Dict[str, Any]) -> int:
    # scanner.json returns Power BI legacy items as lowercase plural keys and
    # Fabric-native items as PascalCase singular keys - count both.
    keys = [
        "datasets", "reports", "dashboards", "dataflows", "lakehouses",
        "warehouses", "notebooks", "pipelines", "kqlDatabases", "mlModels",
        "mlExperiments",
        "SemanticModel", "Report", "Dashboard", "Dataflow", "Dataflow2",
        "Lakehouse", "Warehouse", "Notebook", "DataPipeline", "KQLDatabase",
        "MLModel", "MLExperiment", "Eventstream", "Eventhouse",
        "MirroredDatabase", "Reflex",
    ]
    total = sum(len(ws.get(k) or []) for k in keys)
    # workspace_inventory.json: items is a flat list
    if "items" in ws and isinstance(ws["items"], list):
        total = max(total, len(ws["items"]))
    return total


def _is_shortcut_metadata(obj: Any) -> bool:
    """Best-effort shortcut detection across Fabric REST payload variants."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if "shortcut" in key_l:
                return True
            if isinstance(value, str) and "shortcut" in value.lower():
                return True
            if isinstance(value, (dict, list)) and _is_shortcut_metadata(value):
                return True
    elif isinstance(obj, list):
        return any(_is_shortcut_metadata(x) for x in obj)
    return False


def _table_display_name(table: Dict[str, Any]) -> str | None:
    value = table.get("name") or table.get("displayName") or table.get("tableName")
    return str(value).strip() if value else None


# ---------- ARCH-012 helpers ----------

# Fabric pipeline activity types that invoke a notebook. ADF-style pipelines
# may use "ExecuteNotebook"; Fabric-native pipelines use "TridentNotebook".
_NOTEBOOK_ACTIVITY_TYPES = {"tridentnotebook", "executenotebook"}

# Container activities whose children must be walked recursively.
_NESTED_ACTIVITY_KEYS = (
    "activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities",
)


def _walk_activities(activities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    """Yield every activity in a pipeline tree, descending into nested containers."""
    for a in activities or []:
        if not isinstance(a, dict):
            continue
        yield a
        tp = a.get("typeProperties") or {}
        for key in _NESTED_ACTIVITY_KEYS:
            nested = tp.get(key)
            if isinstance(nested, list):
                yield from _walk_activities(nested)
        # Switch / cases: list of {value, activities}
        for case in (tp.get("cases") or []):
            if isinstance(case, dict):
                yield from _walk_activities(case.get("activities") or [])


def _pipeline_json_from_parts(parts: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Locate the pipeline-content JSON part inside a getDefinition result."""
    for part in parts or []:
        path = (part.get("path") or "").lower()
        decoded = part.get("decoded")
        if isinstance(decoded, dict) and (
            path.endswith("pipeline-content.json") or path.endswith(".json")
        ):
            # Pipeline content always carries either "activities" or "properties.activities".
            if "activities" in decoded or "properties" in decoded:
                return decoded
    return None


def _ipynb_from_parts(parts: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for part in parts or []:
        path = (part.get("path") or "").lower()
        decoded = part.get("decoded")
        if isinstance(decoded, dict) and path.endswith(".ipynb"):
            return decoded
    return None


def _notebook_parameter_names(ipynb: Dict[str, Any]) -> Set[str]:
    """Extract parameter names from the Papermill-style ``parameters``-tagged cell."""
    names: Set[str] = set()
    for cell in ipynb.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        tags = ((cell.get("metadata") or {}).get("tags")) or []
        if "parameters" not in tags or cell.get("cell_type") != "code":
            continue
        src = cell.get("source")
        if isinstance(src, list):
            src = "".join(src)
        if not isinstance(src, str):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _analyze_pipeline_param_contracts(
    defs: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]]]:
    """Cross-check ExecuteNotebook activity parameters against notebook widgets.

    Returns ``(mismatches, activities_checked, unresolved_references)``.
    """
    pipelines = defs.get("pipelines") or []
    notebooks = defs.get("notebooks") or []

    nb_by_id: Dict[str, Dict[str, Any]] = {}
    for n in notebooks:
        ipynb = _ipynb_from_parts(n.get("parts") or [])
        if ipynb is None:
            continue
        nb_by_id[n.get("id")] = {
            "displayName": n.get("displayName"),
            "workspaceId": n.get("workspaceId"),
            "params": _notebook_parameter_names(ipynb),
        }

    mismatches: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    checked = 0

    for p in pipelines:
        content = _pipeline_json_from_parts(p.get("parts") or [])
        if not content:
            continue
        # Activities live either at the top level or under properties.
        activities = content.get("activities") or (content.get("properties") or {}).get("activities") or []
        for act in _walk_activities(activities):
            if (act.get("type") or "").lower() not in _NOTEBOOK_ACTIVITY_TYPES:
                continue
            checked += 1
            tp = act.get("typeProperties") or {}
            nb_ref = tp.get("notebookId") or (tp.get("notebook") or {}).get("referenceName")
            params = tp.get("parameters") or {}
            passed = set(params.keys()) if isinstance(params, dict) else set()
            nb = nb_by_id.get(nb_ref)
            if nb is None:
                unresolved.append({
                    "pipeline": p.get("displayName"),
                    "activity": act.get("name"),
                    "notebookId": nb_ref,
                })
                continue
            declared = nb["params"]
            extra = sorted(passed - declared)
            missing = sorted(declared - passed)
            if extra or missing:
                mismatches.append({
                    "pipeline": p.get("displayName"),
                    "workspace": p.get("workspaceName"),
                    "activity": act.get("name"),
                    "notebook": nb["displayName"],
                    "passedNotDeclared": extra,
                    "declaredNotPassed": missing,
                })
    return mismatches, checked, unresolved


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    # --- ARCH-013 scanner collection completeness ---
    # Surface a partial Scanner API run so a reviewer never treats an incomplete
    # inventory as the whole tenant. The collector writes a "_meta" block with
    # batch/skip counts; absence of the block (older runs) degrades to info.
    rule = rules.get("ARCH-013")
    if rule:
        scan = load_raw(raw_dir / "scanner.json")
        meta = (scan or {}).get("_meta") if isinstance(scan, dict) else None
        if not scan:
            findings.append(missing_raw_finding(rule, "architecture", "scanner.json"))
        elif not isinstance(meta, dict):
            findings.append(make_finding(
                rule, dimension="architecture", status="info",
                title="Scanner collection completeness could not be verified",
                evidence={"note": "scanner.json has no _meta block (collected by an older scanner_api version)."},
                recommendation="Re-run collectors.scanner_api to record collection metadata and confirm the inventory is complete.",
            ))
        elif meta.get("complete", True):
            findings.append(make_finding(
                rule, dimension="architecture", status="pass",
                title="Scanner inventory collected completely",
                evidence={
                    "workspacesCollected": meta.get("workspaces_collected"),
                    "workspacesEligible": meta.get("workspaces_eligible"),
                    "batchesTotal": meta.get("batches_total"),
                    "scoped": meta.get("scoped"),
                },
                recommendation="No action needed — every eligible workspace was scanned.",
            ))
        else:
            findings.append(make_finding(
                rule, dimension="architecture", status="fail",
                title="Scanner inventory is INCOMPLETE — some workspaces were not scanned",
                evidence={
                    "workspacesCollected": meta.get("workspaces_collected"),
                    "workspacesEligible": meta.get("workspaces_eligible"),
                    "batchesFailed": meta.get("batches_failed"),
                    "batchesTotal": meta.get("batches_total"),
                    "failedBatchNumbers": meta.get("failed_batch_numbers"),
                },
                recommendation=(
                    "Re-run collectors.scanner_api to retry the skipped batch(es) before relying on "
                    "this report. Every scanner-derived finding (ARCH/GOV/SEC/COST) understates the "
                    "tenant until the inventory is complete."
                ),
            ))

    workspaces = _workspaces_from_scanner_or_inventory(raw_dir)
    if not workspaces:
        for rid in ("ARCH-001", "ARCH-002", "ARCH-003", "ARCH-005", "ARCH-006", "ARCH-007", "ARCH-008"):
            if rid in rules:
                findings.append(missing_raw_finding(rules[rid], "architecture", "scanner.json or workspace_inventory.json"))
        # still try Git rule
    else:
        # --- ARCH-001 layer naming (workspace OR lakehouse level) ---
        rule = rules.get("ARCH-001")
        if rule:
            layered_ws = [w for w in workspaces if w.get("name") and LAYER_PATTERN.search(w["name"])]
            ws_ratio = len(layered_ws) / len(workspaces)

            # Look inside each workspace's lakehouses too: even when workspace
            # names don't carry a layer token, the medallion convention may
            # live at the lakehouse level (e.g. lh_bronze / lh_silver / lh_gold
            # inside one engineering workspace).
            CORE_LAYERS = ("bronze", "silver", "gold")
            inside_layers_by_ws: Dict[str, set] = {}
            for w in workspaces:
                lhs = (w.get("lakehouses") or w.get("Lakehouse") or [])
                names = [(lh.get("name") or lh.get("displayName") or "").lower() for lh in lhs]
                found = {layer for layer in CORE_LAYERS if any(layer in n for n in names)}
                if found:
                    inside_layers_by_ws[w.get("name") or w.get("id") or "?"] = found

            ws_with_all_three_inside = [n for n, layers in inside_layers_by_ws.items()
                                         if set(CORE_LAYERS).issubset(layers)]

            if ws_ratio >= LAYER_NAMING_MIN_RATIO:
                # Layer is visible at the workspace name level -> convention adopted.
                status = "pass"
                title = "Medallion / layer naming convention"
                reco = ("Layer-based workspace naming is in place. Continue to mirror the convention "
                        "on lakehouses (lh_bronze / lh_silver / lh_gold) and on Deployment Pipeline "
                        "stages.")
            elif ws_with_all_three_inside:
                # All three layers exist as lakehouses inside the same workspace.
                # Convention is technically present, but everything shares one
                # workspace -> no RBAC boundary, no per-layer capacity choice.
                status = "fail"
                title = ("Medallion layers exist inside a single workspace - consider splitting "
                         "into bronze / silver / gold workspaces")
                reco = (
                    "Lakehouses named bronze / silver / gold were detected inside the same "
                    f"workspace(s): {', '.join(ws_with_all_three_inside)}. The medallion convention "
                    "is in place at the lakehouse level, but everything shares one workspace - so "
                    "the layers cannot be governed independently. For the long run, split into "
                    "three workspaces per environment (e.g. *-bronze-dev / *-silver-dev / "
                    "*-gold-dev, mirrored on *-prod) and use OneLake shortcuts to promote data "
                    "between them. This separation of concerns delivers two benefits that are "
                    "hard to retrofit later: (1) role-based access at the workspace boundary "
                    "(engineers on bronze/silver, owners on gold, business users Viewer on gold "
                    "only) without relying on item-level / OneLake data-access roles; and "
                    "(2) per-layer capacity flexibility (bronze ingest on a smaller capacity, "
                    "gold serving on a larger or region-pinned one) without touching the other "
                    "layers."
                )
            else:
                status = "fail"
                title = "Medallion / layer naming convention"
                reco = ("Adopt a layer-based naming convention. Preferred: separate workspaces named "
                        "*-bronze / *-silver / *-gold so each layer has its own RBAC boundary and "
                        "can be assigned to a different capacity. Acceptable interim: name the "
                        "lakehouses lh_bronze / lh_silver / lh_gold inside one workspace.")

            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=title,
                evidence={
                    "workspaceCount": len(workspaces),
                    "layeredWorkspaceCount": len(layered_ws),
                    "layeredWorkspaceRatio": round(ws_ratio, 2),
                    "workspaceExamples": [w.get("name") for w in workspaces[:10]],
                    "lakehouseLayersByWorkspace": {
                        k: sorted(v) for k, v in inside_layers_by_ws.items()
                    },
                    "workspacesWithAllLayersInside": ws_with_all_three_inside,
                },
                recommendation=reco,
            ))

        # --- ARCH-002 capacity assignment ---
        rule = rules.get("ARCH-002")
        if rule:
            unassigned = [w for w in workspaces
                          if not (w.get("capacityId") or w.get("isOnDedicatedCapacity"))
                          and (w.get("type") not in ("PersonalGroup",))]
            status = "pass" if not unassigned else "fail"
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title="Workspaces without Fabric capacity assignment",
                evidence={"unassignedCount": len(unassigned),
                          "unassignedNames": [w.get("name") for w in unassigned[:20]]},
                recommendation=("Assign every production workspace to a Fabric capacity. "
                                "Pro-only / unassigned workspaces cannot host Fabric items.")
            ))

        # --- ARCH-005 monolithic workspaces ---
        rule = rules.get("ARCH-005")
        if rule:
            monoliths = [(w.get("name"), _item_count(w)) for w in workspaces if _item_count(w) > MONOLITH_THRESHOLD]
            status = "pass" if not monoliths else "fail"
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=f"Workspaces exceeding monolithic threshold (>{MONOLITH_THRESHOLD} items)",
                evidence={"threshold": MONOLITH_THRESHOLD, "monolithCount": len(monoliths),
                          "monoliths": [{"name": n, "items": c} for n, c in monoliths]},
                recommendation=("Split large workspaces by domain/layer. Large workspaces complicate "
                                "RBAC, deployment pipelines, and lineage analysis.")
            ))

        # --- ARCH-006 description present ---
        rule = rules.get("ARCH-006")
        if rule:
            # Only evaluate shared, non-empty workspaces: personal ("My workspace")
            # and empty workspaces carry no documentation expectation, so including
            # them would guarantee a fail on virtually every tenant.
            relevant = [w for w in workspaces
                        if w.get("type") not in ("PersonalGroup",) and _item_count(w) > 0]
            if not relevant:
                findings.append(make_finding(
                    rule, dimension="architecture", status="pass",
                    title="Workspaces missing a description",
                    evidence={"evaluatedWorkspaces": 0,
                              "note": "No shared, non-empty workspaces to evaluate."},
                    recommendation="Add a short description to every workspace describing purpose, owner, and environment."
                ))
            else:
                no_desc = [w.get("name") for w in relevant if not (w.get("description") or "").strip()]
                coverage = (len(relevant) - len(no_desc)) / len(relevant)
                status = "pass" if coverage >= DESCRIPTION_COVERAGE_MIN_RATIO else "fail"
                findings.append(make_finding(
                    rule, dimension="architecture", status=status,
                    title="Workspaces missing a description",
                    evidence={"evaluatedWorkspaces": len(relevant),
                              "missingDescriptionCount": len(no_desc),
                              "coverageRatio": round(coverage, 2),
                              "minRatio": DESCRIPTION_COVERAGE_MIN_RATIO,
                              "examples": no_desc[:20]},
                    recommendation="Add a short description to every workspace describing purpose, owner, and environment."
                ))

        # --- ARCH-007 empty workspaces (info) ---
        rule = rules.get("ARCH-007")
        if rule:
            empties = [w.get("name") for w in workspaces if _item_count(w) == 0
                       and (w.get("state", "Active") in (None, "Active"))]
            findings.append(make_finding(
                rule, dimension="architecture",
                status="info" if empties else "pass",
                title="Empty workspaces (no items)",
                evidence={"emptyCount": len(empties), "examples": empties[:20]},
                recommendation="Archive or repurpose empty workspaces to keep the tenant inventory clean."
            ))

        # --- ARCH-008 personal workspaces ---
        rule = rules.get("ARCH-008")
        if rule:
            personal = [w.get("name") for w in workspaces if w.get("type") == "PersonalGroup"]
            status = "pass" if not personal else "fail"
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title="Personal (My workspace) workspaces in use",
                evidence={"personalCount": len(personal), "examples": personal[:20]},
                recommendation="Migrate any production content out of personal workspaces into shared, capacity-backed workspaces."
            ))

    # --- ARCH-003 OneLake shortcuts vs duplicated lakehouse tables ---
    rule = rules.get("ARCH-003")
    if rule:
        lh_payload = load_raw(raw_dir / "lakehouse_warehouse.json")
        if not lh_payload:
            findings.append(missing_raw_finding(rule, "architecture", "lakehouse_warehouse.json"))
        else:
            lakehouses = lh_payload.get("lakehouses") or []
            tables_by_lakehouse = lh_payload.get("tables") or {}
            table_locations: Dict[str, List[Dict[str, Any]]] = {}
            shortcut_rows: List[Dict[str, Any]] = []

            lh_lookup = {lh.get("id"): lh for lh in lakehouses if lh.get("id")}
            for lakehouse in lakehouses:
                if _is_shortcut_metadata(lakehouse):
                    shortcut_rows.append({
                        "lakehouse": lakehouse.get("displayName") or lakehouse.get("name"),
                        "workspace": lakehouse.get("workspaceName"),
                        "source": "lakehouseMetadata",
                    })

            for lakehouse_id, tables in tables_by_lakehouse.items():
                lakehouse = lh_lookup.get(lakehouse_id, {})
                for table in tables or []:
                    if not isinstance(table, dict):
                        continue
                    name = _table_display_name(table)
                    if not name:
                        continue
                    row = {
                        "table": name,
                        "lakehouse": lakehouse.get("displayName") or lakehouse.get("name") or lakehouse_id,
                        "workspace": lakehouse.get("workspaceName"),
                        "isShortcut": _is_shortcut_metadata(table),
                    }
                    table_locations.setdefault(name.lower(), []).append(row)
                    if row["isShortcut"]:
                        shortcut_rows.append(row)

            duplicated = [
                {"table": name, "locations": rows}
                for name, rows in table_locations.items()
                if len({(r.get("workspace"), r.get("lakehouse")) for r in rows}) > 1
                and not any(r.get("isShortcut") for r in rows)
            ]

            table_count = sum(len(v or []) for v in tables_by_lakehouse.values())
            if not lakehouses:
                status = "info"
                title = "No lakehouses discovered for shortcut assessment"
                reco = "If lakehouse data products exist, re-run lakehouse_warehouse after scanner/workspace inventory collection."
            elif table_count == 0 and not shortcut_rows:
                status = "info"
                title = "OneLake shortcut usage not observable from collected table metadata"
                reco = ("The lakehouse inventory found lakehouses but no table/shortcut rows. Re-run with access "
                        "to the lakehouse table metadata or verify shortcuts manually in the Fabric UI.")
            elif duplicated:
                status = "fail"
                title = f"Potential duplicated lakehouse tables found ({len(duplicated)} repeated table name(s))"
                reco = ("Review repeated table names across lakehouses/workspaces. If they represent promoted "
                        "or shared data, replace physical copies with OneLake shortcuts so lineage, freshness, "
                        "and storage cost remain controlled.")
            elif shortcut_rows:
                status = "pass"
                title = f"OneLake shortcut metadata detected ({len(shortcut_rows)} shortcut signal(s))"
                reco = "Keep using shortcuts for cross-workspace/layer sharing and document ownership of the source tables."
            else:
                status = "pass"
                title = "No duplicated lakehouse table names detected"
                reco = ("No metadata signal suggests cross-lakehouse table duplication. When sharing data across "
                        "workspaces or medallion layers, prefer OneLake shortcuts over copying data.")
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=title,
                evidence={
                    "lakehouseCount": len(lakehouses),
                    "tableCount": table_count,
                    "shortcutSignals": shortcut_rows[:20],
                    "duplicateTableGroups": duplicated[:20],
                },
                recommendation=reco,
            ))

    # --- ARCH-009 Deployment Pipelines coverage ---
    rule = rules.get("ARCH-009")
    if rule:
        dp = load_raw(raw_dir / "deployment_pipelines.json")
        if not dp:
            findings.append(missing_raw_finding(rule, "architecture", "deployment_pipelines.json"))
        else:
            pipelines = dp.get("pipelines") or []
            assigned_ws_ids = {wid for p in pipelines for wid in (p.get("assignedWorkspaceIds") or [])}
            # Identify candidate "production" workspaces from naming convention.
            prod_re = re.compile(r"\b(prod|production)\b", re.IGNORECASE)
            prod_ws = [w for w in workspaces if w.get("name") and prod_re.search(w["name"])]
            uncovered = [w.get("name") for w in prod_ws if w.get("id") not in assigned_ws_ids]
            if not pipelines:
                status = "fail"
                title = "No Fabric / Power BI deployment pipelines configured"
                reco = ("Create a deployment pipeline (dev -> test -> prod) for each data product. "
                        "Without one there is no controlled promotion path, comparison view, or rollback.")
            elif uncovered:
                status = "fail"
                title = f"{len(uncovered)} production-named workspace(s) not bound to any deployment pipeline"
                reco = ("Bind each production workspace to a deployment pipeline stage so changes flow "
                        "through dev/test before reaching prod.")
            else:
                status = "pass"
                title = f"{len(pipelines)} deployment pipeline(s) configured; production workspaces covered"
                reco = "Maintain stage assignments as new workspaces are introduced."
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=title,
                evidence={"pipelineCount": len(pipelines),
                          "stages": sum(len(p.get("stages") or []) for p in pipelines),
                          "productionWorkspaces": len(prod_ws),
                          "uncoveredProductionWorkspaces": uncovered[:20],
                          "pipelines": [{"name": p.get("displayName"),
                                          "stageCount": p.get("stageCount"),
                                          "workspaces": [s.get("workspaceId") for s in (p.get("stages") or [])]}
                                         for p in pipelines[:15]]},
                recommendation=reco,
            ))

    # --- ARCH-014 Deployment-pipeline stage staleness / out-of-sync ---
    rule = rules.get("ARCH-014")
    if rule:
        dp = load_raw(raw_dir / "deployment_pipelines.json")
        if not dp:
            findings.append(missing_raw_finding(rule, "architecture", "deployment_pipelines.json"))
        else:
            pipelines = dp.get("pipelines") or []
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=PIPELINE_STALE_DAYS)
            unpromoted: List[Dict[str, Any]] = []
            stale_deploys: List[Dict[str, Any]] = []
            have_artifact_data = False
            for p in pipelines:
                stage_artifacts = p.get("stageArtifacts") or {}
                if not stage_artifacts:
                    continue
                have_artifact_data = True
                # The highest stage order is the final (prod) stage.
                orders = sorted(int(o) for o in stage_artifacts.keys())
                if not orders:
                    continue
                final_order = orders[-1]
                for order in orders[:-1]:
                    for a in stage_artifacts.get(str(order)) or []:
                        # Never promoted to the next stage.
                        if a.get("sourceArtifactId") and not a.get("targetArtifactId"):
                            unpromoted.append({"pipeline": p.get("displayName"),
                                               "stageOrder": order,
                                               "artifact": a.get("artifactName"),
                                               "artifactType": a.get("artifactType")})
                # Stale last deployment on any stage.
                for order in orders:
                    for a in stage_artifacts.get(str(order)) or []:
                        dt = _parse_dt(a.get("lastDeploymentTime"))
                        if dt and dt < stale_cutoff:
                            stale_deploys.append({"pipeline": p.get("displayName"),
                                                  "stageOrder": order,
                                                  "artifact": a.get("artifactName"),
                                                  "lastDeploymentTime": a.get("lastDeploymentTime")})
            if not pipelines:
                findings.append(make_finding(
                    rule, dimension="architecture", status="info",
                    title="No deployment pipelines to evaluate for stage staleness",
                    evidence={"pipelineCount": 0},
                    recommendation="ARCH-009 covers pipeline coverage; this check activates once pipelines exist."
                ))
            elif not have_artifact_data:
                findings.append(make_finding(
                    rule, dimension="architecture", status="info",
                    title="Deployment pipelines present but no stage-artifact data was returned",
                    evidence={"pipelineCount": len(pipelines),
                              "reason": "Stage artifact endpoints returned nothing (permissions or empty stages)."},
                    recommendation=("Re-run the deployment_pipelines collector with an identity that has pipeline "
                                    "admin rights so stage promotion state can be evaluated.")
                ))
            else:
                status = "fail" if (unpromoted or stale_deploys) else "pass"
                findings.append(make_finding(
                    rule, dimension="architecture", status=status,
                    title=("Deployment pipelines have unpromoted or stale items"
                           if status == "fail" else "Deployment-pipeline stages are in sync"),
                    evidence={"staleDays": PIPELINE_STALE_DAYS,
                              "unpromotedCount": len(unpromoted),
                              "unpromotedItems": unpromoted[:20],
                              "staleDeploymentCount": len(stale_deploys),
                              "staleDeployments": stale_deploys[:20]},
                    recommendation=("Promote validated dev/test items to the final stage and redeploy stages whose "
                                    "last deployment predates the staleness window so prod reflects current content.")
                ))


    rule = rules.get("ARCH-010")
    if rule:
        rti = load_raw(raw_dir / "realtime_intelligence.json")
        if not rti:
            findings.append(missing_raw_finding(rule, "architecture", "realtime_intelligence.json"))
        else:
            summary = rti.get("summary") or {}
            total = sum(summary.values()) if summary else 0
            eventhouses = rti.get("eventhouses") or []
            reflexes = rti.get("reflexes") or []
            # If eventhouses exist but no reflex/activator, flag the lost operational-alerts opportunity.
            evh_workspaces = {e.get("workspaceId") for e in eventhouses}
            rflx_workspaces = {r.get("workspaceId") for r in reflexes}
            unalerted = evh_workspaces - rflx_workspaces
            status = "info"
            if total == 0:
                title = "No Real-Time Intelligence or Mirrored Database items detected"
                reco = ("If the architecture has streaming or zero-ETL requirements, evaluate Eventhouse / "
                        "KQL Database (real-time analytics), Eventstream (ingest), Reflex / Activator "
                        "(operational alerts) and Mirrored Databases (zero-ETL replication).")
            else:
                title = f"Real-Time Intelligence usage profile: {total} item(s)"
                reco = ("Inventory above. If Eventhouses exist without a Reflex / Activator the tenant is "
                        "missing the operational-alerts path - consider adding triggers on hot KQL data.")
                if unalerted:
                    reco = (f"{len(unalerted)} workspace(s) host Eventhouses but no Reflex / Activator; "
                            "add triggers to convert real-time data into operational alerts. ") + reco
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=title,
                evidence={"counts": summary,
                          "workspacesWithEventhouseButNoReflex": sorted(list(unalerted))[:20]},
                recommendation=reco,
            ))

    # --- ARCH-011 Storage-mode mix ---
    rule = rules.get("ARCH-011")
    if rule:
        sm = load_raw(raw_dir / "semantic_models.json")
        if not sm:
            findings.append(missing_raw_finding(rule, "architecture", "semantic_models.json"))
        else:
            datasets = sm.get("datasets") or []
            mode_counts: Dict[str, int] = {}
            # Map raw API values to friendly names.
            # Abf = Import, DirectQuery = DirectQuery, PremiumFiles = Direct Lake,
            # Push = Push, Streaming = Streaming.
            FRIENDLY = {
                "abf": "Import",
                "directquery": "DirectQuery",
                "premiumfiles": "Direct Lake",
                "push": "Push",
                "streaming": "Streaming",
                "pushstreaming": "Push/Streaming",
                "directlake": "Direct Lake",
                "import": "Import",
            }
            details: List[Dict[str, Any]] = []
            for d in datasets:
                raw_mode = (d.get("targetStorageMode") or d.get("defaultMode") or "Unknown")
                friendly = FRIENDLY.get(raw_mode.lower(), raw_mode)
                mode_counts[friendly] = mode_counts.get(friendly, 0) + 1
                details.append({"name": d.get("name"), "workspace": d.get("workspaceName"),
                                 "storageMode": friendly})
            total_ds = len(datasets)
            import_n = mode_counts.get("Import", 0)
            dq_n = mode_counts.get("DirectQuery", 0)
            dl_n = mode_counts.get("Direct Lake", 0)
            # Heuristic: fail if Import dominates ( >70% ) and lakehouses are present (Direct Lake fits).
            ws_has_lakehouse = any(
                (w.get("lakehouses") or w.get("Lakehouse") or []) for w in workspaces
            ) if workspaces else False
            if total_ds == 0:
                status = "info"
                title = "No semantic models discovered"
                reco = "If reporting workloads exist, ensure datasets are inventoried."
            elif ws_has_lakehouse and total_ds and (import_n / total_ds) > IMPORT_DOMINANCE_RATIO:
                status = "fail"
                title = (f"Import-heavy semantic-model mix ({import_n}/{total_ds} datasets are Import) "
                         "while lakehouses are present")
                reco = ("Evaluate moving Import models over lakehouse data to Direct Lake. Direct Lake "
                        "reads Delta directly via VertiPaq with no refresh, cutting capacity CU spent on "
                        "scheduled refresh and reducing data freshness lag.")
            else:
                status = "info"
                title = "Semantic-model storage-mode distribution"
                reco = ("If many Import models read lakehouse Delta tables, evaluate Direct Lake. "
                        "DirectQuery should be limited to scenarios that genuinely require it.")
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=title,
                evidence={"datasetCount": total_ds,
                          "modeCounts": mode_counts,
                          "examples": details[:15]},
                recommendation=reco,
            ))

    # --- ARCH-012 Pipeline parameter contract ---
    rule = rules.get("ARCH-012")
    if rule:
        defs = load_raw(raw_dir / "pipeline_definitions.json")
        if not defs:
            findings.append(missing_raw_finding(rule, "architecture", "pipeline_definitions.json"))
        else:
            mismatches, checked, unmatched_refs = _analyze_pipeline_param_contracts(defs)
            if not checked:
                status = "info"
                title = ("No ExecuteNotebook / TridentNotebook activities found in collected "
                         "pipeline definitions")
                reco = ("Either no pipelines orchestrate notebooks yet, or the getDefinition "
                        "calls failed (check pipeline_definitions.json 'errors'). Re-run the "
                        "pipeline_definitions collector to confirm.")
            elif mismatches:
                status = "fail"
                title = (f"{len(mismatches)} notebook activity(ies) pass parameters that don't "
                         "match the notebook's parameter cell")
                reco = ("Align ExecuteNotebook activity parameters with the notebook's "
                        "Papermill-style `parameters`-tagged cell. Names passed by the pipeline "
                        "but not declared in the notebook are silently ignored; names declared "
                        "in the notebook but not passed fall back to defaults - both are common "
                        "causes of runtime failures. Fix either side so the names match exactly.")
            else:
                status = "pass"
                title = f"Pipeline parameter contracts match notebook widgets ({checked} activity(ies) checked)"
                reco = "Keep parameter names in sync as pipelines and notebooks evolve."
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title=title,
                evidence={
                    "activitiesChecked": checked,
                    "mismatchCount": len(mismatches),
                    "mismatches": mismatches[:20],
                    "notebookReferencesNotResolved": unmatched_refs[:20],
                },
                recommendation=reco,
            ))

    # --- ARCH-004 Git integration coverage ---
    rule = rules.get("ARCH-004")
    if rule:
        git = load_raw(raw_dir / "git_integration.json")
        if not git:
            findings.append(missing_raw_finding(rule, "architecture", "git_integration.json"))
        else:
            ws_list = git.get("workspaces") or []
            connected = [w for w in ws_list if w.get("connected")]
            total = len(ws_list)
            ratio = (len(connected) / total) if total else 0
            status = "pass" if total and ratio >= GIT_COVERAGE_MIN_RATIO else ("info" if not total else "fail")
            findings.append(make_finding(
                rule, dimension="architecture", status=status,
                title="Workspaces connected to Git source control",
                evidence={"totalWorkspaces": total, "gitConnectedCount": len(connected),
                          "ratio": round(ratio, 2),
                          "connectedExamples": [w.get("workspaceName") for w in connected[:10]]},
                recommendation=("Connect production workspaces to Git for change tracking, peer review and "
                                "deployment pipeline support.")
            ))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_architecture.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Architecture: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

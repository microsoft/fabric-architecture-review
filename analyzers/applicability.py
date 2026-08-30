# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Deterministic workspace classification and rule applicability helpers."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

_PROD = re.compile(r"(?:^|[-_.\s])(prod|production|live)(?:$|[-_.\s])", re.IGNORECASE)
_NONPROD = re.compile(r"(?:^|[-_.\s])(dev|test|qa|uat|sbx|sandbox|poc|demo)(?:$|[-_.\s])", re.IGNORECASE)
_PLATFORM = re.compile(r"capacity[ -]?metrics|monitoring|admin|platform[ -]?ops", re.IGNORECASE)

_BUCKET_TYPES = {
    "datasets": "SemanticModel", "SemanticModel": "SemanticModel",
    "reports": "Report", "Report": "Report",
    "dashboards": "Dashboard", "Dashboard": "Dashboard",
    "dataflows": "Dataflow", "Dataflow": "Dataflow", "Dataflow2": "Dataflow2",
    "lakehouses": "Lakehouse", "Lakehouse": "Lakehouse",
    "warehouses": "Warehouse", "Warehouse": "Warehouse",
    "notebooks": "Notebook", "Notebook": "Notebook",
    "pipelines": "DataPipeline", "DataPipeline": "DataPipeline",
    "kqlDatabases": "KQLDatabase", "KQLDatabase": "KQLDatabase",
    "mlModels": "MLModel", "MLModel": "MLModel",
    "mlExperiments": "MLExperiment", "MLExperiment": "MLExperiment",
    "Eventstream": "Eventstream", "Eventhouse": "Eventhouse",
    "MirroredDatabase": "MirroredDatabase", "Reflex": "Reflex",
}
_KNOWN_TYPES = {value.lower(): value for value in _BUCKET_TYPES.values()}


def _load_overrides(path: str | Path | None, section: str) -> Dict[str, Dict[str, Any]]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8-sig") as handle:
            payload = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {
        str(entry["id"]).lower(): entry
        for entry in payload.get(section) or []
        if isinstance(entry, dict) and entry.get("id")
    }


def load_workspace_overrides(path: str | Path | None) -> Dict[str, Dict[str, Any]]:
    """Load profile/rule overrides keyed by stable workspace ID."""
    return _load_overrides(path, "workspaces")


def load_capacity_overrides(path: str | Path | None) -> Dict[str, Dict[str, Any]]:
    """Load capacity profile overrides keyed by stable capacity ID."""
    return _load_overrides(path, "capacities")


def item_type_counts(workspace: Dict[str, Any]) -> Dict[str, int]:
    """Normalize Scanner and Fabric REST inventory shapes without double-counting."""
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for bucket, item_type in _BUCKET_TYPES.items():
        for index, item in enumerate(workspace.get(bucket) or []):
            identity = str(item.get("id") or f"{bucket}:{item.get('name') or item.get('displayName') or index}").lower()
            if identity in seen:
                continue
            seen.add(identity)
            counts[item_type] += 1
    for index, item in enumerate(workspace.get("items") or []):
        raw_type = str(item.get("type") or item.get("itemType") or "").strip()
        if not raw_type:
            continue
        item_type = _KNOWN_TYPES.get(raw_type.lower(), raw_type)
        identity = str(item.get("id") or f"items:{item_type}:{item.get('displayName') or item.get('name') or index}").lower()
        if identity in seen:
            continue
        seen.add(identity)
        counts[item_type] += 1
    return dict(counts)


def _environment(name: str, explicit: Any = None) -> str:
    if explicit:
        return str(explicit).lower()
    if _PROD.search(name):
        return "production"
    if _NONPROD.search(name):
        return "nonproduction"
    return "unknown"


def classify_workspace(
    workspace: Dict[str, Any], override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Classify a workspace conservatively from item composition and overrides."""
    override = override or {}
    explicit = override.get("profile") or {}
    name = str(workspace.get("name") or "")
    counts = item_type_counts(workspace)
    unknown_types = sorted(key for key in counts if key.lower() not in _KNOWN_TYPES)

    if explicit.get("archetype"):
        archetype = str(explicit["archetype"]).lower()
        classification = "explicit"
        reason = str(explicit.get("reason") or "Configured workspace profile override.")
    elif (workspace.get("type") or "").lower() in ("personalgroup", "personal"):
        archetype, classification, reason = "personal", "strong", "Personal workspace type."
    elif not counts:
        archetype, classification, reason = "empty", "strong", "No typed Fabric items discovered."
    elif unknown_types:
        archetype, classification = "unknown", "unknown"
        reason = f"Unsupported item type(s): {', '.join(unknown_types)}."
    else:
        engineering = sum(counts.get(key, 0) for key in ("Lakehouse", "Dataflow", "Dataflow2", "DataPipeline", "Notebook"))
        warehouse = counts.get("Warehouse", 0)
        mirror = counts.get("MirroredDatabase", 0)
        realtime = sum(counts.get(key, 0) for key in ("Eventhouse", "Eventstream", "KQLDatabase", "Reflex"))
        science = counts.get("MLModel", 0) + counts.get("MLExperiment", 0)
        bi = sum(counts.get(key, 0) for key in ("SemanticModel", "Report", "Dashboard"))
        primary = sum(bool(value) for value in (engineering, warehouse, mirror, realtime, science))
        if _PLATFORM.search(name) and bi and not primary:
            archetype, classification, reason = "platform_monitoring", "strong", "Operational name and BI-only composition."
        elif mirror and primary == 1:
            archetype, classification, reason = "mirrored_zero_etl", "strong", "Mirrored Database workload detected."
        elif realtime and primary == 1:
            archetype, classification, reason = "realtime", "strong", "Real-Time Intelligence workload detected."
        elif engineering and primary == 1:
            archetype, classification, reason = "batch_engineering", "strong", "Lakehouse/data-engineering workload detected."
        elif warehouse and primary == 1:
            archetype, classification, reason = "warehouse_analytics", "strong", "Warehouse workload detected."
        elif science and primary == 1:
            archetype, classification, reason = "data_science", "strong", "Machine-learning workload detected."
        elif primary == 0 and bi:
            archetype, classification, reason = "bi_serving", "strong", "Semantic-model/report workload only."
        else:
            archetype, classification, reason = "mixed", "mixed", "Multiple primary workload families detected."

    return {
        "workspaceId": workspace.get("id"),
        "workspaceName": workspace.get("name"),
        "archetype": archetype,
        "environment": _environment(name, explicit.get("environment")),
        "classification": classification,
        "reason": reason,
        "usagePattern": str(explicit.get("usage_pattern") or "unknown").lower(),
        "itemTypeCounts": counts,
        "ruleOverrides": override.get("rule_overrides") or {},
    }


def classify_workspaces(
    workspaces: Iterable[Dict[str, Any]], overrides: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Dict[str, Any]]:
    overrides = overrides or {}
    return {
        str(workspace.get("id") or "").lower(): classify_workspace(
            workspace, overrides.get(str(workspace.get("id") or "").lower())
        )
        for workspace in workspaces
    }


def rule_applicability(
    profile: Dict[str, Any], rule_id: str, allowed_archetypes: Iterable[str],
) -> Dict[str, str]:
    """Return applicable/not_applicable/unknown with an auditable reason."""
    override = (profile.get("ruleOverrides") or {}).get(rule_id) or {}
    explicit = str(override.get("applicability") or "").lower()
    if explicit in ("applicable", "not_applicable", "unknown"):
        return {"status": explicit, "reason": str(override.get("reason") or "Explicit rule override.")}
    archetype = profile.get("archetype")
    if archetype in ("mixed", "unknown"):
        return {"status": "unknown", "reason": str(profile.get("reason") or "Workspace purpose is ambiguous.")}
    if archetype in set(allowed_archetypes):
        return {"status": "applicable", "reason": f"Rule applies to {archetype} workspaces."}
    return {"status": "not_applicable", "reason": f"Rule does not apply to {archetype} workspaces."}


def production_scope(
    workspaces: Iterable[Dict[str, Any]], profiles: Dict[str, Dict[str, Any]],
    *, require_content: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Split shared workspaces into production, excluded, and unknown environment scope."""
    scoped: Dict[str, List[Dict[str, Any]]] = {"applicable": [], "not_applicable": [], "unknown": []}
    for workspace in workspaces:
        profile = profiles[str(workspace.get("id") or "").lower()]
        archetype = profile.get("archetype")
        if archetype in ("personal", "empty"):
            scoped["not_applicable"].append(workspace)
        elif require_content and not profile.get("itemTypeCounts"):
            scoped["not_applicable"].append(workspace)
        elif profile.get("environment") == "production":
            scoped["applicable"].append(workspace)
        elif profile.get("environment") == "unknown":
            scoped["unknown"].append(workspace)
        else:
            scoped["not_applicable"].append(workspace)
    return scoped


def applicability_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(row["applicability"]["status"] for row in rows)
    return {
        "applicableCount": counts["applicable"],
        "notApplicableCount": counts["not_applicable"],
        "unknownCount": counts["unknown"],
        "workspaces": rows,
    }
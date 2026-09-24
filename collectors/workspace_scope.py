# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evidence-based workspace/capacity scope, shared by collection and review."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable
from collectors._http import HttpError


def is_admin_workspace(workspace: dict) -> bool:
    """Only an observed AdminWorkspace type excludes the built-in workspace."""
    return str(workspace.get("type") or "").strip().casefold() == "adminworkspace"


def is_excluded_capacity(capacity: dict) -> bool:
    """PP3 is the virtual PPU capacity; P, A, EM and F SKUs remain supported."""
    sku = capacity.get("sku")
    if isinstance(sku, dict):
        sku = sku.get("name")
    return str(sku or "").strip().upper() in {"PP3", "PPU", "PRO", "SHARED"}


def is_excluded_workspace(workspace: dict, capacity_ids=(), *, operational: bool = False) -> bool:
    """Exclude only positive metadata, never names, absent fields or user licenses."""
    state = str(workspace.get("state") or "").strip().casefold()
    return (
        (not operational and is_admin_workspace(workspace))
        or state in {"deleted", "removing", "deleting", "inactive", "notactive"}
        or workspace.get("isOnDedicatedCapacity") is False
        or workspace.get("isOnPremiumPerUserCapacity") is True
        or is_excluded_capacity({"sku": workspace.get("capacitySku")})
        or str(workspace.get("capacityId") or "").strip().lower() in capacity_ids
    )


def _merge_workspace_metadata(previous: dict, fresh: dict) -> dict:
    merged = dict(previous)
    capacity_id = str(fresh.get("capacityId") or "").strip().lower()
    if capacity_id and capacity_id != str(previous.get("capacityId") or "").strip().lower():
        # These observations describe the old assignment, not the workspace
        # permanently. Unknown/absent assignments do not revoke prior evidence.
        for field in ("capacitySku", "isOnDedicatedCapacity", "isOnPremiumPerUserCapacity"):
            merged.pop(field, None)
    merged.update({key: value for key, value in fresh.items() if value is not None})
    return merged


def _scope_records(raw_dir: Path) -> tuple[list[dict], list[dict]]:
    workspaces, capacities, current = [], [], []
    for name in ("review_scope.json", "capacity_metrics.json", "workspace_inventory.json",
                 "scanner.json", "semantic_models.json"):
        try:
            data = json.loads((raw_dir / name).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise HttpError(f"Scope metadata {name} could not be read") from exc
        if not isinstance(data, dict):
            raise HttpError(f"Scope metadata {name} must be an object")
        for key in ("workspaces", "excludedWorkspaces"):
            if data.get(key) is not None and not isinstance(data[key], list):
                raise HttpError(f"Scope metadata {name}.{key} must be a list")
            target = current if name == "review_scope.json" else workspaces
            target.extend(w for w in data.get(key) or [] if isinstance(w, dict))
        for key in ("capacities", "excludedCapacities"):
            if data.get(key) is not None and not isinstance(data[key], list):
                raise HttpError(f"Scope metadata {name}.{key} must be a list")
            capacities.extend(c for c in data.get(key) or [] if isinstance(c, dict))
    authoritative = {str(w.get("id") or w.get("objectId") or "").lower(): w for w in current}
    workspaces = [
        _merge_workspace_metadata(w, authoritative.get(
            str(w.get("id") or w.get("objectId") or "").lower(), {},
        ))
        for w in workspaces
    ]
    return [*workspaces, *current], capacities


def excluded_capacity_ids(raw_dir: Path) -> set[str]:
    return {str(c.get("id") or c.get("capacityId")).strip().lower()
            for c in _scope_records(raw_dir)[1]
            if is_excluded_capacity(c) and (c.get("id") or c.get("capacityId"))}


def _persist_scope(raw_dir: Path, workspaces: list[dict], capacities: list[dict]) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    fields = ("id", "objectId", "type", "state", "capacityId", "capacitySku",
              "isOnDedicatedCapacity", "isOnPremiumPerUserCapacity")
    identities: dict[str, dict] = {}
    for w in workspaces:
        identity = w.get("id") or w.get("objectId")
        if identity:
            key = str(identity).lower()
            identities[key] = _merge_workspace_metadata(
                identities.get(key, {}),
                {field: w[field] for field in fields if w.get(field) is not None},
            )
    (raw_dir / "review_scope.json").write_text(json.dumps({
        "workspaces": list(identities.values()), "capacities": capacities,
    }, indent=2), encoding="utf-8")


def resolve_workspace_scope(workspaces: list[dict], headers, raw_dir: Path | None = None) -> list[dict]:
    """Resolve otherwise ambiguous capacity assignments once, before child probes.

    Persist metadata only, including exclusions, so partial and historical child
    evidence cannot reintroduce unsupported resources. A failed required
    discovery propagates; it must not be mistaken for an empty capacity list.
    """
    from collectors.capacity_metrics import _list_capacities

    previous, capacities = _scope_records(raw_dir) if raw_dir is not None else ([], [])
    known = {str(c.get("id") or c.get("capacityId") or "").lower() for c in capacities}
    unresolved = any(
        w.get("capacityId") and str(w["capacityId"]).lower() not in known
        and w.get("isOnPremiumPerUserCapacity") is not False
        and not is_excluded_workspace(w, operational=True)
        for w in workspaces
    )
    if unresolved:
        if raw_dir is not None:
            _persist_scope(raw_dir, [*previous, *workspaces], capacities)
        capacities = _list_capacities(headers)
    capacity_skus = {str(c.get("id") or c.get("capacityId") or "").lower(): c["sku"]
                     for c in capacities if c.get("sku") is not None}
    resolved = [
        {**w, **({"capacitySku": capacity_skus[str(w.get("capacityId")).lower()]}
                if str(w.get("capacityId") or "").lower() in capacity_skus else {})}
        for w in workspaces
    ]
    if raw_dir is not None:
        _persist_scope(raw_dir, [*previous, *resolved], capacities)
    return resolved


def excluded_workspace_identities(workspaces: Iterable[dict]) -> list[dict]:
    """Retain policy metadata so ID-only downstream catalogs stay excluded."""
    fields = ("type", "state", "capacityId", "capacitySku",
              "isOnDedicatedCapacity", "isOnPremiumPerUserCapacity")
    return [
        {"id": workspace.get("id") or workspace.get("objectId"),
         **{key: workspace[key] for key in fields if key in workspace}}
        for workspace in workspaces
        if is_excluded_workspace(workspace) and (workspace.get("id") or workspace.get("objectId"))
    ]


def excluded_workspace_ids(raw_dir: Path, *, operational: bool = False) -> set[str]:
    """Use observed identities even if an inventory's child probes failed."""
    workspaces, catalog = _scope_records(raw_dir)
    capacities = {str(c.get("id") or c.get("capacityId") or "").lower()
                  for c in catalog if is_excluded_capacity(c)}
    return {str(w.get("id") or w.get("objectId")).strip().lower()
            for w in workspaces
            if is_excluded_workspace(w, capacities, operational=operational)
            and (w.get("id") or w.get("objectId"))}


def filter_review_payload(data: Any, raw_dir: Path) -> Any:
    """Remove excluded workspace records/references without changing raw files.

    Tenant-level records without excluded identities remain untouched.
    """
    excluded = excluded_workspace_ids(raw_dir)
    excluded_caps = excluded_capacity_ids(raw_dir)
    excluded_models: set[str] = set()
    if isinstance(data, dict):
        source = data.get("dataset")
        if isinstance(source, dict) and "queries" in data:
            source_workspace = str(source.get("workspaceId") or source.get("groupId") or "").lower()
            if (source_workspace in excluded_workspace_ids(raw_dir, operational=True)
                    or is_excluded_workspace(source, excluded_caps, operational=True)):
                return {**data, "datasetLocated": False, "dataset": None, "queries": {},
                        "skipped": True, "notes": ["Metrics source is outside supported capacity/workspace scope."]}
        for capacity in data.get("capacities") or []:
            if is_excluded_capacity(capacity):
                excluded_caps.add(str(capacity.get("id") or capacity.get("capacityId") or "").lower())
        for key in ("workspaces", "excludedWorkspaces"):
            for workspace in data.get(key) or []:
                if isinstance(workspace, dict) and is_excluded_workspace(workspace, excluded_caps):
                    identity = workspace.get("id") or workspace.get("objectId")
                    if identity:
                        excluded.add(str(identity).strip().lower())
        for key in ("datasets", "models"):
            for model in data.get(key) or []:
                if isinstance(model, dict) and str(
                    model.get("workspaceId") or model.get("groupId") or ""
                ).lower() in excluded:
                    excluded_models.add(str(model.get("id") or "").lower())

    def visit(value: Any, collection: str = "") -> Any:
        if isinstance(value, list):
            result = []
            for row in value:
                if isinstance(row, dict):
                    evidence = row.get("evidence")
                    if collection == "findings" and isinstance(evidence, dict):
                        if str(evidence.get("workspaceId") or evidence.get("workspace_id") or "").lower() in excluded:
                            continue
                        if str(evidence.get("capacityId") or evidence.get("capacity_id") or "").lower() in excluded_caps:
                            continue
                    identity = next((row[key] for key in (
                        "workspaceId", "workspace_id", "WorkspaceId", "groupId",
                    ) if row.get(key)), None)
                    if collection == "refreshables" and isinstance(row.get("group"), dict):
                        identity = identity or row["group"].get("id")
                    if collection in ("workspaces", "assignedWorkspaces"):
                        identity = identity or row.get("id") or row.get("objectId")
                        if is_excluded_workspace(row, excluded_caps):
                            continue
                    capacity_id = row.get("capacityId") or row.get("capacity_id") or row.get("CapacityId")
                    if collection == "capacities":
                        capacity_id = capacity_id or row.get("id")
                        if is_excluded_capacity(row):
                            continue
                    if capacity_id and str(capacity_id).strip().lower() in excluded_caps:
                        continue
                    if identity and str(identity).strip().lower() in excluded:
                        continue
                elif collection == "assignedWorkspaceIds" and str(row).strip().lower() in excluded:
                    continue
                result.append(visit(row))
            return result
        if isinstance(value, dict):
            if collection in ("refreshes", "refreshErrors"):
                return {key: visit(item) for key, item in value.items() if key.lower() not in excluded_models}
            if collection == "stageArtifacts":
                return {key: visit(item) for key, item in value.items()}
            if isinstance(value.get("stages"), list) and isinstance(value.get("stageArtifacts"), dict):
                allowed_orders = {str(stage.get("order")) for stage in visit(value["stages"], "stages")}
                value = {**value, "stageArtifacts": {key: item for key, item in value["stageArtifacts"].items()
                                                    if key in allowed_orders}}
            result = {
                key: item if key in ("excludedWorkspaces", "excludedCapacities") else visit(item, key)
                for key, item in value.items()
            }
            for items_key, count_key in (("assignedWorkspaces", "assignedWorkspaceCount"),
                                         ("refreshables", "refreshableCount"), ("stages", "stageCount")):
                if isinstance(result.get(items_key), list) and count_key in result:
                    result[count_key] = len(result[items_key])
            return result
        return value

    return visit(data)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure normalization for workspace evidence from Scanner and Fabric REST."""
from __future__ import annotations

from typing import Any, Iterable


ITEM_BUCKET_TYPES = {
    "datasets": "SemanticModel", "semanticModels": "SemanticModel", "SemanticModel": "SemanticModel",
    "reports": "Report", "Report": "Report",
    "dashboards": "Dashboard", "Dashboard": "Dashboard",
    "dataflows": "Dataflow", "Dataflow": "Dataflow", "Dataflow2": "Dataflow2", "DataflowGen2": "Dataflow2",
    "lakehouses": "Lakehouse", "Lakehouse": "Lakehouse",
    "warehouses": "Warehouse", "Warehouse": "Warehouse",
    "notebooks": "Notebook", "Notebook": "Notebook",
    "pipelines": "DataPipeline", "dataPipelines": "DataPipeline", "DataPipeline": "DataPipeline",
    "kqlDatabases": "KQLDatabase", "KQLDatabase": "KQLDatabase",
    "mlModels": "MLModel", "MLModel": "MLModel",
    "mlExperiments": "MLExperiment", "MLExperiment": "MLExperiment",
    "Eventstream": "Eventstream", "Eventhouse": "Eventhouse",
    "MirroredDatabase": "MirroredDatabase", "Reflex": "Reflex",
    "datamarts": "Datamart", "SQLAnalyticsEndpoint": "SQLEndpoint",
}
_ITEM_TYPES = {value.lower(): value for value in ITEM_BUCKET_TYPES.values()}
_ITEM_TYPES.update({"dataset": "SemanticModel", "paginatedreport": "Report",
                    "sqlanalyticsendpoint": "SQLEndpoint", "dataflowgen2": "Dataflow2"})
_NON_ITEM_LISTS = {
    "users", "folders", "workbooks", "dashboardtiles", "widgets", "roles",
    "roleassignments", "datasourceinstances", "datasourceusages", "relations",
}


def _item_buckets(workspace: dict[str, Any]) -> Iterable[tuple[str, list]]:
    for key, value in workspace.items():
        if not isinstance(value, list) or key.lower() in _NON_ITEM_LISTS or key == "items":
            continue
        if key in ITEM_BUCKET_TYPES or (key[:1].isupper() and all(
            isinstance(item, dict) and (item.get("id") or item.get("objectId")) for item in value
        )):
            yield ITEM_BUCKET_TYPES.get(key, key), value


def workspace_items(workspace: dict[str, Any], item_types: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Union flat and typed items by case-insensitive ID, retaining Scanner detail."""
    normalized: dict[str, dict[str, Any]] = {}
    flat = workspace.get("items")
    buckets = [("", flat)] if isinstance(flat, list) and workspace.get("itemsCollectionStatus") in (None, "collected") else []
    buckets.extend(_item_buckets(workspace))
    for bucket_type, values in buckets:
        for item in values:
            raw_type = bucket_type or str(item.get("type") or item.get("itemType") or "").strip()
            item_type = _ITEM_TYPES.get(raw_type.lower(), raw_type)
            identity = item.get("id") or item.get("objectId")
            key = str(identity).lower() if identity else f"anonymous:{len(normalized)}"
            previous = normalized.get(key, {})
            row = {**previous, **{k: v for k, v in item.items() if v is not None}}
            row["type"] = item_type or previous.get("type") or "Unknown"
            if identity:
                row["id"] = identity
            row["name"] = item.get("name") or item.get("displayName") or previous.get("name")
            if bucket_type:
                row["_scannerMetadata"] = True
            if item_type in ("Dataflow", "Dataflow2"):
                row["_dataflowGeneration"] = 1 if values is workspace.get("dataflows") else 2
            normalized[key] = row
    allowed = set(item_types) if item_types is not None else None
    return [item for item in normalized.values() if allowed is None or item["type"] in allowed]


def workspace_users(workspace: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Return distinct collected principals; None means membership is unavailable."""
    users = workspace.get("users")
    if not isinstance(users, list) or workspace.get("usersCollectionStatus") not in (None, "collected"):
        return None
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, user in enumerate(users):
        principal = user.get("principal") or {}
        kind = user.get("principalType") or user.get("type") or principal.get("type") or ""
        identity = (user.get("graphId") or principal.get("id") or user.get("identifier")
                    or user.get("emailAddress") or user.get("userPrincipalName"))
        kind = str(kind).lower()
        if kind == "serviceprincipal":
            kind = "app"
        key = (kind, str(identity).lower() if identity else f"anonymous:{index}")
        previous = result.get(key)
        if previous is None:
            result[key] = dict(user)
        elif str(previous.get("groupUserAccessRight") or previous.get("role") or "").lower() != str(
            user.get("groupUserAccessRight") or user.get("role") or ""
        ).lower():
            result[key]["_roleConflict"] = True
    return list(result.values())


def workspace_items_available(workspace: dict[str, Any]) -> bool:
    """Explicit failed REST probes are not evidence that a workspace is empty."""
    return (
        workspace.get("itemsCollectionStatus") in (None, "collected")
        and ("items" not in workspace or isinstance(workspace["items"], list))
    ) or any(_item_buckets(workspace))


def item_metadata_available(item: dict[str, Any], *fields: str) -> bool:
    """Basic REST item identity alone does not establish optional governance metadata."""
    return bool(item.get("_scannerMetadata")) or any(field in item for field in fields)


def workspace_roles_complete(workspace: dict[str, Any]) -> bool:
    users = workspace_users(workspace)
    return users is not None and all(
        not user.get("_roleConflict") and str(user.get("groupUserAccessRight") or user.get("role") or "").lower()
        in ("admin", "member", "contributor", "viewer") for user in users
    )


def merge_workspace_evidence(
    scanner: Iterable[dict[str, Any]], inventory: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join workspace IDs, not names; never replace known children with failed probes."""
    merged: dict[str, dict[str, Any]] = {}
    for workspace in scanner:
        identity = workspace.get("id")
        if identity:
            merged[str(identity).lower()] = dict(workspace)
    for workspace in inventory:
        identity = workspace.get("id")
        if not identity:
            continue
        key = str(identity).lower()
        previous = merged.get(key, {})
        row = {**previous, **{k: v for k, v in workspace.items()
                             if v is not None and k not in ("users", "usersCollectionStatus", "items", "itemsCollectionStatus")}}
        for component in ("users", "items"):
            status_key = f"{component}CollectionStatus"
            values = workspace.get(component)
            if isinstance(values, list) and workspace.get(status_key) in (None, "collected"):
                row[component] = values
                row[status_key] = "collected"
            elif component == "items" and any(_item_buckets(previous)):
                continue
            elif not (isinstance(previous.get(component), list) and previous.get(status_key) in (None, "collected")):
                if component in workspace or status_key in workspace:
                    row[component] = None
                    row[status_key] = "unavailable"
        merged[key] = row
    return list(merged.values())

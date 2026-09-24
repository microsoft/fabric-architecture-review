# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Organize explicitly identified FAR artifacts without replacing any items."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode

from orchestration.fabric_api import FabricClient, collection, guid


FOLDERS = ("Reporting", "Pipelines", "Ontology", "Agents", "Notebooks")


def find_item(client: FabricClient, workspace_id: str, name: str,
              item_type: str) -> dict[str, Any] | None:
    rows = collection(client, f"/workspaces/{guid(workspace_id)}/items?"
                      + urlencode({"type": item_type}))
    matches = [row for row in rows if row.get("displayName") == name]
    if len(matches) > 1:
        raise ValueError(f"Multiple {item_type} items share the configured FAR name {name!r}.")
    if not matches:
        return None
    item = matches[0]
    guid(item["id"])
    if item.get("type") != item_type:
        raise ValueError("Configured FAR item has an unexpected type.")
    return item


def organize(client: FabricClient, workspace_id: str,
             artifacts: Iterable[tuple[str, str, str | None]], *,
             create_all: bool = False) -> None:
    """Move (established item ID, expected type, root folder or None) in place."""
    workspace_id = guid(workspace_id)
    planned: dict[str, tuple[str, str | None]] = {}
    for item_id, item_type, folder in artifacts:
        if folder is not None and folder not in FOLDERS:
            raise ValueError("Unknown FAR root folder.")
        item_id = guid(item_id)
        if item_id in planned and planned[item_id] != (item_type, folder):
            raise ValueError("Conflicting FAR folder assignments for the same item.")
        planned[item_id] = (item_type, folder)
    base = f"/workspaces/{workspace_id}"
    items = {}
    for item_id, (item_type, _) in planned.items():
        item = client.get_json(f"{base}/items/{item_id}")
        if (not isinstance(item, dict) or item.get("id") != item_id
                or item.get("type") != item_type):
            raise ValueError("FAR item identity/type could not be verified; no items moved.")
        if item.get("folderId") is not None:
            guid(item["folderId"])
        items[item_id] = item
    required = set(FOLDERS) if create_all else {f for _, f in planned.values() if f}
    folders = collection(client, base + "/folders?recursive=false") if required else []
    resolved = {}
    for name in sorted(required):
        matches = [f for f in folders if f.get("displayName") == name
                   and f.get("parentFolderId") is None]
        if len(matches) > 1:
            raise ValueError(f"Multiple root folders named {name!r}.")
        if matches:
            resolved[name] = guid(matches[0]["id"])
        else:
            response = client.request("POST", base + "/folders", json_body={"displayName": name})
            if response.status_code != 201:
                raise RuntimeError("Fabric folder creation did not return 201 Created.")
            folder = response.json()
            if folder.get("displayName") != name or folder.get("parentFolderId") is not None:
                raise ValueError("Fabric did not create the requested root folder.")
            resolved[name] = guid(folder["id"])
    for item_id, (_, name) in planned.items():
        target = resolved[name] if name else None
        if items[item_id].get("folderId") == target:
            continue
        response = client.request(
            "POST", f"{base}/items/{item_id}/move",
            json_body={"targetFolderId": target} if target else {},
        )
        if response.status_code != 200:
            raise RuntimeError("Fabric item move did not return 200 OK.")
        moved = response.json().get("value")
        if not isinstance(moved, list) or not any(
            isinstance(row, dict) and row.get("id") == item_id
            and row.get("folderId") == target for row in moved
        ):
            raise ValueError("Fabric move response did not confirm the requested item destination.")


def organize_existing(client: FabricClient, workspace_id: str,
                      configured: Iterable[tuple[str, str, str | None]], *,
                      known: Iterable[tuple[str, str, str | None]] = ()) -> None:
    """Adopt only exact configured deployment identities, never a type-wide sweep."""
    artifacts = list(known)
    known_ids = {guid(item_id) for item_id, _, _ in artifacts}
    for name, item_type, folder in configured:
        item = find_item(client, workspace_id, name, item_type)
        if item is not None and item["id"] not in known_ids:
            artifacts.append((item["id"], item_type, folder))
            known_ids.add(item["id"])
    organize(client, workspace_id, artifacts, create_all=True)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Retry refused evidence in the current capture without re-reading successful items."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from collectors._http import HttpError, request_deadline
from collectors.auth import FABRIC_SCOPE, get_default_provider


def failure_status(row: dict[str, Any]) -> int | None:
    status = row.get("statusCode")
    if isinstance(status, int):
        return status
    error = row.get("error")
    if error == "not_found":
        return 404
    match = re.fullmatch(r"(?:http_|lro_poll_http_|insufficient_permissions \()(\d{3})\)?", str(error))
    return int(match.group(1)) if match else None


def failure_kind(row: dict[str, Any]) -> str:
    code = str(row.get("errorCode") or "").lower()
    if ("unsupported" in code or "notsupported" in code
            or code in {"invalidtoken", "tokenexpired", "invalidauthenticationtoken", "invalidaudience"}):
        return "other"
    status = failure_status(row)
    if status in (401, 403):
        return "access"
    if status == 404:
        return "not_found"
    if status == 429 or (status is not None and 500 <= status < 600):
        return "transient"
    return "other"


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".retry.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def retry_failed(path: Path, workspace_ids: set[str], collector: str, deadline: float) -> Path:
    """Only granted workspaces' access failures are retried; HTTP handles transient retries."""
    from collectors import (
        git_integration, onelake_roles, pipeline_definitions, pipelines_notebooks,
        semantic_model_definitions, workspace_network,
    )
    name = collector.removeprefix("collectors.")
    collections = {
        "semantic_model_definitions": ("models",), "pipeline_definitions": ("pipelines", "notebooks"),
        "git_integration": ("workspaces",), "onelake_roles": ("lakehouses",),
        "workspace_network": ("workspaces",), "pipelines_notebooks": ("inventoryErrors",),
    }
    if name not in collections:
        raise ValueError(f"No selective retry contract for {collector}")
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Selective evidence retry requires an object capture.")
    scope = {ws.lower() for ws in workspace_ids}
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)
    targets: list[tuple[str, int, dict[str, Any]]] = []
    for collection in collections[name]:
        for index, row in enumerate(payload.get(collection, [])):
            if not isinstance(row, dict) or str(row.get("workspaceId") or "").lower() not in scope:
                continue
            failures = [row]
            if name in {"onelake_roles", "workspace_network"}:
                failures = [error for error in payload.get("collectionErrors", [])
                            if error.get("workspaceId") == row.get("workspaceId")
                            and (name != "onelake_roles" or error.get("id") == row.get("id"))]
            if any(failure_kind(error) in {"access", "not_found"} for error in failures):
                targets.append((collection, index, row))
    print(f"{collector}: retrying {len(targets)} refused item/endpoint(s); successful evidence retained.")
    inventory_done: set[tuple[str, str]] = set()
    try:
        with request_deadline(deadline):
            for collection, index, row in targets:
                if time.monotonic() >= deadline:
                    raise HttpError("Evidence retry time limit reached", error_code="RetryDeadlineExceeded")
                wsid = row["workspaceId"]
                if name == "semantic_model_definitions":
                    payload[collection][index] = semantic_model_definitions.refetch_model(row, headers)
                elif name == "pipeline_definitions":
                    definition, error = pipeline_definitions._get_definition(headers, wsid, row["id"])
                    payload[collection][index] = pipeline_definitions._normalise(
                        row, definition, error, notebook=collection == "notebooks",
                    )
                elif name == "git_integration":
                    payload[collection][index] = git_integration.read_connection(wsid, row.get("workspaceName", ""), headers)
                elif name in {"onelake_roles", "workspace_network"}:
                    if name == "onelake_roles":
                        fresh, errors = onelake_roles.read_roles(wsid, row["id"], headers)
                    else:
                        fresh, errors = workspace_network.read_policy(wsid, headers)
                    payload[collection][index] = fresh
                    payload["collectionErrors"] = [
                        error for error in payload.get("collectionErrors", [])
                        if not (error.get("workspaceId") == wsid
                                and (name != "onelake_roles" or error.get("id") == row.get("id")))
                    ] + errors
                else:
                    item_type = row["itemType"]
                    key = (wsid, item_type)
                    if key in inventory_done:
                        continue
                    inventory_done.add(key)
                    fresh_items, errors = pipelines_notebooks.read_inventory(
                        wsid, row.get("workspaceName", ""), item_type, headers,
                    )
                    destination = {"DataPipeline": "pipelines", "Notebook": "notebooks"}[item_type]
                    # Preserve observed items from a partial page until a complete listing replaces them.
                    old = [item for item in payload[destination] if item.get("workspaceId") == wsid]
                    if errors:
                        identities = {item.get("id") for item in fresh_items if item.get("id")}
                        fresh_items += [item for item in old if item.get("id") not in identities]
                    payload[destination] = [
                        item for item in payload[destination] if item.get("workspaceId") != wsid
                    ] + fresh_items
                    payload["inventoryErrors"] = [
                        error for error in payload["inventoryErrors"]
                        if (error.get("workspaceId"), error.get("itemType")) != key
                    ] + errors
    finally:
        if name in {"semantic_model_definitions", "pipeline_definitions"}:
            payload["errors"] = sum(bool(row.get("error")) for key in collections[name] for row in payload.get(key, []))
            source_complete = (payload.get("status") == "completed" if name == "semantic_model_definitions"
                               else payload.get("sourceCollectionComplete") is True)
            payload["collectionComplete"] = source_complete and payload["errors"] == 0
        elif name == "pipelines_notebooks":
            payload["failedWorkspaces"] = sorted({row["workspaceId"] for row in payload["inventoryErrors"]})
            payload["collectionComplete"] = not payload["inventoryErrors"] and not payload.get("collectionErrors")
        elif name in {"onelake_roles", "workspace_network"}:
            payload["collectionComplete"] = not payload["collectionErrors"]
        _write(path, payload)
    return path

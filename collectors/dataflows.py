# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Collect native Dataflow Gen2 metadata, with no FUAM dependency.

Run ``python -m collectors.dataflows --output-dir output/raw`` after workspace
inventory / scanner. WORKSPACE_IDS is honored. Output: dataflows.json schema 1,
with workspaces (id, name, inventoryStatus), dataflows (id, displayName,
workspaceId, workspaceName, definitionStatus, noticeCodes, queries), and
collectionComplete (inventory completeness, NOT definition completeness).

GET /workspaces/{id}/dataflows provides the native inventory; the shared item
getDefinition helper supplies POST plus LRO polling. POST retrieves metadata
only: there are no refresh, publish, query-execution or customer-row APIs.
Definition parts are decoded for static analysis in memory and discarded.
Even raw output contains no M source, literal data, connections or API errors.

List requires Viewer / Workspace.Read.All. Definition requires read AND write
item permission / Dataflow.ReadWrite.All or Item.ReadWrite.All. Typed REST
references currently support service principals and managed identities, while
the older public-API guide still lists limitations: do not infer availability
from identity type or relabel legacy scanner dataflows. Failures remain visible.

https://learn.microsoft.com/rest/api/fabric/dataflow/items/list-dataflows
https://learn.microsoft.com/rest/api/fabric/dataflow/items/get-dataflow-definition
https://learn.microsoft.com/fabric/data-factory/dataflow-gen2-public-apis
"""
from __future__ import annotations

from collectors.workspace_scope import excluded_workspace_ids

import argparse
import json
import os
from pathlib import Path

from collectors._common import (
    filter_workspaces_by_scope, get_scope_workspace_ids, load_workspace_inventory,
)
from collectors.workspace_evidence import workspace_items
from collectors._http import Headers, HttpError, paginate_value
from collectors.auth import FABRIC_SCOPE, get_default_provider
from collectors.pipeline_definitions import FAB, _get_definition
from reports.dataflow_evidence import inspect_definition, safe_id, safe_name


def _definition(headers: Headers, workspace_id: str, item_id: str) -> dict:
    try:
        payload, error = _get_definition(headers, workspace_id, item_id)
    except (ValueError, TypeError, AttributeError):
        return {"definitionStatus": "unavailable", "noticeCodes": ["PARSE_GAP"], "queries": []}
    if error:
        status = "forbidden" if error in ("http_401", "http_403") else "unavailable"
        # Never propagate helper errors: LRO errors may contain service text.
        return {"definitionStatus": status,
                "noticeCodes": ["PERMISSION_DENIED" if status == "forbidden" else "DEFINITION_UNAVAILABLE"],
                "queries": []}
    return inspect_definition(payload)


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "dataflows.json"
    result: dict = {"schemaVersion": 1, "metadataOnly": True, "collectionComplete": True,
                    "workspaces": [], "dataflows": []}
    try:
        inventory = load_workspace_inventory(raw_dir)
        if not all(isinstance(workspace, dict) for workspace in inventory):
            raise ValueError("invalid workspace inventory")
        workspaces = filter_workspaces_by_scope(inventory)
        scoped_ids = get_scope_workspace_ids()
        observed_ids = {safe_id(workspace.get("id")).lower() for workspace in workspaces}
        for workspace_id in sorted(scoped_ids - observed_ids - excluded_workspace_ids(raw_dir)):
            workspaces.append({"id": workspace_id})
    except (HttpError, ValueError):
        workspaces = []
        result["collectionComplete"] = False
        print("Dataflow inventory unavailable: collect a complete workspace inventory first.")
    provider = get_default_provider() if workspaces else None

    def headers() -> dict[str, str]:
        if provider is None:
            raise HttpError("Authentication unavailable without workspace inventory")
        return provider.headers(FABRIC_SCOPE)

    seen_workspaces: set[str] = set()
    for workspace in workspaces:
        workspace_id = safe_id(workspace.get("id"))
        if not workspace_id:
            result["collectionComplete"] = False
            continue
        if workspace_id in seen_workspaces:
            continue
        seen_workspaces.add(workspace_id)
        name = safe_name(workspace.get("name") or workspace.get("displayName") or "")
        coverage = {"id": workspace_id, "name": name, "inventoryStatus": "available"}
        result["workspaces"].append(coverage)
        items: dict[str, dict] = {}
        # Legacy Scanner dataflows remain Gen1, never Gen2 definition candidates.
        for item in workspace_items(workspace, ("Dataflow", "Dataflow2")):
            if item["_dataflowGeneration"] == 2 and safe_id(item.get("id")):
                items[safe_id(item["id"]).lower()] = item
        try:
            for item in paginate_value(f"{FAB}/workspaces/{workspace_id}/dataflows", headers):
                item_id = safe_id(item.get("id"))
                if not item_id or (item.get("workspaceId") and safe_id(item["workspaceId"]) != workspace_id):
                    coverage["inventoryStatus"] = "partial"
                    result["collectionComplete"] = False
                    continue
                items[item_id.lower()] = item
        except HttpError:
            coverage["inventoryStatus"] = "partial" if items else "unavailable"
            result["collectionComplete"] = False
            print("Dataflow inventory incomplete for an in-scope workspace; coverage gap retained.")
        for _, item in sorted(items.items()):
            item_id = safe_id(item["id"])
            record = {"id": item_id, "displayName": safe_name(item.get("displayName") or item.get("name") or ""),
                      "workspaceId": workspace_id, "workspaceName": name,
                      **_definition(headers, workspace_id, item_id)}
            result["dataflows"].append(record)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    unavailable = sum(item["definitionStatus"] != "inspected" for item in result["dataflows"])
    print(f"Dataflows: {len(result['dataflows'])} items; {unavailable} definition/analysis gaps.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

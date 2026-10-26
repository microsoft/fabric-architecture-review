# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Architecture-only Pipeline + Notebook inventory.

Endpoints:
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/dataPipelines
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/notebooks

Docs: https://learn.microsoft.com/rest/api/fabric/core/items/list-items

DATA SAFETY:
  - Item identity and names only; no job history, notebook outputs or activity payloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from collectors._http import HttpError, paginate_value
from collectors._common import (
    filter_workspaces_by_scope, get_scope_workspace_ids,
    load_workspace_inventory, record_collection_failure,
)
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    return [(w["id"], w.get("name") or "")
            for w in filter_workspaces_by_scope(load_workspace_inventory(raw_dir)) if w.get("id")]


@record_collection_failure("pipelines_notebooks.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    if not workspaces:
        print("Pipelines/Notebooks: no workspace inventory found — run scanner_api or workspace_inventory first.")
        target = raw_dir / "pipelines_notebooks.json"
        target.write_text(
            json.dumps({
                "pipelines": [], "notebooks": [], "workspaceScope": sorted(get_scope_workspace_ids()),
            }, indent=2),
            encoding="utf-8",
        )
        return target

    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)

    pipelines: List[Dict[str, Any]] = []
    notebooks: List[Dict[str, Any]] = []
    failed_workspaces: List[str] = []
    inventory_errors: List[Dict[str, Any]] = []

    print(
        f"Pipelines/Notebooks: scanning architectural inventory in {len(workspaces)} workspace(s)..."
    )
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        for endpoint, item_type, inventory in (
            ("dataPipelines", "DataPipeline", pipelines), ("notebooks", "Notebook", notebooks),
        ):
            items: List[Dict[str, Any]] = []
            try:
                items.extend(paginate_value(f"{FAB}/workspaces/{wsid}/{endpoint}", headers))
            except HttpError as exc:
                failed_workspaces.append(wsid)
                error = {
                    "workspaceId": wsid, "workspaceName": wsname, "itemType": item_type,
                    "statusCode": exc.status_code, "noticeCode": "inventory_request_failed",
                    "collectionStatus": "partial" if items else "unavailable",
                    "observedCount": len(items),
                }
                if exc.error_code:
                    error["errorCode"] = exc.error_code
                inventory_errors.append(error)
                detail = f" ({exc.error_code})" if exc.error_code else ""
                print(
                    f"  {item_type} inventory incomplete in workspace {wsid}: "
                    f"HTTP {exc.status_code}{detail}; retained {len(items)} item(s); continuing."
                )
            for item in items:
                item["workspaceId"] = wsid
                item["workspaceName"] = wsname
                inventory.append(item)
                if not item.get("id"):
                    failed_workspaces.append(wsid)
                    inventory_errors.append({
                        "workspaceId": wsid, "itemType": item_type,
                        "noticeCode": "missing_item_identity", "collectionStatus": "partial",
                    })

        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    target = raw_dir / "pipelines_notebooks.json"
    target.write_text(
        json.dumps(
            {
                "pipelines": pipelines,
                "notebooks": notebooks,
                "metadataOnly": True,
                "failedWorkspaces": sorted(set(failed_workspaces)),
                "collectionComplete": not failed_workspaces,
                "inventoryErrors": inventory_errors,
                "workspaceScope": sorted(get_scope_workspace_ids()),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    skipped = f", {len(set(failed_workspaces))} workspace(s) incomplete" if failed_workspaces else ""
    print(
        f"Wrote {target} ({len(pipelines)} pipelines, {len(notebooks)} notebooks{skipped})."
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

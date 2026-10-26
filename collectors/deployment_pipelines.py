# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Deployment pipelines and the reviewed workspaces assigned to a stage.

Preferred (administrator):
  GET https://api.powerbi.com/v1.0/myorg/admin/pipelines?$expand=stages&$top=100&$skip=N
Docs: https://learn.microsoft.com/rest/api/power-bi/admin/pipelines-get-pipelines-as-admin
Fallback (pipelines the caller can see):
  GET https://api.fabric.microsoft.com/v1/deploymentPipelines
  GET https://api.fabric.microsoft.com/v1/deploymentPipelines/{pipelineId}/stages

The administrator listing is limited to 200 requests per hour; one request covers 100 pipelines.

DATA SAFETY: Pipeline and workspace IDs and the stage order only. Names are not kept, and a stage
whose workspace is not in the review scope is dropped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import filter_workspaces_by_scope, load_workspace_inventory, record_collection_failure
from collectors._http import HttpError, collect_value, get_json
from collectors.auth import FABRIC_SCOPE, POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"
FAB = "https://api.fabric.microsoft.com/v1"
PAGE = 100


def _admin_pipelines(provider) -> List[Dict[str, Any]]:
    headers = lambda: provider.headers(scope=POWERBI_SCOPE)
    pipelines: List[Dict[str, Any]] = []
    while True:
        payload = get_json(f"{PBI}/admin/pipelines", headers,
                           params={"$expand": "stages", "$top": PAGE, "$skip": len(pipelines)})
        page = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise HttpError("The administrator pipeline listing returned an invalid page.")
        pipelines.extend(page)
        if len(page) < PAGE:
            return pipelines


def _visible_pipelines(provider) -> List[Dict[str, Any]]:
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)
    pipelines = []
    for pipeline in collect_value(f"{FAB}/deploymentPipelines", headers):
        stages = collect_value(f"{FAB}/deploymentPipelines/{pipeline['id']}/stages", headers)
        pipelines.append({"id": pipeline["id"], "stages": stages})
    return pipelines


def _reduce(pipelines: List[Dict[str, Any]], scope: set[str]) -> List[Dict[str, Any]]:
    result = []
    for pipeline in pipelines:
        stages = [{"order": stage.get("order"), "workspaceId": str(stage["workspaceId"]).lower()}
                  for stage in pipeline.get("stages") or []
                  if isinstance(stage, dict) and str(stage.get("workspaceId") or "").lower() in scope]
        if stages:
            result.append({"id": pipeline.get("id"), "stages": stages})
    return result


@record_collection_failure("deployment_pipelines.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    scope = {str(w["id"]).lower() for w in filter_workspaces_by_scope(load_workspace_inventory(raw_dir)) if w.get("id")}
    provider = get_default_provider()
    try:
        pipelines, visibility = _admin_pipelines(provider), "tenant"
    except HttpError as exc:
        if exc.status_code not in (401, 403):
            raise
        print(f"Deployment pipelines: administrator listing returned HTTP {exc.status_code}; using the pipelines the caller can see.")
        pipelines, visibility = _visible_pipelines(provider), "caller"
    target = raw_dir / "deployment_pipelines.json"
    target.write_text(json.dumps({
        "pipelines": _reduce(pipelines, scope), "pipelineCount": len(pipelines), "visibility": visibility,
        "collectionComplete": True,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(pipelines)} pipeline(s), visibility={visibility}).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

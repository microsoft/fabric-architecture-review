# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric / Power BI Deployment Pipelines inventory.

Endpoints (Power BI REST, available to Fabric Admin / pipeline admin):
  GET https://api.powerbi.com/v1.0/myorg/pipelines
  GET https://api.powerbi.com/v1.0/myorg/pipelines/{id}/stages
  GET https://api.powerbi.com/v1.0/myorg/pipelines/{id}/users    (best-effort)

Falls back gracefully when the signed-in user has no pipeline visibility.

Docs:
  https://learn.microsoft.com/rest/api/power-bi/pipelines

DATA SAFETY: Pipeline name, description, stage->workspace bindings, and
admin/contributor identifiers only. No item content is read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._http import collect_value, get_json
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"


def _list_pipelines(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    return collect_value(f"{PBI}/pipelines", headers)


def _stages(headers: Dict[str, str], pipeline_id: str) -> List[Dict[str, Any]]:
    payload = get_json(f"{PBI}/pipelines/{pipeline_id}/stages", headers)
    if not payload:
        return []
    return payload.get("value") or []


def _users(headers: Dict[str, str], pipeline_id: str) -> List[Dict[str, Any]]:
    payload = get_json(f"{PBI}/pipelines/{pipeline_id}/users", headers, allow=(200, 401, 403, 404))
    if not payload:
        return []
    return payload.get("value") or []


def _stage_artifacts(headers: Dict[str, str], pipeline_id: str, stage_order: int) -> List[Dict[str, Any]]:
    """Per-stage artifact deploy state (best-effort).

    GET /pipelines/{id}/stages/{order}/artifacts returns one block per artifact
    type (datasets, reports, ...); each artifact carries its source/target object
    ids and the last deployment time. A null ``targetArtifactId`` means the item
    was never promoted to the next stage. Tolerate permission/availability gaps.
    """
    payload = get_json(
        f"{PBI}/pipelines/{pipeline_id}/stages/{stage_order}/artifacts", headers,
        allow=(200, 401, 403, 404),
    )
    if not payload:
        return []
    out: List[Dict[str, Any]] = []
    for kind, items in payload.items():
        if not isinstance(items, list):
            continue
        for a in items:
            if not isinstance(a, dict):
                continue
            out.append({
                "artifactType": kind,
                "artifactName": a.get("artifactDisplayName") or a.get("artifactName"),
                "sourceArtifactId": a.get("sourceArtifactId"),
                "targetArtifactId": a.get("targetArtifactId"),
                "lastDeploymentTime": a.get("lastDeploymentTime"),
            })
    return out



def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)

    print("Deployment pipelines: listing...")
    pipelines = _list_pipelines(headers)
    print(f"  {len(pipelines)} pipeline(s) visible.")

    enriched: List[Dict[str, Any]] = []
    for p in pipelines:
        pid = p.get("id")
        if not pid:
            continue
        stages = _stages(headers, pid)
        users = _users(headers, pid)
        stage_artifacts: Dict[str, List[Dict[str, Any]]] = {}
        for s in stages:
            order = s.get("order")
            if order is None:
                continue
            arts = _stage_artifacts(headers, pid, order)
            if arts:
                stage_artifacts[str(order)] = arts
        enriched.append({
            "id": pid,
            "displayName": p.get("displayName") or p.get("name"),
            "description": p.get("description"),
            "stages": stages,
            "stageCount": len(stages),
            "stageArtifacts": stage_artifacts,
            "users": users,
            "assignedWorkspaceIds": [s.get("workspaceId") for s in stages if s.get("workspaceId")],
        })

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    out = target / "deployment_pipelines.json"
    out.write_text(json.dumps({
        "pipelineCount": len(enriched),
        "pipelines": enriched,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}.")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

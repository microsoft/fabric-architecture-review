# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fetch full pipeline and notebook definitions via the Fabric ``getDefinition`` API.

Endpoints (Long Running Operation):
  POST https://api.fabric.microsoft.com/v1/workspaces/{ws}/items/{itemId}/getDefinition
  GET  https://api.fabric.microsoft.com/v1/operations/{operationId}            (poll)
  GET  https://api.fabric.microsoft.com/v1/operations/{operationId}/result     (final body)

Docs:
  - https://learn.microsoft.com/rest/api/fabric/core/items/get-item-definition
  - https://learn.microsoft.com/rest/api/fabric/articles/long-running-operation

Why this exists:
  ``pipelines_notebooks.json`` only carries catalog metadata (id, displayName).
  To audit *why* a notebook can break when its pipeline parameter contract is
  wrong, we need the activity JSON of the pipeline (the ExecuteNotebook /
  TridentNotebook activities and the parameters they pass) AND the notebook
  source (so we can read its ``parameters``-tagged cell).

  Definitions are inspected in memory. Persistence contains only the pipeline
  dependency skeleton, parameter names and notebook sanitized summaries.

DATA SAFETY:
  - Never persist raw/base64 code, parameter values, secrets or cell outputs.
  - Notebook evidence is parameterNames, parameterCoverage and rule hit counts.
"""
from __future__ import annotations

from collectors.workspace_scope import filter_review_payload

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from collectors._http import Headers, HttpError, request
from collectors._common import collection_incomplete, get_scope_workspace_ids, record_collection_failure
from collectors.auth import FABRIC_SCOPE, get_default_provider
from collectors.definition_safety import notebook_summary, pipeline_summary

FAB = "https://api.fabric.microsoft.com/v1"

# Cap on poll attempts per item to avoid runaway loops.
LRO_MAX_POLLS = int(os.environ.get("PIPELINE_DEF_LRO_MAX_POLLS", "20"))
LRO_DEFAULT_RETRY_AFTER = int(os.environ.get("PIPELINE_DEF_LRO_RETRY_AFTER", "3"))
LRO_FIRST_POLL_DELAY = 2
LRO_WORKERS = 4


def _get_definition(
    headers: Headers,
    workspace_id: str,
    item_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Call getDefinition on a Fabric item, handling the 202 LRO flow.

    Returns ``(definition, error)``. Any ``HttpError`` raised after _http has
    exhausted its 429 / 5xx / connection retries is captured as a per-item
    error so one unreachable item never aborts the whole collection run.
    """
    try:
        return _get_definition_impl(headers, workspace_id, item_id)
    except HttpError as exc:
        return None, f"http_{exc.status_code or 'error'}"


def _get_definition_impl(
    headers: Headers,
    workspace_id: str,
    item_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Call getDefinition on a Fabric item, handling the 202 LRO flow.

    Returns ``(definition, error)``. ``definition`` is the response body
    (with ``definition.parts``) when successful.
    """
    url = f"{FAB}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
    r = request("POST", url, headers)

    if r.status_code == 200 and r.content:
        return r.json(), None
    if r.status_code in (401, 403):
        return None, f"http_{r.status_code}"
    if r.status_code == 404:
        return None, "not_found"

    if r.status_code != 202:
        return None, f"http_{r.status_code}"

    # Long-running operation: poll Location until terminal, then fetch result.
    op_url = r.headers.get("Location")
    if not op_url:
        return None, "lro_missing_location"
    # The service advertises a long Retry-After even when the operation finishes within a
    # second, so the first poll is quick and later polls honor the advertised delay.
    retry_after = int(r.headers.get("Retry-After") or LRO_DEFAULT_RETRY_AFTER)
    delay = min(retry_after, LRO_FIRST_POLL_DELAY)

    for _ in range(LRO_MAX_POLLS):
        time.sleep(max(1, delay))
        pr = request("GET", op_url, headers)
        if pr.status_code in (401, 403):
            return None, f"http_{pr.status_code}"
        if pr.status_code != 200 or not pr.content:
            return None, f"lro_poll_http_{pr.status_code}"
        body = pr.json()
        status = (body.get("status") or "").lower()
        if status == "succeeded":
            # The result is exposed either inline or via a separate /result endpoint.
            result_url = op_url.rstrip("/") + "/result"
            rr = request("GET", result_url, headers)
            if rr.status_code == 200 and rr.content:
                return rr.json(), None
            # Some operations embed the result directly.
            if "definition" in body:
                return body, None
            return None, "lro_result_unavailable"
        if status in ("failed", "canceled"):
            return None, f"lro_{status}"
        delay = int(pr.headers.get("Retry-After") or retry_after)

    return None, "lro_timeout"


def _normalise(
    item: Dict[str, Any],
    definition: Optional[Dict[str, Any]],
    error: Optional[str],
    *,
    notebook: bool = False,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "id": item.get("id"),
        "displayName": item.get("displayName"),
        "workspaceId": item.get("workspaceId"),
        "workspaceName": item.get("workspaceName"),
    }
    if error:
        rec["error"] = error
        rec.update(notebook_summary(None) if notebook else pipeline_summary(None))
        return rec
    rec.update(notebook_summary(definition) if notebook else pipeline_summary(definition))
    return rec


@record_collection_failure("pipeline_definitions.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "pipeline_definitions.json"

    src = raw_dir / "pipelines_notebooks.json"
    if not src.exists():
        raise HttpError(f"Required inventory {src.name} was not collected")
    catalog = filter_review_payload(json.loads(src.read_text(encoding="utf-8-sig")), raw_dir)
    if not isinstance(catalog, dict) or any(
        not isinstance(catalog.get(key), list)
        or any(not isinstance(row, dict) for row in catalog[key])
        for key in ("pipelines", "notebooks")
    ):
        raise HttpError(f"Required inventory {src.name} has no usable pipeline/notebook catalog")
    pipelines_in = catalog["pipelines"]
    notebooks_in = catalog["notebooks"]
    scope = get_scope_workspace_ids()
    if scope:
        pipelines_in = [row for row in pipelines_in if str(row.get("workspaceId") or "").lower() in scope]
        notebooks_in = [row for row in notebooks_in if str(row.get("workspaceId") or "").lower() in scope]
    source_complete = not (
        collection_incomplete(catalog) or catalog.get("inventoryErrors") or catalog.get("collectionErrors")
    )
    if not source_complete:
        print("Pipeline definitions: inventory coverage is incomplete; fetching all known artifact IDs.")

    print(
        f"Pipeline definitions: fetching getDefinition for "
        f"{len(pipelines_in)} pipeline(s) and {len(notebooks_in)} notebook(s)..."
    )
    if pipelines_in or notebooks_in:
        provider = get_default_provider()
        headers = lambda: provider.headers(scope=FABRIC_SCOPE)

    def fetch(item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        wsid, item_id = item.get("workspaceId"), item.get("id")
        if not (wsid and item_id):
            return None, "missing_item_identity"
        return _get_definition(headers, wsid, item_id)

    # Each definition is a long-running operation of a few seconds; a small pool keeps a large
    # estate practical while staying well inside the service's request limits.
    with ThreadPoolExecutor(max_workers=LRO_WORKERS) as pool:
        pipeline_results = list(pool.map(fetch, pipelines_in))
        notebook_results = list(pool.map(fetch, notebooks_in))

    pipelines_out: List[Dict[str, Any]] = []
    notebooks_out: List[Dict[str, Any]] = []
    errors = 0
    for item, (defn, err) in zip(pipelines_in, pipeline_results):
        pipelines_out.append(_normalise(item, defn, err))
        errors += bool(err)
    for item, (defn, err) in zip(notebooks_in, notebook_results):
        notebooks_out.append(_normalise(item, defn, err, notebook=True))
        errors += bool(err)

    target.write_text(
        json.dumps(
            {
                "pipelines": pipelines_out,
                "notebooks": notebooks_out,
                "errors": errors,
                "collectionComplete": source_complete and errors == 0,
                "sourceCollectionComplete": source_complete,
                **{key: catalog[key] for key in (
                    "failedWorkspaces", "inventoryErrors", "collectionErrors", "workspaceScope",
                ) if key in catalog},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"Wrote {target} ({len(pipelines_out)} pipeline def(s), "
        f"{len(notebooks_out)} notebook def(s), {errors} error(s))."
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

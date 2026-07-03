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

  This collector reads ``pipelines_notebooks.json`` produced by the
  ``pipelines_notebooks`` collector, then for every pipeline + notebook calls
  ``getDefinition`` and stores both the raw base64 parts and, where the
  payload is JSON or .ipynb, a decoded view so the analyzers don't have to
  re-decode.

DATA SAFETY:
  - Pipeline definitions are *configuration* (activity JSON) - no row data.
  - Notebook definitions contain source code (.ipynb). The collector stores
    cell source but strips all ``outputs`` and ``execution_count`` so no
    materialised results / customer data leak into the raw dump.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from collectors._http import HttpError, request
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"

# Cap on poll attempts per item to avoid runaway loops.
LRO_MAX_POLLS = int(os.environ.get("PIPELINE_DEF_LRO_MAX_POLLS", "20"))
LRO_DEFAULT_RETRY_AFTER = int(os.environ.get("PIPELINE_DEF_LRO_RETRY_AFTER", "3"))


def _decode_payload(payload: str, payload_type: str, path: str) -> Optional[Any]:
    """Decode a definition part if it's base64-encoded JSON or .ipynb.

    Returns the parsed object or ``None`` if the payload isn't JSON-shaped.
    Notebook outputs are stripped to avoid leaking materialised data.
    """
    if not payload or (payload_type or "").lower() != "inlinebase64":
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    # Only attempt JSON decode for .json / .ipynb / pipeline-content parts.
    lower = (path or "").lower()
    is_json_like = lower.endswith(".json") or lower.endswith(".ipynb")
    if not is_json_like:
        return None
    try:
        obj = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    # Strip notebook outputs / execution counts.
    if lower.endswith(".ipynb") and isinstance(obj, dict):
        for cell in obj.get("cells") or []:
            if isinstance(cell, dict):
                cell.pop("outputs", None)
                cell.pop("execution_count", None)
    return obj


def _get_definition(
    headers: Dict[str, str],
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
        return None, f"http_error:{exc}"


def _get_definition_impl(
    headers: Dict[str, str],
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
    retry_after = int(r.headers.get("Retry-After") or LRO_DEFAULT_RETRY_AFTER)

    for _ in range(LRO_MAX_POLLS):
        time.sleep(max(1, retry_after))
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
            err = body.get("error") or {}
            return None, f"lro_{status}:{err.get('errorCode') or err.get('code') or ''}"
        retry_after = int(pr.headers.get("Retry-After") or retry_after)

    return None, "lro_timeout"


def _normalise(
    item: Dict[str, Any],
    definition: Optional[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "id": item.get("id"),
        "displayName": item.get("displayName"),
        "workspaceId": item.get("workspaceId"),
        "workspaceName": item.get("workspaceName"),
    }
    if error:
        rec["error"] = error
        return rec
    parts_in = ((definition or {}).get("definition") or {}).get("parts") or []
    parts_out: List[Dict[str, Any]] = []
    for p in parts_in:
        path = p.get("path")
        payload_type = p.get("payloadType")
        decoded = _decode_payload(p.get("payload") or "", payload_type or "", path or "")
        out_part: Dict[str, Any] = {"path": path, "payloadType": payload_type}
        # Keep raw payload only when we couldn't decode it (so analyzers can
        # still inspect e.g. .py files). Decoded payloads supersede raw.
        if decoded is not None:
            out_part["decoded"] = decoded
        else:
            out_part["payload"] = p.get("payload")
        parts_out.append(out_part)
    rec["parts"] = parts_out
    return rec


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "pipeline_definitions.json"

    src = raw_dir / "pipelines_notebooks.json"
    if not src.exists():
        print("Pipeline definitions: pipelines_notebooks.json not found - run that collector first.")
        target.write_text(json.dumps({"pipelines": [], "notebooks": []}, indent=2), encoding="utf-8")
        return target

    catalog = json.loads(src.read_text(encoding="utf-8-sig"))
    pipelines_in = catalog.get("pipelines") or []
    notebooks_in = catalog.get("notebooks") or []

    if not pipelines_in and not notebooks_in:
        print("Pipeline definitions: no pipelines or notebooks in catalog - nothing to fetch.")
        target.write_text(json.dumps({"pipelines": [], "notebooks": []}, indent=2), encoding="utf-8")
        return target

    provider = get_default_provider()
    headers = provider.headers(scope=FABRIC_SCOPE)

    print(
        f"Pipeline definitions: fetching getDefinition for "
        f"{len(pipelines_in)} pipeline(s) and {len(notebooks_in)} notebook(s)..."
    )

    pipelines_out: List[Dict[str, Any]] = []
    notebooks_out: List[Dict[str, Any]] = []
    errors = 0

    for i, p in enumerate(pipelines_in, 1):
        wsid, pid = p.get("workspaceId"), p.get("id")
        if not (wsid and pid):
            continue
        defn, err = _get_definition(headers, wsid, pid)
        rec = _normalise(p, defn, err)
        pipelines_out.append(rec)
        if err:
            errors += 1
        if i % 10 == 0:
            print(f"  ... pipelines {i}/{len(pipelines_in)}")

    for i, n in enumerate(notebooks_in, 1):
        wsid, nid = n.get("workspaceId"), n.get("id")
        if not (wsid and nid):
            continue
        defn, err = _get_definition(headers, wsid, nid)
        rec = _normalise(n, defn, err)
        notebooks_out.append(rec)
        if err:
            errors += 1
        if i % 25 == 0:
            print(f"  ... notebooks {i}/{len(notebooks_in)}")

    target.write_text(
        json.dumps(
            {
                "pipelines": pipelines_out,
                "notebooks": notebooks_out,
                "errors": errors,
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
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

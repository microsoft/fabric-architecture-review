# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Inspect TMDL/BIM definitions in memory for architectural model summaries.

Endpoints (Long Running Operation):
  POST https://api.fabric.microsoft.com/v1/workspaces/{ws}/semanticModels/{id}/getDefinition
  GET  <Location>                                                            (poll)
  GET  <Location>/result                                                     (final body)

Docs:
  - https://learn.microsoft.com/rest/api/fabric/semanticmodel/items/get-semantic-model-definition
  - https://learn.microsoft.com/rest/api/fabric/articles/long-running-operation
  - https://learn.microsoft.com/fabric/get-started/direct-lake-overview

Scoping:
    Fetch all demanded in-scope model definitions, including Direct Lake.

DATA SAFETY:
  Persist only storage modes, allowlisted feature flags, GUID identities and row/object-level security
  counts (see collectors.model_security). Role names, permissions and per-role counts are added only when
  model_security__role_detail is enabled. Raw/base64 definitions, DAX, M, role filters, source literals and
  connection details never leave memory. Summary completeness is conservative; candidate fingerprints
  alone cannot authorize checkpoint reuse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from collectors._http import Headers, HttpError, request
from collectors._common import get_scope_workspace_ids, load_complete_raw, record_collection_failure
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"

LRO_MAX_POLLS = int(os.environ.get("SEMANTIC_MODEL_DEF_LRO_MAX_POLLS", "20"))
LRO_DEFAULT_RETRY_AFTER = int(os.environ.get("SEMANTIC_MODEL_DEF_LRO_RETRY_AFTER", "3"))
LRO_FIRST_POLL_DELAY = 2
CHECKPOINT_INTERVAL = max(1, int(os.environ.get("SEMANTIC_MODEL_DEF_CHECKPOINT_INTERVAL", "10")))
MODEL_DELAY_SECONDS = max(0.0, float(os.environ.get("SEMANTIC_MODEL_DEF_MODEL_DELAY_SECONDS", "0")))

def _get_definition(
    headers: Headers,
    workspace_id: str,
    dataset_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Call getDefinition on a semantic model, handling the 202 LRO flow.

    Any ``HttpError`` raised after _http has exhausted its 429 / 5xx /
    connection retries is captured as a per-model error so one unreachable
    model never aborts the whole collection run.
    """
    try:
        return _get_definition_impl(headers, workspace_id, dataset_id)
    except HttpError as exc:
        return None, f"http_{exc.status_code or 'error'}"


def _get_definition_impl(
    headers: Headers,
    workspace_id: str,
    dataset_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Call getDefinition on a semantic model, handling the 202 LRO flow."""
    url = f"{FAB}/workspaces/{workspace_id}/semanticModels/{dataset_id}/getDefinition"
    r = request("POST", url, headers)

    if r.status_code == 200 and r.content:
        return r.json(), None
    if r.status_code in (401, 403):
        return None, f"http_{r.status_code}"
    if r.status_code == 404:
        return None, "not_found"
    if r.status_code != 202:
        return None, f"http_{r.status_code}"

    op_url = r.headers.get("Location")
    if not op_url:
        return None, "lro_missing_location"
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
            result_url = op_url.rstrip("/") + "/result"
            rr = request("GET", result_url, headers)
            if rr.status_code == 200 and rr.content:
                return rr.json(), None
            if "definition" in body:
                return body, None
            return None, "lro_result_unavailable"
        if status in ("failed", "canceled"):
            return None, f"lro_{status}"
        delay = int(pr.headers.get("Retry-After") or retry_after)

    return None, "lro_timeout"


def _normalise(
    dataset: Dict[str, Any],
    definition: Optional[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "id": dataset.get("id"),
        "name": dataset.get("name"),
        "workspaceId": dataset.get("workspaceId") or dataset.get("groupId"),
        "workspaceName": dataset.get("workspaceName"),
        "targetStorageMode": dataset.get("targetStorageMode"),
    }
    from collectors.definition_safety import model_summary
    from collectors.model_security import model_security, role_detail_enabled
    if error:
        rec["error"] = error
    rec.update(model_summary(None if error else definition))
    # Counts and an inspection status only; filter expressions, names and membership are never kept. Role names,
    # permissions and per-role counts are added only when model_security__role_detail is on.
    rec["security"] = model_security(None if error else definition, error, keep_roles=role_detail_enabled())
    flag = dataset.get("isEffectiveIdentityRolesRequired")
    rec["effectiveIdentityRolesRequired"] = flag if isinstance(flag, bool) else None
    return rec


def refetch_model(row: Dict[str, Any], headers: Headers | None = None) -> Dict[str, Any]:
    """Read one model's definition again and return its normalized record (used after a temporary access grant)."""
    if headers is None:
        provider = get_default_provider()
        headers = lambda: provider.headers(scope=FABRIC_SCOPE)
    dataset = {
        "id": row.get("id"), "name": row.get("name"), "workspaceId": row.get("workspaceId"),
        "workspaceName": row.get("workspaceName"), "targetStorageMode": row.get("targetStorageMode"),
        "isEffectiveIdentityRolesRequired": row.get("effectiveIdentityRolesRequired"),
    }
    definition, error = _get_definition(headers, str(row.get("workspaceId")), str(row.get("id")))
    return _normalise(dataset, definition, error)


def _candidate_fingerprint(candidates: List[Dict[str, Any]]) -> str:
    keys = sorted(
        f"{dataset.get('workspaceId') or dataset.get('groupId') or ''}:{dataset.get('id') or ''}"
        for dataset in candidates
    )
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def _write_progress(
    target: Path,
    models: List[Dict[str, Any]],
    errors: int,
    fingerprint: str,
    status: str,
) -> None:
    payload = {
        "models": models,
        "errors": errors,
        "candidate_fingerprint": fingerprint,
        "status": status,
        "collectionComplete": status == "completed" and errors == 0,
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


@record_collection_failure("semantic_model_definitions.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "semantic_model_definitions.json"

    src = raw_dir / "semantic_models.json"
    catalog = load_complete_raw(src)
    datasets = catalog.get("datasets") or []
    scope = get_scope_workspace_ids()
    candidates = [d for d in datasets if not scope or (
        str(d.get("workspaceId") or d.get("groupId") or "").lower() in scope
    )]

    if not candidates:
        print("Semantic model definitions: no datasets in scope - nothing to fetch.")
        target.write_text(json.dumps({"models": [], "collectionComplete": True}, indent=2), encoding="utf-8")
        return target

    provider = get_default_provider()
    fingerprint = _candidate_fingerprint(candidates)

    print(f"Semantic model definitions: fetching getDefinition for {len(candidates)} model(s)...")

    models_out: List[Dict[str, Any]] = []
    # Candidate equality is not a full-capture watermark. Never reuse old parts.
    completed = {
        (model.get("workspaceId"), model.get("id"))
        for model in models_out
    }
    errors = 0
    for i, ds in enumerate(candidates, 1):
        wsid = ds.get("workspaceId") or ds.get("groupId")
        did = ds.get("id")
        if not (wsid and did):
            models_out.append(_normalise(ds, None, "missing_item_identity"))
            errors += 1
            continue
        if (wsid, did) in completed:
            continue
        if MODEL_DELAY_SECONDS:
            time.sleep(MODEL_DELAY_SECONDS)
        headers = lambda: provider.headers(scope=FABRIC_SCOPE)
        defn, err = _get_definition(headers, wsid, did)
        rec = _normalise(ds, defn, err)
        models_out.append(rec)
        if err:
            errors += 1
        if i % CHECKPOINT_INTERVAL == 0:
            _write_progress(target, models_out, errors, fingerprint, "in_progress")
            print(f"  ... models {i}/{len(candidates)}")

    _write_progress(target, models_out, errors, fingerprint, "completed")
    print(f"Wrote {target} ({len(models_out)} model def(s), {errors} error(s)).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

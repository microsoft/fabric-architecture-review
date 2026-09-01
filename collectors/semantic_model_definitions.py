# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fetch TMDL/BIM definitions for semantic models via the Fabric
``getDefinition`` API, so the storage-mode analyzer can audit each model for
DirectLake-migration blockers (M-based partitions, calculated columns,
unsupported types, missing lakehouse-table binding).

Endpoints (Long Running Operation):
  POST https://api.fabric.microsoft.com/v1/workspaces/{ws}/semanticModels/{id}/getDefinition
  GET  <Location>                                                            (poll)
  GET  <Location>/result                                                     (final body)

Docs:
  - https://learn.microsoft.com/rest/api/fabric/semanticmodel/items/get-semantic-model-definition
  - https://learn.microsoft.com/rest/api/fabric/articles/long-running-operation
  - https://learn.microsoft.com/fabric/get-started/direct-lake-overview

Why this exists:
  ``semantic_models.json`` only carries catalog metadata + `targetStorageMode`
  (Import / Abf / PremiumFiles). It cannot tell us *why* a model is in Import
  mode, or whether it could be moved to DirectLake. Reading the TMDL parts
  reveals the partition `mode`, the M source connectors, calculated
  columns/tables, and the column data types — the exact information a
  DirectLake-feasibility review needs.

Scoping:
    By default the collector fetches all in-scope definitions so metadata-only
    DAX analysis covers Direct Lake and Import models. Set
    ``SEMANTIC_MODEL_DEF_ALL=0`` to limit collection to non-DirectLake models.

DATA SAFETY:
  TMDL/BIM definitions are *model metadata* (table names, column types,
  partition expressions, DAX). They do not contain row data. M expressions
  may embed connection strings / parameters but not credentials.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from collectors._http import HttpError, request
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"

LRO_MAX_POLLS = int(os.environ.get("SEMANTIC_MODEL_DEF_LRO_MAX_POLLS", "20"))
LRO_DEFAULT_RETRY_AFTER = int(os.environ.get("SEMANTIC_MODEL_DEF_LRO_RETRY_AFTER", "3"))
CHECKPOINT_INTERVAL = max(1, int(os.environ.get("SEMANTIC_MODEL_DEF_CHECKPOINT_INTERVAL", "10")))
MODEL_DELAY_SECONDS = max(0.0, float(os.environ.get("SEMANTIC_MODEL_DEF_MODEL_DELAY_SECONDS", "0")))

# Storage modes that warrant a definition fetch (anything that *could* be
# migrated to DirectLake but currently isn't).
NON_DIRECTLAKE_MODES = {"abf", "premiumfiles", "import", "push", "pushstreaming", ""}


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _decode_part(payload: str, payload_type: str) -> Optional[str]:
    """Decode a TMDL/BIM part. Returns the decoded text or ``None``."""
    if not payload or (payload_type or "").lower() != "inlinebase64":
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        return raw.decode("utf-8-sig", errors="replace")
    except UnicodeDecodeError:
        return None


def _get_definition(
    headers: Dict[str, str],
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
        return None, f"http_error:{exc}"


def _get_definition_impl(
    headers: Dict[str, str],
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
            result_url = op_url.rstrip("/") + "/result"
            rr = request("GET", result_url, headers)
            if rr.status_code == 200 and rr.content:
                return rr.json(), None
            if "definition" in body:
                return body, None
            return None, "lro_result_unavailable"
        if status in ("failed", "canceled"):
            err = body.get("error") or {}
            return None, f"lro_{status}:{err.get('errorCode') or err.get('code') or ''}"
        retry_after = int(pr.headers.get("Retry-After") or retry_after)

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
    if error:
        rec["error"] = error
        return rec

    parts_in = ((definition or {}).get("definition") or {}).get("parts") or []
    parts_out: List[Dict[str, Any]] = []
    for p in parts_in:
        path = p.get("path") or ""
        decoded = _decode_part(p.get("payload") or "", p.get("payloadType") or "")
        out_part: Dict[str, Any] = {"path": path, "payloadType": p.get("payloadType")}
        if decoded is not None:
            out_part["text"] = decoded
        else:
            out_part["payload"] = p.get("payload")
        parts_out.append(out_part)
    rec["parts"] = parts_out
    return rec


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
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "semantic_model_definitions.json"

    src = raw_dir / "semantic_models.json"
    if not src.exists():
        print("Semantic model definitions: semantic_models.json not found - run that collector first.")
        target.write_text(json.dumps({"models": []}, indent=2), encoding="utf-8")
        return target

    catalog = json.loads(src.read_text(encoding="utf-8-sig"))
    datasets = catalog.get("datasets") or []
    fetch_all_setting = os.environ.get("SEMANTIC_MODEL_DEF_ALL")
    fetch_all = fetch_all_setting is None or _truthy(fetch_all_setting)

    if fetch_all:
        candidates = datasets
    else:
        candidates = [
            d for d in datasets
            if (d.get("targetStorageMode") or "").strip().lower() in NON_DIRECTLAKE_MODES
        ]

    if not candidates:
        print("Semantic model definitions: no datasets in scope - nothing to fetch.")
        target.write_text(json.dumps({"models": []}, indent=2), encoding="utf-8")
        return target

    provider = get_default_provider()
    fingerprint = _candidate_fingerprint(candidates)

    print(f"Semantic model definitions: fetching getDefinition for {len(candidates)} model(s)...")

    models_out: List[Dict[str, Any]] = []
    if target.exists():
        try:
            previous = json.loads(target.read_text(encoding="utf-8-sig"))
            if previous.get("status") == "in_progress" and previous.get("candidate_fingerprint") == fingerprint:
                models_out = [model for model in previous.get("models") or [] if not model.get("error")]
                if models_out:
                    print(f"  resuming {len(models_out)} successful model definition(s) from checkpoint")
        except (json.JSONDecodeError, OSError, AttributeError):
            models_out = []
    completed = {
        (model.get("workspaceId"), model.get("id"))
        for model in models_out
    }
    errors = 0
    for i, ds in enumerate(candidates, 1):
        wsid = ds.get("workspaceId") or ds.get("groupId")
        did = ds.get("id")
        if not (wsid and did):
            continue
        if (wsid, did) in completed:
            continue
        if MODEL_DELAY_SECONDS:
            time.sleep(MODEL_DELAY_SECONDS)
        headers = provider.headers(scope=FABRIC_SCOPE)
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
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

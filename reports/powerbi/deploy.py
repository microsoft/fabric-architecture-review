# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric REST helpers for deploying the governance semantic model + report.

Imported by ``fabric/setup.ipynb``. Kept dependency-light: callers pass in
small callables for the actual HTTP calls (the setup notebook already owns the
token + ``requests`` session), so this module only builds payloads and
orchestrates the Direct Lake binding.

DATA SAFETY: builds deployment payloads only.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Dict, Optional, Tuple

from reports.powerbi.report import build_parts as _report_parts
from reports.powerbi.semantic_model import build_model_bim_json

GetJson = Callable[[str], Dict[str, Any]]


def _part(path: str, text: str) -> Dict[str, str]:
    return {
        "path": path,
        "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def wait_for_sql_endpoint(
    get_json: GetJson, wid: str, lhid: str, *, retries: int = 30, delay: int = 10
) -> Tuple[str, str]:
    """Poll the Lakehouse until its SQL analytics endpoint is provisioned.

    Returns ``(connectionString, sqlEndpointId)``. Raises if it never appears.
    """
    last: Optional[Dict[str, Any]] = None
    for _ in range(retries):
        lh = get_json(f"/workspaces/{wid}/lakehouses/{lhid}")
        props = ((lh or {}).get("properties") or {}).get("sqlEndpointProperties") or {}
        conn, sid = props.get("connectionString"), props.get("id")
        prov = props.get("provisioningStatus")
        if conn and sid and (prov in (None, "Success", "Succeeded")):
            return conn, sid
        last = props
        time.sleep(delay)
    raise RuntimeError(
        "Lakehouse SQL endpoint not ready after polling; last status="
        + str((last or {}).get("provisioningStatus"))
    )


_PBI = "https://api.powerbi.com/v1.0/myorg"
_FINISHED = {"Completed", "Failed", "Disabled", "Cancelled", "Canceled"}


def refresh_model(
    http: Any, headers: Dict[str, str], workspace_id: str, model_id: str, *, wait_seconds: int = 180, poll_seconds: int = 5,
) -> str:
    """Frame a Direct Lake model once, so a table added by this release can be queried straight away.

    Direct Lake follows changes to tables it already knows. A table that a new release adds to the model stays
    "not refreshed" until the model is refreshed once, and every page that reads it fails until then. Best effort:
    it reports the outcome and never raises, because the next pipeline run frames the tables anyway.
    ``http`` is the ``requests`` module (or anything with ``post`` and ``get``).
    """
    base = f"{_PBI}/groups/{workspace_id}/datasets/{model_id}/refreshes"
    try:
        started = http.post(base, headers=headers, json={"type": "full"}, timeout=60)
    except Exception as exc:  # network failure, not an answer from the service
        return f"Model refresh not started ({type(exc).__name__}). Refresh the model once in the Fabric portal."
    if started.status_code not in (200, 202):
        return (f"Model refresh not started (HTTP {started.status_code}). "
                "Refresh the model once in the Fabric portal if a report page says it has not been refreshed.")
    location = (getattr(started, "headers", None) or {}).get("Location")
    deadline = time.monotonic() + wait_seconds
    while location and time.monotonic() < deadline:
        time.sleep(poll_seconds)
        try:
            state = http.get(location, headers=headers, timeout=60).json()
        except Exception:
            continue
        status = state.get("status")
        if status in _FINISHED:
            return "Model refreshed." if status == "Completed" else f"Model refresh ended as {status}. Refresh the model in the Fabric portal."
    return "Model refresh started." if location else "Model refresh requested."


def model_definition(model_name: str, sql_endpoint: str, database_id: str) -> Dict[str, Any]:
    bim = build_model_bim_json(model_name, sql_endpoint, database_id)
    pbism = json.dumps({"version": "1.0", "settings": {}}, indent=2)
    return {"parts": [_part("definition.pbism", pbism), _part("model.bim", bim)]}


def report_definition(semantic_model_id: str) -> Dict[str, Any]:
    out = []
    for p in _report_parts(semantic_model_id):
        if p.get("b64"):
            # Binary part (e.g. the home-map image): payload is already base64.
            out.append({"path": p["path"], "payload": p["b64"], "payloadType": "InlineBase64"})
        else:
            out.append(_part(p["path"], p["text"]))
    return {"parts": out}

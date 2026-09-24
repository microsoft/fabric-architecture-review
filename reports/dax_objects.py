# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure non-measure DAX evidence projection; never exports source expressions.

``gold_dax_objects``: run_id, run_timestamp, workspace_id, workspace_name,
model_id, model_name, table_name, object_type, object_name, risk_level,
risk_rank, risk_score, expression_length, signal_codes, signal_details_json.
``gold_dax_object_coverage``: run_id, run_timestamp, workspace_id, workspace_name,
model_id, model_name, definition_status, object_count, flagged_object_count, notice.

Rank, score, length and counts are Python integers (Gold int64); all other
columns are strings. Object identity is workspace/model/table/type/name.
Measures retain their independent legacy contract. Complete means extraction
coverage for supported types, never that the model is valid or performant.
"""
from __future__ import annotations

from collectors.workspace_scope import filter_review_payload

import json
from pathlib import Path
from typing import Any

from collectors.dax_analysis import OBJECT_COVERAGE_NOTICE, OBJECT_TYPES, build_object_analysis

_CONTEXT = ("workspace_id", "workspace_name", "model_id", "model_name")
_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _load(path: Path, collection: str = "") -> tuple[dict, str]:
    if not path.exists():
        return {}, "not_collected"
    try:
        payload = filter_review_payload(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)
    except (json.JSONDecodeError, UnicodeError):
        return {}, "invalid_json"
    if not isinstance(payload, dict):
        return {}, "invalid_shape"
    if collection and (not isinstance(payload.get(collection), list)
                       or any(not isinstance(row, dict) for row in payload[collection])):
        return {}, "invalid_shape"
    return payload, ""


def _key(row: dict) -> tuple[str, str]:
    return (str(row.get("workspace_id") or "").strip().lower(),
            str(row.get("model_id") or "").strip().lower())


def _integer(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("DAX object evidence requires a non-negative int64.")
    return value


def build_dax_object_evidence(raw_dir: Path, run_id: str, run_timestamp: str) -> dict[str, list[dict]]:
    """Read collected metadata only, without writes, network calls or DAX execution.

    Older analysis payloads can be rebuilt from already-collected definitions.
    Inventory-only models get unavailable coverage rather than a clean result.
    Malformed files are surfaced in coverage notices; I/O errors propagate.
    """
    analysis, analysis_error = _load(raw_dir / "dax_analysis.json")
    definitions, definitions_error = _load(raw_dir / "semantic_model_definitions.json", "models")
    inventory, inventory_error = _load(raw_dir / "semantic_models.json", "datasets")
    if "objects" not in analysis or "object_coverage" not in analysis:
        analysis = build_object_analysis(definitions)
    objects = analysis.get("objects")
    coverages = analysis.get("object_coverage")
    if not isinstance(objects, list) or not isinstance(coverages, list):
        raise ValueError("DAX object analysis requires objects and object_coverage lists.")
    if any(not isinstance(row, dict) for row in objects + coverages):
        raise ValueError("DAX object analysis rows must be objects.")

    meta = {"run_id": str(run_id), "run_timestamp": str(run_timestamp)}
    contexts: dict[tuple[str, str], dict] = {}
    for model in inventory.get("datasets") or []:
        row = {
            "workspace_id": model.get("workspaceId"), "workspace_name": model.get("workspaceName"),
            "model_id": model.get("id"), "model_name": model.get("name"),
        }
        contexts[_key(row)] = row
    inventory_keys = list(contexts)

    def model_context(row: dict) -> dict:
        result = {field: row.get(field) for field in _CONTEXT}
        key = _key(result)
        if not key[0]:
            candidates = [candidate for candidate in inventory_keys if candidate[1] == key[1]]
            if len(candidates) == 1:
                result["workspace_id"] = contexts[candidates[0]]["workspace_id"]
        key = _key(result)
        return {field: result[field] or contexts.get(key, {}).get(field) for field in _CONTEXT}

    for model in definitions.get("models") or []:
        row = model_context({
            "workspace_id": model.get("workspaceId"), "workspace_name": model.get("workspaceName"),
            "model_id": model.get("id"), "model_name": model.get("name") or model.get("displayName"),
        })
        contexts[_key(row)] = row
    by_model: dict[tuple[str, str], dict] = {}
    for row in coverages:
        context = model_context(row)
        key = _key(context)
        if key in by_model:
            raise ValueError("Duplicate DAX object model coverage.")
        contexts[key] = context
        by_model[key] = row

    rows: list[dict] = []
    identities: set[tuple[str, str, str, str, str]] = set()
    for obj in objects:
        if obj.get("object_type") not in OBJECT_TYPES:
            raise ValueError("Unsupported object type in non-measure DAX evidence.")
        context = model_context(obj)
        key = _key(context)
        contexts.setdefault(key, context)
        identity = (*key, str(obj.get("table_name") or ""), obj["object_type"], str(obj.get("object_name") or ""))
        if identity in identities:
            raise ValueError("Duplicate DAX object identity.")
        identities.add(identity)
        risk_level = obj.get("risk_level")
        if risk_level not in _RANK:
            raise ValueError("Invalid DAX object risk level.")
        signals = [{
            field: signal[field] for field in ("code", "category", "points", "message") if field in signal
        } for signal in obj.get("signals") or []]
        rows.append({
            **meta,
            **{field: str(context.get(field) or "") for field in _CONTEXT},
            "table_name": str(obj.get("table_name") or ""),
            "object_type": obj["object_type"],
            "object_name": str(obj.get("object_name") or ""),
            "risk_level": risk_level,
            "risk_rank": _RANK[risk_level],
            "risk_score": _integer(obj.get("risk_score")),
            "expression_length": _integer(obj.get("expression_length")),
            "signal_codes": ", ".join(str(signal["code"]) for signal in signals if signal.get("code")),
            "signal_details_json": json.dumps(signals, ensure_ascii=False, sort_keys=True),
        })

    coverage_rows: list[dict] = []
    for key, context in sorted(contexts.items()):
        coverage = by_model.get(key, {})
        status = coverage.get("definition_status", "unavailable")
        notice = str(coverage.get("notice") or OBJECT_COVERAGE_NOTICE)
        if status not in ("complete", "partial", "unavailable"):
            status = "partial"
            notice += " Invalid non-measure coverage status."
        if not coverage:
            notice += " Non-measure definition coverage was not collected."
        for source, error in (("analysis", analysis_error), ("definitions", definitions_error),
                              ("inventory", inventory_error)):
            if error and error != "not_collected":
                status = "partial" if status == "complete" else status
                notice += f" {source}: {error}."
        model_rows = [row for row in rows if _key(row) == key]
        coverage_rows.append({
            **meta,
            **{field: str(context.get(field) or "") for field in _CONTEXT},
            "definition_status": status,
            "object_count": len(model_rows),
            "flagged_object_count": sum(row["risk_level"] in ("medium", "high") for row in model_rows),
            "notice": notice,
        })
    # A corrupt input with no model identity must not silently produce empty evidence.
    if not contexts and any(error and error != "not_collected"
                            for error in (analysis_error, definitions_error, inventory_error)):
        raise ValueError("Invalid DAX evidence metadata; model coverage cannot be determined.")
    rows.sort(key=lambda row: tuple(row[field] for field in
                                   ("workspace_id", "model_id", "table_name", "object_type", "object_name")))
    return {"gold_dax_objects": rows, "gold_dax_object_coverage": coverage_rows}

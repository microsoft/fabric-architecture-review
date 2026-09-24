# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure native execution snapshot builder; no network, environment or clock access.

Contracts:
https://learn.microsoft.com/rest/api/power-bi/datasets/get-refresh-history-in-group
https://learn.microsoft.com/rest/api/fabric/core/job-scheduler/list-item-job-instances

Completed -> completed; Failed -> failed; Cancelled/Canceled -> cancelled;
InProgress -> in_progress; NotStarted -> not_started; Deduped -> deduped;
Disabled -> disabled. Unknown/unrecognized/missing -> unknown. In particular,
Power BI Unknown does NOT prove InProgress, and Disabled is NOT a failure.

execution_key hashes workspace/item/type/source/native execution ID, never the
FAR run or mutable status/times. Deduplication is within this snapshot only.
Persist each run's rows separately. For totals across runs, select the latest
observation per execution_key by run_timestamp (run_id breaks timestamp ties),
then filter status. Do not sum failed observations across review snapshots.

Coverage count is the number of emitted distinct executions, not the number of
successful/failed jobs. Unknown IDs are not manufactured from mutable times;
unidentifiable records are omitted and explicitly counted in the notice.
Failed workspace inventories retain item-null coverage even when other items
are known. Explicitly mismatched native job item IDs are never reattributed.
Notices are generated from fixed text/codes, never service error text.
"""
from __future__ import annotations

from collectors.workspace_scope import filter_review_payload
from collectors._http import HttpError
from collectors._common import load_workspace_inventory
from collectors.workspace_evidence import workspace_items

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HISTORY_SCOPE = "recent_retained_observations"
STATUS_MAP = {
    "completed": "completed", "failed": "failed", "cancelled": "cancelled",
    "canceled": "cancelled", "inprogress": "in_progress", "notstarted": "not_started",
    "deduped": "deduped", "disabled": "disabled", "unknown": "unknown",
}
COLLECTION_STATUSES = frozenset({
    "collected", "empty", "partial", "forbidden", "not_found", "error",
    "not_collected", "invalid_data",
})
_SOURCES = {
    "SemanticModel": "powerbi_refresh_history",
    "DataPipeline": "fabric_job_instances",
    "Notebook": "fabric_job_instances",
}
_NOTICE_CODES = frozenset({
    "missing_item_identity", "refresh_top_limit", "request_failed", "retained_jobs",
    "invalid_response", "invalid_continuation", "collection_limit",
    "mismatched_execution_item_id",
})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})?\Z"
)
_SENSITIVE = re.compile(
    r"://|www\.|(?:bearer|authorization)\s|"
    r"(?:password|pwd|token|secret|accountkey|sharedaccesssignature|"
    r"data\s*source|server|connection\s*string)\s*[:=]|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.",
    re.IGNORECASE,
)


def _identifier(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value) if 0 <= value <= 2**63 - 1 else None
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value.strip()):
        return value.strip().lower()
    return None


def _name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if _SENSITIVE.search(value):
        return "[redacted]"
    return "".join(char for char in value if char.isprintable())[:512]


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Both native contracts define these fields as UTC, even where the
        # documented Fabric examples omit the timezone suffix.
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _read(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            return {}, "source_invalid"
        value = filter_review_payload(value, path.parent)
    except FileNotFoundError:
        return {}, "source_missing"
    except (OSError, UnicodeError, ValueError):
        return {}, "source_unreadable"
    except HttpError:
        return {}, "source_scope_unavailable"
    return value, None


def _identity(item: dict[str, Any], item_type: str) -> dict[str, Any]:
    return {
        "workspace_id": _identifier(item.get("workspaceId") or item.get("groupId")),
        "workspace_name": _name(item.get("workspaceName")),
        "item_id": _identifier(item.get("itemId") or item.get("id")),
        "item_name": _name(item.get("itemName") or item.get("displayName") or item.get("name")),
        "item_type": item_type,
    }


def _inventory(raw_dir: Path) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, str | None]]:
    items: list[tuple[str, dict[str, Any]]] = []
    names: dict[str, str | None] = {}
    try:
        workspaces = load_workspace_inventory(raw_dir)
    except (HttpError, OSError, ValueError):
        return items, names
    for workspace in workspaces:
        workspace_id = _identifier(workspace.get("id"))
        if workspace_id:
            names[workspace_id] = _name(workspace.get("name"))
        for item in workspace_items(workspace, _SOURCES):
            items.append((item["type"], {
                **item, "workspaceId": workspace_id,
                "workspaceName": workspace.get("name"),
            }))
    return items, names


def _error_status(error: Any) -> str:
    code = error.get("statusCode") if isinstance(error, dict) else None
    return "forbidden" if code in (401, 403) else "not_found" if code == 404 else "error"


def _execution_rows(
    records: list[Any], identity: dict[str, Any], source: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: dict[str, dict[str, Any]] = {}
    issues: dict[str, int] = {}

    def issue(code: str) -> None:
        issues[code] = issues.get(code, 0) + 1

    for record in records:
        if not isinstance(record, dict):
            issue("invalid_execution_record")
            continue
        if source == "powerbi_refresh_history":
            native_id = record.get("requestId") or record.get("id")
            start_value, end_value = record.get("startTime"), record.get("endTime")
        else:
            if "itemId" in record and _identifier(record["itemId"]) != identity["item_id"]:
                issue("mismatched_execution_item_id")
                continue
            native_id = record.get("id")
            start_value, end_value = record.get("startTimeUtc"), record.get("endTimeUtc")
        execution_id = _identifier(native_id)
        if execution_id is None:
            issue("missing_or_invalid_execution_id")
            continue
        start, end = _time(start_value), _time(end_value)
        if start is None:
            issue("missing_or_invalid_start_time")
        if end_value not in (None, "") and end is None:
            issue("invalid_end_time")
        status_value = record.get("status")
        status_key = status_value.strip().lower() if isinstance(status_value, str) else ""
        status = STATUS_MAP.get(status_key, "unknown")
        if status_key not in STATUS_MAP:
            issue("unrecognized_status")
        duration = None
        if start is not None and end is not None:
            delta = end - start
            milliseconds = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
            if 0 <= milliseconds <= 2**63 - 1:
                duration = milliseconds
            else:
                issue("invalid_time_order")
        key_values = [identity["workspace_id"], identity["item_id"], identity["item_type"], source, execution_id]
        execution_key = hashlib.sha256(json.dumps(key_values, separators=(",", ":")).encode()).hexdigest()
        row = {
            **identity, "execution_key": execution_key, "execution_id": execution_id,
            "execution_type": "refresh" if source == "powerbi_refresh_history" else "job",
            "status": status, "start_time": _iso(start), "end_time": _iso(end),
            "duration_ms": duration, "source": source,
        }
        previous = rows.get(execution_key)
        if previous is not None:
            if previous != row:
                issue("conflicting_execution_observations")
            # Prefer an ended observation over an active copy across pages.
            minimum = datetime.min.replace(tzinfo=timezone.utc)
            if (_time(previous["end_time"]) or minimum, _time(previous["start_time"]) or minimum) >= (
                end or minimum, start or minimum,
            ):
                continue
        rows[execution_key] = row
    return [rows[key] for key in sorted(rows)], issues


def build_execution_evidence(raw_dir: Path, run_id: str, run_timestamp: str) -> dict[str, list[dict]]:
    """Return Gold observations and coverage without modifying raw files.

    Date/time fields are nullable UTC ISO-8601 strings for the Gold writer;
    duration/count fields are Python ints (int64-compatible). If an entire
    source is unavailable and no items are known, an identity-null coverage
    row explicitly represents that inventory gap, never a fictitious job.
    """
    run_time = _time(run_timestamp)
    if not isinstance(run_id, str) or not run_id.strip() or run_time is None:
        raise ValueError("Execution evidence requires a run ID and valid run timestamp")
    result: dict[str, list[dict]] = {"gold_item_executions": [], "gold_execution_coverage": []}
    fallback_items, workspace_names = _inventory(raw_dir)
    run = {"run_id": run_id, "run_timestamp": _iso(run_time)}
    sources = (
        ("semantic_models.json", {"SemanticModel": "datasets"}),
        ("pipelines_notebooks.json", {"DataPipeline": "pipelines", "Notebook": "notebooks"}),
    )
    inputs = {filename: _read(raw_dir / filename) for filename, _ in sources}
    known_scope = {
        identifier
        for data, _ in inputs.values()
        if isinstance(data.get("workspaceScope"), list)
        for value in data["workspaceScope"]
        if (identifier := _identifier(value)) is not None
    }
    for filename, kinds in sources:
        data, read_issue = inputs[filename]
        scope_values = data.get("workspaceScope")
        scope = {_identifier(value) for value in scope_values} if isinstance(scope_values, list) else set(known_scope)
        scope.discard(None)
        evidence_present = "executionEvidence" in data
        evidence = data.get("executionEvidence")
        source_issue = read_issue
        if evidence_present and not isinstance(evidence, list):
            source_issue = "invalid_execution_evidence"
            evidence = []
        entries = evidence if isinstance(evidence, list) else []
        candidates: dict[tuple[str | None, str | None, str], dict[str, Any]] = {}
        per_item: dict[tuple[str | None, str | None, str], list[dict[str, Any]]] = {}

        def add(item: dict[str, Any], kind: str) -> tuple[str | None, str | None, str] | None:
            identity = _identity(item, kind)
            if scope and identity["workspace_id"] not in scope:
                return None
            identity["workspace_name"] = identity["workspace_name"] or workspace_names.get(identity["workspace_id"])
            key = (identity["workspace_id"], identity["item_id"], kind)
            existing = candidates.get(key)
            if existing:
                identity = {field: identity[field] or existing[field] for field in identity}
            candidates[key] = identity
            return key

        for kind, item in fallback_items:
            if kind in kinds:
                add(item, kind)
        for kind, inventory_key in kinds.items():
            inventory = data.get(inventory_key)
            if isinstance(inventory, list):
                for item in inventory:
                    if isinstance(item, dict):
                        add(item, kind)
                    else:
                        source_issue = "invalid_item_inventory"
            elif inventory_key in data:
                source_issue = "invalid_item_inventory"
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("itemType"), str) or entry["itemType"] not in kinds:
                source_issue = "invalid_execution_evidence"
                continue
            key = add(entry, entry["itemType"])
            if key is not None:
                per_item.setdefault(key, []).append(entry)

        inventory_errors = data.get("inventoryErrors")
        if isinstance(inventory_errors, list):
            for error in inventory_errors:
                if (not isinstance(error, dict) or not isinstance(error.get("itemType"), str)
                        or error["itemType"] not in kinds):
                    source_issue = "invalid_inventory_errors"
                    continue
                # A failed listing can hide undiscovered items even when other
                # items in this workspace (or another workspace) are known.
                add({
                    "workspaceId": error.get("workspaceId"),
                    "workspaceName": error.get("workspaceName"),
                }, error["itemType"])
        elif "inventoryErrors" in data:
            source_issue = "invalid_inventory_errors"
        incomplete = data.get("collectionComplete") is False or bool(data.get("failedWorkspaces"))
        for kind, inventory_key in kinds.items():
            if not any(key[2] == kind for key in candidates) and (
                source_issue or (incomplete and not isinstance(inventory_errors, list))
                or inventory_key not in data
            ):
                # No known item identity exists to attach this inventory gap to.
                candidates[(None, None, kind)] = _identity({}, kind)

        for key, identity in candidates.items():
            source = _SOURCES[identity["item_type"]]
            notices: set[str] = set()
            records: list[Any] = []
            status = "not_collected"
            explicit = per_item.get(key, [])
            if explicit:
                statuses = []
                for entry in explicit:
                    entry_status = entry.get("collectionStatus")
                    if not isinstance(entry_status, str) or entry_status not in COLLECTION_STATUSES:
                        entry_status = "invalid_data"
                        notices.add("invalid_collection_status")
                    statuses.append(entry_status)
                    notice = entry.get("noticeCode")
                    if isinstance(notice, str) and notice in _NOTICE_CODES:
                        notices.add(notice)
                    status_code = entry.get("statusCode")
                    if type(status_code) is int and 100 <= status_code <= 599:
                        notices.add(f"http_status={status_code}")
                    values = entry.get("executions")
                    if isinstance(values, list):
                        records.extend(values)
                        if entry_status == "empty" and values:
                            statuses.append("invalid_data")
                            notices.add("empty_status_with_records")
                    else:
                        statuses.append("invalid_data")
                        notices.add("invalid_execution_array")
                status = next((value for value in statuses if value not in ("collected", "empty")), "collected")
            elif not evidence_present:
                index_key = "refreshes" if source == "powerbi_refresh_history" else "jobs"
                history = data.get(index_key)
                item_id = identity["item_id"]
                matches = [value for native_id, value in history.items() if _identifier(native_id) == item_id] if isinstance(history, dict) and item_id else []
                # Legacy indexes have no workspace in their key: ambiguous IDs
                # must not borrow another workspace's history.
                ambiguous = sum(1 for candidate in candidates if candidate[1:] == key[1:]) > 1
                if ambiguous or len(matches) > 1:
                    notices.add("ambiguous_legacy_item_id")
                elif matches:
                    values = matches[0]
                    if isinstance(values, list):
                        records.extend(values)
                        status = "collected"
                        notices.add("legacy_snapshot")
                    else:
                        status = "invalid_data"
                        notices.add("invalid_execution_array")
                errors = data.get("refreshErrors")
                if isinstance(errors, dict) and item_id:
                    for native_id, error in errors.items():
                        if _identifier(native_id) == item_id:
                            status = _error_status(error)
                            notices.add("request_failed")
                if incomplete:
                    if records:
                        status = "partial"
                    elif status == "collected":
                        status = "not_collected"
                    notices.add("legacy_collection_incomplete")
            if not explicit:
                inventory_errors = data.get("inventoryErrors")
                if isinstance(inventory_errors, list):
                    for error in inventory_errors:
                        if isinstance(error, dict) and (
                            _identifier(error.get("workspaceId")) == identity["workspace_id"]
                            and error.get("itemType") == identity["item_type"]
                        ):
                            status = _error_status(error)
                            notices.add("inventory_request_failed")
                collection_errors = data.get("collectionErrors")
                if incomplete and isinstance(collection_errors, list) and collection_errors:
                    status = _error_status(collection_errors[0])
                    notices.add("source_collection_failed")
            if source_issue:
                notices.add(source_issue)
                status = "partial" if records else "invalid_data" if source_issue != "source_missing" else "not_collected"
            if not identity["workspace_id"] or not identity["item_id"]:
                if records:
                    notices.add(f"unattributed_records={len(records)}")
                records = []
                if status in ("collected", "empty"):
                    status = "not_collected"
                notices.add("missing_item_identity")
            rows, issues = _execution_rows(records, identity, source)
            notices.update(f"{code}={count}" for code, count in sorted(issues.items()))
            if issues:
                status = "partial" if rows else "invalid_data"
            elif status in ("collected", "empty"):
                status = "collected" if rows else "empty"
            elif rows:
                status = "partial"
            starts = [row["start_time"] for row in rows if row["start_time"] is not None]
            # Use parsed datetimes, not string order (fractional precision varies).
            ordered_starts = sorted(starts, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
            limitation = (
                "Recent retained observations only; not full weekly history. "
                "Power BI requests at most 10 refreshes; OneDrive refreshes are excluded."
                if source == "powerbi_refresh_history" else
                "Recent retained observations only; not full weekly history. "
                "Fabric usually retains 100 completed jobs; collection is bounded to 5 pages/1000 records."
            )
            if any(row["status"] == "unknown" for row in rows):
                notices.add("unknown_status_may_include_active_execution")
            if status == "not_collected":
                notices.add("history_not_collected")
            result["gold_item_executions"].extend({**run, **row} for row in rows)
            result["gold_execution_coverage"].append({
                **run, **identity, "collection_status": status,
                "observed_execution_count": len(rows),
                "oldest_start_time": ordered_starts[0] if ordered_starts else None,
                "newest_start_time": ordered_starts[-1] if ordered_starts else None,
                "history_scope": HISTORY_SCOPE,
                "notice": limitation + (" " + "; ".join(sorted(notices)) + "." if notices else ""),
                "source": source,
            })
    return result

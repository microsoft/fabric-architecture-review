# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Durable successful targeted-review history, independent of email delivery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from orchestration.fabric_api import guid


def cooldown_days(value: Any = "0") -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
        raise ValueError("SKIP_REVIEWED_WITHIN_DAYS must be a nonnegative integer string.")
    days = int(value)
    try:
        datetime.now(timezone.utc) - timedelta(days=days)
    except OverflowError as exc:
        raise ValueError("SKIP_REVIEWED_WITHIN_DAYS exceeds the supported date range.") from exc
    return days


def record_completed_review(
    config: dict[str, Any], parent_run_id: str, child_run_id: str, *, state_dir: Path,
) -> dict[str, Any]:
    """Called only by the parent's success-dependent completion activity."""
    parent_run_id, child_run_id = guid(parent_run_id), guid(child_run_id)
    workspace_id, pipeline_id = guid(config["CHILD_WORKSPACE_ID"]), guid(config["CHILD_PIPELINE_ID"])
    selection = json.loads((state_dir / (parent_run_id + ".json")).read_text(encoding="utf-8"))
    if (
        not isinstance(selection, dict) or selection.get("status") != "selected"
        or selection.get("parent_run_id") != parent_run_id
        or selection.get("child_workspace_id") != workspace_id
        or selection.get("child_pipeline_id") != pipeline_id
        or not isinstance(selection.get("workspaces"), list) or not selection["workspaces"]
    ):
        raise ValueError("Completed review does not match a nonempty selection for this FAR child.")
    workspace_ids = []
    for row in selection["workspaces"]:
        if not isinstance(row, dict) or not isinstance(row.get("workspace_id"), str):
            raise ValueError("Completed review selection has invalid workspace IDs.")
        workspace_ids.append(guid(row["workspace_id"]))
    if (
        len(workspace_ids) > 100 or len(set(workspace_ids)) != len(workspace_ids)
        or selection.get("workspace_ids") != ",".join(workspace_ids)
    ):
        raise ValueError("Completed review selection has inconsistent workspace IDs.")
    record = {
        "status": "succeeded", "parent_run_id": parent_run_id, "child_run_id": child_run_id,
        "child_workspace_id": workspace_id, "child_pipeline_id": pipeline_id,
        "completed_at": datetime.now(timezone.utc).isoformat(), "workspace_ids": workspace_ids,
    }
    directory = state_dir / "completed"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (parent_run_id + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    print(f"Recorded successful targeted review for {len(workspace_ids)} workspace(s).")
    return record


def get_recently_reviewed_workspace_ids(
    days: int, *, state_dir: Path, workspace_id: str, pipeline_id: str, now: datetime,
) -> set[str]:
    if days == 0:
        return set()
    if days < 0 or now.utcoffset() is None:
        raise ValueError("Review history requires nonnegative days and a timezone-aware clock.")
    cutoff = now - timedelta(days=days)
    workspace_id, pipeline_id = guid(workspace_id), guid(pipeline_id)
    directory = state_dir / "completed"
    try:
        directory.stat()
    except FileNotFoundError:
        print("No targeted-review completion history yet; pre-upgrade and standalone reviews are not excluded.")
        return set()
    excluded = set()
    for path in sorted(directory.iterdir()):
        if path.suffix != ".json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict) or record.get("status") != "succeeded"
            or not isinstance(record.get("completed_at"), str)
            or not isinstance(record.get("workspace_ids"), list) or not record["workspace_ids"]
            or any(not isinstance(record.get(key), str) for key in (
                "parent_run_id", "child_run_id", "child_workspace_id", "child_pipeline_id",
            ))
        ):
            raise ValueError(f"Invalid targeted-review completion record: {path.name}")
        if path.stem != guid(record["parent_run_id"]):
            raise ValueError(f"Targeted-review history filename does not match its parent: {path.name}")
        guid(record["child_run_id"])
        recorded_workspace, recorded_pipeline = guid(record["child_workspace_id"]), guid(record["child_pipeline_id"])
        ids = record["workspace_ids"]
        if (
            len(ids) > 100 or any(not isinstance(value, str) for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError(f"Invalid targeted-review workspace IDs: {path.name}")
        ids = [guid(value) for value in ids]
        completed_at = datetime.fromisoformat(record["completed_at"].replace("Z", "+00:00"))
        if completed_at.utcoffset() is None or completed_at > now:
            raise ValueError(f"Invalid targeted-review completion time: {path.name}")
        if (recorded_workspace, recorded_pipeline) == (workspace_id, pipeline_id) and completed_at > cutoff:
            excluded.update(ids)
    return excluded

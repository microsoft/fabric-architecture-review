# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared helpers for collectors.

Loads ``.env.far`` and exposes ``WORKSPACE_IDS`` scoping.

When ``WORKSPACE_IDS`` is set in the environment to a comma- or whitespace-
separated list of workspace GUIDs, every collector that enumerates workspaces
will filter to that set. When unset or empty, collectors operate tenant-wide
(default behaviour).
"""
from __future__ import annotations

import json
import os
import re
from functools import wraps
from pathlib import Path
from typing import Iterable, Set

from orchestration.project import load_environment
from collectors._http import HttpError
from collectors.workspace_scope import filter_review_payload, is_excluded_workspace
from collectors.workspace_evidence import merge_workspace_evidence

load_environment()

from orchestration.review_config import activate_from_environment

activate_from_environment()


def record_collection_failure(filename: str):
    """Persist unavailable evidence on HTTP failure, replacing stale output."""
    def decorate(collect):
        @wraps(collect)
        def wrapped(output_dir="output/far/raw", **options):
            try:
                return collect(output_dir, **options)
            except HttpError as exc:
                target = Path(output_dir) / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({
                    "collectionComplete": False,
                    "collectionErrors": [{"statusCode": exc.status_code, "message": str(exc)}],
                }, indent=2), encoding="utf-8")
                print(f"Collection incomplete: {exc}. Wrote {target}.")
                return target
        return wrapped
    return decorate


def collection_incomplete(data: object, *, include_errors: bool = False) -> bool:
    """Check inventory completeness; summaries can also count child probe errors."""
    return (
        not isinstance(data, dict)
        or data.get("collectionComplete") is False
        or bool(data.get("failedWorkspaces"))
        or (isinstance(data.get("_meta"), dict) and data["_meta"].get("complete") is False)
        or (include_errors and data.get("available") is False and data.get("skipped") is not True)
        or (include_errors and any(data.get(key) for key in (
            "errors", "refreshErrors", "collectionErrors", "inventoryErrors",
        )))
        or (include_errors and isinstance(data.get("queries"), dict) and any(
            isinstance(probe, dict) and probe.get("ok") is False
            for probe in data["queries"].values()
        ))
    )


def load_complete_raw(path: Path) -> dict:
    """Require a complete workspace inventory before deriving more evidence."""
    if not path.exists():
        raise HttpError(f"Required inventory {path.name} was not collected")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if collection_incomplete(data):
        raise HttpError(f"Required inventory {path.name} is incomplete")
    return filter_review_payload(data, path.parent)


def load_workspace_inventory(raw_dir: Path) -> list[dict]:
    """Load workspace identities; child users/items may be unavailable."""
    sources = {}
    for filename in ("scanner.json", "workspace_inventory.json"):
        path = raw_dir / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            continue
        if collection_incomplete(data) and not (
            filename == "workspace_inventory.json" and data.get("workspaceListComplete") is True
        ):
            continue
        if isinstance(data.get("workspaces"), list):
            sources[filename] = filter_review_payload(data, raw_dir)["workspaces"]
    if sources:
        return merge_workspace_evidence(sources.get("scanner.json", []), sources.get("workspace_inventory.json", []))
    raise HttpError("A complete scanner.json or workspace_inventory.json is required")

_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def get_scope_workspace_ids() -> Set[str]:
    """Return the set of workspace IDs in scope, or an empty set if unscoped."""
    raw = os.environ.get("WORKSPACE_IDS", "") or ""
    return {m.group(0).lower() for m in _GUID_RE.finditer(raw)}


RUN_SCOPE_ENV = "FAR_RUN_WORKSPACE_IDS"


def get_run_scope_workspace_ids() -> Set[str]:
    """The workspace scope of the whole review, which can be wider than the one a collector is given.

    The collection planner gives each collector only the workspaces that need its evidence, so the scope a
    collector reads from WORKSPACE_IDS says nothing about whether the review itself was limited. The planner
    records the review's own scope in FAR_RUN_WORKSPACE_IDS; without it the two are the same thing.
    """
    raw = os.environ.get(RUN_SCOPE_ENV)
    if raw is None:
        return get_scope_workspace_ids()
    return {m.group(0).lower() for m in _GUID_RE.finditer(raw)}


def filter_workspaces_by_scope(items: Iterable[dict], id_key: str = "id") -> list:
    """Explicit scope cannot override positive policy exclusions."""
    scope = get_scope_workspace_ids()
    return [w for w in items if not is_excluded_workspace(w)
            and (not scope or (w.get(id_key) or "").lower() in scope)]

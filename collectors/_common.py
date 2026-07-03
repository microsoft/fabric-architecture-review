# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared helpers for collectors.

Loads ``.env`` and exposes ``WORKSPACE_IDS`` scoping.

When ``WORKSPACE_IDS`` is set in the environment to a comma- or whitespace-
separated list of workspace GUIDs, every collector that enumerates workspaces
will filter to that set. When unset or empty, collectors operate tenant-wide
(default behaviour).
"""
from __future__ import annotations

import os
import re
from typing import Iterable, Set

from dotenv import load_dotenv

load_dotenv()

_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def get_scope_workspace_ids() -> Set[str]:
    """Return the set of workspace IDs in scope, or an empty set if unscoped."""
    raw = os.environ.get("WORKSPACE_IDS", "") or ""
    return {m.group(0).lower() for m in _GUID_RE.finditer(raw)}


def filter_workspaces_by_scope(items: Iterable[dict], id_key: str = "id") -> list:
    """Filter an iterable of workspace dicts by ``WORKSPACE_IDS``. If no scope
    is set, returns the items unchanged."""
    scope = get_scope_workspace_ids()
    if not scope:
        return list(items)
    return [w for w in items if (w.get(id_key) or "").lower() in scope]

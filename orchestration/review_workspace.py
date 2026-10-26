# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tell the review which workspace it runs in, from the notebook's own process.

Library code never asks Fabric for the workspace: it may run in a child process, where the notebook runtime is not
the one that owns the Spark session. Each stage notebook calls this once and child processes inherit the result.
"""
from __future__ import annotations

import os
from typing import Any


def export_review_workspace(notebookutils: Any) -> str:
    """Record the notebook's workspace in FAR_REVIEW_WORKSPACE_ID, or return "" when it cannot be read."""
    try:
        workspace = str(notebookutils.runtime.context.get("currentWorkspaceId") or "").strip().lower()
    except Exception:
        return ""
    if workspace:
        os.environ["FAR_REVIEW_WORKSPACE_ID"] = workspace
    return workspace

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Reads a collector's output the way a consumer would, for tests of what collectors write."""
import json
from pathlib import Path
from typing import Any, Dict, Optional

from collectors.workspace_scope import filter_review_payload


def load_raw(path: str | Path, *, allow_incomplete: bool = False) -> Optional[Dict[str, Any]]:
    """The scoped evidence, or None when the file is missing or marked incomplete."""
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8-sig") as f:
        data = filter_review_payload(json.load(f), p.parent)
    if not allow_incomplete and isinstance(data, dict) and (
        data.get("collectionComplete") is False
        or data.get("failedWorkspaces")
        or (isinstance(data.get("_meta"), dict) and data["_meta"].get("complete") is False)
    ):
        return None
    return data

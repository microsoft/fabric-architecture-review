# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Refresh the app's fail-closed compiled profile contract after analyzer edits."""
from __future__ import annotations

from pathlib import Path
import json
import re

from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256


def refresh(path: Path | None = None) -> None:
    path = path or Path(__file__).resolve().parents[1] / "fabric" / "app" / "src" / "lib" / "review-data.ts"
    text = path.read_text(encoding="utf-8")
    contract = (
        f"export const architectureProfile = {json.dumps(PROFILE_ID)};\n"
        f"export const architectureProfileHash = {json.dumps(PROFILE_SHA256)};\n"
        "export const activeArchitectureRules = new Set([\n"
        + "".join(f"    {json.dumps(rule)},\n" for rule in sorted(ACTIVE_RULE_IDS))
        + "]);"
    )
    updated, count = re.subn(
        r"export const architectureProfile = .*?export const activeArchitectureRules = new Set\(\[.*?\]\);",
        lambda _: contract, text, flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError("Cannot locate the app profile contract; no file changed.")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    refresh()

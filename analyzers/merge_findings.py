# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Merge every output/findings_*.json into output/findings.json (flat list).

Run after all analyzers have produced their per-dimension files.

DATA SAFETY: Re-serializes already-analyzed findings only. No data access.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, List


def _iter_files(out_dir: Path) -> Iterable[Path]:
    return sorted(p for p in out_dir.glob("findings_*.json") if p.is_file())


def merge(out_dir: Path) -> Path:
    merged: List[Any] = []
    for path in _iter_files(out_dir):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            merged.extend(data)
        elif isinstance(data, dict):
            merged.append(data)
    target = out_dir / "findings.json"
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Merged {len(merged)} findings from {sum(1 for _ in _iter_files(out_dir))} file(s) -> {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="output")
    args = parser.parse_args()
    merge(Path(args.out_dir))


if __name__ == "__main__":
    main()

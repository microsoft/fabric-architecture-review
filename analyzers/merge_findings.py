# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Merge every output/findings_*.json into output/findings.json (flat list).

Run after all analyzers have produced their per-dimension files.

DATA SAFETY: Re-serializes already-analyzed findings only. No data access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from analyzers._common import FINDING_STATUSES


def _iter_files(out_dir: Path) -> Iterable[Path]:
    return sorted(p for p in out_dir.glob("findings_*.json") if p.is_file())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge(out_dir: Path, run_id: str | None = None) -> Path:
    merged: List[Any] = []
    source_files = list(_iter_files(out_dir))
    artifacts: List[Dict[str, Any]] = []
    for path in source_files:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list) or not data:
            raise ValueError(f"Analyzer artifact must be a nonempty JSON list: {path}")
        for finding in data:
            if not isinstance(finding, dict) or finding.get("status") not in FINDING_STATUSES:
                raise ValueError(f"Analyzer artifact contains an invalid finding: {path}")
        merged.extend(data)
        artifacts.append({"name": path.name, "sha256": _sha256(path), "finding_count": len(data)})
    if not source_files:
        raise ValueError(f"No analyzer artifacts found in {out_dir}")

    target = out_dir / "findings.json"
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    checklist = repo_root / "config" / "review-checklist.yaml"
    analyzer_registry = repo_root / "config" / "analyzer-registry.yaml"
    thresholds = repo_root / "config" / "thresholds.yaml"
    status_counts = {status: 0 for status in sorted(FINDING_STATUSES)}
    for finding in merged:
        status_counts[finding["status"]] += 1
    manifest = {
        "schema_version": 1,
        "run_id": run_id or os.environ.get("RUN_ID") or "local",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "rule_catalog_sha256": _sha256(checklist),
        "analyzer_registry_sha256": _sha256(analyzer_registry),
        "thresholds_sha256": _sha256(thresholds),
        "findings_sha256": _sha256(target),
        "finding_count": len(merged),
        "status_counts": status_counts,
        "artifacts": artifacts,
    }
    manifest_path = out_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Merged {len(merged)} findings from {len(source_files)} file(s) -> {target}")
    print(f"Wrote run manifest -> {manifest_path}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="output")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    merge(Path(args.out_dir), args.run_id)


if __name__ == "__main__":
    main()

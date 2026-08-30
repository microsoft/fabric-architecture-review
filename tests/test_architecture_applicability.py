# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

from analyzers.architecture_review import analyze

CHECKLIST = Path(__file__).resolve().parents[1] / "config" / "review-checklist.yaml"


def _workspace(name: str, *item_types: str) -> dict:
    return {
        "id": name,
        "name": name,
        "type": "Workspace",
        "items": [{"id": f"{name}-{index}", "type": item_type}
                  for index, item_type in enumerate(item_types)],
    }


def _arch001(tmp_path: Path, workspaces: list[dict]) -> dict:
    (tmp_path / "scanner.json").write_text(
        json.dumps({"_meta": {"complete": True}, "workspaces": workspaces}), encoding="utf-8"
    )
    return next(
        finding for finding in analyze(tmp_path, CHECKLIST)
        if finding["rule_id"] == "ARCH-001"
    )


def test_non_engineering_workspaces_do_not_dilute_medallion_ratio(tmp_path: Path) -> None:
    finding = _arch001(tmp_path, [
        _workspace("engineering-prod", "Lakehouse", "DataPipeline"),
        _workspace("mirror-prod", "MirroredDatabase"),
        _workspace("executive-prod", "SemanticModel", "Report"),
        _workspace("telemetry-prod", "Eventhouse", "Eventstream"),
    ])
    assert finding["status"] == "fail"
    assert finding["evidence"]["evaluatedWorkspaceCount"] == 1
    assert finding["evidence"]["applicability"]["notApplicableCount"] == 3


def test_only_exempt_workspaces_is_not_applicable(tmp_path: Path) -> None:
    finding = _arch001(tmp_path, [_workspace("mirror-prod", "MirroredDatabase")])
    assert finding["status"] == "not_applicable"
    assert finding["evidence"]["evaluatedWorkspaceCount"] == 0


def test_mixed_workspace_is_unknown_not_exempt(tmp_path: Path) -> None:
    finding = _arch001(tmp_path, [_workspace("mixed-prod", "Lakehouse", "Eventhouse")])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["applicability"]["unknownCount"] == 1
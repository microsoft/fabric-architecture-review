# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""The report's storage-mode column is filled from real Fabric model definitions."""
import json

import pytest

from collectors.definition_safety import model_summary
from reports.gold_layer import build_gold
from tests._far_surfaces import MID, WID, finding, raw
from tests.test_definition_signals import definition, part


@pytest.mark.parametrize("tmdl, expected", [
    ("table A\n\tpartition A = entity\n\t\tmode: directLake\n", "DirectLake"),
    ("table A\n\tpartition A = m\n\t\tmode: import\n", "Import"),
    ("table A\n\tpartition A = entity\n\t\tmode: directLake\ntable B\n\tpartition B = m\n\t\tmode: import\n", "Composite"),
])
def test_gold_semantic_model_storage_mode_comes_from_the_definition(tmp_path, monkeypatch, tmdl, expected):
    raw(tmp_path, monkeypatch)
    summary = model_summary(definition(part("definition/tables/A.tmdl", tmdl)))
    (tmp_path / "semantic_model_definitions.json").write_text(json.dumps({"models": [
        {"id": MID, "workspaceId": WID, **summary},
    ]}), encoding="utf-8")
    tables = build_gold([finding("ARCH-001", status="fail")], tmp_path, run_id="modes",
                        run_timestamp="2026-10-13T08:00:00Z", check_remote=False)
    assert [row["storage_mode"] for row in tables["gold_semantic_models"]] == [expected]

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Former optimizer output is not part of the architectural Gold contract."""
import json
import pytest
from analyzers.far_profile import ACTIVE_RULE_IDS, RULE_KINDS
from reports.gold_layer import build_gold, build_gold_from_dir
from tests._far_surfaces import META, FORBIDDEN_TABLES, finding, raw


def test_stale_optimizer_raw_is_not_read_or_exposed(tmp_path, monkeypatch):
    tables = build_gold([finding()], raw(tmp_path, monkeypatch),
                        run_id="review", run_timestamp=META["run_timestamp"], check_remote=False)
    assert not FORBIDDEN_TABLES & tables.keys()
    assert "DO-NOT-EXPOSE" not in json.dumps(tables)
    assert {r["rule_id"] for r in tables["gold_findings"]} <= ACTIVE_RULE_IDS
    assert tables["gold_semantic_models"][0]["storage_mode"] == "DirectLake"
    assert tables["gold_run_summary"][0]["score"] == 0
    assert "cost" not in tables["gold_capacities"][0]


@pytest.mark.parametrize("hash_value", [None, "older-profile"])
def test_manifest_mismatch_never_certifies_current_findings(tmp_path, monkeypatch, hash_value):
    raw(tmp_path, monkeypatch)
    (tmp_path / "run_manifest.json").write_text(json.dumps({**META, "profile_sha256": hash_value}))
    tables = build_gold([finding()], tmp_path, run_id="review",
                        run_timestamp=META["run_timestamp"], check_remote=False)
    assert tables["gold_findings"] == []
    assert tables["gold_run_summary"][0]["score"] is None
    assert tables["gold_run_summary"][0]["score_status"] == "no_assessable_controls"


def test_public_output_directory_signature(tmp_path, monkeypatch):
    folder = tmp_path / "raw"
    folder.mkdir()
    raw(folder, monkeypatch)
    (tmp_path / "findings.json").write_text(json.dumps([finding()]))
    tables = build_gold_from_dir(tmp_path, run_id="review",
                                 run_timestamp=META["run_timestamp"], check_remote=False)
    assert len(tables["gold_findings"]) == 1


def test_signals_and_coverage_cannot_lower_score(tmp_path, monkeypatch):
    observations = [finding("ARCH-001", "pass"), finding("ARCH-008", "fail")]
    observations += [finding(rule, "fail") for rule, disposition in RULE_KINDS.items()
                     if disposition in {"signal", "coverage"}]
    tables = build_gold(observations, raw(tmp_path, monkeypatch), run_id="review",
                        run_timestamp=META["run_timestamp"], check_remote=False)
    summary = tables["gold_run_summary"][0]
    assert summary["assessed_count"] == 1
    assert summary["score"] == 100
    assert all(row["is_scored"] == 0 for row in tables["gold_findings"]
               if row["rule_id"] != "ARCH-001")

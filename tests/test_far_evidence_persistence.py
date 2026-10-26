# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from orchestration.review_config import capture, local_configuration, read_snapshot
from reports.far_evidence import persist


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    path = capture(tmp_path, local_configuration())
    snapshot = read_snapshot(path)
    payload = {
        "configuration_sha256": snapshot["sha256"],
        "captured_at_utc": "2026-01-20T12:00:00Z",
        "tables": {},
        "coverage": [{"source": "fuam", "status": "disabled"}],
    }
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "far_evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "pyspark.sql.types", SimpleNamespace(
        StructType=lambda fields: fields,
        StructField=lambda name, kind, nullable: (name, kind, nullable),
        StringType=lambda: "string",
    ))
    return tmp_path, payload


def spark_mock():
    frames = []
    def create(rows, schema):
        frame = SimpleNamespace(rows=rows, schema=schema, write=Mock())
        writer = frame.write
        writer.format.return_value = writer
        writer.mode.return_value = writer
        writer.option.return_value = writer
        writer.partitionBy.return_value = writer
        frames.append(frame)
        return frame
    return SimpleNamespace(createDataFrame=Mock(side_effect=create)), frames


def test_disabled_sources_still_replace_context_and_preserve_other_runs(evidence):
    folder, payload = evidence
    spark, frames = spark_mock()
    persist(spark, folder, "far-run-1")
    assert len(frames) == 6
    names = set()
    for frame in frames:
        frame.write.format.assert_called_once_with("delta")
        frame.write.mode.assert_called_once_with("overwrite")
        frame.write.option.assert_called_once_with("replaceWhere", "review_run_id = 'far-run-1'")
        frame.write.partitionBy.assert_called_once_with("review_run_id")
        names.add(frame.write.saveAsTable.call_args.args[0])
    assert names == {
        "far_evidence_workspaces", "far_evidence_items", "far_evidence_capacities",
        "far_evidence_capacity_resources", "far_evidence_coverage", "far_evidence_tenant_settings",
    }
    assert frames[-1].rows == [(
        "far-run-1", payload["configuration_sha256"], payload["captured_at_utc"],
        json.dumps(payload["coverage"][0], sort_keys=True),
    )]


def test_invalid_last_table_is_rejected_before_any_delta_write(evidence):
    folder, payload = evidence
    payload["tables"]["far_evidence_capacity_resources"] = [{"unexpected": "column"}]
    (folder / "raw" / "far_evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    spark, frames = spark_mock()
    with pytest.raises(ValueError, match="Incompatible"):
        persist(spark, folder, "far-run-1")
    assert all(not frame.write.saveAsTable.called for frame in frames)


def test_configuration_mismatch_is_rejected_before_dataframe_creation(evidence):
    folder, payload = evidence
    payload["configuration_sha256"] = "different-run"
    (folder / "raw" / "far_evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    spark, frames = spark_mock()
    with pytest.raises(ValueError, match="frozen"):
        persist(spark, folder, "far-run-1")
    spark.createDataFrame.assert_not_called()


@pytest.mark.parametrize("run_id", ["", "run' OR 1=1", "../legacy"])
def test_invalid_run_id_never_creates_a_dataframe(evidence, run_id):
    spark, frames = spark_mock()
    with pytest.raises(ValueError, match="run ID"):
        persist(spark, evidence[0], run_id)
    spark.createDataFrame.assert_not_called()

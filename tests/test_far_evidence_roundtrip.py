# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Evidence rows produced by the collector must be accepted by the persistence stage."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from collectors.far_evidence import CONTRACTS, PROVENANCE, TENANT_HISTORY_TARGET, bind
from orchestration.review_config import capture, local_configuration, read_snapshot
from reports.far_evidence import persist
from tests.test_far_evidence import ARM, CAPACITY, NOW, OTHER, WORKSPACE, fuam_query


def both_sources_query(source, statement):
    if source == "fuam":
        return fuam_query(source, statement)
    if "dbo.focus" in statement:
        return [{"ResourceId": ARM, "Tags": '{"owner": "x"}'}]
    return [{"ResourceId": ARM, "ResourceName": "cap", "RegionId": "westeurope", "ResourceKey": 1}]


@pytest.fixture
def persisted(tmp_path, monkeypatch):
    for name in ("WORKSPACES_CONFIG", "FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    config = local_configuration()
    config["sources"]["fuam"].update(enabled=True, workspace_id=WORKSPACE, lakehouse_id=OTHER)
    config["sources"]["fca"].update(enabled=True, workspace_id=WORKSPACE, lakehouse_id=OTHER)
    config["profiles"]["capacities"] = [{"id": CAPACITY, "arm_resource_id": ARM}]
    snapshot = read_snapshot(capture(tmp_path, config))
    evidence = bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], both_sources_query, now=NOW)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "far_evidence.json").write_text(json.dumps(evidence, default=str), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "pyspark.sql.types", SimpleNamespace(
        StructType=lambda fields: fields,
        StructField=lambda name, kind, nullable: (name, kind, nullable),
        StringType=lambda: "string",
    ))
    def create(rows, schema):
        frame = SimpleNamespace(rows=rows, schema=schema, write=Mock())
        for method in ("format", "mode", "option", "partitionBy"):
            getattr(frame.write, method).return_value = frame.write
        return frame

    spark = SimpleNamespace(createDataFrame=Mock(side_effect=create))
    persist(spark, tmp_path, "far-run-1")
    return evidence, spark


def test_real_collector_rows_are_persisted_for_every_evidence_table(persisted):
    evidence, spark = persisted
    names = set(evidence["tables"])
    assert names == {value[2] for value in CONTRACTS.values()} | {TENANT_HISTORY_TARGET, "far_evidence_capacity_resources"}
    assert all(evidence["tables"][name] for name in names)
    assert spark.createDataFrame.call_count == len(names) + 1  # evidence tables plus coverage


def test_every_persisted_row_has_the_full_provenance_columns(persisted):
    evidence, spark = persisted
    for name, rows in evidence["tables"].items():
        for row in rows:
            assert set(PROVENANCE) <= set(row), name
    for call in spark.createDataFrame.call_args_list:
        rows, schema = call.args
        for row in rows:
            assert len(row) == len(schema)
            assert all(value is None or isinstance(value, str) for value in row)
        columns = [field[0] for field in schema]
        if "source_module" in columns:
            assert set(PROVENANCE) <= set(columns)
            assert {"review_run_id", "captured_at_utc"} <= set(columns)

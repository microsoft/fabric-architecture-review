# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persist provenance-bearing upstream context separately from scored findings."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from collectors.far_evidence import CONTRACTS, PROVENANCE, TENANT_HISTORY_COLUMNS, TENANT_HISTORY_TARGET
from orchestration.review_config import read_snapshot


def persist(spark: Any, out_dir: Path, run_id: str) -> None:
    from pyspark.sql.types import StructType, StructField, StringType
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid FAR evidence run ID.")
    evidence = json.loads((out_dir / "raw" / "far_evidence.json").read_text(encoding="utf-8"))
    snapshot = read_snapshot(out_dir / "review_config.json")
    if evidence["configuration_sha256"] != snapshot["sha256"]:
        raise ValueError("FAR evidence does not match the frozen review configuration.")
    tables = {target: columns for _, columns, target in CONTRACTS.values()}
    tables[TENANT_HISTORY_TARGET] = TENANT_HISTORY_COLUMNS
    tables["far_evidence_capacity_resources"] = ("ResourceId", "ResourceName", "RegionId", "ResourceKey", "capacity_id")
    if set(evidence["tables"]) - set(tables):
        raise ValueError("Unexpected FAR evidence table.")
    frames = {}
    for name, fields in tables.items():
        columns = (*fields, *PROVENANCE, "review_run_id", "captured_at_utc")
        rows = []
        for row in evidence["tables"].get(name, []):
            if set(row) != set((*fields, *PROVENANCE)):
                raise ValueError(f"Incompatible FAR evidence schema: {name}")
            record = {**row, "review_run_id": run_id, "captured_at_utc": evidence["captured_at_utc"]}
            rows.append(tuple(None if record[key] is None else str(record[key]) for key in columns))
        schema = StructType([StructField(key, StringType(), True) for key in columns])
        frames[name] = spark.createDataFrame(rows, schema)
    coverage = [
        (run_id, snapshot["sha256"], evidence["captured_at_utc"], json.dumps(entry, sort_keys=True))
        for entry in evidence["coverage"]
    ]
    schema = StructType([
        StructField(key, StringType(), False)
        for key in ("review_run_id", "configuration_sha256", "captured_at_utc", "detail_json")
    ])
    frames["far_evidence_coverage"] = spark.createDataFrame(coverage, schema)
    for name, frame in frames.items():
        frame.write.format("delta").mode("overwrite").option(
            "replaceWhere", f"review_run_id = '{run_id}'",
        ).partitionBy("review_run_id").saveAsTable(name)

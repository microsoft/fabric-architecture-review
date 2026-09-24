# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric-only Delta materialization; imports Spark only when called."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable
from uuid import uuid4


_CONFLICTS = {
    "ConcurrentAppendException", "ConcurrentDeleteReadException",
    "ConcurrentDeleteDeleteException", "ConcurrentWriteException",
    "MetadataChangedException",
}


def _is_delta_conflict(error: Exception) -> bool:
    if type(error).__name__ in _CONFLICTS:
        return True
    java_error = getattr(error, "java_exception", None)
    if java_error is not None:
        return java_error.getClass().getSimpleName() in _CONFLICTS
    return False


def _retry_delta(operation: Callable[[], Any], attempts: int = 4) -> Any:
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            if not _is_delta_conflict(error) or attempt == attempts - 1:
                raise


def _prepare_owner_merge(spark: Any, name: str) -> None:
    if name not in {"owner_reviews", "owner_workspaces"}:
        raise ValueError("Unsupported owner transaction table.")
    # The dimension reads another table; do not allow an asynchronous/stale log snapshot.
    spark.conf.set("spark.databricks.delta.stalenessLimit", "0")
    properties = spark.sql(f"DESCRIBE DETAIL {name}").select("properties").first()[0]
    if properties.get("delta.isolationLevel") != "Serializable":
        spark.sql(
            f"ALTER TABLE {name} SET TBLPROPERTIES ('delta.isolationLevel' = 'Serializable')"
        )
    # Cached/checkpointed sources bypass Delta's transactional source scan.
    for table in {name, "owner_reviews"}:
        if spark.catalog.isCached(table):
            spark.catalog.uncacheTable(table)
        spark.catalog.refreshTable(table)


def _publish_owner_reviews(
    spark: Any, incoming: Any, run_id: str,
) -> None:
    """Use supported Delta SQL MERGE; never detach the source snapshot.

    In Delta 3.2, MergeIntoCommand.runMerge prepares/scans its source within
    withNewTransaction. PrepareDeltaScan and getDeltaScanGenerator use that
    active transaction for self-reads. NOT MATCHED BY SOURCE forces a full
    target read in ClassicMergeExecutor, including when the target is empty.
    Serializable isolation therefore rejects concurrent inserts and rewrites.
    Each retry executes a new statement and recomputes latest flags.

    The dimension MERGE starts its transaction before scanning reviews, so
    concurrent dimension publications conflict rather than regress names.
    Separate table commits are not atomic; replay the run after partial failure.
    """
    from reports.owner.schema import OWNER_TABLES_BY_NAME

    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", run_id):
        raise ValueError("Invalid owner review run ID.")
    columns = ", ".join(
        f"`{column.name}`" for column in OWNER_TABLES_BY_NAME["owner_reviews"].columns
        if column.name != "is_latest"
    )
    view = "_owner_incoming_" + uuid4().hex
    incoming.createOrReplaceTempView(view)
    try:
        statements = {
            "owner_reviews": f"""
                MERGE INTO owner_reviews AS target
                USING (
                    SELECT {columns},
                        (ROW_NUMBER() OVER (
                            PARTITION BY workspace_id
                            ORDER BY run_timestamp DESC, run_id DESC
                        ) = 1) AS is_latest
                    FROM (
                        SELECT * FROM owner_reviews WHERE run_id <> '{run_id}'
                        UNION ALL
                        SELECT * FROM `{view}`
                    ) AS combined
                ) AS source
                ON target.review_key = source.review_key
                WHEN MATCHED THEN UPDATE SET *
                WHEN NOT MATCHED THEN INSERT *
                WHEN NOT MATCHED BY SOURCE THEN DELETE
            """,
            "owner_workspaces": """
                MERGE INTO owner_workspaces AS target
                USING (
                    SELECT workspace_id, workspace_name
                    FROM owner_reviews WHERE is_latest
                ) AS source
                ON target.workspace_id = source.workspace_id
                WHEN MATCHED THEN UPDATE SET *
                WHEN NOT MATCHED THEN INSERT *
                WHEN NOT MATCHED BY SOURCE THEN DELETE
            """,
        }
        for name, statement in statements.items():
            def attempt() -> None:
                _prepare_owner_merge(spark, name)
                spark.sql(statement).collect()
            _retry_delta(attempt)
    finally:
        spark.catalog.dropTempView(view)


def _frame(spark: Any, table: Any, rows: list[dict[str, Any]]) -> Any:
    from pyspark.sql.types import (
        BooleanType, DoubleType, LongType, StringType, StructField, StructType, TimestampType,
    )

    types = {
        "string": StringType(), "int64": LongType(), "double": DoubleType(),
        "boolean": BooleanType(), "dateTime": TimestampType(),
    }
    schema = StructType([StructField(c.name, types[c.kind], True) for c in table.columns])
    values = []
    for row in rows:
        record = []
        for column in table.columns:
            value = row.get(column.name)
            if column.kind == "dateTime" and value is not None:
                if isinstance(value, str):
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if not isinstance(value, datetime):
                    raise ValueError("Owner table timestamp is invalid.")
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                else:
                    value = value.astimezone(timezone.utc)
            record.append(value)
        values.append(tuple(record))
    return spark.createDataFrame(values, schema)


def bootstrap_owner_tables(spark: Any, table_root: str) -> None:
    from reports.owner.schema import OWNER_TABLES

    for table in OWNER_TABLES:
        (_frame(spark, table, []).write.format("delta").mode("ignore")
         .save(table_root.rstrip("/") + "/" + table.name))


def _validate_owner_schemas(spark: Any, frames: dict[str, Any]) -> None:
    """Refuse schema drift before replacing any run; never overwrite a schema."""
    for name, frame in frames.items():
        if not spark.catalog.tableExists(name):
            if name not in {"owner_executions", "owner_coverage"}:
                raise ValueError(f"Missing owner table {name}; complete owner setup first.")
            (frame.limit(0).write.format("delta").mode("ignore").saveAsTable(name))
        expected = [(field.name, field.dataType) for field in frame.schema.fields]
        actual = [(field.name, field.dataType) for field in spark.table(name).schema.fields]
        if actual != expected:
            raise ValueError(f"Incompatible owner table schema: {name}. No schema overwrite performed.")


def materialize_owner_gold(
    spark: Any, governance_tables: dict[str, list[dict[str, Any]]],
) -> bool:
    """Optional, offline projection. Entitlements are never copied from a run."""
    if not spark.catalog.tableExists("owner_access"):
        print("Owner projection skipped: owner_access is absent; owner reporting has not been provisioned.")
        return False

    from reports.owner.gold import build_owner_gold
    from reports.owner.schema import OWNER_TABLES_BY_NAME

    tables = build_owner_gold(governance_tables)
    if not tables["owner_reviews"]:
        raise RuntimeError(
            "Owner projection has no attributable workspace reviews. No owner tables were changed. "
            "Check this run's gold_workspaces and workspace/Scanner collection evidence; "
            "existing owner report rows may belong to older runs."
        )

    from pyspark.sql import functions as F

    frames = {name: _frame(spark, OWNER_TABLES_BY_NAME[name], rows)
              for name, rows in tables.items() if name != "owner_workspaces"}
    _validate_owner_schemas(spark, {
        **frames,
        "owner_workspaces": _frame(spark, OWNER_TABLES_BY_NAME["owner_workspaces"], []),
        "owner_access": _frame(spark, OWNER_TABLES_BY_NAME["owner_access"], []),
    })
    run_ids = {str(row["run_id"]) for row in governance_tables["gold_run_summary"]}
    if len(run_ids) != 1:
        raise ValueError("Owner materialization requires exactly one review run.")
    run_id = run_ids.pop()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", run_id):
        raise ValueError("Owner review IDs require 1-256 letters, digits, underscores, dots, colons or hyphens.")
    predicate = f"run_id = '{run_id}'"
    for name in ("owner_findings", "owner_details", "owner_executions", "owner_coverage"):
        _retry_delta(lambda: (
            frames[name].write.format("delta").mode("overwrite")
            .option("replaceWhere", predicate).saveAsTable(name)
        ))
        actual = spark.table(name).where(F.col("run_id") == F.lit(run_id)).count()
        if actual != len(tables[name]):
            raise RuntimeError(f"{name} row count verification failed.")
        print(f"{name}: replaced run {run_id} with {actual} row(s).")

    _publish_owner_reviews(spark, frames["owner_reviews"], run_id)
    print(f"owner_reviews: published run {run_id} for {len(tables['owner_reviews'])} workspace(s).")
    print("Owner projection updated; access grants require the independent daily sync.")
    return True

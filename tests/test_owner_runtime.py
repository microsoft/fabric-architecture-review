# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""SQL projection/interleaving tests; fake commits do not certify Delta OCC."""
from copy import deepcopy
import sqlite3
from unittest.mock import MagicMock

import pytest

from reports.owner import fabric_runtime as runtime
from reports.owner.schema import OWNER_TABLES_BY_NAME


class ConcurrentAppendException(Exception):
    pass


class Result:
    def __init__(self, value=None):
        self.value = value

    def select(self, name):
        assert name == "properties"
        return self

    def first(self):
        return [self.value]

    def collect(self):
        return []


class Incoming:
    def __init__(self, store, rows):
        self.store, self.rows = store, rows

    def createOrReplaceTempView(self, name):
        assert name not in self.store.views
        self.store.views[name] = self.rows


class Store:
    """Execute the helper's actual source SELECT in SQLite, fake only Delta commit."""

    def __init__(self):
        self.rows = {"owner_reviews": [], "owner_workspaces": []}
        self.versions = dict.fromkeys(self.rows, 0)
        self.properties = {name: {} for name in self.rows}
        self.before_commit = None
        self.attempts = dict.fromkeys(self.rows, 0)
        self.conflicts = 0
        self.views = {}
        self.cached = set(self.rows)
        self.catalog = self
        self.conf = self
        self.settings = {}

    def set(self, name, value):
        assert name == "spark.databricks.delta.stalenessLimit"
        self.settings[name] = value

    def isCached(self, name):
        return name in self.cached

    def uncacheTable(self, name):
        self.cached.remove(name)

    def refreshTable(self, name):
        assert name in self.rows

    def dropTempView(self, name):
        del self.views[name]

    def sql(self, statement):
        statement = statement.strip()
        if statement.startswith("DESCRIBE DETAIL "):
            return Result(self.properties[statement.split()[-1]])
        if statement.startswith("ALTER TABLE "):
            assert "('delta.isolationLevel' = 'Serializable')" in statement
            self.properties[statement.split()[2]]["delta.isolationLevel"] = "Serializable"
            return Result()
        name = statement.split()[2]
        assert statement.startswith(f"MERGE INTO {name} AS target")
        assert "WHEN MATCHED THEN UPDATE SET *" in statement
        assert "WHEN NOT MATCHED THEN INSERT *" in statement
        assert "WHEN NOT MATCHED BY SOURCE THEN DELETE" in statement
        assert self.properties[name]["delta.isolationLevel"] == "Serializable"
        assert self.settings["spark.databricks.delta.stalenessLimit"] == "0"
        assert not ({name, "owner_reviews"} & self.cached)
        version = self.versions[name]
        self.attempts[name] += 1
        source = statement.split("USING (", 1)[1].rsplit(") AS source", 1)[0]
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            for table, rows in {**self.rows, **self.views}.items():
                schema = OWNER_TABLES_BY_NAME[
                    "owner_reviews" if table in self.views else table
                ].columns
                columns = ", ".join(f"`{column.name}`" for column in schema)
                connection.execute(f"CREATE TABLE `{table}` ({columns})")
                for row in rows:
                    placeholders = ", ".join("?" for _ in schema)
                    connection.execute(
                        f"INSERT INTO `{table}` VALUES ({placeholders})",
                        [row[column.name] for column in schema],
                    )
            rows = [dict(row) for row in connection.execute(source)]
        hook = self.before_commit
        if hook is not None:
            hook(name)
        if version != self.versions[name]:
            self.conflicts += 1
            raise ConcurrentAppendException()
        self.rows[name] = rows
        self.versions[name] += 1
        return Result()


@pytest.fixture
def store():
    return Store()


def review(run, workspace, stamp, name=None):
    return {
        **dict.fromkeys(column.name for column in OWNER_TABLES_BY_NAME["owner_reviews"].columns),
        "review_key": f"{run}:{workspace}",
        "run_id": run, "workspace_id": workspace, "run_timestamp": stamp,
        "workspace_name": name or workspace, "is_latest": True,
    }


def publish(store, rows, run=None):
    runtime._publish_owner_reviews(
        store, Incoming(store, rows), run or rows[0]["run_id"],
    )


@pytest.mark.parametrize("workspace_b", ["a", "b"])
@pytest.mark.parametrize("stamp_a,stamp_b", [(1, 2), (2, 1), (1, 1)])
def test_stale_review_snapshot_cannot_delete_concurrent_run(
    store, workspace_b, stamp_a, stamp_b,
):
    a = [review("run-a", "a", stamp_a, "Name A")]
    b = [review("run-b", workspace_b, stamp_b, "Name B")]

    def interleave(name):
        if name == "owner_reviews":
            store.before_commit = None
            publish(store, b)

    store.before_commit = interleave
    publish(store, a)
    assert store.conflicts == 1
    assert store.attempts["owner_reviews"] == 3
    assert {row["run_id"] for row in store.rows["owner_reviews"]} == {"run-a", "run-b"}
    latest = [row for row in store.rows["owner_reviews"] if row["is_latest"]]
    if workspace_b == "a":
        winner = max(a + b, key=lambda row: (row["run_timestamp"], row["run_id"]))
        assert latest == [winner]
    else:
        assert {row["workspace_id"] for row in latest} == {"a", "b"}
    assert store.rows["owner_workspaces"] == [
        {key: row[key] for key in ("workspace_id", "workspace_name")} for row in latest
    ]
    before = deepcopy(store.rows)
    publish(store, a)
    assert sorted(store.rows["owner_reviews"], key=lambda row: row["run_id"]) == sorted(
        before["owner_reviews"], key=lambda row: row["run_id"],
    )
    assert store.rows["owner_workspaces"] == before["owner_workspaces"]
    assert not store.views


@pytest.mark.parametrize("workspace_b", ["a", "b"])
def test_stale_dimension_snapshot_reloads_reviews_on_conflict(store, workspace_b):
    def interleave(name):
        if name == "owner_workspaces":
            store.before_commit = None
            publish(store, [review("run-b", workspace_b, 2, "New B")])

    store.before_commit = interleave
    publish(store, [review("run-a", "a", 1, "Old A")])
    assert store.conflicts == 1
    assert store.attempts["owner_workspaces"] == 3
    expected = [{"workspace_id": workspace_b, "workspace_name": "New B"}]
    if workspace_b == "b":
        expected.insert(0, {"workspace_id": "a", "workspace_name": "Old A"})
    assert store.rows["owner_workspaces"] == expected
    assert len(store.rows["owner_reviews"]) == 2
    assert not store.views


def test_partial_publication_is_recoverable_by_same_run_retry(store):
    def fail_dimension(name):
        if name == "owner_workspaces":
            store.before_commit = None
            raise RuntimeError("synthetic write failure")

    store.before_commit = fail_dimension
    a = [review("run-a", "a", 1)]
    with pytest.raises(RuntimeError, match="synthetic write failure"):
        publish(store, a)
    assert store.rows["owner_reviews"] == a
    assert store.rows["owner_workspaces"] == []
    assert not store.views
    publish(store, a)
    assert store.rows["owner_reviews"] == a
    assert store.rows["owner_workspaces"] == [{"workspace_id": "a", "workspace_name": "a"}]


def test_replay_can_remove_workspaces_from_a_run_without_removing_other_runs(store):
    publish(store, [review("run-a", "a", 1), review("run-a", "b", 1)])
    publish(store, [review("run-b", "b", 2)])
    publish(store, [], run="run-a")
    assert store.rows["owner_reviews"] == [review("run-b", "b", 2)]
    assert store.rows["owner_workspaces"] == [{"workspace_id": "b", "workspace_name": "b"}]


def test_conflicts_are_bounded_and_other_failures_are_not_retried():
    operation = MagicMock(side_effect=ConcurrentAppendException())
    with pytest.raises(ConcurrentAppendException):
        runtime._retry_delta(operation)
    assert operation.call_count == 4
    operation = MagicMock(side_effect=ValueError("schema"))
    with pytest.raises(ValueError, match="schema"):
        runtime._retry_delta(operation)
    operation.assert_called_once()


def test_jvm_conflict_is_recognized_without_retrying_arbitrary_java_errors():
    error = RuntimeError()
    error.java_exception = MagicMock()
    error.java_exception.getClass().getSimpleName.return_value = "ConcurrentDeleteReadException"
    assert runtime._is_delta_conflict(error)
    error.java_exception.getClass().getSimpleName.return_value = "AnalysisException"
    assert not runtime._is_delta_conflict(error)


@pytest.mark.parametrize("run", ["bad'run", "", "a" * 257])
def test_run_ids_are_validated_before_sql_or_view_creation(store, run):
    with pytest.raises(ValueError, match="run ID"):
        runtime._publish_owner_reviews(store, Incoming(store, []), run)
    assert not store.views
    assert sum(store.attempts.values()) == 0

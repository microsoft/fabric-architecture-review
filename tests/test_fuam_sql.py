# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Read-only source sessions never hold transactions across long API collectors."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from collectors import fuam_sql
from orchestration import config_deployment


def test_read_only_session_uses_autocommit_reuses_batch_connection_and_closes(monkeypatch):
    class Connection:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, kind, value, traceback):
            self.closed = True
            if not self.autocommit:
                raise RuntimeError("Driver Error: Communication link failure; TCP Provider 0x2746")

    connection = Connection()

    def connect(server, database, token, *, autocommit=False):
        connection.autocommit = autocommit
        return connection

    factory = Mock(side_effect=connect)
    monkeypatch.setattr(config_deployment, "sql_connection", factory)
    monkeypatch.setattr(config_deployment, "endpoint", Mock(return_value=("synthetic.fabric.microsoft.com", "source")))
    monkeypatch.setattr(config_deployment, "sql_rows", Mock(return_value=[{"value": 1}]))
    with fuam_sql.Session({"configuration": {"sources": {"fuam": {"workspace_id": "w", "lakehouse_id": "l"}}}}) as session:
        session._client = object()
        session._sql_token = lambda: "synthetic"
        assert session.query("fuam", "SELECT 1") == [{"value": 1}]
        assert session.query("fuam", "SELECT 2") == [{"value": 1}]
    factory.assert_called_once_with("synthetic.fabric.microsoft.com", "source", "synthetic", autocommit=True)
    assert connection.closed and session._connections == {}


def test_session_exit_passes_query_exception_to_driver_and_clears_state():
    seen = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, kind, value, traceback):
            seen.append(kind)

    session = fuam_sql.Session({})
    session._connections["fuam"] = session._stack.enter_context(Connection())
    with pytest.raises(ValueError, match="query failed"):
        with session:
            raise ValueError("query failed")
    assert seen == [ValueError] and not session._connections


def test_sql_connection_default_transactions_and_explicit_read_only_mode(monkeypatch):
    import mssql_python
    connect = Mock()
    monkeypatch.setattr(mssql_python, "connect", connect)
    config_deployment.sql_connection("example.fabric.microsoft.com", "source", "token")
    assert connect.call_args.kwargs["autocommit"] is False
    config_deployment.sql_connection("example.fabric.microsoft.com", "source", "token", autocommit=True)
    assert connect.call_args.kwargs["autocommit"] is True


def test_collection_closes_source_sessions_before_native_api_work(tmp_path, monkeypatch):
    from collectors import far_collection
    from tests.test_far_collection_runtime import snapshot, WORKSPACE
    frozen = snapshot()
    frozen["configuration"]["sources"]["fuam"]["enabled"] = True
    monkeypatch.setenv("FAR_CONFIG_SNAPSHOT", "synthetic")
    monkeypatch.setenv("WORKSPACE_IDS", WORKSPACE)
    monkeypatch.setattr(far_collection, "read_snapshot", lambda _: frozen)
    monkeypatch.setattr(far_collection, "load_workspace_inventory", lambda _: [{"id": WORKSPACE}])
    state = {"open": 0, "batches": 0}

    class Session:
        def __init__(self, snapshot):
            pass

        def __enter__(self):
            assert state["open"] == 0
            state["open"] += 1
            state["batches"] += 1
            return self

        def query(self, *args):
            assert state["open"] == 1
            return []

        def __exit__(self, *args):
            state["open"] -= 1

    def artifact(raw_dir, filename):
        path = raw_dir / filename
        path.write_text('{"collectionComplete": true}')
        return path

    def inventory(*, output_dir, fuam_reader):
        fuam_reader({WORKSPACE})
        assert state["open"] == 0
        return artifact(tmp_path, "workspace_inventory.json")

    def source(name, raw_dir, snapshot, query, **kwargs):
        query("fuam", "SELECT 1")
        return artifact(raw_dir, far_collection.ARTIFACTS[name])

    def native(name):
        def collect(*, output_dir):
            assert state["open"] == 0, "A SQL connection was held across native API collection"
            short = name.removeprefix("collectors.")
            filename = far_collection.ARTIFACTS.get(short, {
                "scanner_api": "scanner.json", "far_evidence": "far_evidence.json",
            }.get(short))
            return artifact(tmp_path, filename)
        return SimpleNamespace(collect=collect)

    monkeypatch.setattr(far_collection, "Session", Session)
    monkeypatch.setattr(far_collection.workspace_inventory, "collect", inventory)
    monkeypatch.setattr(far_collection.fuam_inventory, "read", lambda snapshot, query, *args: query("fuam", "SELECT 1"))
    monkeypatch.setattr(far_collection.fuam_sourced, "collect", source)
    monkeypatch.setattr(far_collection.importlib, "import_module", native)
    grant = SimpleNamespace(settings=SimpleNamespace(enabled=False), audit=[],
                            retry_collection=lambda produce, *args: produce())
    assert far_collection._collect(tmp_path, grant).exists()
    assert state["open"] == 0 and state["batches"] >= 2

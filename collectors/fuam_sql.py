# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Read-only SQL access to the connected FUAM and FCA Lakehouses, shared by every reader of them."""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any


class Session:
    """Opens each module's SQL analytics endpoint on first use and closes all of them on exit.

    The review identity needs read access only. Locally it signs in like every other collector; in a
    Fabric notebook it uses the notebook's own tokens.
    """

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self._stack = ExitStack()
        self._connections: dict[str, Any] = {}
        self._client: Any = None
        self._sql_token: Any = None

    def _credentials(self) -> None:
        if self._client is not None:
            return
        from orchestration.fabric_api import FabricClient
        if self.snapshot["configuration"]["origin"] == "fabric":
            import notebookutils
            api_token = lambda: notebookutils.credentials.getToken("pbi")
            self._sql_token = lambda: notebookutils.credentials.getToken("https://database.windows.net/")
        else:
            from collectors.auth import FABRIC_SCOPE, get_default_provider
            provider = get_default_provider()
            api_token = lambda: provider.get_token(FABRIC_SCOPE)
            self._sql_token = lambda: provider.get_token("https://database.windows.net/.default")
        self._client = FabricClient(api_token)

    def query(self, source: str, statement: str) -> list[dict[str, Any]]:
        from orchestration.config_deployment import endpoint, sql_connection, sql_rows
        if source not in self._connections:
            self._credentials()
            connection = self.snapshot["configuration"]["sources"][source]
            server, database = endpoint(
                self._client, connection["workspace_id"], connection["lakehouse_id"], "lakehouses",
            )
            self._connections[source] = self._stack.enter_context(sql_connection(server, database, self._sql_token()))
        return sql_rows(self._connections[source], statement)

    def close(self) -> None:
        self._stack.close()
        self._connections.clear()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

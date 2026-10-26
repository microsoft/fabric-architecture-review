# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric-only targeted TOM edits; no model replacement or permission API writes."""
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID

if TYPE_CHECKING:
    from azure.core.credentials import AccessToken

from orchestration.fabric_api import FabricClient


class _NotebookToken:
    def __init__(self, token: Callable[[], str]) -> None:
        self._token = token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        from azure.core.credentials import AccessToken

        token = self._token()
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expiry = claims.get("exp")
        if not isinstance(expiry, int):
            raise ValueError("Notebook token lacks a valid expiry for TOM authentication.")
        return AccessToken(token, expiry)


class TomEditor:
    def __init__(
        self, client: FabricClient, workspace_id: str, model_id: str, *, readonly: bool,
    ) -> None:
        try:
            import sempy.fabric as fabric
        except ImportError as exc:
            raise RuntimeError(
                "Owner upgrade requires Fabric Semantic Link (sempy.fabric) and XMLA access. "
                "Enable XMLA read-write on the capacity and use a deployer with model write permission."
            ) from exc
        self._fabric = fabric
        self._workspace = UUID(workspace_id)
        self._credential = _NotebookToken(client.token)
        self._readonly = readonly
        self._server = fabric.create_tom_server(
            dataset=UUID(model_id), workspace=self._workspace,
            readonly=readonly, credential=self._credential,
        )
        from Microsoft.AnalysisServices import Tabular as tom

        self._tom = tom
        matches = [database for database in self._server.Databases
                   if str(database.ID).lower() == model_id.lower()]
        if len(matches) != 1:
            self.close()
            raise ValueError("TOM did not return the exact owner model ID.")
        self._database = matches[0]

    def read(self) -> dict[str, Any]:
        self._database.Refresh()
        return self._serialize()

    def _serialize(self) -> dict[str, Any]:
        options = self._tom.SerializeOptions()
        options.IgnoreInferredObjects = True
        options.IgnoreInferredProperties = True
        options.IgnoreTimestamps = True
        options.IncludeRestrictedInformation = False
        result = json.loads(str(self._tom.JsonSerializer.SerializeDatabase(self._database, options)))
        roles = {role["name"]: role for role in result["model"].get("roles", [])}
        for role in self._database.Model.Roles:
            members = roles[str(role.Name)].get("members", [])
            if len(members) != role.Members.Count:
                raise ValueError("Cannot verify serialized owner role memberships.")
        return result

    def apply(self, target: dict[str, Any], migration: str) -> None:
        if self._readonly:
            raise ValueError("Read-only owner migration cannot apply changes.")
        from reports.owner.schema import OWNER_CONTRACT_VERSION
        from reports.owner.upgrade import REFRESH_BINDING
        marker = f"restore-contract-{OWNER_CONTRACT_VERSION}-marker"
        if migration not in {marker, REFRESH_BINDING}:
            raise ValueError("Unsupported owner migration.")
        from reports.owner.deployment import _contract_difference, _security_shape
        from reports.owner.upgrade import _members, migration_for

        before = self.read()
        if migration_for(before, target) != migration:
            raise ValueError("Owner contract changed before the TOM update.")
        model = self._database.Model
        if migration == marker:
            annotation = self._tom.Annotation()
            annotation.Name = "OwnerContractVersion"
            annotation.Value = str(OWNER_CONTRACT_VERSION)
            model.Annotations.Add(annotation)
        else:
            self._rebind(model, target)
        candidate = self._serialize()
        if (_contract_difference(_security_shape(target), _security_shape(candidate))
                or _members(before) != _members(candidate)):
            raise ValueError("Owner migration failed pre-save security validation; no model update performed.")
        # Neither migration replaces roles, members, sources, or item IDs; nothing is saved unless the
        # result already equals the target.
        model.SaveChanges()

    @staticmethod
    def _rebind(model: Any, target: dict[str, Any]) -> None:
        """Point the security filters and measures at this release's rule logic; nothing else is touched."""
        from reports.owner.deployment import _expression

        filters = {permission["name"]: permission["filterExpression"]
                   for role in target["model"]["roles"] for permission in role.get("tablePermissions", [])}
        measures = {(table["name"], measure["name"]): measure["expression"]
                    for table in target["model"]["tables"] for measure in table.get("measures", [])}
        for role in model.Roles:
            for permission in role.TablePermissions:
                wanted = filters.get(str(permission.Name))
                if wanted is not None and _expression(str(permission.FilterExpression)) != _expression(wanted):
                    permission.FilterExpression = wanted if isinstance(wanted, str) else "\n".join(wanted)
        for (table_name, measure_name), wanted in measures.items():
            measure = model.Tables[table_name].Measures[measure_name]
            if _expression(str(measure.Expression)) != _expression(wanted):
                measure.Expression = wanted if isinstance(wanted, str) else "\n".join(wanted)

    def close(self) -> None:
        self._server.Disconnect()
        self._fabric.refresh_tom_cache(workspace=self._workspace, credential=self._credential)

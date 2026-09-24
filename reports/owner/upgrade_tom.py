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
        if migration not in {"legacy-five-table-to-2", "restore-contract-2-marker"}:
            raise ValueError("Unsupported owner migration.")
        from reports.owner.deployment import _contract_difference, _security_shape
        from reports.owner.upgrade import _NEW_MEASURES, _NEW_TABLES, _OLD_MEASURES, _members, migration_for

        before = self.read()
        if migration_for(before, target) != migration:
            raise ValueError("Owner contract changed before the TOM update.")
        desired = self._tom.JsonSerializer.DeserializeDatabase(json.dumps(target)).Model
        model = self._database.Model
        if migration == "legacy-five-table-to-2":
            for table in desired.Tables:
                if str(table.Name) in _NEW_TABLES:
                    added = table.Clone()
                    for partition in added.Partitions:
                        source = table.Partitions[partition.Name].Source.ExpressionSource
                        partition.Source.ExpressionSource = model.Expressions[source.Name]
                    model.Tables.Add(added)
            for table in desired.Tables:
                for measure in table.Measures:
                    name = str(measure.Name)
                    if name in _NEW_MEASURES:
                        model.Tables[table.Name].Measures.Add(measure.Clone())
                    elif name in _OLD_MEASURES:
                        model.Tables[table.Name].Measures[name].Expression = measure.Expression
            for relationship in desired.Relationships:
                if str(relationship.FromTable.Name) in _NEW_TABLES:
                    added = relationship.Clone()
                    added.FromColumn = model.Tables[relationship.FromTable.Name].Columns[relationship.FromColumn.Name]
                    added.ToColumn = model.Tables[relationship.ToTable.Name].Columns[relationship.ToColumn.Name]
                    model.Relationships.Add(added)
            role = model.Roles["WorkspaceOwner"]
            for permission in desired.Roles["WorkspaceOwner"].TablePermissions:
                if str(permission.Table.Name) in _NEW_TABLES:
                    added = self._tom.TablePermission()
                    added.Table = model.Tables[permission.Table.Name]
                    added.FilterExpression = permission.FilterExpression
                    role.TablePermissions.Add(added)
        annotation = self._tom.Annotation()
        annotation.Name = "OwnerContractVersion"
        annotation.Value = "2"
        model.Annotations.Add(annotation)
        candidate = self._serialize()
        if (_contract_difference(_security_shape(target), _security_shape(candidate))
                or _members(before) != _members(candidate)):
            raise ValueError("Owner migration failed pre-save security validation; no model update performed.")
        # One metadata save includes new tables and their RLS. Existing roles,
        # members, source expressions, partitions and item IDs are never replaced.
        model.SaveChanges()

    def close(self) -> None:
        self._server.Disconnect()
        self._fabric.refresh_tom_cache(workspace=self._workspace, credential=self._credential)

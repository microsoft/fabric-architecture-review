# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Automatic, security-preserving migrations of recognized FAR owner models."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable, Protocol
from uuid import uuid4

from orchestration.fabric_api import FabricClient, guid
from reports.owner.deployment import _contract_difference, _security_shape
from reports.owner.schema import OWNER_CONTRACT_VERSION

_NEW_TABLES = {"owner_executions", "owner_coverage"}
_NEW_MEASURES = {
    "Scoped DAX object rows", "Scoped Dataflow rows", "Scoped executions",
    "Scoped failed executions", "Scoped timed executions", "Scoped maximum duration ms",
    "Scoped coverage rows", "Scoped incomplete coverage", "Scoped static objects",
}
_OLD_MEASURES = {
    "Scoped DAX findings": 'COALESCE(CALCULATE(COUNTROWS(owner_findings), '
    'KEEPFILTERS(owner_reviews[is_latest] = TRUE()), '
    'KEEPFILTERS(owner_findings[item_type] = "SemanticModel")), 0)',
    "Scoped notebook findings": 'COALESCE(CALCULATE(COUNTROWS(owner_findings), '
    'KEEPFILTERS(owner_reviews[is_latest] = TRUE()), '
    'KEEPFILTERS(owner_findings[item_type] = "Notebook")), 0)',
}


class ModelEditor(Protocol):
    def read(self) -> dict[str, Any]: ...
    def apply(self, target: dict[str, Any], migration: str) -> None: ...
    def close(self) -> None: ...


def _legacy_contract(target: dict[str, Any]) -> dict[str, Any]:
    """The unversioned five-table contract shipped at 99a4e15, not arbitrary drift."""
    if OWNER_CONTRACT_VERSION != 2:
        raise ValueError("This migration only supports owner contract 2.")
    legacy = deepcopy(target)
    model = legacy["model"]
    model["annotations"] = [a for a in model["annotations"] if a["name"] != "OwnerContractVersion"]
    model["tables"] = [t for t in model["tables"] if t["name"] not in _NEW_TABLES]
    for table in model["tables"]:
        if "measures" in table:
            table["measures"] = [m for m in table["measures"] if m["name"] not in _NEW_MEASURES]
            for measure in table["measures"]:
                if measure["name"] in _OLD_MEASURES:
                    measure["expression"] = _OLD_MEASURES[measure["name"]]
    model["relationships"] = [
        r for r in model["relationships"] if r["fromTable"] not in _NEW_TABLES
    ]
    for role in model["roles"]:
        role["tablePermissions"] = [
            p for p in role["tablePermissions"] if p["name"] not in _NEW_TABLES
        ]
    return legacy


def migration_for(existing: dict[str, Any], target: dict[str, Any]) -> str:
    actual = _security_shape(existing)
    expected = _security_shape(target)
    if _contract_difference(expected, actual) is None:
        return "none"
    version = actual["ownerContractVersion"]
    if version is None:
        if _contract_difference(_security_shape(_legacy_contract(target)), actual) is None:
            return "legacy-five-table-to-2"
        unmarked = deepcopy(expected)
        unmarked["ownerContractVersion"] = None
        if _contract_difference(unmarked, actual) is None:
            return "restore-contract-2-marker"
    raise ValueError(
        "Existing owner model security/schema/calculation contract differs. "
        "No approved owner migration matches the deployed contract. "
        f"First mismatch: {_contract_difference(expected, actual)}. "
        "No model update performed; inspect the deployed schema/security/source locally."
    )


def _members(bim: dict[str, Any]) -> dict[str, Any]:
    return {
        role["name"]: sorted(role.get("members", []), key=lambda m: json.dumps(m, sort_keys=True))
        for role in bim["model"].get("roles", [])
    }


def _connections(client: FabricClient, workspace_id: str, model_id: str) -> list[dict[str, Any]]:
    path = f"/workspaces/{workspace_id}/items/{model_id}/connections"
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    while path:
        if path in seen or len(seen) >= 100:
            raise ValueError("Cannot verify owner connections: invalid pagination.")
        seen.add(path)
        body = client.get_json(path)
        values = body.get("value")
        if not isinstance(values, list) or not values:
            raise ValueError("Cannot verify owner connections: missing connection metadata.")
        for row in values:
            if not isinstance(row, dict) or not isinstance(row.get("connectionDetails"), dict):
                raise ValueError("Cannot verify owner connection metadata.")
            entry = deepcopy(row)
            if row.get("connectivityType") == "ShareableCloud":
                connection_id = guid(row["id"])
                settings = client.get_json(f"/connections/{connection_id}")
                credentials = settings.get("credentialDetails")
                if not isinstance(credentials, dict) or not all(
                    isinstance(credentials.get(key), str)
                    for key in ("credentialType", "singleSignOnType")
                ):
                    raise ValueError("Cannot verify fixed-identity/SSO connection settings.")
                entry["settings"] = {
                    key: settings.get(key) for key in (
                        "id", "connectivityType", "gatewayId", "connectionDetails",
                        "privacyLevel",
                    )
                }
                entry["settings"]["credentialDetails"] = {
                    key: credentials.get(key) for key in (
                        "credentialType", "singleSignOnType", "connectionEncryption", "skipTestConnection",
                    )
                }
            elif row.get("connectivityType") != "Automatic":
                raise ValueError("Owner migration supports only Automatic or ShareableCloud connections.")
            rows.append(entry)
        path = body.get("continuationUri") or ""
        if body.get("continuationToken") and not path:
            raise ValueError("Cannot verify owner connections: missing continuation URI.")
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def _save_snapshot(workspace_id: str, lakehouse_id: str, model_id: str, state: dict[str, Any]) -> str:
    import notebookutils

    directory = (
        f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}"
        "/Files/_far/owner-model-backups"
    )
    path = f"{directory}/{model_id}-{uuid4()}.json"
    content = json.dumps(state, sort_keys=True, indent=2)
    if not notebookutils.fs.mkdirs(directory) or not notebookutils.fs.put(path, content, False):
        raise RuntimeError("Cannot save owner model recovery snapshot; no model update performed.")
    if notebookutils.fs.head(path, len(content.encode("utf-8")) + 1) != content:
        raise RuntimeError("Cannot verify owner model recovery snapshot; no model update performed.")
    return path


def upgrade_owner_model(
    client: FabricClient, *, workspace_id: str, lakehouse_id: str,
    model_id: str, target: dict[str, Any],
    editor_factory: Callable[..., ModelEditor] | None = None,
) -> None:
    """Update only a known FAR contract; never replace items or write item ACLs."""
    workspace_id, lakehouse_id, model_id = map(guid, (workspace_id, lakehouse_id, model_id))
    if editor_factory is None:
        from reports.owner.upgrade_tom import TomEditor

        editor_factory = TomEditor
    editor = editor_factory(client, workspace_id, model_id, readonly=False)
    failure: BaseException | None = None
    try:
        existing = editor.read()
        migration = migration_for(existing, target)
        if migration == "none":
            return
        connections = _connections(client, workspace_id, model_id)
        state = {
            "workspace_id": workspace_id, "lakehouse_id": lakehouse_id,
            "model_id": model_id, "model": existing,
            "connections": connections, "target": _security_shape(target),
        }
        backup = _save_snapshot(workspace_id, lakehouse_id, model_id, state)
        before = editor.read()
        if (_security_shape(before) != _security_shape(existing)
                or _members(before) != _members(existing)
                or _connections(client, workspace_id, model_id) != connections):
            raise ValueError("Owner model/connections changed during deployment. No model update performed; rerun setup.")
        try:
            editor.apply(target, migration)
            actual = editor.read()
            difference = _contract_difference(_security_shape(target), _security_shape(actual))
            if difference or _members(actual) != _members(existing):
                raise ValueError("Owner contract or approved role membership changed unexpectedly.")
            if _connections(client, workspace_id, model_id) != connections:
                raise ValueError("Owner connection binding or credential/SSO settings changed unexpectedly.")
        except Exception as exc:
            raise RuntimeError(
                f"Owner upgrade did not complete verification. Recovery snapshot: {backup}. "
                "The model may have changed; no automatic rollback was attempted. "
                "Keep owner access restricted and schedules paused; inspect/recover before rerunning."
            ) from exc
        print(f"Owner model upgraded in place. Recovery snapshot: {backup}")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            editor.close()
        except Exception as exc:
            if failure is None:
                raise RuntimeError("Owner model session cleanup failed; verify the deployed state before rerunning.") from exc
            failure.add_note(f"Owner model session cleanup also failed ({type(exc).__name__}).")

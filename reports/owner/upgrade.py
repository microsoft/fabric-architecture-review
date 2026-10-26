# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Automatic, security-preserving migrations of recognized FAR owner models."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Callable, Protocol
from uuid import uuid4

from orchestration.fabric_api import FabricClient, guid
from reports.owner.deployment import _contract_difference, _security_shape
from reports.owner.schema import OWNER_CONTRACT_VERSION

class ModelEditor(Protocol):
    def read(self) -> dict[str, Any]: ...
    def apply(self, target: dict[str, Any], migration: str) -> None: ...
    def close(self) -> None: ...


REFRESH_BINDING = "refresh-profile-binding"
_PINNED_HASH = re.compile(r'(profile_sha256\]\s*==?\s*)"[0-9a-fA-F]{64}"')
_PINNED_RULES = re.compile(r'(rule_id\]\s+IN\s+)\{[^{}]*\}')


def _unpinned(shape: dict[str, Any]) -> dict[str, Any]:
    """The contract with the pinned rule-logic hash and rule list blanked out.

    Security filters and measures pin the rule logic a release was built for, so every release that changes the
    rules changes them. Comparing without those two values tells a routine rebinding from real drift: a filter
    that lost its profile condition, or any other change, still differs.
    """
    def blank(text: str) -> str:
        return _PINNED_RULES.sub(r"\1{...}", _PINNED_HASH.sub(r'\1"..."', text))

    shape = deepcopy(shape)
    for role in shape["roles"]:
        for permission in role["tablePermissions"]:
            permission["filterExpression"] = blank(permission["filterExpression"])
    for table in shape["tables"]:
        for measure in table["measures"]:
            measure["expression"] = blank(measure["expression"])
    return shape


def migration_for(existing: dict[str, Any], target: dict[str, Any]) -> str:
    actual = _security_shape(existing)
    expected = _security_shape(target)
    if _contract_difference(expected, actual) is None:
        return "none"
    version = actual["ownerContractVersion"]
    if version is None:
        unmarked = deepcopy(expected)
        unmarked["ownerContractVersion"] = None
        if _contract_difference(unmarked, actual) is None:
            return f"restore-contract-{OWNER_CONTRACT_VERSION}-marker"
    if _contract_difference(_unpinned(expected), _unpinned(actual)) is None:
        return REFRESH_BINDING
    raise ValueError(
        "Existing owner model security/schema/calculation contract differs. "
        "No approved owner migration matches the deployed contract: a release that only changes the pinned "
        "rule-logic version is upgraded automatically, anything more is not. "
        f"First mismatch: {_contract_difference(expected, actual)}. "
        "No model update performed. Legacy optimizer contracts require a separately "
        "provisioned architecture model/Lakehouse; preserve their data and ACLs."
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

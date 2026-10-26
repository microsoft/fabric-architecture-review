# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Provision editable configuration: create what is missing and never change a value the customer set."""
from __future__ import annotations

import base64
import ast
import json
import re
import struct
from pathlib import Path
from typing import Any

from orchestration.fabric_api import FabricClient, definition_part, guid
from orchestration.review_config import ROOT, read_yaml
from orchestration.project import validate_artifact_names

_SCHEMA_ROOT = "https://developer.microsoft.com/json-schemas/fabric/item/variableLibrary/definition"
VARIABLES_SCHEMA = f"{_SCHEMA_ROOT}/variables/1.0.0/schema.json"
SETTINGS_SCHEMA = f"{_SCHEMA_ROOT}/settings/1.0.0/schema.json"

def stamp_notebook_bindings(notebook: dict[str, Any], bindings: dict[str, str]) -> dict[str, Any]:
    from orchestration.deployment import stamp_parameters
    names = {
        node.targets[0].id
        for cell in notebook["cells"]
        if cell["cell_type"] == "code" and "parameters" in cell.get("metadata", {}).get("tags", [])
        for node in ast.parse("".join(cell["source"])).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    values = {name: value for name, value in bindings.items() if name in names}
    return stamp_parameters(notebook, values) if values else notebook


def library_definition(groups: dict[str, Any]) -> dict[str, Any]:
    variables = []
    for group, entries in groups.items():
        for key, value in entries.items():
            kind = "Boolean" if isinstance(value, bool) else (
                "Integer" if isinstance(value, int) else (
                    "Number" if isinstance(value, float) else "String"
                )
            )
            variables.append({"name": f"{group}__{key}", "type": kind, "value": value})
    return {
        "parts": [
            definition_part("variables.json", {"$schema": VARIABLES_SCHEMA, "variables": variables}),
            definition_part("settings.json", {"$schema": SETTINGS_SCHEMA, "valueSetsOrder": []}),
        ],
    }


def ensure_library(client: FabricClient, workspace_id: str, name: str, groups: dict[str, Any]) -> str:
    matches = [
        item for item in client.list_items(workspace_id, "VariableLibrary")
        if item["displayName"].casefold() == name.casefold()
    ]
    expected = library_definition(groups)
    if len(matches) > 1:
        raise ValueError(f"Duplicate Variable Library name: {name}")
    if matches:
        item_id = guid(matches[0]["id"])
        result = client.complete(client.request(
            "POST", f"/workspaces/{guid(workspace_id)}/variableLibraries/{item_id}/getDefinition",
        ))
        parts = result["definition"]["parts"]
        part = next((part for part in parts if part["path"] == "variables.json"), None)
        if part is None:
            raise ValueError(f"{name} has no variables.json definition.")
        actual = json.loads(base64.b64decode(part["payload"], validate=True))["variables"]
        required = json.loads(base64.b64decode(expected["parts"][0]["payload"]))["variables"]
        actual_types = {entry["name"]: entry["type"].casefold() for entry in actual}
        expected_types = {entry["name"]: entry["type"].casefold() for entry in required}
        missing = sorted(set(expected_types) - set(actual_types))
        unexpected = sorted(set(actual_types) - set(expected_types))
        retyped = sorted(name for name in set(actual_types) & set(expected_types)
                         if actual_types[name] != expected_types[name])
        if len(actual_types) != len(actual) or retyped:
            raise ValueError(
                f"{name} has duplicate variables or variables of the wrong type; its values were not changed. "
                f"Wrong type: {', '.join(retyped) or 'none'}. "
                "Fix these variables in the library (Fabric: open the library, edit variables), "
                "or rename the library by setting FAR_CONFIG_PREFIX."
            )
        if unexpected:
            # A variable the review does not read (one you added, or one an older release defined) is harmless.
            print(f"{name}: ignoring variable(s) this release does not use: {', '.join(unexpected)}.")
        if missing:
            # A variable this version added is created with its default; every existing value is kept.
            added = [entry for entry in required if entry["name"] in missing]
            merged = [definition_part("variables.json", {"$schema": VARIABLES_SCHEMA, "variables": [*actual, *added]})
                      if part["path"] == "variables.json" else part for part in parts]
            client.complete(client.request(
                "POST", f"/workspaces/{guid(workspace_id)}/variableLibraries/{item_id}/updateDefinition",
                json_body={"definition": {"parts": merged}},
            ))
            print(f"{name}: added {len(added)} variable(s) with their defaults: {', '.join(missing)}.")
        return item_id
    result = client.complete(client.request(
        "POST", f"/workspaces/{guid(workspace_id)}/variableLibraries",
        json_body={"displayName": name, "definition": expected},
    ))
    return guid(result["id"])


def sql_connection(server: str, database: str, token: str) -> Any:
    from mssql_python import connect
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.fabric\.microsoft\.com", server):
        raise ValueError("Expected a Fabric SQL endpoint hostname.")
    if not database or any(character in database for character in "{};\r\n"):
        raise ValueError("Invalid Fabric SQL database name.")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("SQL access token must be nonempty.")
    encoded = token.encode("utf-16-le")
    return connect(
        f"Server={server};Database={database};Encrypt=yes;TrustServerCertificate=no;",
        attrs_before={1256: struct.pack("<I", len(encoded)) + encoded},
        timeout=60,
    )


def endpoint(client: FabricClient, workspace_id: str, item_id: str, item_type: str) -> tuple[str, str]:
    if item_type not in {"warehouses", "lakehouses"}:
        raise ValueError("Unsupported SQL source type.")
    result = client.get_json(f"/workspaces/{guid(workspace_id)}/{item_type}/{guid(item_id)}")
    properties = result["properties"]
    sql_properties = properties["sqlEndpointProperties"] if item_type == "lakehouses" else properties
    server = sql_properties.get("connectionString")
    if not server:
        raise RuntimeError("Fabric SQL endpoint is not ready. Wait for provisioning/synchronization and retry.")
    return server, result["displayName"]


def sql_rows(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(query)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def provision_configuration(client: FabricClient, workspace_id: str, prefix: str) -> dict[str, str]:
    workspace_id = guid(workspace_id)
    validate_artifact_names({"FAR_CONFIG_PREFIX": prefix})
    thresholds_name, connections_name, environment_name = (
        f"{prefix}_Thresholds", f"{prefix}_Connections", f"{prefix}_Environment",
    )
    # One library holds the rule thresholds and the scope/policy declarations.
    ensure_library(client, workspace_id, thresholds_name, {
        **read_yaml(ROOT / "config" / "thresholds.yaml"), **read_yaml(ROOT / "config" / "policy.yaml"),
    })
    ensure_library(client, workspace_id, connections_name, read_yaml(ROOT / "config" / "sources.yaml"))
    ensure_library(client, workspace_id, environment_name, read_yaml(ROOT / "config" / "environment.yaml"))
    return {
        "FAR_THRESHOLDS_LIBRARY": thresholds_name, "FAR_CONNECTIONS_LIBRARY": connections_name,
        "FAR_ENVIRONMENT_LIBRARY": environment_name,
    }


def capture_fabric(out_dir: Path, parameters: dict[str, Any], notebookutils: Any) -> Path:
    from orchestration.review_config import capture, fabric_configuration, read_snapshot
    existing = out_dir / "review_config.json"
    if existing.exists():
        read_snapshot(existing)
        return existing
    config = fabric_configuration(
        notebookutils.variableLibrary.getLibrary,
        parameters["FAR_THRESHOLDS_LIBRARY"], parameters["FAR_CONNECTIONS_LIBRARY"],
    )
    return capture(out_dir, config)

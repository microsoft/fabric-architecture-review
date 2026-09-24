# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Opt-in owner artifacts, with security-preserving model redeployment."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import re
from typing import Any, Callable

from orchestration.fabric_api import FabricClient, definition_part, guid


def _expression(value: Any) -> str:
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        value = "\n".join(value)
    if not isinstance(value, str):
        raise ValueError("Cannot verify an owner model expression.")
    # TMSL can serialize expressions as a string or lines. Preserve whitespace
    # inside quoted identifiers/literals while tolerating formatter differences.
    return re.sub(
        r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|\s+',
        lambda match: " " if match.group().isspace() else match.group(), value,
    ).strip()


def _relationship_shape(relationship: dict[str, Any]) -> dict[str, Any]:
    # TOM omits these defaults when serializing an unchanged SingleColumnRelationship.
    return {
        "type": "singleColumn",
        "isActive": True,
        "fromCardinality": "many",
        "toCardinality": "one",
        "crossFilteringBehavior": "oneDirection",
        "securityFilteringBehavior": "oneDirection",
        "joinOnDateBehavior": "dateAndTime",
        "relyOnReferentialIntegrity": False,
        **relationship,
    }


def _security_shape(bim: dict[str, Any]) -> dict[str, Any]:
    model = bim["model"]
    tables = model["tables"]
    roles = model.get("roles", [])
    if len({t["name"] for t in tables}) != len(tables):
        raise ValueError("Duplicate owner model tables.")
    return {
        "ownerContractVersion": next(
            (entry.get("value") for entry in model.get("annotations", [])
             if entry.get("name") == "OwnerContractVersion"), None,
        ),
        "directLakeBehavior": model.get("directLakeBehavior"),
        "tables": sorted([{
            "name": table["name"],
            "columns": sorted([
                {key: column.get(key) for key in (
                    "name", "dataType", "sourceColumn", "expression", "type",
                )}
                for column in table["columns"]
            ], key=lambda column: column["name"]),
            "partitions": [{
                "mode": partition["mode"], "source": partition["source"],
            } for partition in table["partitions"]],
            "measures": sorted([{
                "name": measure["name"],
                "expression": _expression(measure["expression"]),
                "formatString": measure.get("formatString"),
                "formatStringExpression": (
                    _expression(measure["formatStringDefinition"]["expression"])
                    if measure.get("formatStringDefinition") else None
                ),
            } for measure in table.get("measures", [])], key=lambda measure: measure["name"]),
        } for table in tables], key=lambda table: table["name"]),
        "expressions": sorted([{
            "name": expression["name"], "kind": expression["kind"],
            "expression": _expression(expression["expression"]),
        } for expression in model.get("expressions", [])], key=lambda entry: entry["name"]),
        "roles": sorted([{
            "name": role["name"], "modelPermission": role["modelPermission"],
            "tablePermissions": sorted([{
                **permission,
                "filterExpression": _expression(permission["filterExpression"]),
            } for permission in role.get("tablePermissions", [])], key=lambda entry: entry["name"]),
        } for role in roles], key=lambda role: role["name"]),
        "relationships": sorted(
            [_relationship_shape(relationship) for relationship in model.get("relationships", [])],
            key=lambda entry: entry["name"],
        ),
    }


def _contract_difference(expected: Any, actual: Any, path: str = "model") -> str | None:
    """Locate a mismatch without exposing expression or connection values."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(expected.keys() | actual.keys()):
            child = f"{path}.{key}"
            if key not in expected or key not in actual:
                return child
            difference = _contract_difference(expected[key], actual[key], child)
            if difference:
                return difference
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path} (item count)"
        for index, (left, right) in enumerate(zip(expected, actual)):
            label = left.get("name", index) if isinstance(left, dict) else index
            difference = _contract_difference(left, right, f"{path}[{label}]")
            if difference:
                return difference
        return None
    return path if expected != actual else None


def ensure_owner_model(
    client: FabricClient, workspace_id: str, name: str, bim: dict[str, Any],
    *, upgrade: Callable[[str], None] | None = None,
) -> str:
    """Never replace an existing model's roles, members or connection bindings.

    Setup supplies a targeted migration for recognized older FAR contracts.
    Arbitrary definitions are never overwritten.
    """
    workspace_id = guid(workspace_id)
    matches = [item for item in client.list_items(workspace_id, "SemanticModel")
               if item.get("displayName") == name]
    if len(matches) > 1:
        raise ValueError("Multiple owner semantic models share the configured name.")
    if not matches:
        return client.upsert_item(workspace_id, name, "SemanticModel", {"parts": [
            definition_part("definition.pbism", {"version": "1.0", "settings": {}}),
            definition_part("model.bim", bim),
        ]})
    model_id = guid(matches[0]["id"])
    result = client.complete(client.request(
        "POST", f"/workspaces/{workspace_id}/semanticModels/{model_id}/getDefinition?format=TMSL",
    ))
    parts = result.get("definition", {}).get("parts", [])
    models = [part for part in parts if part.get("path") == "model.bim"]
    if len(models) != 1:
        raise ValueError("Cannot verify existing owner model security; no definition updated.")
    existing = json.loads(base64.b64decode(models[0]["payload"], validate=True))
    difference = _contract_difference(_security_shape(bim), _security_shape(existing))
    if difference:
        if upgrade is not None:
            from reports.owner.upgrade import migration_for

            migration_for(existing, bim)
            upgrade(model_id)
            return model_id
        raise ValueError(
            "Existing owner model security/schema/calculation contract differs. No model update performed. "
            f"First mismatch: {difference}. "
            "Do not delete Lakehouse tables. Routine setup reuses a compatible model with the same name. "
            "Check the deployed code and model/source changes first. "
            "Rerun the current setup to upgrade a recognized older FAR model automatically. "
            "Only for an approved replacement, "
            "set unused OWNER_SEMANTIC_MODEL_NAME and OWNER_REPORT_NAME, keep the same Lakehouse, and rerun setup. "
            "Map the new model's data connection, run access sync, and validate reader access before sharing. "
            "See fabric/DEPLOYMENT.md#existing-owner-model-mismatch."
        )
    return model_id


def _report_model_id(definition: Any) -> str:
    """Resolve documented PBIR v1/v2 connection references without comparing formatting."""
    reference = definition.get("datasetReference") if isinstance(definition, dict) else None
    if not isinstance(reference, dict) or reference.get("byPath") is not None:
        raise ValueError("A remote byConnection reference is required.")
    connection = reference.get("byConnection")
    if not isinstance(connection, dict):
        raise ValueError("The remote byConnection reference is missing or invalid.")
    connection_string = connection.get("connectionString")
    if connection_string is not None and not isinstance(connection_string, str):
        raise ValueError("The remote connection string is invalid.")

    identifiers = []
    if connection.get("pbiModelDatabaseName") is not None:
        identifiers.append(connection["pbiModelDatabaseName"])
    # Semicolons and escaped quotes inside XMLA values must not become binding keys.
    property_pattern = re.compile(
        r"""\s*([^=;]+?)\s*=\s*("(?:[^"]|"")*"|'(?:[^']|'')*'|[^;'"]*)\s*(?:;|$)"""
    )
    remaining = (connection_string or "").strip()
    while remaining:
        remaining = remaining.lstrip("; \t\r\n")
        if not remaining:
            break
        match = property_pattern.match(remaining)
        if match is None:
            raise ValueError("The remote connection string cannot be parsed safely.")
        key, value = match.group(1).strip().casefold(), match.group(2).strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            value = value[1:-1].replace(quote * 2, quote)
        if key == "semanticmodelid":
            identifiers.append(value)
        remaining = remaining[match.end():]

    normalized = set()
    for value in identifiers:
        if not isinstance(value, str):
            raise ValueError("The binding contains an invalid semantic-model ID.")
        try:
            normalized.add(guid(value))
        except ValueError:
            raise ValueError("The binding contains an invalid semantic-model ID.") from None
    if not normalized:
        raise ValueError("The binding does not expose a semantic-model ID; names alone are not identity.")
    if len(normalized) != 1:
        raise ValueError("The binding contains conflicting semantic-model IDs.")
    return normalized.pop()


def _verify_report_binding(
    client: FabricClient, workspace_id: str, name: str, model_id: str,
) -> None:
    matches = [item for item in client.list_items(workspace_id, "Report")
               if item.get("displayName") == name]
    if len(matches) > 1:
        raise ValueError("Multiple owner reports share the configured name.")
    if not matches:
        return
    report_id = guid(matches[0]["id"])
    result = client.complete(client.request(
        "POST", f"/workspaces/{workspace_id}/reports/{report_id}/getDefinition",
    ))
    definitions = [part for part in result.get("definition", {}).get("parts", [])
                   if part.get("path") == "definition.pbir"]
    if len(definitions) != 1:
        raise ValueError("Cannot verify existing owner report binding; no report updated.")
    definition = json.loads(base64.b64decode(definitions[0]["payload"], validate=True))
    model_id = guid(model_id)
    try:
        existing_model_id = _report_model_id(definition)
    except ValueError as exc:
        raise ValueError(
            f"Cannot verify existing owner report {report_id} binding. No report update performed. "
            f"{exc} Expected owner model ID: {model_id}. "
            "Inspect definition.pbir locally; do not share connection strings or credentials."
        ) from exc
    if existing_model_id != model_id:
        raise ValueError(
            f"Existing owner report {report_id} is bound to model {existing_model_id}, "
            f"not the configured owner model {model_id}. No report update performed. "
            "Verify the configured owner artifact names and model IDs before changing this binding."
        )


def access_notebook(
    repo_dir: Path, workspace_id: str, lakehouse_id: str, lakehouse_name: str,
    model_id: str,
) -> dict[str, Any]:
    """Embed the deployed sync implementation, not a mutable branch download."""
    workspace_id, lakehouse_id, model_id = map(guid, (workspace_id, lakehouse_id, model_id))
    runtime = (repo_dir / "reports" / "owner" / "access.py").read_text(encoding="utf-8")
    execute = (
        "import notebookutils\n"
        "from pyspark.sql import SparkSession\n"
        "from pyspark.sql.types import StructType, StructField, StringType, TimestampType\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        f"REPORTING_WORKSPACE_ID = {workspace_id!r}\n"
        f"OWNER_MODEL_ID = {model_id!r}\n"
        "schema = StructType([\n"
        "    StructField('workspace_id', StringType(), False),\n"
        "    StructField('principal_object_id', StringType(), False),\n"
        "    StructField('refreshed_at', TimestampType(), False),\n"
        "    StructField('expires_at', TimestampType(), False),\n"
        "])\n"
        "def replace_access(rows):\n"
        "    values = [(r['workspace_id'], r['principal_object_id'],\n"
        "               r['refreshed_at'], r['expires_at']) for r in rows]\n"
        "    (spark.createDataFrame(values, schema).write.format('delta')\n"
        "        .mode('overwrite').saveAsTable('owner_access'))\n"
        "token = lambda: notebookutils.credentials.getToken('pbi')\n"
        "refresh = lambda: refresh_owner_model(REPORTING_WORKSPACE_ID, OWNER_MODEL_ID, token)\n"
        "workspaces = [r['workspace_id'] for r in\n"
        "              spark.table('owner_workspaces').select('workspace_id').distinct().collect()]\n"
        "unavailable_workspaces = []\n"
        "count = synchronize_access(workspaces, token, replace=replace_access, refresh=refresh,\n"
        "                           on_unavailable=unavailable_workspaces.append)\n"
        "print('Owner access sync completed with warnings:' if unavailable_workspaces else\n"
        "      'Owner access sync complete:', len(workspaces), 'workspace(s) checked,', count,\n"
        "      'direct-user grants. Report permissions were NOT changed.')\n"
        "if unavailable_workspaces:\n"
        "    print('HTTP 404: access denied for', len(unavailable_workspaces),\n"
        "          'workspace(s):', ', '.join(unavailable_workspaces))\n"
        "    print('Review history retained. Check workspace existence, tenant and execution identity.')\n"
    )
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": lakehouse_id, "default_lakehouse_name": lakehouse_name,
                "default_lakehouse_workspace_id": workspace_id,
                "known_lakehouses": [{"id": lakehouse_id}],
            }},
        },
        "cells": [
            {"cell_type": "markdown", "id": "owner-access-intro", "metadata": {}, "source": [
                "# FAR - Workspace Owner access sync\n\n",
                "Copyright (c) Microsoft Corporation. Licensed under the MIT License.\n\n",
                "Run after the first owner-enabled review, then schedule daily independently of FAR. "
                "Run as the approved tenant admin identity with owner-model refresh permissions. "
                "Do not overlap daily, targeted or manual sync runs. Clears and reframes old grants before lookup; "
                "HTTP 404 denies that workspace with a warning while other lookups continue. Other failures "
                "stop the sync, not restore stale grants. Grants expire after 24 hours. "
                "No report sharing or RLS role membership is granted here. "
                "Configure the owner's fixed-identity connection before running. "
                "The admin API is paced at 20 seconds per workspace (200 requests/hour service limit).\n",
            ]},
            {"cell_type": "code", "id": "owner-access-runtime", "metadata": {},
             "execution_count": None, "outputs": [], "source": runtime.splitlines(keepends=True)},
            {"cell_type": "code", "id": "owner-access-execute", "metadata": {},
             "execution_count": None, "outputs": [], "source": execute.splitlines(keepends=True)},
        ],
    }


def deploy_owner_reporting(
    client: FabricClient, *, repo_dir: Path, workspace_id: str, lakehouse_id: str,
    lakehouse_name: str, notebook_prefix: str, model_name: str, report_name: str,
    spark: Any,
) -> dict[str, str]:
    from reports.owner.fabric_runtime import bootstrap_owner_tables
    from reports.owner.model import build_bim
    from reports.owner.report import build_parts
    from reports.powerbi.deploy import wait_for_sql_endpoint

    workspace_id, lakehouse_id = guid(workspace_id), guid(lakehouse_id)
    if not model_name.strip() or not report_name.strip():
        raise ValueError("Owner model and report names must not be empty.")
    sql_server, sql_database = wait_for_sql_endpoint(
        client.get_json, workspace_id, lakehouse_id,
    )
    table_root = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Tables"
    bootstrap_owner_tables(spark, table_root)
    bim = build_bim(model_name, sql_server, sql_database)

    def upgrade(model_id: str) -> None:
        from reports.owner.upgrade import upgrade_owner_model

        _verify_report_binding(client, workspace_id, report_name, model_id)
        upgrade_owner_model(
            client, workspace_id=workspace_id, lakehouse_id=lakehouse_id,
            model_id=model_id, target=bim,
        )

    model_id = ensure_owner_model(
        client, workspace_id, model_name, bim, upgrade=upgrade,
    )
    _verify_report_binding(client, workspace_id, report_name, model_id)
    parts = []
    for part in build_parts(model_id):
        if part.get("b64"):
            parts.append({"path": part["path"], "payload": part["b64"], "payloadType": "InlineBase64"})
        else:
            parts.append({
                "path": part["path"],
                "payload": base64.b64encode(part["text"].encode("utf-8")).decode("ascii"),
                "payloadType": "InlineBase64",
            })
    report_id = client.upsert_item(workspace_id, report_name, "Report", {"parts": parts})
    notebook = access_notebook(repo_dir, workspace_id, lakehouse_id, lakehouse_name, model_id)
    notebook_id = client.upsert_item(
        workspace_id, notebook_prefix + "_07_OwnerAccessSync", "Notebook",
        {"format": "ipynb", "parts": [definition_part("notebook-content.ipynb", notebook)]},
    )
    print("Owner artifacts deployed; NOT automatically shared or certified for consumer access.")
    print("Configure fixed identity with SSO disabled; validate actual read-only users and RLS.")
    print("Run FAR, then run and schedule 07_OwnerAccessSync daily. Approve access requests manually.")
    print("For targeted reviews, bind 07 in targeted setup to sync after FAR and before email; keep daily sync.")
    print("Do not overlap daily, targeted or manual access syncs. Workspace HTTP 404 denies that workspace with warnings.")
    print("Assign WorkspaceOwner plus item-scoped report/model Read, NOT FAR workspace membership (even Viewer).")
    print(f"Owner report URL: https://app.powerbi.com/groups/{workspace_id}/reports/{report_id}")
    return {"model_id": model_id, "report_id": report_id, "access_notebook_id": notebook_id}

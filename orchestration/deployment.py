# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Build the opt-in parent pipeline without changing the standalone FAR pipeline."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
from typing import Any

from orchestration.fabric_api import FabricClient, definition_part, guid
from orchestration.sources import capacity_selector


DEFAULTS = {
    "FUAM_WORKSPACE_ID": "",
    "FUAM_LAKEHOUSE_ID": "",
    "CAPACITY_ID_OR_NAME": "",
    "RANKING_METRIC": "cu_seconds",
    "LOOKBACK_DAYS": "7",
    "TOP_N": "5",
    "NOTIFICATIONS_ENABLED": "false",
    "FAR_REPORT_URL": "",
}

SELECTION_PARAMETERS = ("WORKSPACE_IDS", *DEFAULTS)
NOTIFICATION_PARAMETERS = ("NOTIFICATIONS_ENABLED", "FAR_REPORT_URL")
EMPTY_CAPABLE_PARAMETERS = frozenset({
    "WORKSPACE_IDS", "FUAM_LAKEHOUSE_ID", "CAPACITY_ID_OR_NAME", "FAR_REPORT_URL",
})
STRING_TRANSPORT_PREFIX = "far-string:"
SELECT_ACTIVITY = "Select workspaces"
REVIEW_ACTIVITY = "Review selected workspaces"
CHILD_ACTIVITY = "Invoke FAR"
OWNER_ACCESS_ACTIVITY = "Sync owner access"
NOTIFY_ACTIVITY = "Notify owners if enabled"
PREPARE_ACTIVITY = "Prepare owner emails"
STORE_EMAILS_ACTIVITY = "Store owner emails"
SEND_ACTIVITY = "Send owner emails"
EMAIL_ACTIVITY = "Email workspace administrator"
CHILD_RUN_VARIABLE = "FAR_CHILD_RUN_ID"
EMAILS_VARIABLE = "FAR_OWNER_EMAILS"
SELECTION_OUTPUT = f"json(activity('{SELECT_ACTIVITY}').output.result.exitValue)"
RUNTIME_MODULES = (
    "__init__.py", "completion.py", "deployment.py", "fabric_api.py",
    "ranking.py", "runtime.py", "setup.py", "sources.py",
)
RUNTIME_COLLECTOR_MODULES = ("__init__.py", "_http.py", "workspace_scope.py")


def expression(value: str) -> dict[str, str]:
    return {"value": value, "type": "Expression"}


def notebook_parameter(value: str, *, preserve_empty: bool = False) -> dict[str, Any]:
    if preserve_empty:
        if not value.startswith("@"):
            raise ValueError("Notebook parameter transport requires a pipeline expression.")
        value = f"@concat('{STRING_TRANSPORT_PREFIX}', {value[1:]})"
    return {"value": expression(value), "type": "string"}


def read_notebook_parameters(namespace: dict[str, Any], names: tuple[str, ...]) -> dict[str, str]:
    invalid = [name for name in names if not isinstance(namespace.get(name), str)]
    if invalid:
        raise ValueError(
            "Missing or non-string pipeline notebook parameters: " + ", ".join(invalid)
            + ". Run the parent pipeline after rerunning all cells of 06; do not run its stage notebooks manually."
        )
    result = {}
    for name in names:
        value = namespace[name]
        if name in EMPTY_CAPABLE_PARAMETERS:
            if not value.startswith(STRING_TRANSPORT_PREFIX):
                raise ValueError(
                    f"Missing notebook string transport marker for {name}. "
                    "Rerun all cells of 06 to update the parent and both stage notebooks together."
                )
            value = value[len(STRING_TRANSPORT_PREFIX):]
        result[name] = value
    return result


def stamp_parameters(notebook: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    result = copy.deepcopy(notebook)
    replaced: set[str] = set()
    for cell in result["cells"]:
        if cell["cell_type"] != "code":
            continue
        for index, line in enumerate(cell["source"]):
            for name, value in values.items():
                if line.startswith(name + " ="):
                    cell["source"][index] = f"{name} = {json.dumps(value)}\n"
                    replaced.add(name)
    if set(values) != replaced:
        raise ValueError("Unstamped notebook parameters: " + ", ".join(sorted(set(values) - replaced)))
    return result


def runtime_notebook(
    bundle_path: str, workspace_id: str, lakehouse_id: str, *,
    stage: str = "selection", child_pipeline_id: str | None = None,
) -> dict[str, Any]:
    if stage not in ("selection", "notifications"):
        raise ValueError("Unknown targeted-review notebook stage.")
    if child_pipeline_id is None:
        raise ValueError("Runtime notebook requires fixed child pipeline wiring.")
    child_pipeline_id = guid(child_pipeline_id)
    names = SELECTION_PARAMETERS if stage == "selection" else NOTIFICATION_PARAMETERS
    names = (*names, "PARENT_RUN_ID")
    if stage == "notifications":
        names = (*names, "CHILD_RUN_ID")
    code = [
        "import json, sys\n",
        "import notebookutils\n",
        "for module in [key for key in sys.modules if key == 'orchestration' or key.startswith('orchestration.')]:\n",
        "    del sys.modules[module]\n",
        f"sys.path.insert(0, {json.dumps(bundle_path)})\n",
        "from orchestration.deployment import read_notebook_parameters\n",
        f"from orchestration.runtime import run_{stage}\n",
        f"config = read_notebook_parameters(globals(), {names!r})\n",
        f"config['CHILD_WORKSPACE_ID'] = {json.dumps(guid(workspace_id))}\n",
    ]
    code.append(f"config['CHILD_PIPELINE_ID'] = {json.dumps(child_pipeline_id)}\n")
    code.extend([
        f"result = run_{stage}(config)\n",
        "notebookutils.notebook.exit(json.dumps(result))\n",
    ])
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Synapse PySpark", "language": "python", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": guid(lakehouse_id),
                "default_lakehouse_workspace_id": guid(workspace_id),
                "known_lakehouses": [{"id": guid(lakehouse_id)}],
            }},
        },
        "cells": [
            {
                "cell_type": "code", "id": "parameters", "execution_count": None,
                "metadata": {"tags": ["parameters"]}, "outputs": [],
                "source": [
                    "# Copyright (c) Microsoft Corporation.\n",
                    "# Licensed under the MIT License.\n",
                ] + [f"{name} = None\n" for name in names],
            },
            {
                "cell_type": "code", "id": "run", "execution_count": None,
                "metadata": {}, "outputs": [],
                "source": code,
            },
        ],
    }


def build_parent_pipeline(
    child: dict[str, Any],
    *,
    workspace_id: str,
    pipeline_id: str,
    notebook_id: str,
    notification_notebook_id: str,
    owner_access_notebook_id: str | None = None,
    defaults: dict[str, str] | None = None,
    email_connection_id: str | None = None,
    email_from: str = "",
    email_state: str | None = None,
) -> dict[str, Any]:
    child_parameters = copy.deepcopy(child["properties"]["parameters"])
    if "WORKSPACE_IDS" not in child_parameters:
        raise ValueError("Child pipeline must expose WORKSPACE_IDS.")
    if any(p.get("type", "").lower() != "string" for p in child_parameters.values()):
        raise ValueError("FAR child parameters must be strings; update orchestration for other types.")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in child_parameters):
        raise ValueError("FAR child parameter names must be identifiers.")
    options = {**DEFAULTS, **(defaults or {})}
    options["CAPACITY_ID_OR_NAME"] = capacity_selector(options["CAPACITY_ID_OR_NAME"])
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unknown orchestration defaults: " + ", ".join(sorted(unknown)))
    collision = set(child_parameters) & set(options)
    if collision:
        raise ValueError("Child/orchestration parameter collision: " + ", ".join(sorted(collision)))
    if any(not isinstance(value, str) for value in options.values()):
        raise ValueError("Orchestration defaults must be strings.")
    if email_connection_id is not None:
        email_connection_id = guid(email_connection_id)
    if email_state is None:
        email_state = "Active" if email_connection_id else "Inactive"
    if email_state not in ("Active", "Inactive"):
        raise ValueError("Email activity state must be Active or Inactive.")
    if email_state == "Active" and not email_connection_id:
        raise ValueError("Connect the email activity before activating it.")
    if email_from:
        from orchestration.completion import validate_email_address
        email_from = validate_email_address(email_from)
        if email_connection_id is None:
            raise ValueError("An Outlook connection is required when an email From address is supplied.")
    # FUAM targeting must not inherit a standalone review's workspace restriction.
    child_parameters["WORKSPACE_IDS"]["defaultValue"] = ""
    parameters = {**child_parameters, **{
        key: {"type": "string", "defaultValue": value} for key, value in options.items()
    }}
    policy = {
        "timeout": "0.12:00:00", "retry": 0, "retryIntervalInSeconds": 120,
        "secureInput": True, "secureOutput": True,
    }
    if owner_access_notebook_id is not None:
        owner_access_notebook_id = guid(owner_access_notebook_id)

    def succeeded(activity: str) -> list[dict[str, Any]]:
        return [{"activity": activity, "dependencyConditions": ["Succeeded"]}]

    def notebook_activity(name: str, item_id: str, keys: tuple[str, ...]) -> dict[str, Any]:
        return {
            "name": name, "type": "TridentNotebook", "dependsOn": [],
            "policy": copy.deepcopy(policy),
            "typeProperties": {
                "workspaceId": guid(workspace_id), "notebookId": guid(item_id),
                "parameters": {
                    **{key: notebook_parameter(
                        (f"@coalesce(pipeline().parameters.{key}, '')" if key == "CAPACITY_ID_OR_NAME"
                         else "@pipeline().parameters." + key),
                        preserve_empty=key in EMPTY_CAPABLE_PARAMETERS,
                    ) for key in keys},
                    "PARENT_RUN_ID": notebook_parameter("@pipeline().RunId"),
                },
            },
        }

    selection = notebook_activity(SELECT_ACTIVITY, notebook_id, SELECTION_PARAMETERS)
    prepare = notebook_activity(PREPARE_ACTIVITY, notification_notebook_id, NOTIFICATION_PARAMETERS)
    prepare["typeProperties"]["parameters"]["CHILD_RUN_ID"] = notebook_parameter(
        f"@variables('{CHILD_RUN_VARIABLE}')"
    )
    store_emails = {
        "name": STORE_EMAILS_ACTIVITY, "type": "SetVariable",
        "dependsOn": succeeded(PREPARE_ACTIVITY),
        "policy": {"secureInput": True, "secureOutput": True},
        "typeProperties": {
            "variableName": EMAILS_VARIABLE,
            "value": expression(f"@json(activity('{PREPARE_ACTIVITY}').output.result.exitValue).messages"),
        },
    }
    send = {
        "name": SEND_ACTIVITY, "type": "ForEach", "dependsOn": succeeded(NOTIFY_ACTIVITY),
        "typeProperties": {
            "items": expression(f"@variables('{EMAILS_VARIABLE}')"),
            "isSequential": True,
            "activities": [{
                "name": EMAIL_ACTIVITY, "type": "Office365Email", "dependsOn": [],
                "policy": copy.deepcopy(policy),
                # Inactive activities can omit the connection during Fabric validation.
                "state": email_state, "onInactiveMarkAs": "Failed",
                **({"externalReferences": {"connection": email_connection_id}} if email_connection_id else {}),
                "typeProperties": {
                    "to": "@{item().to}", "subject": "@{item().subject}", "body": "@{item().body}",
                    **({"from": email_from} if email_from else {}),
                },
            }],
        },
    }
    child_values = {key: expression("@pipeline().parameters." + key) for key in child_parameters}
    child_values["WORKSPACE_IDS"] = expression("@" + SELECTION_OUTPUT + ".workspace_ids")
    nonempty = f"@not(empty({SELECTION_OUTPUT}.workspace_ids))"
    return {"properties": {
        "parameters": parameters,
        "variables": {
            CHILD_RUN_VARIABLE: {"type": "String", "defaultValue": ""},
            EMAILS_VARIABLE: {"type": "Array", "defaultValue": []},
        },
        "activities": [
            selection,
            {
                "name": REVIEW_ACTIVITY, "type": "IfCondition", "dependsOn": succeeded(SELECT_ACTIVITY),
                "typeProperties": {
                    "expression": expression(nonempty),
                    "ifTrueActivities": [
                        {
                            "name": CHILD_ACTIVITY, "type": "ExecutePipeline", "dependsOn": [],
                            "policy": copy.deepcopy(policy),
                            "typeProperties": {
                                "pipeline": {"referenceName": guid(pipeline_id), "type": "PipelineReference"},
                                "waitOnCompletion": True, "parameters": child_values,
                            },
                        },
                        {
                            "name": "Record FAR run ID", "type": "SetVariable",
                            "dependsOn": succeeded(CHILD_ACTIVITY),
                            "typeProperties": {
                                "variableName": CHILD_RUN_VARIABLE,
                                "value": expression(f"@activity('{CHILD_ACTIVITY}').output.pipelineRunId"),
                            },
                        },
                        *([{
                            "name": OWNER_ACCESS_ACTIVITY, "type": "TridentNotebook",
                            "dependsOn": succeeded("Record FAR run ID"),
                            "policy": copy.deepcopy(policy),
                            "typeProperties": {
                                "workspaceId": guid(workspace_id),
                                "notebookId": owner_access_notebook_id,
                                "parameters": {},
                            },
                        }] if owner_access_notebook_id is not None else []),
                    ],
                    "ifFalseActivities": [],
                },
            },
            {
                "name": NOTIFY_ACTIVITY, "type": "IfCondition", "dependsOn": succeeded(REVIEW_ACTIVITY),
                "typeProperties": {
                    "expression": expression(
                        f"@and(not(empty(variables('{CHILD_RUN_VARIABLE}'))), "
                        "equals(toLower(pipeline().parameters.NOTIFICATIONS_ENABLED), 'true'))"
                    ),
                    "ifTrueActivities": [prepare, store_emails], "ifFalseActivities": [],
                },
            },
            send,
        ],
    }}


def package_runtime(repo_dir: Path, destination: Path) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for package, names in (("orchestration", RUNTIME_MODULES), ("collectors", RUNTIME_COLLECTOR_MODULES)):
            for name in names:
                path = repo_dir / package / name
                info = zipfile.ZipInfo(package + "/" + path.name, date_time=(2026, 1, 1, 0, 0, 0))
                archive.writestr(info, path.read_bytes())
    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (digest + ".zip")
    if not target.exists():
        target.write_bytes(content)
    return target


def _activity(activities: list[dict[str, Any]], name: str, kind: str) -> dict[str, Any]:
    matches = [item for item in activities if item.get("name") == name]
    if len(matches) != 1 or matches[0].get("type") != kind:
        raise ValueError(f"Existing parent must contain one {kind} activity named {name!r}.")
    return matches[0]


def _saved_email_settings(
    pipeline: dict[str, Any], child_pipeline_id: str,
) -> tuple[dict[str, Any], str]:
    """Retain only operator-owned mail settings, never edited message bindings."""
    from orchestration.completion import validate_email_address, validate_report_url

    properties = pipeline["properties"]
    review = _activity(properties["activities"], REVIEW_ACTIVITY, "IfCondition")
    child = _activity(review["typeProperties"]["ifTrueActivities"], CHILD_ACTIVITY, "ExecutePipeline")
    if guid(child["typeProperties"]["pipeline"]["referenceName"]) != guid(child_pipeline_id):
        raise ValueError("Existing parent targets a different FAR child; inspect it before redeploying.")
    notification = _activity(properties["activities"], NOTIFY_ACTIVITY, "IfCondition")
    branch = notification["typeProperties"]["ifTrueActivities"]
    report_url = properties["parameters"]["FAR_REPORT_URL"].get("defaultValue", "")
    if not isinstance(report_url, str):
        raise ValueError("Existing FAR_REPORT_URL must be a string.")
    if report_url:
        validate_report_url(report_url)
    activities = properties["activities"] + branch
    # Accept saved nested loops and connection-free placeholders during upgrades.
    if not any(item.get("name") == SEND_ACTIVITY for item in activities):
        placeholder = _activity(branch, "Email setup required", "Fail")
        if placeholder.get("typeProperties", {}).get("errorCode") != "FarEmailNotConfigured":
            raise ValueError("Existing parent has an unknown email placeholder.")
        return {}, report_url
    loop = _activity(activities, SEND_ACTIVITY, "ForEach")
    if any(item.get("name") == SEND_ACTIVITY for item in properties["activities"]):
        _activity(branch, STORE_EMAILS_ACTIVITY, "SetVariable")
    email = _activity(loop["typeProperties"]["activities"], EMAIL_ACTIVITY, "Office365Email")
    connection = email.get("externalReferences", {}).get("connection")
    if connection is not None:
        connection = guid(connection)
    state = email.get("state", "Active")
    if state not in ("Active", "Inactive") or (state == "Active" and not connection):
        raise ValueError("Existing email activity must be connected or Inactive before redeploying.")
    sender = email["typeProperties"].get("from", "")
    if not isinstance(sender, str):
        raise ValueError("Existing email From must be a mailbox address or blank.")
    if sender:
        validate_email_address(sender)
        if not connection:
            raise ValueError("Existing email From requires a connection.")
    return {
        "email_connection_id": connection, "email_from": sender, "email_state": state,
    }, report_url


def deploy(
    client: FabricClient,
    *,
    workspace_id: str,
    lakehouse_id: str,
    child_pipeline_id: str,
    repo_dir: Path,
    name: str,
    defaults: dict[str, str],
    owner_access_notebook_id: str | None = None,
) -> dict[str, str]:
    if owner_access_notebook_id is not None:
        owner_access_notebook_id = guid(owner_access_notebook_id)
        notebooks = client.list_items(guid(workspace_id), "Notebook")
        matches = [
            item for item in notebooks
            if str(item.get("id", "")).lower() == owner_access_notebook_id
            and item.get("type") == "Notebook"
        ]
        if len(matches) != 1:
            raise ValueError("OWNER_ACCESS_NOTEBOOK_ID must identify one existing Notebook in the FAR workspace.")
    items = client.list_items(workspace_id, "DataPipeline")
    if any(
        item["id"].lower() == guid(child_pipeline_id) and item["displayName"] == name
        for item in items
    ):
        raise ValueError("The parent pipeline name must differ from the existing FAR child pipeline.")
    child = client.pipeline_definition(workspace_id, child_pipeline_id)
    matches = [item for item in items if item["displayName"] == name]
    if len(matches) > 1:
        raise ValueError(f"Multiple DataPipeline items named {name!r}; select unique artifact names.")
    existing = None
    email_settings = {}
    options = {**defaults, "NOTIFICATIONS_ENABLED": "false"}
    if matches:
        existing = client.pipeline_definition(workspace_id, guid(matches[0]["id"]))
        email_settings, options["FAR_REPORT_URL"] = _saved_email_settings(existing, child_pipeline_id)
    bundle = package_runtime(repo_dir, Path("/lakehouse/default/Files/far-orchestration/runtime"))
    notebook = runtime_notebook(
        str(bundle), workspace_id, lakehouse_id, child_pipeline_id=child_pipeline_id,
    )
    notebook_id = client.upsert_item(workspace_id, name + " - Runner", "Notebook", {
        "format": "ipynb", "parts": [definition_part("notebook-content.ipynb", notebook)],
    })
    completion = runtime_notebook(
        str(bundle), workspace_id, lakehouse_id,
        stage="notifications", child_pipeline_id=child_pipeline_id,
    )
    completion_id = client.upsert_item(workspace_id, name + " - Completion", "Notebook", {
        "format": "ipynb", "parts": [definition_part("notebook-content.ipynb", completion)],
    })
    pipeline = build_parent_pipeline(
        child, workspace_id=workspace_id, pipeline_id=child_pipeline_id,
        notebook_id=notebook_id, notification_notebook_id=completion_id, defaults=options,
        owner_access_notebook_id=owner_access_notebook_id,
        **email_settings,
    )
    if matches and client.pipeline_definition(workspace_id, guid(matches[0]["id"])) != existing:
        raise RuntimeError("Parent changed during deployment; no parent update was sent. Inspect it and rerun 06.")
    parent_id = client.upsert_item(workspace_id, name, "DataPipeline", {
        "parts": [definition_part("pipeline-content.json", pipeline)],
    })
    return {
        "pipeline_id": parent_id, "notebook_id": notebook_id, "runtime_bundle": str(bundle),
        "completion_notebook_id": completion_id,
        **({"owner_access_notebook_id": owner_access_notebook_id}
           if owner_access_notebook_id is not None else {}),
    }

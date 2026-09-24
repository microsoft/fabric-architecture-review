# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Provision the optional feature only from its explicitly enabled setup notebook."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestration.deployment import DEFAULTS, deploy
from orchestration.fabric_api import FabricClient, guid
from orchestration.runtime import validate_config
from orchestration.sources import capacity_selector, validate_source


def validate_attachment(workspace_id: str, lakehouse_id: str, context: dict[str, Any]) -> None:
    if (
        str(context.get("defaultLakehouseId") or "").lower() != guid(lakehouse_id)
        or str(context.get("defaultLakehouseWorkspaceId") or "").lower() != guid(workspace_id)
    ):
        raise ValueError(
            "The setup notebook's default Lakehouse does not match WORKSPACE_ID/LAKEHOUSE_ID. "
            "Attach the FAR Lakehouse and restart the session before provisioning."
        )


def provision(
    client: FabricClient, *, workspace_id: str, lakehouse_id: str, child_pipeline_id: str,
    repo_dir: Path, name: str,
    defaults: dict[str, str], context: dict[str, Any] | None = None,
    owner_access_notebook_id: str = "",
) -> dict[str, str]:
    if context is None:
        import notebookutils
        context = dict(notebookutils.runtime.context)
    validate_attachment(workspace_id, lakehouse_id, context)
    if not isinstance(owner_access_notebook_id, str):
        raise ValueError("OWNER_ACCESS_NOTEBOOK_ID must be a string.")
    owner_access_notebook_id = owner_access_notebook_id.strip()
    if owner_access_notebook_id:
        owner_access_notebook_id = guid(owner_access_notebook_id)
    if not name.strip():
        raise ValueError("PARENT_PIPELINE_NAME is required.")
    options = {**DEFAULTS, **defaults, "NOTIFICATIONS_ENABLED": "false"}
    options["CAPACITY_ID_OR_NAME"] = capacity_selector(options["CAPACITY_ID_OR_NAME"])
    validate_config(options)
    validate_source(options)
    return deploy(
        client, workspace_id=guid(workspace_id), lakehouse_id=guid(lakehouse_id),
        child_pipeline_id=guid(child_pipeline_id), repo_dir=repo_dir, name=name,
        defaults=options,
        **({"owner_access_notebook_id": owner_access_notebook_id}
           if owner_access_notebook_id else {}),
    )

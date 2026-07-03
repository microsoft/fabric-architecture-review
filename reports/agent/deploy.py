# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Assemble the Fabric Data Agent item definition for deployment.

Thin wrapper over :mod:`reports.agent.data_agent` that binds the agent to the two
already-deployed items (the governance semantic model + the gold lakehouse) and
stamps the release version. ``fabric/setup.ipynb`` imports
:func:`data_agent_definition` and upserts it with the generic Items REST endpoint
(``POST /workspaces/{wid}/items`` with ``type: "DataAgent"``), the same
base64-parts mechanism it uses for the model and report.

DATA SAFETY: builds the deployment payload only; no data access.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from reports.powerbi.schema import GOLD_TABLES
from reports.agent.data_agent import (
    DEFAULT_LAKEHOUSE_FEWSHOTS,
    agent_publish_description,
    build_definition,
    compose_instructions,
    data_source,
)


def _slug(name: str) -> str:
    """A path-safe data-source name token (folder = '<type>-<name>')."""
    return re.sub(r"[^0-9A-Za-z]+", "", name or "") or "src"


def data_agent_sources(
    model_id: str,
    lakehouse_id: str,
    workspace_id: str,
    *,
    model_name: str,
    lakehouse_name: str,
    tables: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    tbls = tables if tables is not None else [t.name for t in GOLD_TABLES]
    semantic = data_source(
        source_type="semantic_model",
        name=_slug(model_name),
        artifact_id=model_id,
        workspace_id=workspace_id,
        tables=tbls,
        display_name=model_name,
        instructions=(
            "Use this governed semantic model for scores, measures and business "
            "definitions (natural language to DAX). Prefer it for roll-ups such as "
            "Best Practice Score, Fail Count and Average Risk Score."
        ),
        description=(
            "Fabric Architecture Review governance model - findings, scores, risk, "
            "estate graph and VertiPaq footprint. Contains review metadata only."
        ),
    )
    lakehouse = data_source(
        source_type="lakehouse",
        name=_slug(lakehouse_name),
        artifact_id=lakehouse_id,
        workspace_id=workspace_id,
        tables=tbls,
        fewshots=DEFAULT_LAKEHOUSE_FEWSHOTS,
        instructions=(
            "Use for raw exploration and drill-down across the gold tables "
            "(natural language to SQL). Join facts to gold_run_summary and filter "
            "is_latest = 1 for current-state answers."
        ),
        description="Gold Delta tables backing the governance model.",
    )
    return [semantic, lakehouse]


def data_agent_definition(
    model_id: str,
    lakehouse_id: str,
    workspace_id: str,
    *,
    model_name: str,
    lakehouse_name: str,
    tables: Optional[List[str]] = None,
    version: str = "",
    publish: bool = True,
) -> Dict[str, Any]:
    """Full Data Agent item definition (``{"parts": [...]}``) bound to both sources."""
    sources = data_agent_sources(
        model_id, lakehouse_id, workspace_id,
        model_name=model_name, lakehouse_name=lakehouse_name, tables=tables,
    )
    return build_definition(
        ai_instructions=compose_instructions(version),
        sources=sources,
        publish=publish,
        publish_description=agent_publish_description(version),
    )

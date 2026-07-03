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
from reports.ontology.ontology import entity_type_names
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
    ontology_id: Optional[str] = None,
    ontology_name: Optional[str] = None,
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
    sources = [semantic, lakehouse]
    if ontology_id:
        sources.append(data_source(
            source_type="ontology",
            name=_slug(ontology_name or "ontology"),
            artifact_id=ontology_id,
            workspace_id=workspace_id,
            tables=entity_type_names(),
            display_name=ontology_name or "Estate Ontology",
            instructions=(
                "Use the estate ontology to reason over relationships between "
                "capacities, workspaces, semantic models, their tables and findings "
                "(e.g. which models live in a workspace, which workspace is on which "
                "capacity). Prefer it for graph / lineage questions."
            ),
            description=(
                "Fabric IQ knowledge graph of the reviewed estate (Capacity, "
                "Workspace, SemanticModel, ModelTable, Finding entities)."
            ),
        ))
    return sources


def data_agent_definition(
    model_id: str,
    lakehouse_id: str,
    workspace_id: str,
    *,
    model_name: str,
    lakehouse_name: str,
    tables: Optional[List[str]] = None,
    ontology_id: Optional[str] = None,
    ontology_name: Optional[str] = None,
    version: str = "",
    publish: bool = True,
) -> Dict[str, Any]:
    """Full Data Agent item definition (``{"parts": [...]}``) bound to its sources.

    When ``ontology_id`` is provided the Fabric IQ estate ontology is added as a
    third source (preview). Callers that cannot guarantee the ontology exists
    should omit it or wrap the upsert best-effort.
    """
    sources = data_agent_sources(
        model_id, lakehouse_id, workspace_id,
        model_name=model_name, lakehouse_name=lakehouse_name, tables=tables,
        ontology_id=ontology_id, ontology_name=ontology_name,
    )
    return build_definition(
        ai_instructions=compose_instructions(version),
        sources=sources,
        publish=publish,
        publish_description=agent_publish_description(version),
    )

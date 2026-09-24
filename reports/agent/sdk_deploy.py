# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""SDK-based Data Agent deployment - the **stable** path.

Why this exists (root cause of the "table has been deleted or you don't have
permission" warnings): hand-authoring the Data Agent item definition means
reproducing the *backend-enumerated* datasource schema tree - schema -> table ->
column, each node carrying a backend element id and ``children``. A flat,
hand-written table list never matches that, so the service can't resolve any
table and flags **every** one, no matter how often you redeploy or whether the
tables hold data.

The official ``fabric-data-agent-sdk`` sidesteps this completely: it POSTs only
the *artifact reference* and the **backend enumerates the real schema**; table
selection then always resolves. This module drives that SDK flow.

RUNTIME: Fabric only (needs ``fabric-data-agent-sdk`` + a Fabric identity). Run
it **after** the pipeline's first run so the gold tables already exist and the
datasource schema enumerates against populated tables. All SDK imports are lazy
so importing this module off-Fabric (e.g. in unit tests) never fails.

DATA SAFETY: configures the agent's grounding (metadata) only; no data access.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from reports.agent.data_agent import (
    DEFAULT_LAKEHOUSE_FEWSHOTS,
    agent_publish_description,
    compose_instructions,
)

# Deliberately independent of GOLD_TABLES: new tables require an explicit review
# before becoming agent grounding. This is not an artifact authorization policy.
APPROVED_GOLD_TABLES: FrozenSet[str] = frozenset({
    "gold_findings", "gold_run_summary", "gold_dimension_summary",
    "gold_workspaces", "gold_workspace_risk", "gold_semantic_models",
    "gold_capacities", "gold_capacity_items", "gold_cost_finding_impacts",
    "gold_cost_impact_items", "gold_tenant_setting_changes",
    "gold_dax_models", "gold_dax_measures", "gold_notebook_smells",
    "gold_bpa_violations", "gold_graph_nodes", "gold_graph_edges",
    "gold_reports", "gold_notebooks", "gold_pipelines", "gold_lakehouses",
    "gold_lineage_edges", "gold_finding_targets", "gold_severity_matrix",
    "gold_release", "gold_agent_eval", "gold_model_tables", "gold_model_columns",
    "gold_model_partitions", "gold_model_relationships", "gold_model_hierarchies",
    "gold_item_executions", "gold_execution_coverage", "gold_dataflows",
    "gold_dataflow_queries", "gold_dax_objects", "gold_dax_object_coverage",
})

# Tables to leave unselected: they are empty on most runs, so selecting them adds
# no Q&A value and only invites the empty-table warning. gold_agent_eval is
# populated by the self-eval (which needs the SDK) and the report reads it
# directly regardless; the VertiPaq gold_model_* tables are empty unless the
# Fabric-only VertiPaq collector produced rows.
DEFAULT_SKIP_TABLES: Set[str] = {
    "gold_agent_eval",
    "gold_model_tables",
    "gold_model_columns",
    "gold_model_partitions",
    "gold_model_relationships",
    "gold_model_hierarchies",
}

# Fabric artifact-type token per source kind (see the SDK's ``schema_types``).
_SEMANTIC_MODEL = "semanticmodel"
_LAKEHOUSE = "lakehouse"
_SCHEMA_GROUPINGS = frozenset({
    "schema_grouping", "table_grouping", "view_grouping", "function_grouping",
})


def agent_tables(skip_tables: Optional[Iterable[str]] = None) -> List[str]:
    """Explicitly approved central Gold tables, minus optional exclusions."""
    skip = set(DEFAULT_SKIP_TABLES if skip_tables is None else skip_tables)
    return sorted(APPROVED_GOLD_TABLES - skip)


def deploy_agent(
    *,
    agent_name: str,
    model_name: str,
    lakehouse_name: str,
    version: str = "",
    skip_tables: Optional[Iterable[str]] = None,
    publish: bool = True,
    to_m365: bool = False,
) -> Any:
    """Create / configure / publish the Data Agent via the SDK.

    Idempotent: ``create_data_agent`` returns the existing agent if the name is
    taken, and ``add_datasource`` returns the existing datasource for the same
    artifact - so re-running just reconciles the configuration.

    Only the two named central sources are allowed. Existing unexpected sources
    or unverifiable schema selection abort before publish; they are not deleted.
    Artifact permissions/sharing must separately enforce central-only access.
    Required instruction/configuration and requested publication failures
    propagate to the caller. Returns the ``FabricDataAgentManagement`` handle
    only after those operations succeed; ``publish=False`` leaves a draft.
    """
    from fabric.dataagent.client import create_data_agent  # lazy: Fabric-only

    skip = set(DEFAULT_SKIP_TABLES if skip_tables is None else skip_tables)

    print("Deploying data agent via SDK:", agent_name)
    agent = create_data_agent(agent_name)

    agent.update_configuration(instructions=compose_instructions(version))
    print("  instructions set.")

    # The backend enumerates each source's schema/tables - this is the fix.
    _add_datasource(agent, model_name, _SEMANTIC_MODEL)
    _add_datasource(agent, lakehouse_name, _LAKEHOUSE)

    sources = list(agent.get_datasources())
    expected_sources = {(model_name, _SEMANTIC_MODEL), (lakehouse_name, _LAKEHOUSE)}
    actual_sources = {
        (cfg.get("display_name"), _source_kind(cfg))
        for cfg in (ds.get_configuration() for ds in sources)
    }
    if actual_sources != expected_sources or len(sources) != 2:
        raise ValueError(
            "Central agent requires exactly the configured governance model and Gold "
            "Lakehouse. Reconcile unexpected/missing sources before publishing."
        )
    for ds in sources:
        selected = _select_tables(ds, skip)
        fewshots = {
            fs["question"]: fs["query"] for fs in DEFAULT_LAKEHOUSE_FEWSHOTS
            if set(re.findall(r"\bgold_[a-z_]+\b", fs["query"])) <= selected
        }
        _maybe_add_fewshots(ds, fewshots)

    if publish:
        agent.publish(description=agent_publish_description(version), to_m365=to_m365)
        print("  agent published.")
    return agent


def _add_datasource(agent: Any, name: str, kind: str) -> None:
    agent.add_datasource(name, type=kind)
    print(f"  datasource: {name} ({kind})")


def _source_kind(cfg: Dict[str, Any]) -> str:
    dtype = str(cfg.get("type") or "").lower().replace("_", "")
    if dtype in {"lakehouse", "lakehousetables"}:
        return _LAKEHOUSE
    if dtype in {"semanticmodel", "semanticmodeltables"}:
        return _SEMANTIC_MODEL
    raise ValueError(f"Unsupported central agent datasource type: {cfg.get('type')!r}")


@dataclass(frozen=True)
class _TableElement:
    definition: Dict[str, Any]
    logical_path: Tuple[str, ...]


def _unselected_files(element: Dict[str, Any]) -> None:
    if element.get("is_selected") is not False:
        raise ValueError("Lakehouse file grounding must be unselected before publishing.")
    children = element.get("children")
    if not isinstance(children, list):
        raise ValueError("Cannot verify Lakehouse file grounding.")
    for child in children:
        if not isinstance(child, dict):
            raise ValueError("Cannot verify Lakehouse file grounding.")
        _unselected_files(child)


def _table_elements(
    elements: Any, prefix: Tuple[str, ...] = (), logical_prefix: Tuple[str, ...] = (),
    *, allow_files: bool = False,
) -> Dict[Tuple[str, ...], _TableElement]:
    """Keep SDK paths intact; grouping labels are not database schema names."""
    if not isinstance(elements, list):
        raise ValueError("Datasource schema elements must be a list; selection not verified.")
    tables: Dict[Tuple[str, ...], _TableElement] = {}
    names: Set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            raise ValueError("Invalid datasource schema element.")
        name = element.get("name") or element.get("display_name")
        if not isinstance(name, str) or not name:
            raise ValueError("Datasource schema element has no name.")
        if name in names:
            raise ValueError("Duplicate datasource schema path.")
        names.add(name)
        path = (*prefix, name)
        kind = str(element.get("type") or "").rsplit(".", 1)[-1].lower()
        logical_path = logical_prefix if kind in _SCHEMA_GROUPINGS else (*logical_prefix, name)
        if kind == "schema" or kind in _SCHEMA_GROUPINGS:
            children = _table_elements(element.get("children"), path, logical_path, allow_files=allow_files)
            if tables.keys() & children.keys():
                raise ValueError("Duplicate datasource schema path.")
            tables.update(children)
        elif kind in {"table", "view", "function"}:
            if not isinstance(element.get("is_selected"), bool) or path in tables:
                raise ValueError("Invalid or duplicate datasource selection state.")
            tables[path] = _TableElement(element, logical_path)
        elif kind == "lakehouse_files" and allow_files:
            _unselected_files(element)
        else:
            raise ValueError(f"Unsupported datasource schema element type: {kind!r}")
    identities = {(element.definition["type"], element.logical_path) for element in tables.values()}
    if len(identities) != len(tables):
        raise ValueError("Duplicate datasource logical table path.")
    return tables


def _select_tables(ds: Any, skip: Set[str]) -> Set[str]:
    """Reconcile exact table paths, then verify selection before publication.

    Use the backend-enumerated SDK path, including schema/table grouping nodes
    when present. Only the logical dbo schema (or a model's own tables) is eligible.
    Never use a no-argument select, which would include raw and access tables.
    """
    cfg = ds.get_configuration()
    kind = _source_kind(cfg)
    tables = _table_elements(cfg.get("elements"), allow_files=kind == _LAKEHOUSE)
    allowed = set(agent_tables(skip))
    planned = {
        path for path, element in tables.items()
        if str(element.definition["type"]).endswith(".table")
        and element.logical_path[-1] in allowed
        and (element.logical_path == ("dbo", path[-1])
             if kind == _LAKEHOUSE else len(element.logical_path) == 1)
    }
    if not planned:
        raise ValueError("No approved Gold tables found; refusing to publish an ungrounded agent.")
    for path in tables:
        ds.unselect(*path)
    for path in sorted(planned):
        ds.select(*path)
    verified = _table_elements(ds.get_configuration().get("elements"), allow_files=kind == _LAKEHOUSE)
    if {path for path, element in verified.items() if element.definition["is_selected"]} != planned:
        raise ValueError("Datasource selection verification failed; agent was not published.")
    selected = {path[-1] for path in planned}
    missing = allowed - selected
    if missing:
        print("  approved tables unavailable in this source:", ", ".join(sorted(missing)))
    return selected


def _maybe_add_fewshots(ds: Any, fewshots: Dict[str, str]) -> None:
    """Attach NL->SQL few-shots to SQL sources only (lakehouse/warehouse)."""
    try:
        cfg = ds.get_configuration()
    except Exception as exc:
        raise ValueError("Cannot verify datasource type for few-shots.") from exc
    dtype = str(cfg.get("type") or "").lower()
    if not ("lakehouse" in dtype or "warehouse" in dtype):
        return
    try:
        ds.add_fewshots(fewshots)
        print(f"  few-shots: {len(fewshots)} on {cfg.get('display_name', dtype)}")
    except Exception as exc:
        print("  (few-shots skipped:", exc, ")")

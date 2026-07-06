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

from typing import Any, Dict, Iterable, List, Optional, Set

from reports.powerbi.schema import GOLD_TABLES
from reports.agent.data_agent import (
    DEFAULT_LAKEHOUSE_FEWSHOTS,
    agent_publish_description,
    compose_instructions,
)

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


def agent_tables(skip_tables: Optional[Iterable[str]] = None) -> List[str]:
    """Gold tables the agent should ground on (all of them minus the skips)."""
    skip = set(DEFAULT_SKIP_TABLES if skip_tables is None else skip_tables)
    return [t.name for t in GOLD_TABLES if t.name not in skip]


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

    Returns the ``FabricDataAgentManagement`` handle.
    """
    from fabric.dataagent.client import create_data_agent  # lazy: Fabric-only

    skip = set(DEFAULT_SKIP_TABLES if skip_tables is None else skip_tables)

    print("Deploying data agent via SDK:", agent_name)
    agent = create_data_agent(agent_name)

    try:
        agent.update_configuration(instructions=compose_instructions(version))
        print("  instructions set.")
    except Exception as exc:  # non-fatal - keep going
        print("  (instructions skipped:", exc, ")")

    # The backend enumerates each source's schema/tables - this is the fix.
    _add_datasource(agent, model_name, _SEMANTIC_MODEL)
    _add_datasource(agent, lakehouse_name, _LAKEHOUSE)

    fewshots = {fs["question"]: fs["query"] for fs in DEFAULT_LAKEHOUSE_FEWSHOTS}
    for ds in agent.get_datasources():
        _select_tables(ds, skip)
        _maybe_add_fewshots(ds, fewshots)

    if publish:
        try:
            agent.publish(description=agent_publish_description(version), to_m365=to_m365)
            print("  agent published.")
        except Exception as exc:
            print("  (publish failed - configure/publish manually:", exc, ")")
    return agent


def _add_datasource(agent: Any, name: str, kind: str) -> None:
    try:
        agent.add_datasource(name, type=kind)
        print(f"  datasource: {name} ({kind})")
    except Exception as exc:  # returns existing for the same artifact; be safe
        print(f"  (datasource {name} [{kind}] not added: {exc})")


def _select_tables(ds: Any, skip: Set[str]) -> None:
    """Select every backend-enumerated table, then unselect the skipped ones."""
    try:
        ds.select()  # no path = select all selectable (table) elements
    except Exception as exc:
        print("  (table selection skipped:", exc, ")")
        return
    for tbl in skip:
        # Lakehouse/warehouse tables sit under the 'dbo' schema; semantic-model
        # tables are top-level. Try both; ignore if the table isn't in this source.
        for path in (("dbo", tbl), (tbl,)):
            try:
                ds.unselect(*path)
                break
            except Exception:
                continue


def _maybe_add_fewshots(ds: Any, fewshots: Dict[str, str]) -> None:
    """Attach NL->SQL few-shots to SQL sources only (lakehouse/warehouse)."""
    try:
        cfg = ds.get_configuration()
    except Exception:
        return
    dtype = str(cfg.get("type") or "").lower()
    if not ("lakehouse" in dtype or "warehouse" in dtype):
        return
    try:
        ds.add_fewshots(fewshots)
        print(f"  few-shots: {len(fewshots)} on {cfg.get('display_name', dtype)}")
    except Exception as exc:
        print("  (few-shots skipped:", exc, ")")

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Build the Fabric **Ontology** item definition (base64 JSON parts).

A Fabric Ontology (part of Fabric IQ) is a first-class Fabric item that models a
knowledge graph: **entity types** (classes with typed properties, each bound to a
source table) and **relationship types** (directed source -> target links, bound
to a table that carries the source/target keys). Like the semantic model, report
and data agent, it is created/updated through the generic *Items* REST API with
base64-encoded JSON parts, so ``fabric/setup.ipynb`` can upsert it with the same
helper it already uses.

Rather than a single generic node type, the ontology mirrors the real gold tables
as distinct **entity types**, so the graph explorer shows a proper estate model
instead of one opaque box:

* **Capacity**      <- ``gold_capacities``         key ``(capacity_id, run_id)``
* **Workspace**     <- ``gold_workspaces``         key ``(workspace_id, run_id)``
* **SemanticModel** <- ``gold_semantic_models``    key ``(model_id, run_id)``
* **ModelTable**    <- ``gold_model_tables``       key ``(model_id, table_name, run_id)``
* **Report**        <- ``gold_reports``            key ``(report_id, run_id)``
* **Notebook**      <- ``gold_notebooks``          key ``(notebook_id, run_id)``
* **Pipeline**      <- ``gold_pipelines``          key ``(pipeline_id, run_id)``
* **Lakehouse**     <- ``gold_lakehouses``         key ``(lakehouse_id, run_id)``
* **Owner**         <- ``gold_owners``             key ``(owner_id, run_id)``
* **Finding**       <- ``gold_findings``           key ``(rule_id, run_id)``

and the relationships that connect them (each bound to a table that carries both
foreign keys within the same ``run_id`` so every review run is its own graph):

* **Workspace  -HostedOnCapacity-> Capacity**       (``gold_workspaces``)
* **SemanticModel -InWorkspace-> Workspace**        (``gold_semantic_models``)
* **ModelTable -BelongsToModel-> SemanticModel**    (``gold_model_tables``)
* **Report -ReportInWorkspace-> Workspace**         (``gold_reports``)
* **Notebook -NotebookInWorkspace-> Workspace**     (``gold_notebooks``)
* **Pipeline -PipelineInWorkspace-> Workspace**     (``gold_pipelines``)
* **Lakehouse -LakehouseInWorkspace-> Workspace**   (``gold_lakehouses``)
* **Owner -OwnerAdministersWorkspace-> Workspace**  (``gold_owner_edges``)
* **SemanticModel -ModelFeedsReport-> Report**      (``gold_lineage_edges``)
* **Finding -AffectsWorkspace-> Workspace**         (``gold_finding_targets``)

Definition parts (per the Fabric "Ontology item definition" schema)::

    .platform                                              {"metadata":{"type":"Ontology",...}}
    definition.json                                        {}
    EntityTypes/<eid>/definition.json                      the entity type + its properties
    EntityTypes/<eid>/DataBindings/<guid>.json             bind properties -> gold table columns
    RelationshipTypes/<rid>/definition.json                source -> target relationship
    RelationshipTypes/<rid>/Contextualizations/<guid>.json bind the relationship -> a keyed table

Entity-type / property / relationship ids are positive 64-bit integers that must
be unique across the ontology; we derive them deterministically from stable names
(a truncated SHA-256) so re-deploys are idempotent. Data-binding and
contextualization ids are deterministic GUIDs (uuid5). Only the proven value
types ``String`` / ``Double`` / ``BigInt`` are used, so booleans and timestamps
are left to the semantic model / lakehouse (which the data agent also queries).

DATA SAFETY: builds the item definition only; no data access.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Dict, List, NamedTuple, Tuple

_NS = uuid.UUID("9c4e2f1b-7a63-4d58-8e21-3f0a5b6c7d81")


# --------------------------------------------------------------------------- #
# Declarative model: entity types + relationship types.                        #
# --------------------------------------------------------------------------- #
class Prop(NamedTuple):
    name: str          # ontology property name
    column: str        # source (gold) column
    value_type: str    # String | Double | BigInt


class Entity(NamedTuple):
    name: str
    table: str                 # gold Delta table it binds to
    id_props: Tuple[str, ...]  # property names forming the composite entity id
    display: str               # property name used as the display name
    props: List[Prop]


class KeyBinding(NamedTuple):
    column: str        # source column on the relationship's data-binding table
    prop: str          # id property (on the source/target entity) it maps to


class Relationship(NamedTuple):
    name: str
    source: str        # source entity name
    target: str        # target entity name
    table: str         # data-binding table carrying both foreign keys + run_id
    source_keys: List[KeyBinding]
    target_keys: List[KeyBinding]


# Entity types, one per real gold table. Only String/Double/BigInt props (the
# value types proven to bind), so the ontology never fails on an unsupported type.
ENTITIES: List[Entity] = [
    Entity(
        name="Capacity",
        table="gold_capacities",
        id_props=("CapacityId", "RunId"),
        display="CapacityName",
        props=[
            Prop("CapacityId", "capacity_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("CapacityName", "capacity_name", "String"),
            Prop("Sku", "sku", "String"),
            Prop("Kind", "kind", "String"),
            Prop("Region", "region", "String"),
            Prop("State", "state", "String"),
        ],
    ),
    Entity(
        name="Workspace",
        table="gold_workspaces",
        id_props=("WorkspaceId", "RunId"),
        display="WorkspaceName",
        props=[
            Prop("WorkspaceId", "workspace_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
            Prop("CapacityId", "capacity_id", "String"),
            Prop("ItemCount", "item_count", "BigInt"),
            Prop("AdminCount", "admin_count", "BigInt"),
            Prop("Description", "description", "String"),
        ],
    ),
    Entity(
        name="SemanticModel",
        table="gold_semantic_models",
        id_props=("ModelId", "RunId"),
        display="ModelName",
        props=[
            Prop("ModelId", "model_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("ModelName", "model_name", "String"),
            Prop("WorkspaceId", "workspace_id", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
            Prop("StorageMode", "storage_mode", "String"),
            Prop("TotalSize", "total_size", "BigInt"),
            Prop("TableCount", "table_count", "BigInt"),
            Prop("ColumnCount", "column_count", "BigInt"),
            Prop("MaxRefreshSeconds", "max_refresh_seconds", "Double"),
        ],
    ),
    Entity(
        name="ModelTable",
        table="gold_model_tables",
        id_props=("ModelId", "TableName", "RunId"),
        display="TableName",
        props=[
            Prop("ModelId", "model_id", "String"),
            Prop("TableName", "table_name", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("ModelName", "model_name", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
            Prop("RowCount", "row_count", "BigInt"),
            Prop("TotalSize", "total_size", "BigInt"),
            Prop("ColumnCount", "column_count", "BigInt"),
            Prop("PctDb", "pct_db", "Double"),
        ],
    ),
    Entity(
        name="Finding",
        table="gold_findings",
        id_props=("RuleId", "RunId"),
        display="Title",
        props=[
            Prop("RuleId", "rule_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("Title", "title", "String"),
            Prop("Dimension", "dimension", "String"),
            Prop("Severity", "severity", "String"),
            Prop("Status", "status", "String"),
            Prop("Recommendation", "recommendation", "String"),
            Prop("Affected", "affected", "String"),
            Prop("IsFail", "is_fail", "BigInt"),
        ],
    ),
    Entity(
        name="Report",
        table="gold_reports",
        id_props=("ReportId", "RunId"),
        display="ReportName",
        props=[
            Prop("ReportId", "report_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("ReportName", "report_name", "String"),
            Prop("WorkspaceId", "workspace_id", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
            Prop("Status", "status", "String"),
            Prop("RiskScore", "risk_score", "Double"),
            Prop("IssueCount", "issue_count", "BigInt"),
        ],
    ),
    Entity(
        name="Notebook",
        table="gold_notebooks",
        id_props=("NotebookId", "RunId"),
        display="NotebookName",
        props=[
            Prop("NotebookId", "notebook_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("NotebookName", "notebook_name", "String"),
            Prop("WorkspaceId", "workspace_id", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
            Prop("Status", "status", "String"),
            Prop("RiskScore", "risk_score", "Double"),
            Prop("IssueCount", "issue_count", "BigInt"),
        ],
    ),
    Entity(
        name="Pipeline",
        table="gold_pipelines",
        id_props=("PipelineId", "RunId"),
        display="PipelineName",
        props=[
            Prop("PipelineId", "pipeline_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("PipelineName", "pipeline_name", "String"),
            Prop("WorkspaceId", "workspace_id", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
        ],
    ),
    Entity(
        name="Lakehouse",
        table="gold_lakehouses",
        id_props=("LakehouseId", "RunId"),
        display="LakehouseName",
        props=[
            Prop("LakehouseId", "lakehouse_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("LakehouseName", "lakehouse_name", "String"),
            Prop("WorkspaceId", "workspace_id", "String"),
            Prop("WorkspaceName", "workspace_name", "String"),
        ],
    ),
    Entity(
        name="Owner",
        table="gold_owners",
        id_props=("OwnerId", "RunId"),
        display="OwnerName",
        props=[
            Prop("OwnerId", "owner_id", "String"),
            Prop("RunId", "run_id", "String"),
            Prop("OwnerName", "owner_name", "String"),
        ],
    ),
]

# Relationship types. Each binds to a table that carries BOTH foreign keys plus
# run_id, so source/target keys resolve to the composite entity ids of one run.
RELATIONSHIPS: List[Relationship] = [
    Relationship(
        name="HostedOnCapacity",
        source="Workspace",
        target="Capacity",
        table="gold_workspaces",
        source_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("capacity_id", "CapacityId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="InWorkspace",
        source="SemanticModel",
        target="Workspace",
        table="gold_semantic_models",
        source_keys=[KeyBinding("model_id", "ModelId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="BelongsToModel",
        source="ModelTable",
        target="SemanticModel",
        table="gold_model_tables",
        source_keys=[
            KeyBinding("model_id", "ModelId"),
            KeyBinding("table_name", "TableName"),
            KeyBinding("run_id", "RunId"),
        ],
        target_keys=[KeyBinding("model_id", "ModelId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="ReportInWorkspace",
        source="Report",
        target="Workspace",
        table="gold_reports",
        source_keys=[KeyBinding("report_id", "ReportId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="NotebookInWorkspace",
        source="Notebook",
        target="Workspace",
        table="gold_notebooks",
        source_keys=[KeyBinding("notebook_id", "NotebookId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="PipelineInWorkspace",
        source="Pipeline",
        target="Workspace",
        table="gold_pipelines",
        source_keys=[KeyBinding("pipeline_id", "PipelineId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="LakehouseInWorkspace",
        source="Lakehouse",
        target="Workspace",
        table="gold_lakehouses",
        source_keys=[KeyBinding("lakehouse_id", "LakehouseId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="OwnerAdministersWorkspace",
        source="Owner",
        target="Workspace",
        table="gold_owner_edges",
        source_keys=[KeyBinding("owner_id", "OwnerId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="ModelFeedsReport",
        source="SemanticModel",
        target="Report",
        table="gold_lineage_edges",
        source_keys=[KeyBinding("model_id", "ModelId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("report_id", "ReportId"), KeyBinding("run_id", "RunId")],
    ),
    Relationship(
        name="AffectsWorkspace",
        source="Finding",
        target="Workspace",
        table="gold_finding_targets",
        source_keys=[KeyBinding("rule_id", "RuleId"), KeyBinding("run_id", "RunId")],
        target_keys=[KeyBinding("workspace_id", "WorkspaceId"), KeyBinding("run_id", "RunId")],
    ),
]

ENTITIES_BY_NAME: Dict[str, Entity] = {e.name: e for e in ENTITIES}


# --------------------------------------------------------------------------- #
# Deterministic id helpers.                                                     #
# --------------------------------------------------------------------------- #
def _bigint(*parts: str) -> str:
    """Deterministic positive 64-bit integer id (as a string) from stable names."""
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    n = int(h, 16) % (2 ** 63)
    return str(n or 1)


def _guid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _b64(obj: Any) -> str:
    return base64.b64encode(json.dumps(obj, indent=2).encode("utf-8")).decode("ascii")


def _part(path: str, obj: Any) -> Dict[str, str]:
    return {"path": path, "payload": _b64(obj), "payloadType": "InlineBase64"}


def _entity_id(entity: Entity) -> str:
    return _bigint("entity", entity.name)


def _prop_ids(entity: Entity) -> Dict[str, str]:
    return {p.name: _bigint("prop", entity.name, p.name) for p in entity.props}


def _rel_id(rel: Relationship) -> str:
    return _bigint("relationship", rel.name)


# --------------------------------------------------------------------------- #
# Part builders.                                                                #
# --------------------------------------------------------------------------- #
def _lakehouse_table(workspace_id: str, lakehouse_id: str, table: str) -> Dict[str, Any]:
    return {
        "sourceType": "LakehouseTable",
        "workspaceId": workspace_id,
        "itemId": lakehouse_id,
        "sourceTableName": table,
        "sourceSchema": "dbo",
    }


def _entity_part(entity: Entity, entity_id: str, prop_ids: Dict[str, str]) -> Dict[str, Any]:
    return {
        "id": entity_id,
        "namespace": "usertypes",
        "baseEntityTypeId": None,
        "name": entity.name,
        "entityIdParts": [prop_ids[p] for p in entity.id_props],
        "displayNamePropertyId": prop_ids[entity.display],
        "namespaceType": "Custom",
        "visibility": "Visible",
        "properties": [
            {
                "id": prop_ids[p.name],
                "name": p.name,
                "redefines": None,
                "baseTypeNamespaceType": None,
                "valueType": p.value_type,
            }
            for p in entity.props
        ],
    }


def _entity_databinding(entity: Entity, binding_id: str, prop_ids: Dict[str, str],
                        workspace_id: str, lakehouse_id: str) -> Dict[str, Any]:
    return {
        "id": binding_id,
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": [
                {"sourceColumnName": p.column, "targetPropertyId": prop_ids[p.name]}
                for p in entity.props
            ],
            "sourceTableProperties": _lakehouse_table(workspace_id, lakehouse_id, entity.table),
        },
    }


def _relationship_part(rel: Relationship, rel_id: str,
                       source_id: str, target_id: str) -> Dict[str, Any]:
    return {
        "namespace": "usertypes",
        "id": rel_id,
        "name": rel.name,
        "namespaceType": "Custom",
        "source": {"entityTypeId": source_id},
        "target": {"entityTypeId": target_id},
    }


def _relationship_contextualization(rel: Relationship, ctx_id: str,
                                    source_prop_ids: Dict[str, str],
                                    target_prop_ids: Dict[str, str],
                                    workspace_id: str, lakehouse_id: str) -> Dict[str, Any]:
    return {
        "id": ctx_id,
        "dataBindingTable": _lakehouse_table(workspace_id, lakehouse_id, rel.table),
        "sourceKeyRefBindings": [
            {"sourceColumnName": kb.column, "targetPropertyId": source_prop_ids[kb.prop]}
            for kb in rel.source_keys
        ],
        "targetKeyRefBindings": [
            {"sourceColumnName": kb.column, "targetPropertyId": target_prop_ids[kb.prop]}
            for kb in rel.target_keys
        ],
    }


def build_definition(lakehouse_id: str, workspace_id: str, *, name: str) -> Dict[str, Any]:
    """Assemble the full Ontology item definition (``{"parts": [...]}``)."""
    entity_ids: Dict[str, str] = {e.name: _entity_id(e) for e in ENTITIES}
    prop_ids: Dict[str, Dict[str, str]] = {e.name: _prop_ids(e) for e in ENTITIES}

    parts: List[Dict[str, str]] = [
        _part(".platform", {"metadata": {"type": "Ontology", "displayName": name}}),
        _part("definition.json", {}),
    ]

    for e in ENTITIES:
        eid = entity_ids[e.name]
        pids = prop_ids[e.name]
        binding_id = _guid("databinding", e.name, e.table)
        parts.append(_part(f"EntityTypes/{eid}/definition.json", _entity_part(e, eid, pids)))
        parts.append(_part(
            f"EntityTypes/{eid}/DataBindings/{binding_id}.json",
            _entity_databinding(e, binding_id, pids, workspace_id, lakehouse_id),
        ))

    for r in RELATIONSHIPS:
        rid = _rel_id(r)
        ctx_id = _guid("contextualization", r.name, r.table)
        parts.append(_part(
            f"RelationshipTypes/{rid}/definition.json",
            _relationship_part(r, rid, entity_ids[r.source], entity_ids[r.target]),
        ))
        parts.append(_part(
            f"RelationshipTypes/{rid}/Contextualizations/{ctx_id}.json",
            _relationship_contextualization(
                r, ctx_id, prop_ids[r.source], prop_ids[r.target], workspace_id, lakehouse_id,
            ),
        ))

    return {"parts": parts}


def ontology_definition(lakehouse_id: str, workspace_id: str, *, name: str) -> Dict[str, Any]:
    """Alias used by ``setup.ipynb`` (mirrors reports.agent.deploy naming)."""
    return build_definition(lakehouse_id, workspace_id, name=name)


def entity_type_names() -> List[str]:
    """The entity-type names, e.g. for wiring the ontology as a data-agent source."""
    return [e.name for e in ENTITIES]


def build_definition_json(lakehouse_id: str, workspace_id: str, *, name: str) -> str:
    return json.dumps(build_definition(lakehouse_id, workspace_id, name=name), indent=2)

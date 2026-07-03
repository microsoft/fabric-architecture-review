"""Build the Fabric **Ontology** item definition (base64 JSON parts).

A Fabric Ontology (part of Fabric IQ) is a first-class Fabric item that models a
knowledge graph: **entity types** (classes with typed properties, each bound to a
source table) and **relationship types** (directed source -> target links, bound
to a table that carries the source/target keys). Like the semantic model, report
and data agent, it is created/updated through the generic *Items* REST API with
base64-encoded JSON parts, so ``fabric/setup.ipynb`` can upsert it with the same
helper it already uses.

The Fabric Architecture Review already materialises the estate as a graph in two
gold Delta tables, which map onto an ontology almost 1:1:

* ``gold_graph_nodes`` -> the **EstateNode** entity type (capacities, workspaces,
  semantic models, reports, notebooks, pipelines, lakehouses, owners), keyed by
  ``(node_id, run_id)`` so each review run is its own consistent graph;
* ``gold_graph_edges`` -> the **RelatedTo** relationship type (hosts, administers,
  contains, feeds ...), whose ``source_id`` / ``target_id`` reference the node ids
  within the same ``run_id``.

Definition parts (per the Fabric "Ontology item definition" schema)::

    .platform                                              {"metadata":{"type":"Ontology",...}}
    definition.json                                        {}
    EntityTypes/<eid>/definition.json                      the entity type + its properties
    EntityTypes/<eid>/DataBindings/<guid>.json             bind properties -> gold table columns
    RelationshipTypes/<rid>/definition.json                source -> target relationship
    RelationshipTypes/<rid>/Contextualizations/<guid>.json bind the relationship -> edge table

Entity-type / property / relationship ids are positive 64-bit integers that must
be unique across the ontology; we derive them deterministically from stable names
(a truncated SHA-256) so re-deploys are idempotent. Data-binding and
contextualization ids are deterministic GUIDs (uuid5).

DATA SAFETY: builds the item definition only; no data access.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Dict, List, Tuple

_NS = uuid.UUID("9c4e2f1b-7a63-4d58-8e21-3f0a5b6c7d81")

NODE_TABLE = "gold_graph_nodes"
EDGE_TABLE = "gold_graph_edges"


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


# EstateNode properties: (property name, source column, value type). The first
# two are the composite entity id; ``NodeName`` is the display name.
_ENTITY = "EstateNode"
_NODE_PROPERTIES: List[Tuple[str, str, str]] = [
    ("NodeId", "node_id", "String"),
    ("RunId", "run_id", "String"),
    ("NodeType", "node_type", "String"),
    ("NodeName", "node_name", "String"),
    ("WorkspaceName", "workspace_name", "String"),
    ("CapacityName", "capacity_name", "String"),
    ("Owner", "owner", "String"),
    ("Status", "status", "String"),
    ("RiskScore", "risk_score", "Double"),
    ("IssueCount", "issue_count", "BigInt"),
]
_ID_PARTS = ("NodeId", "RunId")
_DISPLAY_PROP = "NodeName"

_RELATIONSHIP = "RelatedTo"


def _lakehouse_table(workspace_id: str, lakehouse_id: str, table: str) -> Dict[str, Any]:
    return {
        "sourceType": "LakehouseTable",
        "workspaceId": workspace_id,
        "itemId": lakehouse_id,
        "sourceTableName": table,
        "sourceSchema": "dbo",
    }


def _entity_part(entity_id: str, prop_ids: Dict[str, str]) -> Dict[str, Any]:
    return {
        "id": entity_id,
        "namespace": "usertypes",
        "baseEntityTypeId": None,
        "name": _ENTITY,
        "entityIdParts": [prop_ids[p] for p in _ID_PARTS],
        "displayNamePropertyId": prop_ids[_DISPLAY_PROP],
        "namespaceType": "Custom",
        "visibility": "Visible",
        "properties": [
            {
                "id": prop_ids[name],
                "name": name,
                "redefines": None,
                "baseTypeNamespaceType": None,
                "valueType": vtype,
            }
            for name, _col, vtype in _NODE_PROPERTIES
        ],
    }


def _entity_databinding(binding_id: str, prop_ids: Dict[str, str],
                        workspace_id: str, lakehouse_id: str) -> Dict[str, Any]:
    return {
        "id": binding_id,
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": [
                {"sourceColumnName": col, "targetPropertyId": prop_ids[name]}
                for name, col, _vtype in _NODE_PROPERTIES
            ],
            "sourceTableProperties": _lakehouse_table(workspace_id, lakehouse_id, NODE_TABLE),
        },
    }


def _relationship_part(rel_id: str, entity_id: str) -> Dict[str, Any]:
    return {
        "namespace": "usertypes",
        "id": rel_id,
        "name": _RELATIONSHIP,
        "namespaceType": "Custom",
        "source": {"entityTypeId": entity_id},
        "target": {"entityTypeId": entity_id},
    }


def _relationship_contextualization(ctx_id: str, prop_ids: Dict[str, str],
                                    workspace_id: str, lakehouse_id: str) -> Dict[str, Any]:
    # gold_graph_edges carries source_id/target_id + run_id, which key both ends
    # of the relationship against the EstateNode composite id (NodeId, RunId).
    return {
        "id": ctx_id,
        "dataBindingTable": _lakehouse_table(workspace_id, lakehouse_id, EDGE_TABLE),
        "sourceKeyRefBindings": [
            {"sourceColumnName": "source_id", "targetPropertyId": prop_ids["NodeId"]},
            {"sourceColumnName": "run_id", "targetPropertyId": prop_ids["RunId"]},
        ],
        "targetKeyRefBindings": [
            {"sourceColumnName": "target_id", "targetPropertyId": prop_ids["NodeId"]},
            {"sourceColumnName": "run_id", "targetPropertyId": prop_ids["RunId"]},
        ],
    }


def build_definition(lakehouse_id: str, workspace_id: str, *, name: str) -> Dict[str, Any]:
    """Assemble the full Ontology item definition (``{"parts": [...]}``)."""
    entity_id = _bigint("entity", _ENTITY)
    rel_id = _bigint("relationship", _RELATIONSHIP)
    prop_ids = {p[0]: _bigint("prop", _ENTITY, p[0]) for p in _NODE_PROPERTIES}
    binding_id = _guid("databinding", _ENTITY, NODE_TABLE)
    ctx_id = _guid("contextualization", _RELATIONSHIP, EDGE_TABLE)

    parts = [
        _part(".platform", {"metadata": {"type": "Ontology", "displayName": name}}),
        _part("definition.json", {}),
        _part(f"EntityTypes/{entity_id}/definition.json", _entity_part(entity_id, prop_ids)),
        _part(f"EntityTypes/{entity_id}/DataBindings/{binding_id}.json",
              _entity_databinding(binding_id, prop_ids, workspace_id, lakehouse_id)),
        _part(f"RelationshipTypes/{rel_id}/definition.json", _relationship_part(rel_id, entity_id)),
        _part(f"RelationshipTypes/{rel_id}/Contextualizations/{ctx_id}.json",
              _relationship_contextualization(ctx_id, prop_ids, workspace_id, lakehouse_id)),
    ]
    return {"parts": parts}


def ontology_definition(lakehouse_id: str, workspace_id: str, *, name: str) -> Dict[str, Any]:
    """Alias used by ``setup.ipynb`` (mirrors reports.agent.deploy naming)."""
    return build_definition(lakehouse_id, workspace_id, name=name)


def build_definition_json(lakehouse_id: str, workspace_id: str, *, name: str) -> str:
    return json.dumps(build_definition(lakehouse_id, workspace_id, name=name), indent=2)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Optional static estate ontology in the new (TMDL, version 2.0) Fabric IQ definition format.

Historical decisions stay behind model RLS: entities are the estate and the static rule catalogue,
never a finding. Every entity is backed by a Gold table through DirectLake and identified by one
key column that joins the run to the ID (a Gold table holds one row per entity per run).

The Lakehouse binding is written in the form the Fabric portal writes: one shared ``AzureStorage.DataLake``
expression on the Lakehouse's OneLake root, and ``ONT_*`` annotations on every table partition that name the
item. A ``Sql.Database`` expression without them is accepted by the API but the graph is not built from it, so
the nodes and relationships do not populate.
"""
from __future__ import annotations
import base64
import datetime
import json
import re
import uuid
from typing import NamedTuple
from reports.powerbi.schema import GOLD_TABLES_BY_NAME

_NS = uuid.UUID("9c4e2f1b-7a63-4d58-8e21-3f0a5b6c7d81")
_TMDL_TYPES = {"string": "string", "int64": "int64", "double": "double", "boolean": "boolean", "dateTime": "dateTime"}
_FORMATS = {"int64": "0", "dateTime": "General Date"}
_ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LAKEHOUSE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


class LakehouseSource(NamedTuple):
    """The Gold Lakehouse every entity is bound to."""

    workspace_id: str
    workspace_name: str
    lakehouse_id: str
    lakehouse_name: str
    sql_endpoint_server: str

    @property
    def expression(self) -> str:
        return f"DirectLake - {self.lakehouse_name}"

    def annotations(self, pinned_at: str) -> list[tuple[str, str]]:
        return [("ONT_WorkspaceId", self.workspace_id), ("ONT_ItemId", self.lakehouse_id), ("ONT_ItemKind", "Lakehouse"),
                ("ONT_ItemName", self.lakehouse_name), ("ONT_WorkspaceName", self.workspace_name),
                ("ONT_SqlEndpoint", self.sql_endpoint_server), ("ONT_SqlDatabase", self.lakehouse_name),
                ("ONT_PinnedAtUtc", pinned_at)]

    def checked(self) -> "LakehouseSource":
        if not self.sql_endpoint_server or re.search(r"\s", self.sql_endpoint_server):
            raise ValueError("The ontology needs the Gold Lakehouse SQL endpoint server.")
        if not _GUID.match(self.workspace_id or "") or not _GUID.match(self.lakehouse_id or ""):
            raise ValueError("The ontology needs the workspace and Lakehouse IDs.")
        if not _LAKEHOUSE_NAME.match(self.lakehouse_name or ""):
            raise ValueError("The ontology needs the Lakehouse name (letters, digits and underscores).")
        if not self.workspace_name or re.search(r"[\x00-\x1f]", self.workspace_name):
            raise ValueError("The ontology needs the workspace name.")
        return self


class Prop(NamedTuple):
    name: str
    column: str


class Entity(NamedTuple):
    name: str
    table: str
    label: str
    description: str
    props: list[Prop]

    @property
    def key_property(self) -> str:
        return "EntityKey"

    @property
    def key_column(self) -> str:
        return next(prop.column for prop in self.props if prop.name == self.key_property)


class Relationship(NamedTuple):
    """A many-to-one link: ``source`` rows carry ``source_column``, which names one ``target`` row."""

    name: str
    source: str
    target: str
    source_column: str
    target_column: str
    description: str


def _entity(name: str, table: str, key: str, id_column: str, label: str, description: str, extras=()) -> Entity:
    props = [Prop("EntityKey", key), Prop("Id", id_column), Prop("RunId", "run_id"),
             Prop("Name", label), Prop("ProfileId", "profile_id"),
             Prop("ProfileHash", "profile_sha256")]
    props += [Prop(n, c) for n, c in extras]
    return Entity(name, table, label, description, props)


ENTITIES = [
    _entity("Capacity", "gold_capacities", "capacity_key", "capacity_id", "capacity_name",
            "A Fabric capacity and, when FCA is connected, the Azure region and tag gaps of its resource.",
            [("Sku", "sku"), ("Kind", "kind"), ("Region", "region"),
             ("State", "state"), ("FcaRegion", "fca_region"),
             ("MissingTags", "missing_tags")]),
    _entity("Workspace", "gold_workspaces", "workspace_key", "workspace_id", "workspace_name",
            "A reviewed workspace with its declared environment.",
            [("CapacityId", "capacity_id"), ("Environment", "environment"),
             ("ItemCount", "item_count")]),
    _entity("SemanticModel", "gold_semantic_models", "model_key", "model_id", "model_name",
            "A semantic model, its storage mode and whether its definition has row-level (RLS) and object-level (OLS) "
            "security: present, absent or unknown. Never a requirement or a result.",
            [("WorkspaceId", "workspace_id"), ("StorageMode", "storage_mode"),
             ("RlsStatus", "rls_status"), ("OlsStatus", "ols_status"),
             ("SecurityInspection", "security_inspection")]),
    _entity("Report", "gold_reports", "report_key", "report_id", "report_name", "A report in a reviewed workspace.",
            [("WorkspaceId", "workspace_id"), ("ModelId", "model_id")]),
    _entity("Notebook", "gold_notebooks", "notebook_key", "notebook_id", "notebook_name",
            "A notebook in a reviewed workspace.", [("WorkspaceId", "workspace_id")]),
    _entity("Pipeline", "gold_pipelines", "pipeline_key", "pipeline_id", "pipeline_name",
            "A data pipeline in a reviewed workspace.", [("WorkspaceId", "workspace_id")]),
    _entity("Lakehouse", "gold_lakehouses", "lakehouse_key", "lakehouse_id", "lakehouse_name",
            "A lakehouse in a reviewed workspace.", [("WorkspaceId", "workspace_id")]),
    _entity("Owner", "gold_owners", "owner_key", "owner_id", "owner_name",
            "A principal observed as a workspace administrator. Never an access entitlement."),
    _entity("OwnerAssignment", "gold_owner_edges", "assignment_key", "owner_id", "owner_name",
            "One principal administering one workspace.", [("WorkspaceId", "workspace_id")]),
    # The static rule catalogue: definitions only, never a decision about a workspace.
    _entity("Rule", "gold_rules", "rule_key", "rule_id", "title",
            "An architecture rule: what it checks and its kind. Never a result.",
            [("Dimension", "dimension"), ("Kind", "rule_kind"),
             ("Severity", "severity")]),
]
_BY_NAME = {entity.name: entity for entity in ENTITIES}

RELATIONSHIPS = [
    Relationship("HostedOnCapacity", "Workspace", "Capacity", "capacity_key", "capacity_key",
                 "The capacity a workspace runs on."),
    *[Relationship(name + "InWorkspace", name, "Workspace", "workspace_key", "workspace_key",
                   f"The workspace that holds the {name.lower()}.")
      for name in ("SemanticModel", "Report", "Notebook", "Pipeline", "Lakehouse", "OwnerAssignment")],
    Relationship("ReportUsesSemanticModel", "Report", "SemanticModel", "model_key", "model_key",
                 "The semantic model a report reads from."),
    Relationship("AssignmentOfOwner", "OwnerAssignment", "Owner", "owner_key", "owner_key",
                 "The principal in an administrator assignment."),
]


def _guid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _kind(table: str, column: str) -> str:
    kind = next(c.kind for c in GOLD_TABLES_BY_NAME[table].columns if c.name == column)
    return _TMDL_TYPES[kind]


def _columns(entity: Entity) -> list[str]:
    """The only columns the ontology exposes: the bound properties and the keys that join entities."""
    joins = [r.source_column for r in RELATIONSHIPS if r.source == entity.name]
    joins += [r.target_column for r in RELATIONSHIPS if r.target == entity.name]
    return list(dict.fromkeys([*(p.column for p in entity.props), *joins]))


def _doc(text: str, indent: str = "") -> str:
    return f"{indent}/// {text}\n"


def _quoted(name: str) -> str:
    return f"'{name}'" if re.search(r"[^A-Za-z0-9_]", name) else name


def render_table(name: str, tag: str, columns: list[tuple[str, str, str]], expression: str = "DatabaseQuery",
                 annotations: list[tuple[str, str]] | None = None) -> str:
    """A backing table part: ``columns`` are (name, type, lineage tag), bound to the same-named DirectLake column.

    ``expression`` names the shared expression the partition reads. ``annotations`` are the ``ONT_*`` lines the
    portal writes on a Lakehouse table partition."""
    lines = [f"table {name}", f"\tlineageTag: {tag}", ""]
    for column, kind, column_tag in columns:
        lines += [f"\tcolumn {column}", f"\t\tdataType: {kind}"]
        if kind in _FORMATS:
            lines.append(f"\t\tformatString: {_FORMATS[kind]}")
        lines += [f"\t\tlineageTag: {column_tag}", f"\t\tsourceColumn: {column}", ""]
    lines += [f"\tpartition {name} = entity", "\t\tmode: directLake", "\t\tsource",
              f"\t\t\tentityName: {name}", "\t\t\tschemaName: dbo", f"\t\t\texpressionSource: {_quoted(expression)}", ""]
    for key, value in annotations or []:
        lines += [f"\t\tannotation {key} = {value}", ""]
    return "\n".join(lines) + "\n"


def render_entity(name: str, description: str, tag: str, table: str, key: str,
                  properties: list[tuple[str, str, str, str, str]]) -> str:
    """An entity part: ``properties`` are (name, type, column, lineage tag, description)."""
    text = _doc(description) + f"entity {name}\n\tlineageTag: {tag}\n\tbackingTable: {table}\n\tkeyProperty: {key}\n"
    for prop, kind, column, prop_tag, doc in properties:
        text += (f"\n{_doc(doc, chr(9)) if doc else ''}\tproperty {prop}\n\t\tdataType: {kind}\n\t\tlineageTag: {prop_tag}\n"
                 f"\n\t\tbackingConfiguration\n\t\t\tvalueColumn: {table}.{column}\n")
    return text


def table_tmdl(entity: Entity, source: LakehouseSource, pinned_at: str) -> str:
    return render_table(entity.table, _guid("table", entity.table), [
        (column, _kind(entity.table, column), _guid("column", entity.table, column)) for column in _columns(entity)],
        source.expression, source.annotations(pinned_at))


def entity_tmdl(entity: Entity) -> str:
    return render_entity(entity.name, entity.description, _guid("entity", entity.name), entity.table, entity.key_property, [
        (prop.name, _kind(entity.table, prop.column), prop.column, _guid("property", entity.name, prop.name), "")
        for prop in entity.props])

def _tom_name(relationship: Relationship) -> str:
    return "rel_" + relationship.name.lower()


def relationships_tmdl() -> str:
    blocks = []
    for r in RELATIONSHIPS:
        source, target = _BY_NAME[r.source], _BY_NAME[r.target]
        blocks.append(f"relationship {_tom_name(r)}\n\tfromColumn: {source.table}.{r.source_column}\n"
                      f"\ttoColumn: {target.table}.{r.target_column}\n")
    return "\n".join(blocks) + "\n"


def entity_relationships_tmdl() -> str:
    blocks = []
    for r in RELATIONSHIPS:
        blocks.append(_doc(r.description) + f"entityRelationship {r.name}\n\tlineageTag: {_guid('entityRelationship', r.name)}\n"
                      f"\tfromEntity: {r.source}\n\ttoEntity: {r.target}\n\n\tbackingConfiguration\n"
                      f"\t\trelationship: {_tom_name(r)}\n")
    return "\n".join(blocks) + "\n"


def model_tmdl() -> str:
    refs = [f"ref table {e.table}" for e in ENTITIES], [f"ref entity {e.name}" for e in ENTITIES]
    return "model Model\n\n" + "\n".join(refs[0]) + "\n\n" + "\n".join(refs[1]) + "\n\nref namespace default\n"


def expressions_tmdl(source: LakehouseSource) -> str:
    """The shared Direct Lake expression: the Lakehouse's OneLake root, exactly as the portal writes it."""
    root = f"{_ONELAKE}/{source.workspace_id}/{source.lakehouse_id}"
    return (f"expression {_quoted(source.expression)} =\n\t\tlet\n"
            f'\t\t    Source = AzureStorage.DataLake("{root}", [HierarchicalNavigation=true])\n'
            "\t\tin\n\t\t    Source\n"
            f"\tlineageTag: {_guid('expression', source.expression)}\n")


def _part(path: str, text: str) -> dict:
    return {"path": path, "payload": base64.b64encode(text.encode("utf-8")).decode(), "payloadType": "InlineBase64"}


def build_definition(source: LakehouseSource, *, name: str, pinned_at: str | None = None) -> dict:
    """The ontology definition parts, bound to ``source``, the Gold Lakehouse."""
    source = source.checked()
    pinned_at = pinned_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    for entity in ENTITIES:
        columns = {c.name for c in GOLD_TABLES_BY_NAME[entity.table].columns}
        if any(column not in columns for column in _columns(entity)):
            raise ValueError("Ontology binding is outside architectural schema.")
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Ontology", "displayName": name},
        "config": {"version": "2.0", "logicalId": _guid("logicalId", name)},
    }
    parts = [
        _part(".platform", json.dumps(platform, indent=2)),
        _part("database.tmdl", "database\n\tcompatibilityLevel: 1000000\n"),
        _part("model.tmdl", model_tmdl()),
        _part("namespaces/default.tmdl", "namespace default\n\tlineageTag: default\n"),
        _part("expressions.tmdl", expressions_tmdl(source)),
        _part("relationships.tmdl", relationships_tmdl()),
        _part("entityRelationships.tmdl", entity_relationships_tmdl()),
    ]
    for entity in ENTITIES:
        parts.append(_part(f"tables/{entity.table}.tmdl", table_tmdl(entity, source, pinned_at)))
        parts.append(_part(f"entities/{entity.name}.tmdl", entity_tmdl(entity)))
    return {"parts": parts}


ontology_definition = build_definition


def dropped_parts(sent: dict, returned: dict) -> list[str]:
    """The parts the service did not keep. It canonicalises a definition, so reading it back shows what it accepted."""
    kept = {part["path"] for part in returned.get("parts") or []}
    return sorted(part["path"] for part in sent["parts"] if part["path"] not in kept and part["path"] != ".platform")

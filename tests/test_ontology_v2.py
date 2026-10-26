# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The estate ontology in the new (TMDL, version 2.0) definition format."""
import base64
import json
import re

import pytest

from reports.ontology.ontology import ENTITIES, RELATIONSHIPS, LakehouseSource, build_definition, dropped_parts, render_entity, render_table
from reports.powerbi.schema import GOLD_TABLES_BY_NAME, KEY_COLUMNS, ONTOLOGY_KEYS, ontology_key

SERVER = "abc.datawarehouse.fabric.microsoft.com"
WORKSPACE = "a4c58626-1e3c-42c5-af73-573f18bb1251"
LAKEHOUSE = "8960f3a7-13d5-45e6-ad9f-43ae62090fe9"
SOURCE = LakehouseSource(WORKSPACE, "FAR", LAKEHOUSE, "fabric_arch_review_lh", SERVER)
PINNED = "2026-10-19T08:00:00.000Z"

# Layout of the "WindFarm" sample in the Microsoft Learn article "Ontology (new) item definition",
# reduced to three columns. The generator must reproduce it byte for byte (tabs, order, blank lines).
SAMPLE_TABLE = (
    "table wind_power_plants\n\tlineageTag: e5618891-6941-4fe6-a163-71a94ca9cf90\n\n"
    "\tcolumn PlantId\n\t\tdataType: string\n\t\tlineageTag: b44f13a3-1bff-4f5d-b9d0-c197f1751869\n\t\tsourceColumn: PlantId\n\n"
    "\tcolumn CapacityFactor\n\t\tdataType: double\n\t\tlineageTag: 7958efda-d3aa-4518-b583-256e07f2a130\n\t\tsourceColumn: CapacityFactor\n\n"
    "\tcolumn ZipCode\n\t\tdataType: int64\n\t\tformatString: 0\n\t\tlineageTag: 997acfd2-ad08-4418-b83d-51607ad62792\n\t\tsourceColumn: ZipCode\n\n"
    "\tpartition wind_power_plants = entity\n\t\tmode: directLake\n\t\tsource\n\t\t\tentityName: wind_power_plants\n"
    "\t\t\tschemaName: dbo\n\t\t\texpressionSource: DatabaseQuery\n\n\n"
)
SAMPLE_ENTITY = (
    "/// A wind power plant (site) containing turbines and weather towers.\nentity wind_power_plants\n"
    "\tlineageTag: 71ee9f34-58e2-49a1-8355-be595c108e67\n\tbackingTable: wind_power_plants\n\tkeyProperty: PlantId\n\n"
    "\t/// Unique plant identifier.\n\tproperty PlantId\n\t\tdataType: string\n\t\tlineageTag: c3c86746-adbf-4ea1-8515-85af37d90144\n\n"
    "\t\tbackingConfiguration\n\t\t\tvalueColumn: wind_power_plants.PlantId\n\n"
    "\t/// Postal code of the plant location.\n\tproperty ZipCode\n\t\tdataType: int64\n\t\tlineageTag: a555076c-743e-43d4-9cc7-b9774e4a7279\n\n"
    "\t\tbackingConfiguration\n\t\t\tvalueColumn: wind_power_plants.ZipCode\n\n"
)


def parts():
    return {part["path"]: base64.b64decode(part["payload"]).decode("utf-8")
            for part in build_definition(SOURCE, name="FAR", pinned_at=PINNED)["parts"]}


def test_the_renderers_reproduce_the_documented_layout():
    table = render_table("wind_power_plants", "e5618891-6941-4fe6-a163-71a94ca9cf90", [
        ("PlantId", "string", "b44f13a3-1bff-4f5d-b9d0-c197f1751869"),
        ("CapacityFactor", "double", "7958efda-d3aa-4518-b583-256e07f2a130"),
        ("ZipCode", "int64", "997acfd2-ad08-4418-b83d-51607ad62792")])
    assert table.rstrip("\n") == SAMPLE_TABLE.rstrip("\n")
    entity = render_entity("wind_power_plants", "A wind power plant (site) containing turbines and weather towers.",
                           "71ee9f34-58e2-49a1-8355-be595c108e67", "wind_power_plants", "PlantId", [
        ("PlantId", "string", "PlantId", "c3c86746-adbf-4ea1-8515-85af37d90144", "Unique plant identifier."),
        ("ZipCode", "int64", "ZipCode", "a555076c-743e-43d4-9cc7-b9774e4a7279", "Postal code of the plant location.")])
    assert entity.rstrip("\n") == SAMPLE_ENTITY.rstrip("\n")


def test_the_item_is_a_version_2_ontology_with_the_required_parts():
    files = parts()
    platform = json.loads(files[".platform"])
    assert platform["metadata"] == {"type": "Ontology", "displayName": "FAR"}
    assert platform["config"]["version"] == "2.0" and platform["config"]["logicalId"]
    assert platform["$schema"].endswith("/platformProperties/2.0.0/schema.json")
    for required in ("database.tmdl", "model.tmdl", "namespaces/default.tmdl", "expressions.tmdl",
                     "relationships.tmdl", "entityRelationships.tmdl"):
        assert required in files
    assert files["database.tmdl"] == "database\n\tcompatibilityLevel: 1000000\n"
    assert files["namespaces/default.tmdl"].startswith("namespace default\n")
    assert "Sql.Database" not in files["expressions.tmdl"]
    assert not any(path.startswith(("EntityTypes/", "RelationshipTypes/")) for path in files) and "definition.json" not in files


def test_the_ontology_never_exposes_a_table_that_holds_decisions():
    """The ontology reads the Lakehouse directly, so it bypasses the model's profile gate and row-level security."""
    decisions = {t for t, table in GOLD_TABLES_BY_NAME.items()
                 if {"rule_id", "status"} <= {c.name for c in table.columns}}
    assert {"gold_findings", "gold_finding_targets", "gold_finding_items"} <= decisions
    assert not {entity.table for entity in ENTITIES} & decisions


def test_every_entity_has_a_table_an_entity_part_and_a_model_reference():
    files, model = parts(), parts()["model.tmdl"]
    assert model.startswith("model Model\n") and "ref namespace default" in model
    for entity in ENTITIES:
        assert f"tables/{entity.table}.tmdl" in files and f"entities/{entity.name}.tmdl" in files
        assert f"ref table {entity.table}\n" in model and f"ref entity {entity.name}\n" in model


def test_each_entity_has_one_unique_key_property_over_a_run_scoped_key_column():
    for entity in ENTITIES:
        text = parts()[f"entities/{entity.name}.tmdl"]
        assert text.count("keyProperty:") == 1 and "keyProperty: EntityKey\n" in text
        assert f"valueColumn: {entity.table}.{entity.key_column}\n" in text
        assert entity.key_column in ONTOLOGY_KEYS[entity.table]
        assert len({prop.name for prop in entity.props}) == len(entity.props)


def test_only_declared_columns_are_exposed_and_all_exist_in_gold():
    for entity in ENTITIES:
        table = parts()[f"tables/{entity.table}.tmdl"]
        exposed = re.findall(r"^\tcolumn (\w+)$", table, re.M)
        schema = {c.name for c in GOLD_TABLES_BY_NAME[entity.table].columns}
        assert set(exposed) <= schema and len(exposed) == len(set(exposed))
        assert {prop.column for prop in entity.props} <= set(exposed)
    forbidden = re.compile(r"evidence_json|expression_preview|duration|cardinality|vertipaq|principal_object_id", re.I)
    assert not forbidden.search("".join(text for path, text in parts().items() if path.startswith(("tables/", "entities/"))))


def test_property_types_follow_the_gold_column_types():
    text = parts()["entities/Workspace.tmdl"]
    assert re.search(r"property ItemCount\n\t\tdataType: int64", text) and re.search(r"property Name\n\t\tdataType: string", text)


def test_relationships_join_a_foreign_key_to_a_key_and_have_an_entity_relationship_each():
    files = parts()
    by_name = {entity.name: entity for entity in ENTITIES}
    tom, semantic = files["relationships.tmdl"], files["entityRelationships.tmdl"]
    assert len(RELATIONSHIPS) == tom.count("relationship rel_") == semantic.count("entityRelationship ")
    for r in RELATIONSHIPS:
        source, target = by_name[r.source], by_name[r.target]
        assert r.source_column in ONTOLOGY_KEYS[source.table] and r.target_column in ONTOLOGY_KEYS[target.table]
        assert r.target_column == target.key_column
        name = "rel_" + r.name.lower()
        assert f"relationship {name}\n\tfromColumn: {source.table}.{r.source_column}\n\ttoColumn: {target.table}.{r.target_column}\n" in tom
        assert f"entityRelationship {r.name}\n" in semantic and f"\t\trelationship: {name}\n" in semantic
        assert f"\tfromEntity: {r.source}\n\ttoEntity: {r.target}\n" in semantic
    assert {r.target for r in RELATIONSHIPS if r.name.endswith("InWorkspace")} == {"Workspace"}


def test_the_rule_entity_is_the_static_catalogue_and_findings_are_never_entities():
    assert {entity.table for entity in ENTITIES if entity.name == "Rule"} == {"gold_rules"}
    assert not any(entity.name in {"Finding", "TenantSetting"} or entity.table in {"gold_findings", "gold_tenant_settings"}
                   for entity in ENTITIES)


def test_lineage_tags_are_stable_between_builds():
    assert parts() == parts()


def test_every_table_is_bound_to_the_lakehouse_the_way_the_portal_writes_it():
    files = parts()
    expression = files["expressions.tmdl"]
    assert expression.startswith("expression 'DirectLake - fabric_arch_review_lh' =\n")
    assert f'AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/{WORKSPACE}/{LAKEHOUSE}", [HierarchicalNavigation=true])' in expression
    tables = {path: text for path, text in files.items() if path.startswith("tables/")}
    assert len(tables) == len(ENTITIES)
    for text in tables.values():
        assert "\t\t\texpressionSource: 'DirectLake - fabric_arch_review_lh'\n" in text
        assert "DatabaseQuery" not in text
        assert re.findall(r"annotation (ONT_\w+) = (.*)", text) == [
            ("ONT_WorkspaceId", WORKSPACE), ("ONT_ItemId", LAKEHOUSE), ("ONT_ItemKind", "Lakehouse"),
            ("ONT_ItemName", "fabric_arch_review_lh"), ("ONT_WorkspaceName", "FAR"), ("ONT_SqlEndpoint", SERVER),
            ("ONT_SqlDatabase", "fabric_arch_review_lh"), ("ONT_PinnedAtUtc", PINNED)]


def test_the_binding_is_pinned_at_deployment_time_when_no_time_is_given():
    text = base64.b64decode(build_definition(SOURCE, name="FAR")["parts"][-2]["payload"]).decode("utf-8")
    assert re.search(r"annotation ONT_PinnedAtUtc = \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", text)


@pytest.mark.parametrize("field, value, message", [
    ("sql_endpoint_server", "", "SQL endpoint"), ("sql_endpoint_server", "a b", "SQL endpoint"),
    ("workspace_id", "not-a-guid", "IDs"), ("lakehouse_id", "", "IDs"),
    ("lakehouse_name", "", "Lakehouse name"), ("lakehouse_name", "a'b", "Lakehouse name"), ("lakehouse_name", "a b", "Lakehouse name"),
    ("workspace_name", "", "workspace name"), ("workspace_name", "a\nb", "workspace name"),
])
def test_an_incomplete_or_unsafe_lakehouse_source_is_refused(field, value, message):
    with pytest.raises(ValueError, match=message):
        build_definition(SOURCE._replace(**{field: value}), name="FAR")


def test_dropped_parts_are_the_sent_parts_the_service_did_not_return():
    sent = build_definition(SOURCE, name="FAR", pinned_at=PINNED)
    kept = {"parts": [part for part in sent["parts"] if part["path"] != "entities/Capacity.tmdl"]}
    assert dropped_parts(sent, kept) == ["entities/Capacity.tmdl"]
    assert dropped_parts(sent, sent) == []
    assert dropped_parts(sent, {"parts": [part for part in sent["parts"] if part["path"] != ".platform"]}) == []
    assert dropped_parts(sent, {}) == sorted(part["path"] for part in sent["parts"] if part["path"] != ".platform")


def test_ontology_keys_join_the_run_to_the_ids_and_are_empty_when_a_part_is_missing():
    assert ontology_key("run1", "ws1") == "run1|ws1"
    assert ontology_key("run1", "o1", "w1") == "run1|o1|w1"
    assert ontology_key("run1", "") is None and ontology_key("run1", None) is None and ontology_key("", "ws1") is None
    assert {"workspace_key", "capacity_key", "model_key"} <= KEY_COLUMNS

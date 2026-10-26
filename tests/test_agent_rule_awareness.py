# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The agents and the ontology know the rules: instructions, approved tables and catalogue entity."""
from analyzers.far_profile import ACTIVE_RULE_IDS, RULE_KINDS
from reports.agent.data_agent import DEFAULT_AI_INSTRUCTIONS, DEFAULT_LAKEHOUSE_FEWSHOTS
from reports.agent.owner_agent import OWNER_AGENT_EXAMPLES, OWNER_AGENT_TABLES, owner_instructions
from reports.agent.sdk_deploy import APPROVED_GOLD_TABLES
from reports.ontology.ontology import ENTITIES, RELATIONSHIPS, LakehouseSource, build_definition
from reports.owner.schema import OWNER_TABLES_BY_NAME
from reports.powerbi.schema import GOLD_TABLES_BY_NAME
from tests._far_surfaces import FORBIDDEN_TABLES


def test_central_instructions_name_every_rule_under_its_kind():
    text = DEFAULT_AI_INSTRUCTIONS
    assert all(rule in text for rule in ACTIVE_RULE_IDS)
    for label, kind in (("Scored controls", "control"), ("Signals", "signal"), ("Coverage", "coverage")):
        line = next(line for line in text.splitlines() if line.startswith(label))
        assert {rule for rule in ACTIVE_RULE_IDS if rule in line} == {r for r, k in RULE_KINDS.items() if k == kind}


def test_central_instructions_explain_how_to_answer_and_keep_the_boundaries():
    text = DEFAULT_AI_INSTRUCTIONS
    for phrase in ("gold_rules", "status_meaning", "next_action", "reason_code", "review_recommended",
                   "never SELECT *", "Permissions and reader RLS", "Unknown and missing_evidence"):
        assert phrase in text
    # FUAM-backed signals are answerable; the old blanket ban on job monitoring contradicted them.
    assert "OPS-002 to OPS-005" in text and "individual job or refresh monitoring" in text
    assert "Do not answer optimization, expression tuning, model internals, job monitoring" not in text


def test_central_instructions_route_results_to_the_model_and_the_estate_to_the_lakehouse():
    text = DEFAULT_AI_INSTRUCTIONS
    for phrase in ("Two sources", "semantic model holds decisions", "Gold Lakehouse holds the static estate",
                   "missing_tags", "fca_region", "latest compatible run_id"):
        assert phrase in text


def test_every_lakehouse_example_reads_only_tables_the_lakehouse_may_expose():
    import re
    from reports.agent.sdk_deploy import _LAKEHOUSE_TABLES
    for example in DEFAULT_LAKEHOUSE_FEWSHOTS:
        assert set(re.findall(r"\bgold_[a-z_]+\b", example["query"])) <= _LAKEHOUSE_TABLES, example["question"]


def test_the_rule_catalogue_is_approved_and_has_a_sql_example():
    assert "gold_rules" in APPROVED_GOLD_TABLES and not FORBIDDEN_TABLES & APPROVED_GOLD_TABLES
    assert any("FROM gold_rules" in example["query"] for example in DEFAULT_LAKEHOUSE_FEWSHOTS)
    for example in DEFAULT_LAKEHOUSE_FEWSHOTS:
        columns = {column.name for table in GOLD_TABLES_BY_NAME.values() for column in table.columns}
        assert "SELECT *" not in example["query"] and columns


def test_owner_instructions_lead_with_the_finding_row_and_keep_findings_distinct():
    text = owner_instructions("1")
    for phrase in ("status_meaning", "why_it_matters", "next_action", "how_to_fix", "rule_kind control",
                   "never a pass", "no identity is stored", "Do not query owner_access"):
        assert phrase in text
    assert len(text) <= 15000


def test_the_owner_findings_example_asks_for_the_guidance_columns():
    query = next(query for question, query in OWNER_AGENT_EXAMPLES.items() if "findings" in question)
    columns = {column.name for column in OWNER_TABLES_BY_NAME["owner_findings"].columns}
    for column in ("next_action", "status_meaning", "why_it_matters", "how_to_fix", "rule_kind"):
        assert f"[{column}]" in query and column in columns
    assert "owner_findings" in OWNER_AGENT_TABLES


def test_the_ontology_holds_the_static_rule_catalogue_but_no_decisions():
    rule = next(entity for entity in ENTITIES if entity.name == "Rule")
    assert rule.table == "gold_rules"
    assert {prop.name for prop in rule.props} >= {"Id", "RunId", "Name", "Dimension", "Kind", "Severity"}
    assert not any(entity.name in {"Finding", "TenantSetting"} for entity in ENTITIES)
    assert not any(rel.source == "Rule" or rel.target == "Rule" for rel in RELATIONSHIPS)
    source = LakehouseSource("11111111-1111-4111-8111-111111111111", "ws", "22222222-2222-4222-8222-222222222222", "lh", "server.example")
    parts = build_definition(source, name="FAR")["parts"]
    assert any(part["path"].startswith("entities/") for part in parts)

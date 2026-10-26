# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
import copy
import re
import sqlite3
import pytest
from reports.agent.data_agent import DEFAULT_LAKEHOUSE_FEWSHOTS, DEFAULT_AI_INSTRUCTIONS
from reports.agent.sdk_deploy import _select_tables, APPROVED_GOLD_TABLES
from reports.powerbi.schema import GOLD_TABLES, GOLD_TABLES_BY_NAME
from tests._far_surfaces import FORBIDDEN_TABLES


def test_every_approved_agent_table_exists_in_the_gold_schema():
    assert APPROVED_GOLD_TABLES <= GOLD_TABLES_BY_NAME.keys()


def test_sql_examples_resolve_only_static_architectural_allowlisted_columns():
    assert not FORBIDDEN_TABLES & APPROVED_GOLD_TABLES
    with sqlite3.connect(":memory:") as db:
        for table in GOLD_TABLES:
            columns = ", ".join(f'"{c.name}" TEXT' for c in table.columns)
            db.execute(f"CREATE TABLE {table.name} ({columns})")
        for example in DEFAULT_LAKEHOUSE_FEWSHOTS:
            query = example["query"]
            assert "SELECT *" not in query
            assert not {"gold_findings", "gold_run_summary"} & set(re.findall(r"gold_\w+", query))
            # Translate each TOP clause for SQLite, including the nested query.
            query = re.sub(r"SELECT TOP 1 (.*?) ORDER BY run_timestamp DESC\)",
                           r"SELECT \1 ORDER BY run_timestamp DESC LIMIT 1)", query)
            limit = re.search(r"TOP (\d+)", query)[1]
            query = re.sub(r"TOP \d+ ", "", query) + f" LIMIT {limit}"
            db.execute(query)
    assert "Unknown and missing_evidence" in DEFAULT_AI_INSTRUCTIONS


class Source:
    def __init__(self, extra=False):
        self.table = {"name": "gold_workspaces", "type": "lakehouse.table", "is_selected": True,
                      "children": [{"name": column.name, "type": "lakehouse.column"}
                                   for column in GOLD_TABLES_BY_NAME["gold_workspaces"].columns]}
        if extra:
            self.table["children"].append({"name": "expression_preview"})
    def get_configuration(self):
        return {"type": "lakehouse", "elements": [{"name": "dbo", "type": "lakehouse.schema",
                                                   "children": [copy.deepcopy(self.table)]}]}
    def select(self, *path):
        assert path == ("dbo", "gold_workspaces")
        self.table["is_selected"] = True
    def unselect(self, *path):
        self.table["is_selected"] = False


def test_stale_extra_columns_abort_agent_selection():
    with pytest.raises(ValueError, match="Stale or unverifiable columns"):
        _select_tables(Source(extra=True), set())
    assert _select_tables(Source(), set()) == {"gold_workspaces"}


def test_incomplete_column_enumeration_is_not_verified_grounding():
    source = Source()
    source.table["children"].pop()
    with pytest.raises(ValueError, match="unverifiable columns"):
        _select_tables(source, set())


def test_semantic_model_agent_view_omits_the_hidden_ontology_keys_and_nothing_else():
    from reports.powerbi.schema import KEY_COLUMNS
    columns = GOLD_TABLES_BY_NAME["gold_workspaces"].columns
    hidden = [c.name for c in columns if c.name in KEY_COLUMNS]
    assert hidden, "gold_workspaces should carry ontology key columns"

    class ModelSource(Source):
        def __init__(self, names):
            self.table = {"name": "gold_workspaces", "type": "semanticmodel.table", "is_selected": True,
                          "children": [{"name": n, "type": "semanticmodel.column"} for n in names]}
        def get_configuration(self):
            return {"type": "semanticmodel", "elements": [copy.deepcopy(self.table)]}
        def select(self, *path):
            assert path == ("gold_workspaces",)
            self.table["is_selected"] = True

    visible = [c.name for c in columns if c.name not in KEY_COLUMNS]
    assert _select_tables(ModelSource(visible), set()) == {"gold_workspaces"}
    with pytest.raises(ValueError, match="unverifiable columns"):
        _select_tables(ModelSource(visible[:-1]), set())
    # The Lakehouse shows every column, so a Lakehouse source still has to list the keys.
    lake = Source()
    lake.table["children"] = [c for c in lake.table["children"] if c["name"] not in KEY_COLUMNS]
    with pytest.raises(ValueError, match="unverifiable columns"):
        _select_tables(lake, set())


def test_semantic_model_accepts_only_current_generated_measures():
    from reports.powerbi.semantic_model import _measures
    class ModelSource(Source):
        def __init__(self):
            self.table = {"name": "gold_findings", "type": "semanticmodel.table", "is_selected": True,
                          "children": [{"name": c.name, "type": "semanticmodel.column"}
                                       for c in GOLD_TABLES_BY_NAME["gold_findings"].columns] + [
                              {"name": m["name"], "type": "semanticmodel.measure"} for m in _measures()]}
        def get_configuration(self):
            return {"type": "semanticmodel", "elements": [copy.deepcopy(self.table)]}
        def select(self, *path):
            assert path == ("gold_findings",)
            self.table["is_selected"] = True
    source = ModelSource()
    assert _select_tables(source, set()) == {"gold_findings"}
    source.table["children"].append({"name": "Model Size", "type": "semanticmodel.measure"})
    with pytest.raises(ValueError, match="unverifiable columns"):
        _select_tables(source, set())

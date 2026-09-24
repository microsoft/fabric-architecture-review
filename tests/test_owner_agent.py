# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Owner Agent source, RLS, SDK and instruction regression contracts."""
from __future__ import annotations

import ast
import base64
import copy
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest

from reports.agent.owner_agent import (
    OWNER_AGENT_EXAMPLES, OWNER_AGENT_TABLES, deploy_owner_agent, owner_instructions,
)
from reports.owner.model import build_bim

WORKSPACE = "11111111-1111-1111-1111-111111111111"
MODEL = "22222222-2222-2222-2222-222222222222"
AGENT = "33333333-3333-3333-3333-333333333333"
OTHER = "44444444-4444-4444-4444-444444444444"
NAME = "Owner Agent"
ROOT = Path(__file__).resolve().parents[1]


class Source:
    def __init__(self):
        self.config = {
            "type": "semantic_model",
            "elements": [
                {"name": name, "type": "semantic_model.table", "is_selected": True}
                for name in sorted(OWNER_AGENT_TABLES | {"owner_access"})
            ],
        }
        self.ignore_selection = False

    def get_configuration(self):
        return copy.deepcopy(self.config)

    def _element(self, path):
        elements = self.config["elements"]
        for name in path:
            element = next(e for e in elements if (e.get("name") or e.get("display_name")) == name)
            elements = element.get("children", [])
        return element

    def select(self, *path):
        if not self.ignore_selection:
            self._element(path)["is_selected"] = True

    def unselect(self, *path):
        self._element(path)["is_selected"] = False


class Agent:
    def __init__(self):
        self.sources = []
        self.instructions = ""
        self.published_instructions = ""
        self.added = []
        self.publish_calls = 0
        self.drop_instructions = False
        self.fail_publish = False

    def get_datasources(self):
        return self.sources

    def add_datasource(self, artifact_name_or_id, workspace_id_or_name=None, type=None):
        self.added.append((artifact_name_or_id, workspace_id_or_name, type))
        self.sources.append(Source())

    def update_configuration(self, instructions=None):
        if not self.drop_instructions:
            self.instructions = instructions

    def get_configuration(self):
        return SimpleNamespace(instructions=self.instructions)

    def publish(self, description=None, to_m365=False):
        assert to_m365 is False
        if self.fail_publish:
            raise RuntimeError("Synthetic publish failure")
        self.publish_calls += 1
        self.published_instructions = self.instructions


class Client:
    def __init__(self, agent):
        self.agent = agent
        self.model = build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", MODEL)
        self.items = []
        self.requests = []
        self.source_override = None
        self.extra_page = False
        self.parts_override = None

    def request(self, method, path):
        assert method == "POST"
        assert path == f"/workspaces/{WORKSPACE}/semanticModels/{MODEL}/getDefinition?format=TMSL"
        self.requests.append((method, path))
        return {"definition": {"parts": self.parts_override if self.parts_override is not None else [{
            "path": "model.bim", "payloadType": "InlineBase64",
            "payload": base64.b64encode(json.dumps(self.model).encode()).decode(),
        }]}}

    def complete(self, response):
        return response

    def list_items(self, workspace_id, item_type):
        assert workspace_id == WORKSPACE and item_type == "DataAgent"
        return self.items

    def get_json(self, path):
        if path.endswith("/settings"):
            return {"aiInstructions": self.agent.published_instructions}
        assert path.endswith("/datasources")
        sources = [{
            "id": OTHER, "type": "FabricItem",
            "itemReference": {"referenceType": "ById", "itemId": MODEL, "workspaceId": WORKSPACE},
        }] if self.agent.sources else []
        if self.source_override is not None:
            sources = self.source_override
        return {"value": sources, "continuationToken": "next" if self.extra_page else None}


@pytest.fixture
def environment(monkeypatch):
    agent = Agent()
    client = Client(agent)
    creates = []
    opens = []

    def create_data_agent(data_agent_name, workspace_id=None):
        assert data_agent_name == NAME and workspace_id == UUID(WORKSPACE)
        creates.append(data_agent_name)
        client.items = [{"id": AGENT, "displayName": NAME}]
        return agent

    def management(data_agent, workspace=None):
        assert data_agent == UUID(AGENT) and workspace == UUID(WORKSPACE)
        opens.append(data_agent)
        return agent

    for name in ("fabric", "fabric.dataagent", "fabric.dataagent.client",
                 "fabric.dataagent.client._fabric_data_agent_mgmt"):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["fabric.dataagent.client"].create_data_agent = create_data_agent
    sys.modules["fabric.dataagent.client._fabric_data_agent_mgmt"].FabricDataAgentManagement = management
    return SimpleNamespace(agent=agent, client=client, creates=creates, opens=opens)


def deploy(env):
    return deploy_owner_agent(client=env.client, workspace_id=WORKSPACE, model_id=MODEL,
                              agent_name=NAME, version="2026.09.2")


def test_deploy_and_compatible_rerun_preserve_model_and_only_select_owner_tables(environment):
    env = environment
    original = copy.deepcopy(env.client.model)
    assert deploy(env) == AGENT
    assert deploy(env) == AGENT
    assert env.creates == [NAME] and env.opens == [UUID(AGENT)]
    assert env.agent.added == [(UUID(MODEL), UUID(WORKSPACE), "semanticmodel")]
    assert env.client.model == original
    assert len(env.client.requests) == 4
    assert env.agent.publish_calls == 2
    selected = {e["name"] for e in env.agent.sources[0].config["elements"] if e["is_selected"]}
    assert selected == OWNER_AGENT_TABLES
    assert "owner_access" not in selected


def test_owner_agent_handles_grouped_tables_without_selecting_entitlements(environment):
    env = environment
    env.client.items = [{"id": AGENT, "displayName": NAME}]
    source = Source()
    tables = source.config["elements"]
    source.config["elements"] = [
        dict(display_name="Tables", type="table_grouping", children=tables),
    ]
    env.agent.sources = [source]
    assert deploy(env) == AGENT
    assert {e["name"] for e in tables if e["is_selected"]} == OWNER_AGENT_TABLES
    assert env.agent.publish_calls == 1


@pytest.mark.parametrize("mutation", [
    "rls", "extra_role", "extra_table", "partition", "measure", "relationship",
    "source_expression", "contract", "hidden_access", "missing_roles",
])
def test_invalid_owner_model_stops_before_agent_creation(environment, mutation):
    model = environment.client.model["model"]
    if mutation == "rls":
        model["roles"][0]["tablePermissions"][0]["filterExpression"] = "TRUE()"
    elif mutation == "extra_role":
        model["roles"].append({"name": "Unrestricted", "modelPermission": "read", "tablePermissions": []})
    elif mutation == "extra_table":
        extra = copy.deepcopy(model["tables"][0])
        extra["name"] = "raw_data"
        model["tables"].append(extra)
    elif mutation == "partition":
        model["tables"][0]["partitions"][0]["source"]["entityName"] = "gold_workspace_risk"
    elif mutation == "measure":
        model["tables"][0]["measures"][0]["expression"] = "1"
    elif mutation == "relationship":
        model["relationships"][0]["crossFilteringBehavior"] = "bothDirections"
    elif mutation == "source_expression":
        model["expressions"][0]["expression"] = 'Sql.Database("server", "database", [Query="SELECT 1"])'
    elif mutation == "contract":
        model["annotations"][0]["value"] = "1"
    elif mutation == "hidden_access":
        next(t for t in model["tables"] if t["name"] == "owner_access")["isHidden"] = False
    else:
        model["roles"] = []
    with pytest.raises(ValueError):
        deploy(environment)
    assert not environment.creates
    assert environment.agent.publish_calls == 0


@pytest.mark.parametrize("parts", [[], [{"path": "definition.pbism"}],
                                  [{"path": "model.bim", "payloadType": "Other"}]])
def test_unverifiable_definition_fails_closed(environment, parts):
    environment.client.parts_override = parts
    with pytest.raises(ValueError, match="verify owner model"):
        deploy(environment)
    assert not environment.creates


@pytest.mark.parametrize("mutation", ["extra", "type", "model", "workspace", "missing_reference", "page"])
def test_wrong_existing_source_is_not_modified(environment, mutation):
    env = environment
    env.client.items = [{"id": AGENT, "displayName": NAME}]
    env.agent.sources = [Source()]
    source = env.client.get_json("/datasources")["value"]
    if mutation == "extra":
        source *= 2
    elif mutation == "type":
        source[0]["type"] = "LakehouseTables"
    elif mutation == "model":
        source[0]["itemReference"]["itemId"] = OTHER
    elif mutation == "workspace":
        source[0]["itemReference"]["workspaceId"] = OTHER
    elif mutation == "missing_reference":
        source[0]["itemReference"] = {}
    else:
        env.client.extra_page = True
    env.client.source_override = source
    before = copy.deepcopy(env.agent.sources[0].config)
    with pytest.raises(ValueError):
        deploy(env)
    assert env.agent.sources[0].config == before
    assert not env.agent.instructions and not env.agent.added
    assert not env.creates and env.agent.publish_calls == 0


@pytest.mark.parametrize("response", [
    None, [], {}, {"value": None}, {"value": [None]}, {"value": ["unexpected"]},
    {"value": [], "error": {"message": "Unexpected service error"}},
    {"value": [], "@odata.nextLink": "https://example.invalid/next"},
    {"value": [{"type": "FabricItem", "itemReference": None}]},
    {"value": [{"type": "FabricItem", "itemReference": []}]},
    {"value": [{"type": "FabricItem", "itemReference": {
        "referenceType": "ById", "workspaceId": None, "itemId": MODEL,
    }}]},
    {"value": [{"type": "FabricItem", "itemReference": {
        "referenceType": "ById", "workspaceId": WORKSPACE, "itemId": 42,
    }}]},
    {"value": [{"type": "FabricItem", "itemReference": {
        "referenceType": "ById", "workspaceId": WORKSPACE, "itemId": "not-a-model-guid",
    }}]},
])
def test_malformed_public_source_response_fails_explicitly_before_configuration(environment, response):
    env = environment
    env.client.items = [{"id": AGENT, "displayName": NAME}]
    env.agent.sources = [Source()]
    env.client.get_json = lambda path: response
    before = copy.deepcopy(env.agent.sources[0].config)
    with pytest.raises(ValueError, match="Owner Agent"):
        deploy(env)
    assert env.agent.sources[0].config == before
    assert not env.agent.instructions and not env.agent.added
    assert env.agent.publish_calls == 0


@pytest.mark.parametrize("configuration", [None, [], "unexpected"])
def test_malformed_sdk_source_configuration_fails_explicitly(environment, configuration):
    env = environment
    env.client.items = [{"id": AGENT, "displayName": NAME}]
    env.agent.sources = [Source()]
    env.agent.sources[0].get_configuration = lambda: configuration
    with pytest.raises(ValueError, match="Owner Agent SDK"):
        deploy(env)
    assert not env.agent.instructions and not env.agent.added
    assert env.agent.publish_calls == 0


@pytest.mark.parametrize("configuration", [None, {}, []])
def test_unverifiable_sdk_instructions_stop_publication(environment, configuration):
    environment.agent.get_configuration = lambda: configuration
    with pytest.raises(ValueError, match="instructions did not persist"):
        deploy(environment)
    assert environment.agent.publish_calls == 0


@pytest.mark.parametrize("fault", ["source", "source_read", "settings_shape", "settings_value", "settings_read"])
def test_postpublication_verification_failure_warns_publication_was_not_rolled_back(environment, fault):
    env = environment
    get_json = env.client.get_json

    def response(path):
        if env.agent.publish_calls:
            if fault == "source" and path.endswith("/datasources"):
                return {"value": []}
            if fault == "source_read" and path.endswith("/datasources"):
                raise RuntimeError("Synthetic datasource read failure")
            if path.endswith("/settings"):
                if fault == "settings_shape":
                    return []
                if fault == "settings_value":
                    return {"aiInstructions": "Unexpected instructions"}
                if fault == "settings_read":
                    raise RuntimeError("Synthetic settings read failure")
        return get_json(path)

    env.client.get_json = response
    with pytest.raises(RuntimeError, match="publication succeeded.*not rolled back"):
        deploy(env)
    assert env.agent.publish_calls == 1
    assert env.agent.published_instructions == owner_instructions("2026.09.2")


@pytest.mark.parametrize("fault", ["schema", "selection", "instructions", "publish"])
def test_configuration_and_publication_failures_propagate(environment, fault):
    env = environment
    env.client.items = [{"id": AGENT, "displayName": NAME}]
    env.agent.sources = [Source()]
    if fault == "schema":
        env.agent.sources[0].config["elements"].pop()
    elif fault == "selection":
        env.agent.sources[0].ignore_selection = True
    elif fault == "instructions":
        env.agent.drop_instructions = True
    else:
        env.agent.fail_publish = True
    with pytest.raises((ValueError, RuntimeError)):
        deploy(env)
    assert env.agent.publish_calls == 0


def test_ambiguous_agent_name_is_rejected(environment):
    environment.client.items = [{"id": AGENT, "displayName": NAME}] * 2
    with pytest.raises(ValueError, match="Multiple owner Agents"):
        deploy(environment)
    assert not environment.creates and not environment.opens


def test_dax_examples_only_reference_actual_owner_columns():
    model = build_bim("Owner", "server", "db")["model"]
    schema = {table["name"]: {column["name"] for column in table["columns"]}
              for table in model["tables"]}
    assert len(OWNER_AGENT_EXAMPLES) == 7
    for query in OWNER_AGENT_EXAMPLES.values():
        assert query.count("EVALUATE") == 1
        assert "owner_access" not in query and "REMOVEFILTERS" not in query
        references = re.findall(r"'([^']+)'\[([^\]]+)\]", query)
        assert references
        for table, column in references:
            assert table in OWNER_AGENT_TABLES
            assert column in schema[table]
        if "'owner_findings'" in query or "'owner_details'" in query or "'owner_executions'" in query or "'owner_coverage'" in query:
            assert "'owner_reviews'[is_latest] = TRUE()" in query
            assert "TOPN(25" in query


def test_instructions_preserve_security_and_evidence_boundaries():
    instructions = owner_instructions("2026.09.2")
    assert len(instructions) <= 15000
    for phrase in ("not authorization controls", "Do not query owner_access", "PER\nWORKSPACE",
                   "deduplicate by execution_key", "duration_ms", "static investigation signals",
                   "not semantic-model few-shots", "not measured latency"):
        assert phrase in instructions


def test_owner_notebook_has_no_outputs_and_uses_pinned_sdk():
    notebook = json.loads((ROOT / "fabric/notebooks/08_owner_agent.ipynb").read_text())
    code = ""
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None and cell["outputs"] == []
            source = "".join(cell["source"])
            ast.parse("\n".join(line for line in source.splitlines() if not line.startswith("%")))
            code += source
    assert "fabric-data-agent-sdk==0.1.30a0" in code
    assert "deploy_owner_agent(" in code
    assert "deploy_agent(" not in code
    assert "getToken(\"pbi\")" in code
    assert "except Exception" not in code

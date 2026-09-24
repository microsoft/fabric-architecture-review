# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Owner reporting provisions a model-bound Agent notebook without publishing."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from orchestration.deployment import stamp_parameters


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"


def setup_sources() -> list[str]:
    notebook = json.loads((ROOT / "fabric" / "setup.ipynb").read_text(encoding="utf-8"))
    return ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]


def preflight(owner_enabled: object, name: object = "Owner Agent") -> None:
    source = next(source for source in setup_sources() if 'REPO_DIR = os.path.join(WORK_ROOT' in source)
    validation = source.split("import os, sys, shutil, subprocess")[0]
    exec(compile(validation, "owner-agent-preflight", "exec"), {
        "DEPLOY_WORKSPACE_OWNER_REPORT": owner_enabled,
        "OWNER_AGENT_NAME": name,
    })


def deployment_code():
    source = next(source for source in setup_sources() if 'owner_access_notebook_id = ""' in source)
    branch = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.If)
        and any(isinstance(child, ast.Name) and child.id == "_owner_agent_nb"
                for child in ast.walk(node))
    )
    return compile(ast.Module(body=[branch], type_ignores=[]), "owner-agent-setup", "exec")


def deployment_namespace(enabled: str, model_id: str = MODEL_ID) -> dict:
    return {
        "DEPLOY_WORKSPACE_OWNER_REPORT": enabled,
        "owner_model_id": model_id,
        "wid": WORKSPACE_ID,
        "NOTEBOOK_PREFIX": "FAR",
        "OWNER_AGENT_NAME": "Owner Agent",
        "GITHUB_REPO_URL": "https://example.invalid/far.git",
        "GITHUB_BRANCH": "main",
        "GITHUB_REF": "v2026.09.2",
        "stamp_parameters": stamp_parameters,
        "load_nb": Mock(),
        "upsert_notebook": Mock(return_value="notebook-id"),
    }


def test_owner_agent_follows_owner_reporting_default():
    parameters = next(source for source in setup_sources() if 'DEPLOY_WORKSPACE_OWNER_REPORT = "false"' in source)
    values = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(parameters).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"DEPLOY_WORKSPACE_OWNER_REPORT", "OWNER_AGENT_NAME"}
    }
    assert values == {
        "DEPLOY_WORKSPACE_OWNER_REPORT": "false",
        "OWNER_AGENT_NAME": "Fabric Arch Review - Workspace Owner Agent",
    }
    namespace = deployment_namespace("false", "")
    exec(deployment_code(), namespace)
    namespace["load_nb"].assert_not_called()
    namespace["upsert_notebook"].assert_not_called()


@pytest.mark.parametrize("enabled", ["", None, "yes", 1])
def test_invalid_owner_reporting_flag_fails_before_deployment(enabled):
    with pytest.raises(ValueError, match="DEPLOY_WORKSPACE_OWNER_REPORT must be true or false"):
        preflight(enabled)


@pytest.mark.parametrize("name", ["", "  ", None, 123])
def test_owner_agent_requires_a_name(name):
    with pytest.raises(ValueError, match="OWNER_AGENT_NAME"):
        preflight("true", name)


def test_owner_agent_preflight_accepts_disabled_or_valid_settings():
    preflight("false", None)
    preflight(" TRUE ", "Owner Agent")


def test_owner_agent_deployment_requires_successful_owner_model():
    namespace = deployment_namespace("true", "")
    with pytest.raises(ValueError, match="successfully deployed owner model"):
        exec(deployment_code(), namespace)
    namespace["load_nb"].assert_not_called()
    namespace["upsert_notebook"].assert_not_called()


def test_owner_agent_notebook_stamps_exact_owner_model_and_release(capsys):
    template = json.loads((ROOT / "fabric" / "notebooks" / "08_owner_agent.ipynb").read_text(encoding="utf-8"))
    namespace = deployment_namespace("true")
    namespace["load_nb"].return_value = template
    exec(deployment_code(), namespace)
    namespace["load_nb"].assert_called_once_with("fabric/notebooks/08_owner_agent.ipynb")
    args = namespace["upsert_notebook"].call_args.args
    assert args[:2] == (WORKSPACE_ID, "FAR_08_OwnerAgent")
    source = next(
        "".join(cell["source"]) for cell in args[2]["cells"]
        if "parameters" in cell.get("metadata", {}).get("tags", [])
    )
    values = {}
    exec(compile(source, "owner-agent-parameters", "exec"), values)
    assert values["OWNER_SEMANTIC_MODEL_ID"] == MODEL_ID
    assert values["WORKSPACE_ID"] == WORKSPACE_ID
    assert values["OWNER_AGENT_NAME"] == "Owner Agent"
    assert values["GITHUB_REF"] == "v2026.09.2"
    assert "has not published or shared" in capsys.readouterr().out

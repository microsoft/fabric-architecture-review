# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from reports.agent import sdk_deploy


@pytest.fixture
def agent(monkeypatch):
    handle = Mock()
    handle.get_configuration.return_value = SimpleNamespace(enable_preview_runtime=False)
    handle.get_datasources.return_value = [
        Mock(get_configuration=lambda: {"display_name": "model", "type": "semanticmodel"}),
        Mock(get_configuration=lambda: {"display_name": "lakehouse", "type": "lakehouse"}),
    ]
    create = Mock(return_value=handle)
    monkeypatch.setitem(sys.modules, "fabric.dataagent.client", SimpleNamespace(create_data_agent=create))
    monkeypatch.setattr(sdk_deploy, "_select_tables", lambda *_: set())
    monkeypatch.setattr(sdk_deploy, "_maybe_add_fewshots", lambda *_: None)
    return handle, create


@pytest.mark.parametrize("runtime,preview", [("standard", False), ("preview", True)])
def test_runtime_is_explicit_and_verified_before_publish(agent, runtime, preview):
    handle, _ = agent
    handle.get_configuration.return_value.enable_preview_runtime = preview
    sdk_deploy.deploy_agent(agent_name="test", model_name="model", lakehouse_name="lakehouse", runtime=runtime)
    assert handle.update_configuration.call_args.kwargs["enable_preview_runtime"] is preview
    calls = [call[0] for call in handle.mock_calls]
    assert calls.index("update_configuration") < calls.index("get_configuration") < calls.index("publish")
    handle.publish.assert_called_once()


def test_default_is_standard_and_draft_stays_unpublished(agent):
    handle, _ = agent
    sdk_deploy.deploy_agent(agent_name="test", model_name="model", lakehouse_name="lakehouse", publish=False)
    assert handle.update_configuration.call_args.kwargs["enable_preview_runtime"] is False
    handle.publish.assert_not_called()


def test_invalid_runtime_does_not_create_or_modify_an_agent(agent):
    handle, create = agent
    with pytest.raises(ValueError, match="runtime"):
        sdk_deploy.deploy_agent(agent_name="test", model_name="model", lakehouse_name="lakehouse", runtime="typo")
    create.assert_not_called()
    handle.update_configuration.assert_not_called()


def test_runtime_mismatch_aborts_before_sources_or_publication(agent):
    handle, _ = agent
    with pytest.raises(ValueError, match="runtime could not be verified"):
        sdk_deploy.deploy_agent(agent_name="test", model_name="model", lakehouse_name="lakehouse", runtime="preview")
    handle.add_datasource.assert_not_called()
    handle.publish.assert_not_called()


def test_runtime_update_error_is_not_suppressed(agent):
    handle, _ = agent
    handle.update_configuration.side_effect = RuntimeError("service rejected runtime")
    with pytest.raises(RuntimeError, match="service rejected"):
        sdk_deploy.deploy_agent(agent_name="test", model_name="model", lakehouse_name="lakehouse")
    handle.publish.assert_not_called()

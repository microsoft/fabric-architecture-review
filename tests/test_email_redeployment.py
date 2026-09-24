# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Preserve manual mail setup without preserving edits to generated bindings."""
import base64
import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from orchestration.deployment import build_parent_pipeline, deploy


WS = "11111111-1111-1111-1111-111111111111"
CHILD = "22222222-2222-2222-2222-222222222222"
PARENT = "33333333-3333-3333-3333-333333333333"
RUNNER = "44444444-4444-4444-4444-444444444444"
COMPLETION = "55555555-5555-5555-5555-555555555555"
CONNECTION = "66666666-6666-6666-6666-666666666666"
REPORT = f"https://app.powerbi.com/groups/{WS}/reports/{CHILD}"
NAME = "Targeted"
ROOT = Path(__file__).resolve().parents[1]
CHILD_DEFINITION = {"properties": {"parameters": {
    "WORKSPACE_IDS": {"type": "string", "defaultValue": WS},
    "CLIENT_NAME": {"type": "string", "defaultValue": "Customer"},
}}}


def parent():
    return build_parent_pipeline(
        CHILD_DEFINITION, workspace_id=WS, pipeline_id=CHILD, notebook_id=RUNNER,
        notification_notebook_id=COMPLETION,
    )


def email(pipeline):
    activities = pipeline["properties"]["activities"]
    branch = activities[2]["typeProperties"]["ifTrueActivities"]
    loop, = [activity for activity in activities + branch if activity["name"] == "Send owner emails"]
    return loop["typeProperties"]["activities"][0]


def nested_parent():
    pipeline = parent()
    properties = pipeline["properties"]
    loop = properties["activities"].pop()
    loop["dependsOn"] = [{"activity": "Prepare owner emails", "dependencyConditions": ["Succeeded"]}]
    loop["typeProperties"]["items"] = {
        "value": "@json(activity('Prepare owner emails').output.result.exitValue).messages",
        "type": "Expression",
    }
    properties["activities"][2]["typeProperties"]["ifTrueActivities"][1] = loop
    properties["variables"].pop("FAR_OWNER_EMAILS")
    return pipeline


def client_for(existing):
    client = Mock()
    client.list_items.return_value = [{"id": PARENT, "displayName": NAME}]
    client.pipeline_definition.side_effect = lambda workspace, item: copy.deepcopy(
        CHILD_DEFINITION if item == CHILD else existing
    )
    client.upsert_item.side_effect = [RUNNER, COMPLETION, PARENT]
    return client


def redeploy(client):
    with patch("orchestration.deployment.package_runtime", return_value=Path("runtime.zip")):
        deploy(
            client, workspace_id=WS, lakehouse_id=WS, child_pipeline_id=CHILD,
            repo_dir=ROOT, name=NAME, defaults={"FUAM_WORKSPACE_ID": WS},
        )
    return json.loads(base64.b64decode(client.upsert_item.call_args_list[2].args[3]["parts"][0]["payload"]))


@pytest.mark.parametrize("activity_state", ["Active", "Inactive", None])
@pytest.mark.parametrize("factory", [parent, nested_parent])
def test_preserves_manual_connection_sender_activation_and_report(activity_state, factory):
    existing = factory()
    old_email = email(existing)
    old_email["externalReferences"] = {"connection": CONNECTION}
    old_email["typeProperties"]["from"] = "sender@example.com"
    if activity_state is None:
        old_email.pop("state")
    else:
        old_email["state"] = activity_state
    # Inactive mail must not turn into success on a future enabled run.
    old_email["onInactiveMarkAs"] = "Succeeded"
    properties = existing["properties"]
    properties["parameters"]["FAR_REPORT_URL"]["defaultValue"] = REPORT
    properties["parameters"]["NOTIFICATIONS_ENABLED"]["defaultValue"] = "true"
    before = copy.deepcopy(existing)
    updated = redeploy(client_for(existing))
    assert existing == before
    assert email(updated)["externalReferences"] == {"connection": CONNECTION}
    assert email(updated)["typeProperties"]["from"] == "sender@example.com"
    assert email(updated)["state"] == (activity_state or "Active")
    assert email(updated)["onInactiveMarkAs"] == "Failed"
    assert updated["properties"]["parameters"]["FAR_REPORT_URL"]["defaultValue"] == REPORT
    assert updated["properties"]["parameters"]["NOTIFICATIONS_ENABLED"]["defaultValue"] == "false"
    assert updated["properties"]["activities"][1] == before["properties"]["activities"][1]
    assert updated["properties"]["activities"][0] == before["properties"]["activities"][0]
    assert updated["properties"]["activities"][3]["type"] == "ForEach"
    assert updated["properties"]["activities"][2]["typeProperties"]["ifTrueActivities"][1]["type"] == "SetVariable"
    assert updated["properties"]["variables"]["FAR_OWNER_EMAILS"] == {"type": "Array", "defaultValue": []}


@pytest.mark.parametrize("factory", [parent, nested_parent])
def test_unconfigured_parent_remains_inactive_and_connection_free(factory):
    updated = redeploy(client_for(factory()))
    assert email(updated)["state"] == "Inactive"
    assert "externalReferences" not in email(updated)


def test_redeploy_restores_generated_message_bindings_not_manual_recipients():
    existing = parent()
    email(existing)["typeProperties"].update(
        to="temporary@example.com", subject="Temporary subject", body="Temporary body",
    )
    updated = redeploy(client_for(existing))
    assert email(updated)["typeProperties"] == {
        "to": "@{item().to}", "subject": "@{item().subject}", "body": "@{item().body}",
    }


def test_migrates_old_unconfigured_fail_branch_without_requiring_07():
    existing = nested_parent()
    existing["properties"]["activities"][0]["typeProperties"]["parameters"]["EMAIL_CONFIGURED"] = {
        "value": "false", "type": "string",
    }
    existing["properties"]["activities"][2]["typeProperties"]["ifTrueActivities"][1] = {
        "name": "Email setup required", "type": "Fail",
        "dependsOn": [{"activity": "Prepare owner emails", "dependencyConditions": ["Succeeded"]}],
        "typeProperties": {"errorCode": "FarEmailNotConfigured", "message": "old guidance"},
    }
    updated = redeploy(client_for(existing))
    assert email(updated)["state"] == "Inactive"
    assert "EMAIL_CONFIGURED" not in json.dumps(updated)


def test_migrates_old_configured_branch_without_changing_far_bindings():
    existing = nested_parent()
    email(existing).update(externalReferences={"connection": CONNECTION})
    email(existing).pop("state")
    existing["properties"]["activities"][0]["typeProperties"]["parameters"]["EMAIL_CONFIGURED"] = {
        "value": "true", "type": "string",
    }
    existing["properties"]["parameters"]["FAR_REPORT_URL"]["defaultValue"] = REPORT
    updated = redeploy(client_for(existing))
    assert email(updated)["state"] == "Active"
    assert email(updated)["externalReferences"]["connection"] == CONNECTION
    selection = copy.deepcopy(existing["properties"]["activities"][0])
    selection["typeProperties"]["parameters"].pop("EMAIL_CONFIGURED")
    assert updated["properties"]["activities"][0] == selection
    assert updated["properties"]["activities"][1] == existing["properties"]["activities"][1]


@pytest.mark.parametrize("mutation", [
    lambda p: email(p).update(externalReferences={"connection": "not-a-guid"}),
    lambda p: email(p).update(state="Active"),
    lambda p: email(p).update(state="invalid"),
    lambda p: email(p)["typeProperties"].update({"from": "a@example.com;b@example.com"}),
    lambda p: p["properties"]["parameters"]["FAR_REPORT_URL"].update(defaultValue="https://other.example/report"),
    lambda p: p["properties"]["activities"][1]["typeProperties"]["ifTrueActivities"][0]["typeProperties"]["pipeline"].update(referenceName=PARENT),
    lambda p: p["properties"]["activities"][2]["typeProperties"]["ifTrueActivities"].pop(),
    lambda p: p["properties"]["activities"].pop(),
    lambda p: p["properties"]["activities"][2]["typeProperties"]["ifTrueActivities"].append(copy.deepcopy(p["properties"]["activities"][3])),
    lambda p: p["properties"]["activities"].append(copy.deepcopy(p["properties"]["activities"][3])),
    lambda p: p["properties"]["activities"].append(copy.deepcopy(p["properties"]["activities"][2])),
    lambda p: email(p).update(type="WebActivity"),
])
def test_invalid_existing_wiring_fails_before_any_deployment(mutation):
    existing = parent()
    mutation(existing)
    client = client_for(existing)
    with pytest.raises(ValueError):
        redeploy(client)
    client.upsert_item.assert_not_called()


def test_duplicate_parent_names_fail_before_any_deployment():
    client = client_for(parent())
    client.list_items.return_value *= 2
    with pytest.raises(ValueError, match="Multiple"):
        redeploy(client)
    client.upsert_item.assert_not_called()


def test_concurrent_parent_edit_is_not_overwritten():
    existing = parent()
    client = client_for(existing)
    changed = copy.deepcopy(existing)
    changed["properties"]["parameters"]["TOP_N"]["defaultValue"] = "2"
    client.pipeline_definition.side_effect = [CHILD_DEFINITION, existing, changed]
    with pytest.raises(RuntimeError, match="changed"):
        redeploy(client)
    assert all(call.args[2] == "Notebook" for call in client.upsert_item.call_args_list)

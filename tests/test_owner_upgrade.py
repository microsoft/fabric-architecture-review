# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from orchestration.fabric_api import definition_part
from reports.owner.deployment import _security_shape, deploy_owner_reporting, ensure_owner_model
from reports.owner.model import build_bim
from reports.owner.upgrade import _legacy_contract, _save_snapshot, migration_for, upgrade_owner_model

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = "00000000-0000-4000-8000-000000000001"
LAKEHOUSE = "00000000-0000-4000-8000-000000000002"
MODEL = "00000000-0000-4000-8000-000000000003"
REPORT = "00000000-0000-4000-8000-000000000004"
CONNECTION = "00000000-0000-4000-8000-000000000005"


def target():
    return build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)


class Editor:
    def __init__(self):
        self.bim = _legacy_contract(target())
        self.bim["model"]["roles"][0]["members"] = [{"memberName": "approved-reader"}]
        self.applies = 0
        self.closed = False
        self.after_apply = lambda: None

    def read(self):
        return deepcopy(self.bim)

    def apply(self, desired, migration):
        self.applies += 1
        members = self.bim["model"]["roles"][0]["members"]
        self.bim = deepcopy(desired)
        self.bim["model"]["roles"][0]["members"] = members
        self.after_apply()

    def close(self):
        self.closed = True


@pytest.fixture
def setup(monkeypatch):
    editor = Editor()
    client = Mock()
    connections = [{
        "id": CONNECTION, "connectivityType": "ShareableCloud",
        "connectionDetails": {"type": "SQL", "path": "example;database"},
    }]
    settings = {
        "id": CONNECTION, "connectivityType": "ShareableCloud",
        "credentialDetails": {"credentialType": "OAuth2", "singleSignOnType": "None"},
    }
    client.get_json.side_effect = lambda path: deepcopy(
        settings if path == f"/connections/{CONNECTION}" else {"value": connections}
    )
    snapshot = Mock(return_value="abfss://output/Files/_far/owner-model-backups/snapshot.json")
    monkeypatch.setattr("reports.owner.upgrade._save_snapshot", snapshot)
    factory = Mock(return_value=editor)
    kwargs = dict(
        client=client, workspace_id=WORKSPACE, lakehouse_id=LAKEHOUSE,
        model_id=MODEL, target=target(), editor_factory=factory,
    )
    return SimpleNamespace(
        editor=editor, client=client, settings=settings, connections=connections,
        snapshot=snapshot, factory=factory, kwargs=kwargs,
    )


def test_exact_legacy_and_marker_repair_are_recognized():
    current = target()
    assert migration_for(_legacy_contract(current), current) == "legacy-five-table-to-2"
    unmarked = deepcopy(current)
    unmarked["model"]["annotations"] = [
        a for a in unmarked["model"]["annotations"] if a["name"] != "OwnerContractVersion"
    ]
    assert migration_for(unmarked, current) == "restore-contract-2-marker"
    assert migration_for(current, current) == "none"


@pytest.mark.parametrize("mutate", [
    lambda m: m["model"]["roles"][0]["tablePermissions"][0].update(filterExpression="TRUE()"),
    lambda m: m["model"]["roles"][0].update(modelPermission="administrator"),
    lambda m: m["model"]["tables"][0]["columns"].pop(),
    lambda m: m["model"]["expressions"][0].update(expression='"other source"'),
    lambda m: m["model"]["relationships"][0].update(crossFilteringBehavior="bothDirections"),
    lambda m: m["model"]["tables"][0]["measures"][0].update(expression="123"),
    lambda m: m["model"]["annotations"].append({"name": "OwnerContractVersion", "value": "99"}),
])
def test_custom_or_unknown_contract_is_not_overwritten(setup, mutate):
    mutate(setup.editor.bim)
    with pytest.raises(ValueError, match="No approved owner migration"):
        upgrade_owner_model(**setup.kwargs)
    assert setup.editor.applies == 0 and setup.editor.closed
    setup.snapshot.assert_not_called()


def test_automatic_upgrade_preserves_members_connections_and_snapshots_previous_state(setup):
    before = deepcopy(setup.editor.bim)
    upgrade_owner_model(**setup.kwargs)
    assert setup.editor.applies == 1 and setup.editor.closed
    assert _security_shape(setup.editor.bim) == _security_shape(target())
    assert setup.editor.bim["model"]["roles"][0]["members"] == [{"memberName": "approved-reader"}]
    state = setup.snapshot.call_args.args[3]
    assert state["model"] == before
    assert state["model_id"] == MODEL
    assert state["connections"][0]["settings"]["credentialDetails"]["singleSignOnType"] == "None"
    setup.client.upsert_item.assert_not_called()
    setup.client.request.assert_not_called()


def test_repeat_setup_is_idempotent(setup):
    upgrade_owner_model(**setup.kwargs)
    upgrade_owner_model(**setup.kwargs)
    assert setup.editor.applies == 1 and setup.snapshot.call_count == 1


def test_backup_failure_prevents_model_update(setup):
    setup.snapshot.side_effect = OSError("storage unavailable")
    with pytest.raises(OSError, match="storage unavailable"):
        upgrade_owner_model(**setup.kwargs)
    assert setup.editor.applies == 0 and setup.editor.closed


def test_snapshot_excludes_unexpected_credential_payloads(setup):
    setup.settings["credentialDetails"]["credentials"] = {"password": "synthetic-test-only"}
    upgrade_owner_model(**setup.kwargs)
    assert "synthetic-test-only" not in json.dumps(setup.snapshot.call_args.args[3])


@pytest.mark.parametrize("change", ["members", "connection", "source"])
def test_concurrent_changes_rejected_before_model_update(setup, change):
    def snapshot(*args):
        if change == "members":
            setup.editor.bim["model"]["roles"][0]["members"].append({"memberName": "new-reader"})
        elif change == "connection":
            setup.settings["credentialDetails"]["singleSignOnType"] = "MicrosoftEntraID"
        else:
            setup.editor.bim["model"]["expressions"][0]["expression"] = '"other source"'
        return "snapshot.json"
    setup.snapshot.side_effect = snapshot
    with pytest.raises(ValueError, match="changed during deployment"):
        upgrade_owner_model(**setup.kwargs)
    assert setup.editor.applies == 0


@pytest.mark.parametrize("failure", ["save", "members", "rls", "connection"])
def test_failed_update_or_verification_does_not_claim_rollback(setup, failure):
    def fail():
        if failure == "save":
            raise RuntimeError("ambiguous server failure")
        if failure == "members":
            setup.editor.bim["model"]["roles"][0]["members"] = []
        if failure == "rls":
            setup.editor.bim["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = "TRUE()"
        if failure == "connection":
            setup.settings["credentialDetails"]["singleSignOnType"] = "MicrosoftEntraID"
    setup.editor.after_apply = fail
    with pytest.raises(RuntimeError, match="no automatic rollback.*Keep owner access restricted"):
        upgrade_owner_model(**setup.kwargs)
    setup.snapshot.assert_called_once()
    assert setup.editor.closed
    setup.client.upsert_item.assert_not_called()


@pytest.mark.parametrize("response", [
    {}, {"value": []}, {"value": [None]},
    {"value": [{"connectionDetails": {}, "connectivityType": "unknown"}]},
])
def test_unverifiable_connections_fail_closed(setup, response):
    setup.client.get_json.side_effect = None
    setup.client.get_json.return_value = response
    with pytest.raises(ValueError, match="connection"):
        upgrade_owner_model(**setup.kwargs)
    setup.snapshot.assert_not_called()
    assert setup.editor.applies == 0


def test_connection_pagination_loop_rejected(setup):
    setup.client.get_json.side_effect = None
    setup.client.get_json.return_value = {
        "value": [{"connectivityType": "Automatic", "connectionDetails": {}}],
        "continuationUri": f"/workspaces/{WORKSPACE}/items/{MODEL}/connections",
    }
    with pytest.raises(ValueError, match="pagination"):
        upgrade_owner_model(**setup.kwargs)
    assert setup.editor.applies == 0


def test_cleanup_does_not_mask_update_failure(setup):
    setup.editor.after_apply = Mock(side_effect=RuntimeError("save failed"))
    setup.editor.close = Mock(side_effect=OSError("close failed"))
    with pytest.raises(RuntimeError, match="no automatic rollback") as failure:
        upgrade_owner_model(**setup.kwargs)
    assert "cleanup also failed" in failure.value.__notes__[0]


@pytest.mark.parametrize("failure", [None, "mkdirs", "put", "head"])
def test_automatic_snapshot_is_saved_and_verified_in_output_lakehouse(monkeypatch, failure):
    fs = Mock()
    fs.mkdirs.return_value = failure != "mkdirs"
    fs.put.return_value = failure != "put"
    fs.head.side_effect = lambda *args: "truncated" if failure == "head" else fs.put.call_args.args[1]
    monkeypatch.setitem(__import__("sys").modules, "notebookutils", SimpleNamespace(fs=fs))
    if failure:
        with pytest.raises(RuntimeError, match="no model update performed"):
            _save_snapshot(WORKSPACE, LAKEHOUSE, MODEL, {"model": {}})
    else:
        path = _save_snapshot(WORKSPACE, LAKEHOUSE, MODEL, {"model": {}})
        assert path.startswith(f"abfss://{WORKSPACE}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE}/Files/")
        assert fs.put.call_args.args[2] is False
        assert json.loads(fs.put.call_args.args[1]) == {"model": {}}


def test_setup_wires_automatic_upgrade_and_preserves_model_and_report_ids(monkeypatch):
    client = Mock()
    client.list_items.side_effect = lambda workspace, kind: [{
        "id": MODEL if kind == "SemanticModel" else REPORT,
        "displayName": "Owner" if kind == "SemanticModel" else "Owner report",
    }]
    client.request.side_effect = lambda method, path: path
    pbir = {"datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={MODEL}"}}}
    legacy = _legacy_contract(target())
    client.complete.side_effect = lambda path: {"definition": {"parts": [
        definition_part("model.bim", legacy) if "/semanticModels/" in path
        else definition_part("definition.pbir", pbir),
    ]}}
    client.upsert_item.side_effect = lambda workspace, name, kind, definition: REPORT if kind == "Report" else "sync-id"
    migration = Mock()
    monkeypatch.setattr("reports.owner.upgrade.upgrade_owner_model", migration)
    monkeypatch.setattr("reports.owner.fabric_runtime.bootstrap_owner_tables", Mock())
    monkeypatch.setattr("reports.powerbi.deploy.wait_for_sql_endpoint", Mock(
        return_value=("example.datawarehouse.fabric.microsoft.com", LAKEHOUSE),
    ))
    result = deploy_owner_reporting(
        client, repo_dir=ROOT, workspace_id=WORKSPACE, lakehouse_id=LAKEHOUSE,
        lakehouse_name="FAR", notebook_prefix="FAR", model_name="Owner",
        report_name="Owner report", spark=Mock(),
    )
    assert result["model_id"] == MODEL and result["report_id"] == REPORT
    migration.assert_called_once()
    assert migration.call_args.kwargs["model_id"] == MODEL
    assert [c.args[2] for c in client.upsert_item.call_args_list] == ["Report", "Notebook"]


def test_unrecognized_definition_is_rejected_before_upgrade_callback():
    client = Mock()
    client.list_items.return_value = [{"id": MODEL, "displayName": "Owner"}]
    legacy = _legacy_contract(target())
    legacy["model"]["roles"] = []
    client.complete.return_value = {"definition": {"parts": [definition_part("model.bim", legacy)]}}
    upgrade = Mock()
    with pytest.raises(ValueError, match="No approved owner migration"):
        ensure_owner_model(client, WORKSPACE, "Owner", target(), upgrade=upgrade)
    upgrade.assert_not_called()


def test_setup_has_no_upgrade_parameters():
    notebook = json.loads((ROOT / "fabric" / "setup.ipynb").read_text(encoding="utf-8"))
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    assert "OWNER_MODEL_UPGRADE_" not in source
    assert "deploy_owner_reporting(" in source

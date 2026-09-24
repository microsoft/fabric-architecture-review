# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import copy
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from orchestration.fabric_api import FabricClient, definition_part
from orchestration.deployment import stamp_parameters
from reports.owner.deployment import _verify_report_binding, access_notebook, deploy_owner_reporting, ensure_owner_model
from reports.owner.fabric_runtime import materialize_owner_gold
from reports.owner.model import build_bim


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = "00000000-0000-4000-8000-000000000001"
LAKEHOUSE = "00000000-0000-4000-8000-000000000002"
MODEL = "00000000-0000-4000-8000-000000000003"


def model():
    return {"model": {
        "tables": [{"name": "owner_access", "columns": [{"name": "workspace_id", "dataType": "string"}],
                    "partitions": [{"mode": "directLake", "source": {"entityName": "owner_access"}}]}],
        "roles": [{"name": "WorkspaceOwner", "modelPermission": "read",
                   "tablePermissions": [{"name": "owner_access", "filterExpression": "FALSE()"}]}],
        "expressions": [],
    }}


def existing_client(bim):
    client = Mock()
    client.list_items.return_value = [{"displayName": "Owner", "id": MODEL}]
    client.complete.return_value = {"definition": {"parts": [definition_part("model.bim", bim)]}}
    return client


def test_new_model_created_without_role_members():
    client = Mock()
    client.list_items.return_value = []
    client.upsert_item.return_value = MODEL
    assert ensure_owner_model(client, WORKSPACE, "Owner", model()) == MODEL
    assert client.upsert_item.call_args.args[:3] == (WORKSPACE, "Owner", "SemanticModel")


def test_redeployment_preserves_manual_members_and_connections_without_model_write():
    bim = model()
    bim["model"]["roles"][0]["members"] = [{"memberName": "already-approved-user"}]
    client = existing_client(bim)
    assert ensure_owner_model(client, WORKSPACE, "Owner", model()) == MODEL
    client.upsert_item.assert_not_called()
    assert len(client.request.call_args_list) == 1
    assert "getDefinition" in client.request.call_args.args[1]


def test_actual_generated_owner_model_roundtrip_preserves_approved_members():
    expected = build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)
    existing = json.loads(json.dumps(expected))
    existing["model"]["roles"][0]["members"] = [{"memberName": "approved@example.com"}]
    for expression in existing["model"]["expressions"]:
        if isinstance(expression["expression"], str):
            expression["expression"] = expression["expression"].splitlines()
    client = existing_client(existing)
    assert ensure_owner_model(client, WORKSPACE, "Owner", expected) == MODEL
    client.upsert_item.assert_not_called()


def test_internal_contract_marker_is_required_and_compatible_model_is_reused():
    expected = build_bim("Owner", "host", LAKEHOUSE)
    existing = copy.deepcopy(expected)
    existing["model"]["annotations"] = [
        entry for entry in existing["model"]["annotations"] if entry["name"] != "OwnerContractVersion"
    ]
    client = existing_client(existing)
    with pytest.raises(ValueError, match="No model update performed"):
        ensure_owner_model(client, WORKSPACE, "Owner", expected)
    client.upsert_item.assert_not_called()
    expected["model"]["roles"][0]["members"] = [{"memberName": "approved-reader"}]
    client = existing_client(expected)
    for _ in range(2):
        assert ensure_owner_model(client, WORKSPACE, "Owner", expected) == MODEL
    client.upsert_item.assert_not_called()


@pytest.mark.parametrize("binding", [
    {"byConnection": {"connectionString": f"semanticmodelid={LAKEHOUSE}"}},
    {"byPath": {"path": "../Legacy.SemanticModel"}},
    None,
])
def test_existing_report_with_different_binding_is_never_rebound(binding):
    client = Mock()
    client.list_items.return_value = [{"displayName": "Owner", "id": MODEL}]
    client.complete.return_value = {"definition": {"parts": [
        definition_part("definition.pbir", {"version": "4.0", "datasetReference": binding}),
    ]}}
    with pytest.raises(ValueError, match="No report update performed"):
        _verify_report_binding(client, WORKSPACE, "Owner", MODEL)
    client.upsert_item.assert_not_called()


def saved_report_binding(shape):
    if shape == "minimal":
        return {"byConnection": {"connectionString": f"semanticmodelid={MODEL}"}}
    if shape == "expanded":
        return {"byPath": None, "byConnection": {
            "connectionString": (
                'Data Source="powerbi://api.powerbi.com/v1.0/myorg/Workspace; Shared";'
                'initial catalog="Owner model";access mode=readonly;integrated security=ClaimsToken;'
                f"semanticmodelid={MODEL}"
            ),
        }}
    if shape == "legacy":
        return {"byConnection": {
            "connectionString": (
                "Data Source=powerbi://api.powerbi.com/v1.0/myorg/Workspace;"
                "Initial Catalog=Owner model;Integrated Security=ClaimsToken"
            ),
            "pbiModelDatabaseName": MODEL,
            "connectionType": "pbiServiceXmlaStyleLive",
            "pbiModelVirtualServerName": "sobe_wowvirtualserver",
            "pbiServiceModelId": None,
            "name": "EntityDataSource",
        }}
    raise AssertionError(f"Unknown synthetic binding shape: {shape}")


@pytest.mark.parametrize("binding", [
    saved_report_binding("minimal"),
    saved_report_binding("expanded"),
    saved_report_binding("legacy"),
    {"byConnection": {"connectionString": None, "pbiModelDatabaseName": MODEL}},
    {"byConnection": {"pbiModelDatabaseName": MODEL}},
    {"byConnection": {"connectionString": f" ; SemanticModelId = '{MODEL}' ; "}},
    {"byConnection": {
        "connectionString": f'semanticmodelid="{MODEL}";',
        "pbiModelDatabaseName": MODEL,
        "name": "EntityDataSource",
    }},
    {"byConnection": {
        "connectionString": (
            f'Data Source="Workspace ""quoted;semanticmodelid={LAKEHOUSE}""";'
            f"semanticmodelid={MODEL};"
        ),
    }},
])
def test_equivalent_binding_updates_existing_report_in_place(binding):
    report_id = "00000000-0000-4000-8000-000000000004"
    client = FabricClient(lambda: "unused-synthetic-token")
    client.list_items = Mock(return_value=[{"displayName": "Owner", "id": report_id}])
    client.request = Mock()
    client.complete = Mock(side_effect=[
        {"definition": {"parts": [
            definition_part("definition.pbir", {"datasetReference": binding}),
        ]}},
        {},
    ])
    _verify_report_binding(client, WORKSPACE, "Owner", MODEL)
    definition = {"parts": [definition_part("definition.pbir", {
        "version": "4.0", "datasetReference": saved_report_binding("minimal"),
    })]}
    assert client.upsert_item(WORKSPACE, "Owner", "Report", definition) == report_id
    assert [call.args for call in client.request.call_args_list] == [
        ("POST", f"/workspaces/{WORKSPACE}/reports/{report_id}/getDefinition"),
        ("POST", f"/workspaces/{WORKSPACE}/items/{report_id}/updateDefinition"),
    ]
    assert client.request.call_args.kwargs["json_body"] == {"definition": definition}


@pytest.mark.parametrize("binding", [
    {"byConnection": {"connectionString": f"semanticmodelid={MODEL};semanticmodelid={LAKEHOUSE}"}},
    {"byConnection": {"connectionString": f"semanticmodelid={MODEL}", "pbiModelDatabaseName": LAKEHOUSE}},
    {"byConnection": {"connectionString": f'Data Source="Workspace;semanticmodelid={MODEL}"'}},
    {"byConnection": {"connectionString": f"semanticmodelid={MODEL};broken"}},
    {"byConnection": {"connectionString": f'semanticmodelid={MODEL};Data Source="unterminated'}},
    {"byConnection": {"connectionString": f"semanticmodelid={MODEL};", "pbiModelDatabaseName": "not-an-id"}},
    {"byConnection": {"connectionString": ["not-a-string"], "pbiModelDatabaseName": MODEL}},
    {"byConnection": {"connectionString": None}},
    {"byConnection": {"connectionString": f"semanticmodelid={MODEL}"}, "byPath": {"path": "../Other"}},
])
def test_existing_report_rejects_ambiguous_or_unverifiable_binding(binding):
    client = Mock()
    client.list_items.return_value = [{"displayName": "Owner", "id": MODEL}]
    client.complete.return_value = {"definition": {"parts": [
        definition_part("definition.pbir", {"version": "4.0", "datasetReference": binding}),
    ]}}
    with pytest.raises(ValueError, match="No report update performed"):
        _verify_report_binding(client, WORKSPACE, "Owner", MODEL)
    client.upsert_item.assert_not_called()


def test_report_binding_normalizes_guid_case_without_exposing_connection_text():
    model_id = "abcdefab-0000-4000-8000-000000000003"
    client = Mock()
    client.list_items.return_value = [{"displayName": "Owner", "id": MODEL}]

    def binding(connection):
        return {"definition": {"parts": [
            definition_part("definition.pbir", {"datasetReference": {"byConnection": connection}}),
        ]}}

    client.complete.return_value = binding({
        "connectionString": f"semanticmodelid={model_id.upper()}", "pbiModelDatabaseName": model_id,
    })
    _verify_report_binding(client, WORKSPACE, "Owner", model_id.upper())
    client.complete.return_value = binding({
        "connectionString": f"Password=synthetic-private-value;semanticmodelid={LAKEHOUSE}",
    })
    with pytest.raises(ValueError, match=f"bound to model {LAKEHOUSE}") as error:
        _verify_report_binding(client, WORKSPACE, "Owner", model_id)
    assert "synthetic-private-value" not in str(error.value)
    assert model_id in str(error.value)


def test_repeated_deployment_retains_model_bindings_and_uses_unversioned_sync_notebook(monkeypatch):
    import reports.owner.fabric_runtime
    import reports.powerbi.deploy
    from reports.owner.report import build_parts

    expected = build_bim("Owner", "host", LAKEHOUSE)
    expected["model"]["roles"][0]["members"] = [{"memberName": "approved-reader"}]
    pbir = json.loads(next(p["text"] for p in build_parts(MODEL) if p["path"] == "definition.pbir"))
    client = existing_client(expected)
    client.list_items.side_effect = lambda workspace, kind: [{
        "displayName": "Owner" if kind == "SemanticModel" else "Owner report", "id": MODEL,
    }]
    client.request.side_effect = lambda method, path: path
    client.complete.side_effect = lambda path: {"definition": {"parts": [
        definition_part("model.bim", expected) if "/semanticModels/" in path
        else definition_part("definition.pbir", pbir),
    ]}}
    client.upsert_item.return_value = MODEL
    monkeypatch.setattr(reports.owner.fabric_runtime, "bootstrap_owner_tables", Mock())
    monkeypatch.setattr(reports.powerbi.deploy, "wait_for_sql_endpoint", Mock(return_value=("host", LAKEHOUSE)))
    for _ in range(2):
        result = deploy_owner_reporting(
            client, repo_dir=ROOT, workspace_id=WORKSPACE, lakehouse_id=LAKEHOUSE,
            lakehouse_name="review_output", notebook_prefix="TestFAR",
            model_name="Owner", report_name="Owner report", spark=Mock(),
        )
        assert result == {"model_id": MODEL, "report_id": MODEL, "access_notebook_id": MODEL}
    assert [call.args[1:3] for call in client.upsert_item.call_args_list] == [
        ("Owner report", "Report"), ("TestFAR_07_OwnerAccessSync", "Notebook"),
    ] * 2


RELATIONSHIP_DEFAULTS = {
    "type": "singleColumn",
    "isActive": True,
    "fromCardinality": "many",
    "toCardinality": "one",
    "crossFilteringBehavior": "oneDirection",
    "securityFilteringBehavior": "oneDirection",
    "joinOnDateBehavior": "dateAndTime",
    "relyOnReferentialIntegrity": False,
}


def test_generated_owner_model_reuses_tom_sparse_relationships():
    expected = build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)
    existing = copy.deepcopy(expected)
    existing["model"]["roles"][0]["members"] = [{"memberName": "approved@example.com"}]
    # Matches the relationship serialization from Microsoft.AnalysisServices 19.114.12.
    for relationship in existing["model"]["relationships"]:
        for key in RELATIONSHIP_DEFAULTS:
            relationship.pop(key, None)
        assert set(relationship) == {"name", "fromTable", "fromColumn", "toTable", "toColumn"}
    client = existing_client(existing)
    assert ensure_owner_model(client, WORKSPACE, "Owner", expected) == MODEL
    client.upsert_item.assert_not_called()
    assert len(client.request.call_args_list) == 1
    assert "getDefinition" in client.request.call_args.args[1]


@pytest.mark.parametrize(("key", "default"), RELATIONSHIP_DEFAULTS.items())
@pytest.mark.parametrize("omit_from_existing", [True, False])
def test_explicit_and_omitted_relationship_defaults_are_equivalent(key, default, omit_from_existing):
    expected = build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)
    existing = copy.deepcopy(expected)
    explicit, omitted = (expected, existing) if omit_from_existing else (existing, expected)
    explicit["model"]["relationships"][0][key] = default
    omitted["model"]["relationships"][0].pop(key, None)
    client = existing_client(existing)
    assert ensure_owner_model(client, WORKSPACE, "Owner", expected) == MODEL
    client.upsert_item.assert_not_called()


@pytest.mark.parametrize(("key", "changed"), [
    ("type", "unexpectedType"),
    ("isActive", False),
    ("fromCardinality", "one"),
    ("toCardinality", "many"),
    ("crossFilteringBehavior", "bothDirections"),
    ("crossFilteringBehavior", "automatic"),
    ("securityFilteringBehavior", "bothDirections"),
    ("joinOnDateBehavior", "datePartOnly"),
    ("relyOnReferentialIntegrity", True),
    ("fromTable", "gold_findings"),
    ("toTable", "gold_findings"),
    ("fromColumn", "foreign_key"),
    ("toColumn", "foreign_key"),
    ("unknownSetting", True),
    *[(key, None) for key in RELATIONSHIP_DEFAULTS],
])
def test_real_relationship_changes_still_block_reuse(key, changed):
    expected = build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)
    existing = copy.deepcopy(expected)
    for relationship in existing["model"]["relationships"]:
        for default_key in RELATIONSHIP_DEFAULTS:
            relationship.pop(default_key, None)
    existing["model"]["relationships"][0][key] = changed
    client = existing_client(existing)
    with pytest.raises(ValueError, match="contract differs"):
        ensure_owner_model(client, WORKSPACE, "Owner", expected)
    client.upsert_item.assert_not_called()


@pytest.mark.parametrize("change", [
    "extra_role", "missing_rls", "foreign_table", "source", "extra_column", "fallback",
])
def test_security_or_schema_drift_blocks_redeployment(change):
    bim = copy.deepcopy(model())
    if change == "extra_role":
        bim["model"]["roles"].append({"name": "Bypass", "modelPermission": "read"})
    elif change == "missing_rls":
        bim["model"]["roles"][0]["tablePermissions"] = []
    elif change == "foreign_table":
        table = copy.deepcopy(bim["model"]["tables"][0])
        table["name"] = "gold_findings"
        bim["model"]["tables"].append(table)
    elif change == "source":
        bim["model"]["tables"][0]["partitions"][0]["source"]["entityName"] = "gold_findings"
    elif change == "extra_column":
        bim["model"]["tables"][0]["columns"].append({"name": "secret", "dataType": "string"})
    else:
        bim["model"]["directLakeBehavior"] = "automatic"
    client = existing_client(bim)
    with pytest.raises(ValueError, match="differs"):
        ensure_owner_model(client, WORKSPACE, "Owner", model())
    client.upsert_item.assert_not_called()


def test_duplicate_model_names_fail():
    client = existing_client(model())
    client.list_items.return_value *= 2
    with pytest.raises(ValueError, match="Multiple"):
        ensure_owner_model(client, WORKSPACE, "Owner", model())


@pytest.mark.parametrize(("change", "location"), [
    ("source", "model.tables[owner_access].partitions[0].source.entityName"),
    ("column", "model.tables[owner_access].columns[workspace_id].dataType"),
    ("role", "model.roles[WorkspaceOwner].tablePermissions[owner_access].filterExpression"),
    ("extra_role", "model.roles (item count)"),
])
def test_contract_error_identifies_mismatch_without_values_or_destructive_advice(change, location):
    expected = model()
    existing = copy.deepcopy(expected)
    private_value = "synthetic-private-definition"
    if change == "source":
        existing["model"]["tables"][0]["partitions"][0]["source"]["entityName"] = private_value
    elif change == "column":
        existing["model"]["tables"][0]["columns"][0]["dataType"] = private_value
    elif change == "role":
        existing["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = private_value
    else:
        existing["model"]["roles"].append({"name": private_value, "modelPermission": "read"})
    client = existing_client(existing)
    with pytest.raises(ValueError) as error:
        ensure_owner_model(client, WORKSPACE, "Owner", expected)
    message = str(error.value)
    assert f"First mismatch: {location}." in message
    assert private_value not in message
    assert "Do not delete Lakehouse tables" in message
    assert "OWNER_SEMANTIC_MODEL_NAME and OWNER_REPORT_NAME" in message
    client.upsert_item.assert_not_called()


def test_tmsl_expression_line_serialization_preserves_security_equivalence():
    expected = model()
    expected["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = (
        'VAR identity = USEROBJECTID() RETURN identity = "two  spaces"'
    )
    existing = copy.deepcopy(expected)
    existing["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = [
        "VAR identity = USEROBJECTID()", 'RETURN identity = "two  spaces"',
    ]
    assert ensure_owner_model(existing_client(existing), WORKSPACE, "Owner", expected) == MODEL
    existing["model"]["roles"][0]["tablePermissions"][0]["filterExpression"][1] = (
        'RETURN identity = "two spaces"'
    )
    with pytest.raises(ValueError, match="differs"):
        ensure_owner_model(existing_client(existing), WORKSPACE, "Owner", expected)


@pytest.mark.parametrize("change", [
    "missing_measure", "added_measure", "expression", "format", "dynamic_format",
])
def test_calculation_drift_blocks_report_redeployment_against_stale_model(change):
    expected = model()
    measures = expected["model"]["tables"][0]["measures"] = [{
        "name": "Scoped rows", "expression": "COUNTROWS(owner_access)", "formatString": "0",
    }]
    existing = copy.deepcopy(expected)
    existing_measures = existing["model"]["tables"][0]["measures"]
    if change == "missing_measure":
        existing_measures.clear()
    elif change == "added_measure":
        existing_measures.append({"name": "Old rows", "expression": "0"})
    elif change == "expression":
        existing_measures[0]["expression"] = "0"
    elif change == "format":
        existing_measures[0]["formatString"] = "0%"
    else:
        measures[0]["formatStringDefinition"] = {"expression": '"0"'}
        existing_measures[0]["formatStringDefinition"] = {"expression": '"0%"'}
    client = existing_client(existing)
    with pytest.raises(ValueError, match="calculation contract differs"):
        ensure_owner_model(client, WORKSPACE, "Owner", expected)
    client.upsert_item.assert_not_called()


def test_tmsl_measure_line_serialization_preserves_compatibility():
    expected = model()
    expected["model"]["tables"][0]["measures"] = [{
        "name": "Scoped rows", "expression": "VAR count = COUNTROWS(owner_access)\nRETURN count",
        "formatStringDefinition": {"expression": 'IF(TRUE(), "0", "0%")'},
    }]
    existing = copy.deepcopy(expected)
    measure = existing["model"]["tables"][0]["measures"][0]
    measure["expression"] = ["VAR count = COUNTROWS(owner_access)", "RETURN count"]
    measure["formatStringDefinition"]["expression"] = ['IF(TRUE(), "0", "0%")']
    client = existing_client(existing)
    assert ensure_owner_model(client, WORKSPACE, "Owner", expected) == MODEL
    client.upsert_item.assert_not_called()


def test_deployment_never_updates_report_when_existing_model_lacks_required_measure(monkeypatch):
    import reports.owner.fabric_runtime
    import reports.powerbi.deploy

    existing = build_bim("Owner", "example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)
    table = next(table for table in existing["model"]["tables"] if table.get("measures"))
    table["measures"].pop()
    client = existing_client(existing)
    monkeypatch.setattr(reports.owner.fabric_runtime, "bootstrap_owner_tables", Mock())
    monkeypatch.setattr(
        reports.powerbi.deploy, "wait_for_sql_endpoint",
        Mock(return_value=("example.datawarehouse.fabric.microsoft.com", LAKEHOUSE)),
    )
    with pytest.raises(ValueError, match="calculation contract differs"):
        deploy_owner_reporting(
            client, repo_dir=ROOT, workspace_id=WORKSPACE, lakehouse_id=LAKEHOUSE,
            lakehouse_name="review_output", notebook_prefix="TestFAR",
            model_name="Owner", report_name="Owner report", spark=Mock(),
        )
    client.upsert_item.assert_not_called()


def test_generated_notebook_is_clean_pinned_and_reads_all_reviewed_workspaces():
    notebook = access_notebook(ROOT, WORKSPACE, LAKEHOUSE, "output_lakehouse", MODEL)
    assert notebook["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"] == LAKEHOUSE
    source = ""
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert not cell["outputs"] and cell["execution_count"] is None
            text = "".join(cell["source"])
            compile(text, cell["id"], "exec")
            source += text
    assert "spark.table('owner_workspaces')" in source
    assert "synchronize_access(workspaces" in source
    assert "GITHUB" not in source and "TOP_N" not in source
    assert "NOTIFICATIONS_ENABLED" not in source
    assert "getToken('pbi')" in source


@pytest.mark.parametrize("unavailable", [False, True])
def test_generated_notebook_reports_404_warning_summary(unavailable, capsys):
    notebook = access_notebook(ROOT, WORKSPACE, LAKEHOUSE, "output_lakehouse", MODEL)
    source = next("".join(cell["source"]) for cell in notebook["cells"]
                  if cell["id"] == "owner-access-execute")
    tree = ast.parse(source)
    start = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "unavailable_workspaces"
                         for target in node.targets))
    def sync(workspaces, token, *, replace, refresh, on_unavailable):
        assert workspaces == [WORKSPACE]
        if unavailable:
            on_unavailable(WORKSPACE)
        return 0 if unavailable else 1
    namespace = {
        "workspaces": [WORKSPACE], "token": Mock(), "replace_access": Mock(),
        "refresh": Mock(), "synchronize_access": sync,
    }
    exec(compile(ast.Module(body=tree.body[start:], type_ignores=[]), "<sync-summary>", "exec"), namespace)
    output = capsys.readouterr().out
    assert "Report permissions were NOT changed." in output
    assert ("completed with warnings:" in output) is unavailable
    assert ("HTTP 404: access denied for 1 workspace(s): " + WORKSPACE in output) is unavailable


def test_disabled_owner_feature_has_no_spark_import_or_data_access():
    spark = Mock()
    spark.catalog.tableExists.return_value = False
    assert materialize_owner_gold(spark, {}) is False
    spark.table.assert_not_called()


def test_setup_is_opt_in_and_not_in_06():
    setup = json.loads((ROOT / "fabric" / "setup.ipynb").read_text(encoding="utf-8"))
    source = "\n".join("".join(cell["source"]) for cell in setup["cells"] if cell["cell_type"] == "code")
    assert 'DEPLOY_WORKSPACE_OWNER_REPORT = "false"' in source
    assert "deploy_owner_reporting(" in source
    assert "OWNER_SEMANTIC_MODEL_NAME == SEMANTIC_MODEL_NAME" in source
    for cell in setup["cells"]:
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), cell["id"], "exec")
    gold = json.loads((ROOT / "fabric" / "notebooks" / "04_gold.ipynb").read_text(encoding="utf-8"))
    assert any("materialize_owner_gold(spark, tables)" in "".join(cell["source"]) for cell in gold["cells"])


def owner_setup_source(filename="setup.ipynb"):
    path = ROOT / "fabric" / filename
    if not path.exists():
        pytest.skip("Private disposable setup is not published.")
    setup = json.loads(path.read_text(encoding="utf-8"))
    source = next("".join(cell["source"]) for cell in setup["cells"]
                  if cell["cell_type"] == "code" and "deploy_owner_reporting(" in "".join(cell["source"]))
    return source[source.index('owner_access_notebook_id = ""'):]


@pytest.mark.parametrize("enabled", ["true", " TRUE ", "false", "invalid"])
@pytest.mark.parametrize("filename", ["setup.ipynb", "setup_private_test_DISPOSABLE.ipynb"])
def test_setup_owner_deployment_is_independent_and_passes_current_context(monkeypatch, enabled, filename):
    import orchestration.fabric_api
    import reports.owner.deployment

    source = owner_setup_source(filename)
    deploy = Mock(return_value={"model_id": MODEL, "report_id": MODEL, "access_notebook_id": LAKEHOUSE})
    upsert = Mock(return_value=MODEL)
    client = Mock()
    spark = Mock()
    sql_module = ModuleType("pyspark.sql")
    sql_module.SparkSession = SimpleNamespace(builder=SimpleNamespace(getOrCreate=lambda: spark))
    monkeypatch.setitem(sys.modules, "pyspark", ModuleType("pyspark"))
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql_module)
    monkeypatch.setattr(orchestration.fabric_api, "FabricClient", client)
    monkeypatch.setattr(reports.owner.deployment, "deploy_owner_reporting", deploy)
    get_token = Mock()
    context = {
        "DEPLOY_WORKSPACE_OWNER_REPORT": enabled, "DEPLOY_GOLD_REPORT": "false",
        "OWNER_AGENT_NAME": "Owner Agent",
        "OWNER_SEMANTIC_MODEL_NAME": "Owner model", "OWNER_REPORT_NAME": "Owner report",
        "SEMANTIC_MODEL_NAME": "Governance model", "REPORT_NAME": "Governance report",
        "REPO_DIR": str(ROOT), "wid": WORKSPACE, "lhid": LAKEHOUSE,
        "LAKEHOUSE_NAME": "review_output", "NOTEBOOK_PREFIX": "TestFAR",
        "notebookutils": SimpleNamespace(credentials=SimpleNamespace(getToken=get_token)),
        "pid": WORKSPACE, "pipeline_id": WORKSPACE, "PIPELINE_NAME": "Test FAR",
        "GITHUB_REPO_URL": "https://github.com/example/synthetic", "GITHUB_BRANCH": "main",
        "GITHUB_REF": "", "stamp_parameters": stamp_parameters,
        "load_nb": lambda relative: json.loads((ROOT / relative).read_text(encoding="utf-8")),
        "upsert_notebook": upsert,
    }
    if enabled == "invalid":
        with pytest.raises(ValueError, match="must be true or false"):
            exec(compile(source, "owner-setup", "exec"), context)
        deploy.assert_not_called()
        upsert.assert_not_called()
        return
    elif enabled.strip().lower() == "false":
        exec(compile(source, "owner-setup", "exec"), context)
        client.assert_not_called()
        deploy.assert_not_called()
    else:
        exec(compile(source, "owner-setup", "exec"), context)
        deploy.assert_called_once_with(
            client.return_value, repo_dir=ROOT, workspace_id=WORKSPACE, lakehouse_id=LAKEHOUSE,
            lakehouse_name="review_output", notebook_prefix="TestFAR",
            model_name="Owner model", report_name="Owner report", spark=spark,
        )
        assert context["owner_artifacts"] == deploy.return_value
        client.call_args.args[0]()
        get_token.assert_called_once_with("pbi")
        context["OWNER_SEMANTIC_MODEL_NAME"] = context["SEMANTIC_MODEL_NAME"]
        with pytest.raises(ValueError, match="distinct from governance"):
            exec(compile(source, "owner-setup", "exec"), context)
        assert deploy.call_count == 1
    assert upsert.call_count == (2 if enabled.strip().lower() == "true" else 1)
    if enabled.strip().lower() == "true":
        first = upsert.call_args_list[0].args
        assert first[:2] == (WORKSPACE, "TestFAR_08_OwnerAgent")
        assert any(f"OWNER_SEMANTIC_MODEL_ID = {json.dumps(MODEL)}\n" in cell["source"]
                   for cell in first[2]["cells"] if cell["cell_type"] == "code")
    assert upsert.call_args.args[:2] == (WORKSPACE, "TestFAR_06_TargetedReviewSetup")
    notebook = upsert.call_args.args[2]
    expected = LAKEHOUSE if enabled.strip().lower() == "true" else ""
    assert any(f"OWNER_ACCESS_NOTEBOOK_ID = {json.dumps(expected)}\n" in cell["source"]
               for cell in notebook["cells"] if cell["cell_type"] == "code")


@pytest.mark.parametrize("path", sorted((ROOT / "reports" / "owner").glob("*.py")), ids=lambda path: path.name)
def test_owner_sources_have_notices_and_valid_syntax(path):
    source = path.read_text(encoding="utf-8")
    header = "\n".join(source.splitlines()[:8]).casefold()
    assert "copyright (c) microsoft corporation" in header
    assert "licensed under the mit license" in header
    compile(source, str(path), "exec")

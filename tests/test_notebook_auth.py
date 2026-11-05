# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""SP/user regressions without live credentials or Fabric services."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from collectors import auth, fuam_sql
from orchestration import config_deployment, runtime, sources
from orchestration.notebook_auth import (
    FABRIC_SCOPE, IDENTITY_KEYS, POWERBI_SCOPE, SQL_SCOPE,
    NotebookTokenProvider, notebook_token_provider,
    ensure_notebook_dependencies,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
WS = "33333333-3333-3333-3333-333333333333"
LH = "44444444-4444-4444-4444-444444444444"
IDENTITY = {
    "tenant_id": TENANT, "service_principal_client_id": CLIENT,
    "key_vault": "synthetic-far-kv", "secret_name": "synthetic-client-secret",
}


@pytest.fixture
def notebook(monkeypatch):
    monkeypatch.setattr(subprocess, "run", Mock())
    values = dict(IDENTITY)
    library = SimpleNamespace(getVariable=lambda name: values[name.removeprefix("identity__")])
    credentials = Mock()
    credentials.getSecret.return_value = "synthetic-never-log-secret"
    credentials.getToken.side_effect = lambda audience: "user-" + audience
    utils = SimpleNamespace(
        credentials=credentials,
        variableLibrary=SimpleNamespace(getLibrary=Mock(return_value=library)),
    )
    application = Mock()
    application.acquire_token_for_client.side_effect = lambda *, scopes: {"access_token": "sp-" + scopes[0]}
    factory = Mock(return_value=application)
    monkeypatch.setitem(__import__("sys").modules, "msal", SimpleNamespace(ConfidentialClientApplication=factory))
    monkeypatch.setitem(__import__("sys").modules, "notebookutils", utils)
    return utils, values, factory, application


@pytest.mark.parametrize("sql", [False, True])
def test_dependency_bootstrap_uses_current_interpreter_without_kernel_restart(monkeypatch, sql):
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    ensure_notebook_dependencies(sql=sql)
    expected = ["msal>=1.23,<2"] + (["mssql-python>=1.15,<2"] if sql else [])
    run.assert_called_once_with(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *expected],
        check=True, timeout=300,
    )


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, ["pip"]),
    subprocess.TimeoutExpired(["pip"], 300),
])
def test_failed_dependency_install_stops_before_credentials(notebook, monkeypatch, failure):
    utils, _, factory, _ = notebook
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(RuntimeError, match="dependency provisioning failed"):
        notebook_token_provider(utils, "FAR_Environment", sql=True)
    utils.credentials.getSecret.assert_not_called()
    utils.credentials.getToken.assert_not_called()
    factory.assert_not_called()


def test_sp_uses_msal_for_each_audience_and_never_notebook_token(notebook, capsys):
    utils, _, factory, application = notebook
    provider = notebook_token_provider(utils, "Custom_Environment")
    for scope in (FABRIC_SCOPE, POWERBI_SCOPE, SQL_SCOPE, POWERBI_SCOPE):
        assert provider.get_token(scope) == "sp-" + scope
    utils.variableLibrary.getLibrary.assert_called_once_with("Custom_Environment")
    utils.credentials.getSecret.assert_called_once_with(
        "https://synthetic-far-kv.vault.azure.net/", "synthetic-client-secret",
    )
    factory.assert_called_once_with(
        CLIENT, authority=f"https://login.microsoftonline.com/{TENANT}",
        client_credential="synthetic-never-log-secret", timeout=60,
    )
    assert application.acquire_token_for_client.call_args_list == [
        call(scopes=[scope]) for scope in (FABRIC_SCOPE, POWERBI_SCOPE, SQL_SCOPE, POWERBI_SCOPE)
    ]
    utils.credentials.getToken.assert_not_called()
    output = capsys.readouterr().out
    assert output.rstrip().endswith("FAR startup: authentication provider ready")
    for value in (*IDENTITY.values(), "synthetic-never-log-secret", "sp-" + POWERBI_SCOPE):
        assert value not in output


@pytest.mark.parametrize("stage", ["library", *IDENTITY_KEYS, "vault", "msal"])
def test_auth_startup_markers_locate_failure_without_logging_values(notebook, stage, capsys):
    utils, _, factory, _ = notebook
    failure = RuntimeError("synthetic-never-log-secret")
    if stage == "library":
        utils.variableLibrary.getLibrary.side_effect = failure
        expected = "loading Environment library"
    elif stage in IDENTITY_KEYS:
        library = utils.variableLibrary.getLibrary.return_value
        read = library.getVariable

        def fail_selected_variable(name):
            if name == f"identity__{stage}":
                raise failure
            return read(name)

        library.getVariable = fail_selected_variable
        expected = f"reading Environment setting identity__{stage}"
    elif stage == "vault":
        utils.credentials.getSecret.side_effect = failure
        expected = "reading configured Key Vault secret"
    else:
        factory.side_effect = failure
        expected = "initializing MSAL client"
    with pytest.raises(RuntimeError) as exc:
        notebook_token_provider(utils, "Custom_Environment")
    assert exc.value is failure
    output = capsys.readouterr().out
    assert output.rstrip().endswith("FAR startup: " + expected)
    for value in (*IDENTITY.values(), "synthetic-never-log-secret"):
        assert value not in output
    assert "authentication provider ready" not in output
    utils.credentials.getToken.assert_not_called()


@pytest.mark.parametrize("result", [
    {"error": "invalid_client", "error_description": "synthetic-never-log-secret"},
    {"error": "synthetic-never-log-secret"},
    {"access_token": ""}, {"access_token": None}, {},
])
def test_sp_auth_failure_is_explicit_redacted_and_never_falls_back(notebook, result):
    utils, _, _, application = notebook
    provider = notebook_token_provider(utils, "FAR_Environment")
    application.acquire_token_for_client.side_effect = None
    application.acquire_token_for_client.return_value = result
    with pytest.raises(RuntimeError, match="no identity fallback") as exc:
        provider.get_token(POWERBI_SCOPE)
    assert "synthetic-never-log-secret" not in str(exc.value)
    utils.credentials.getToken.assert_not_called()


def test_token_refresh_failure_does_not_reuse_prior_token(notebook):
    utils, _, _, application = notebook
    provider = notebook_token_provider(utils, "FAR_Environment")
    application.acquire_token_for_client.side_effect = [
        {"access_token": "first"}, {"error": "invalid_client"},
    ]
    assert provider.get_token() == "first"
    with pytest.raises(RuntimeError, match="invalid_client"):
        provider.get_token()
    utils.credentials.getToken.assert_not_called()


def test_vault_failure_and_empty_secret_do_not_construct_msal_or_fallback(notebook):
    utils, _, factory, _ = notebook
    utils.credentials.getSecret.side_effect = RuntimeError("ForbiddenByFirewall")
    with pytest.raises(RuntimeError, match="ForbiddenByFirewall"):
        notebook_token_provider(utils, "FAR_Environment")
    utils.credentials.getSecret.side_effect = None
    utils.credentials.getSecret.return_value = ""
    with pytest.raises(ValueError, match="empty"):
        notebook_token_provider(utils, "FAR_Environment")
    factory.assert_not_called()
    utils.credentials.getToken.assert_not_called()


def test_library_failure_is_not_an_identity_fallback(notebook):
    utils, values, factory, _ = notebook
    values["key_vault"] = ""
    with pytest.raises(ValueError, match="together"):
        notebook_token_provider(utils, "FAR_Environment")
    utils.variableLibrary.getLibrary.side_effect = RuntimeError("Library inaccessible")
    with pytest.raises(RuntimeError, match="Library inaccessible"):
        notebook_token_provider(utils, "FAR_Environment")
    factory.assert_not_called()
    utils.credentials.getToken.assert_not_called()


def test_user_mode_and_repeated_runs_do_not_reuse_sp_credentials(notebook):
    utils, values, factory, _ = notebook
    first = notebook_token_provider(utils, "Custom_Environment")
    assert first.is_service_principal
    values.update(dict.fromkeys(IDENTITY_KEYS, ""))
    user = notebook_token_provider(utils, "Custom_Environment")
    assert not user.is_service_principal
    for scope, audience in (
        (FABRIC_SCOPE, "pbi"), (POWERBI_SCOPE, "pbi"),
        (SQL_SCOPE, "https://database.windows.net/"),
        ("https://storage.azure.com/.default", "storage"),
        ("https://vault.azure.net/.default", "keyvault"),
        ("https://management.azure.com/.default", "azuremanagement"),
    ):
        assert user.get_token(scope) == "user-" + audience
    assert factory.call_count == 1
    with pytest.raises(ValueError, match="Unsupported"):
        user.get_token("https://unrecognized.example/.default")


def test_collect_notebook_sets_shared_provider_without_secret_environment(notebook, monkeypatch):
    utils, _, _, _ = notebook
    nb = json.loads((ROOT / "fabric/notebooks/01_collect.ipynb").read_text())
    code = next("".join(c["source"]) for c in nb["cells"] if c.get("id") == "cell07")
    monkeypatch.setattr(auth, "_default_provider", Mock())
    monkeypatch.setattr(auth, "_grant_provider", None)
    for name in ("CLIENT_ID", "CLIENT_SECRET", "TENANT_ID"):
        monkeypatch.setenv(name, "stale")
    monkeypatch.setattr("orchestration.environment.fabric_access_grant", lambda *_: {"enabled": False, "role": "auto"})
    monkeypatch.setattr("orchestration.environment.fabric_model_security", lambda *_: {"role_detail": False})
    for name in ("ACCESS_GRANT_ENABLED", "ACCESS_GRANT_ROLE", "ACCESS_GRANT_SCOPE", "MODEL_SECURITY_ROLE_DETAIL"):
        monkeypatch.delenv(name, raising=False)
    exec(compile(code, "<collect-auth>", "exec"), {"os": os, "FAR_ENVIRONMENT_LIBRARY": "Custom_Environment"})
    assert isinstance(auth._default_provider, NotebookTokenProvider)
    assert auth._default_provider.get_token(POWERBI_SCOPE) == "sp-" + POWERBI_SCOPE
    assert "CLIENT_SECRET" not in os.environ
    assert "CLIENT_ID" not in os.environ
    assert os.environ["TENANT_ID"] == TENANT
    utils.credentials.getToken.assert_not_called()
    assert isinstance(auth._grant_provider, NotebookTokenProvider)
    assert auth._grant_provider.is_service_principal is False


def test_selection_and_notifications_bind_the_environment_and_correct_audience(notebook, monkeypatch, tmp_path):
    from orchestration.deployment import DEFAULTS

    utils, _, _, _ = notebook
    install = Mock()
    monkeypatch.setattr(subprocess, "run", install)
    config = {**DEFAULTS, "PARENT_RUN_ID": WS, "FAR_ENVIRONMENT_LIBRARY": "Custom_Environment"}
    read = Mock(return_value=("fuam", []))
    monkeypatch.setattr(sources, "read_source", read)
    monkeypatch.setattr(runtime, "select_workspaces", lambda options, parent, **kw: kw["read_source"](options))
    runtime.run_selection(config)
    assert install.call_args.args[0][-1] == "mssql-python>=1.15,<2"
    selected_client = read.call_args.args[1]
    assert selected_client.token() == "sp-" + FABRIC_SCOPE
    assert read.call_args.kwargs["token_provider"].get_token(POWERBI_SCOPE) == "sp-" + POWERBI_SCOPE
    monkeypatch.setattr(runtime, "prepare_notifications", lambda options, parent, child, **kw: kw["prepare"](options, {}))
    prepare = Mock(return_value=[])
    monkeypatch.setattr(runtime, "prepare_owner_emails", prepare)
    runtime.run_notifications({
        **config, "CHILD_RUN_ID": LH, "NOTIFICATIONS_ENABLED": "true",
        "FAR_REPORT_URL": "https://app.powerbi.com/groups/test/reports/test",
    })
    assert prepare.call_args.args[2].token() == "sp-" + POWERBI_SCOPE
    assert install.call_args.args[0][-1] == "msal>=1.23,<2"
    utils.credentials.getToken.assert_not_called()


@pytest.mark.parametrize("sql_failure", [False, True])
def test_sp_selection_reads_sql_with_sp_and_inventory_with_powerbi_token(notebook, monkeypatch, sql_failure, capsys):
    from orchestration.deployment import DEFAULTS

    utils, _, _, _ = notebook
    provider = notebook_token_provider(utils, "FAR_Environment")
    client = Mock()
    client.get_json.return_value = {"id": LH, "displayName": "FUAM"}
    monkeypatch.setattr(config_deployment, "endpoint", Mock(return_value=("server", "FUAM")))
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connect = Mock(return_value=connection)
    monkeypatch.setattr(config_deployment, "sql_connection", connect)
    rows = Mock(side_effect=[
        [{"capacity_id": CLIENT, "capacity_name": "Capacity"}],
        RuntimeError("SQL access denied") if sql_failure else
        [{"workspace_id": WS, "metric_value": 1, "invalid_values": 0}],
    ])
    monkeypatch.setattr(config_deployment, "sql_rows", rows)
    spark_query = Mock(side_effect=AssertionError("SP must not use the user-only Spark SQL connector"))
    monkeypatch.setattr(sources, "query_rows", spark_query)

    def inventory(url, headers):
        assert headers()["Authorization"] == "Bearer sp-" + POWERBI_SCOPE
        return [{"id": WS}]

    monkeypatch.setattr(sources, "collect_workspace_groups", inventory)
    config = {
        **DEFAULTS, "FUAM_WORKSPACE_ID": WS, "FUAM_LAKEHOUSE_ID": LH,
        "CAPACITY_ID_OR_NAME": "Capacity",
    }
    if sql_failure:
        with pytest.raises(RuntimeError, match="SQL access denied"):
            sources.read_source(config, client, token_provider=provider)
    else:
        result = sources.read_source(config, client, token_provider=provider)
        assert result[1][0]["workspace_id"] == WS
    assert connect.call_args_list == [call("server", "FUAM", "sp-" + SQL_SCOPE)] * 2
    assert all(c.kwargs == {"max_rows": sources.MAX_WORKSPACES} for c in rows.call_args_list)
    assert connection.__exit__.call_count == 2
    spark_query.assert_not_called()
    utils.credentials.getToken.assert_not_called()
    output = capsys.readouterr().out
    last_stage = "reading FUAM SQL rows" if sql_failure else "matching current workspace inventory"
    assert output.rstrip().endswith("FAR startup: " + last_stage)
    assert "synthetic-never-log-secret" not in output
    assert "sp-" + SQL_SCOPE not in output


def test_user_selection_keeps_spark_sql_and_notebook_admin_token(notebook, monkeypatch):
    install = Mock()
    monkeypatch.setattr(subprocess, "run", install)
    from orchestration.deployment import DEFAULTS

    utils, values, factory, _ = notebook
    values.update(dict.fromkeys(IDENTITY_KEYS, ""))
    provider = notebook_token_provider(utils, "Custom_Environment")
    client = Mock()
    lakehouse = {"id": LH, "displayName": "FUAM"}
    client.get_json.return_value = lakehouse
    query = Mock(return_value=[{"workspace_id": WS, "metric_value": 1, "invalid_values": 0}])
    monkeypatch.setattr(sources, "query_rows", query)
    sql_connect = Mock(side_effect=AssertionError("User selection must retain Spark SQL"))
    monkeypatch.setattr(config_deployment, "sql_connection", sql_connect)

    def inventory(url, headers):
        assert headers()["Authorization"] == "Bearer user-pbi"
        return [{"id": WS}]

    monkeypatch.setattr(sources, "collect_workspace_groups", inventory)
    result = sources.read_source({
        **DEFAULTS, "FUAM_WORKSPACE_ID": WS, "FUAM_LAKEHOUSE_ID": LH,
        "CAPACITY_ID_OR_NAME": CLIENT,
    }, client, token_provider=provider)
    assert result[1][0]["workspace_id"] == WS
    assert query.call_count == 1
    assert query.call_args.args[:2] == (WS, lakehouse)
    utils.credentials.getToken.assert_called_once_with("pbi")
    utils.credentials.getSecret.assert_not_called()
    factory.assert_not_called()
    sql_connect.assert_not_called()
    install.assert_not_called()


@pytest.mark.parametrize("stage", ["selection", "notifications"])
def test_generated_targeted_stages_bind_custom_identity_library(stage):
    from orchestration.deployment import runtime_notebook

    nb = runtime_notebook(
        "synthetic-runtime.zip", WS, LH, stage=stage, child_pipeline_id=CLIENT,
        environment_library="Custom_Environment",
    )
    run = next("".join(c["source"]) for c in nb["cells"] if c["id"] == "run")
    assert 'config[\'FAR_ENVIRONMENT_LIBRARY\'] = "Custom_Environment"' in run
    compile(run, "<targeted-runtime>", "exec")


def test_collector_sql_uses_the_same_provider_as_rest(notebook, monkeypatch):
    utils, _, _, _ = notebook
    provider = notebook_token_provider(utils, "FAR_Environment")
    monkeypatch.setattr(auth, "_default_provider", provider)
    with fuam_sql.Session({"configuration": {"origin": "fabric"}}) as session:
        session._credentials()
        assert session._client.token() == "sp-" + FABRIC_SCOPE
        assert session._sql_token() == "sp-" + SQL_SCOPE
    utils.credentials.getToken.assert_not_called()


@pytest.mark.parametrize("oversized", [True, False])
def test_bounded_sql_reads_close_cursor_and_never_silently_truncate(oversized):
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.description = [("id",)]
    cursor.fetchmany.return_value = [("one",), ("two",)] if oversized else [("one",)]
    if oversized:
        with pytest.raises(RuntimeError, match="not truncated"):
            config_deployment.sql_rows(connection, "SELECT id FROM synthetic", max_rows=1)
    else:
        assert config_deployment.sql_rows(connection, "SELECT id FROM synthetic", max_rows=1) == [{"id": "one"}]
    cursor.fetchmany.assert_called_once_with(2)
    cursor.close.assert_called_once()


def test_embedded_owner_sync_uses_same_provider_and_custom_library(notebook):
    from reports.owner.deployment import access_notebook

    utils, _, _, _ = notebook
    nb = access_notebook(ROOT, WS, LH, "synthetic", CLIENT, environment_library="Custom_Environment")
    embedded = next("".join(c["source"]) for c in nb["cells"] if c["id"] == "owner-access-auth")
    assert embedded == (ROOT / "orchestration/notebook_auth.py").read_text()
    namespace = {}
    exec(compile(embedded, "<embedded-auth>", "exec"), namespace)
    provider = namespace["notebook_token_provider"](utils, "Custom_Environment")
    assert provider.get_token(POWERBI_SCOPE) == "sp-" + POWERBI_SCOPE
    execute = next("".join(c["source"]) for c in nb["cells"] if c["id"] == "owner-access-execute")
    assert "notebook_token_provider(notebookutils, 'Custom_Environment')" in execute
    assert "provider.get_token(POWERBI_SCOPE)" in execute

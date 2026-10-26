# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from orchestration.environment import (
    IDENTITY_KEYS, fabric_identity, uses_service_principal, validate_identity,
)
from orchestration.review_config import read_yaml

TENANT = "33333333-3333-3333-3333-333333333333"
CLIENT = "44444444-4444-4444-4444-444444444444"
ROOT = Path(__file__).resolve().parents[1]


def default_identity():
    return read_yaml(ROOT / "config" / "environment.yaml")["identity"]


def service_principal(**changes):
    return {"tenant_id": TENANT, "service_principal_client_id": CLIENT,
            "key_vault": "contoso-far-kv", "secret_name": "far-collector-secret", **changes}


def test_default_identity_runs_as_the_notebook_identity():
    identity = validate_identity(default_identity())
    assert set(identity) == set(IDENTITY_KEYS)
    assert not uses_service_principal(identity)


def test_service_principal_is_normalized_to_a_vault_uri():
    identity = validate_identity(service_principal(tenant_id=TENANT.upper()))
    assert uses_service_principal(identity)
    assert identity["tenant_id"] == TENANT
    assert identity["key_vault"] == "https://contoso-far-kv.vault.azure.net/"
    assert validate_identity(service_principal(key_vault="https://contoso-far-kv.vault.azure.net"))["key_vault"] \
        == identity["key_vault"]


@pytest.mark.parametrize("changes", [
    {"service_principal_client_id": ""},
    {"key_vault": ""},
    {"secret_name": ""},
])
def test_partial_service_principal_settings_never_fall_back_silently(changes):
    with pytest.raises(ValueError, match="together"):
        validate_identity(service_principal(**changes))


@pytest.mark.parametrize("changes", [
    {"tenant_id": ""}, {"tenant_id": "not-a-guid"}, {"service_principal_client_id": "not-a-guid"},
    {"key_vault": "https://example.com/vault"}, {"key_vault": "a"}, {"secret_name": "has space"},
])
def test_invalid_service_principal_settings_are_rejected(changes):
    with pytest.raises(ValueError):
        validate_identity(service_principal(**changes))


def test_unknown_missing_or_non_string_settings_are_rejected():
    with pytest.raises(ValueError):
        validate_identity({**default_identity(), "client_secret": "never"})
    with pytest.raises(ValueError):
        validate_identity({key: value for key, value in default_identity().items() if key != "tenant_id"})
    with pytest.raises(ValueError):
        validate_identity({**default_identity(), "tenant_id": None})


def test_identity_is_read_from_the_environment_variable_library():
    class Library:
        def getVariable(self, name):
            return {f"identity__{key}": value for key, value in service_principal().items()}[name]
    names = []
    identity = fabric_identity(lambda name: names.append(name) or Library(), "FAR_Environment")
    assert names == ["FAR_Environment"]
    assert identity["service_principal_client_id"] == CLIENT


def test_collect_notebook_has_no_credential_parameters_and_reads_the_library():
    notebook = json.loads((ROOT / "fabric" / "notebooks" / "01_collect.ipynb").read_text(encoding="utf-8"))
    parameters = next("".join(cell["source"]) for cell in notebook["cells"]
                      if "parameters" in cell.get("metadata", {}).get("tags", []))
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
    for removed in ("TENANT_ID", "SP_CLIENT_ID", "SP_SECRET", "SP_CONNECTION", "ACTIVITY_DAYS_LOG",
                    "CAPACITY_METRICS_APP_INSTALLED", "VERTIPAQ"):
        assert removed not in parameters
    assert "fabric_identity(notebookutils.variableLibrary.getLibrary, FAR_ENVIRONMENT_LIBRARY)" in source

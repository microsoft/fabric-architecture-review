# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared notebook authentication, also embedded in the pinned owner-sync notebook."""
from __future__ import annotations

import re
import importlib
import subprocess
import sys
from typing import Any, Callable
from uuid import UUID

FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
SQL_SCOPE = "https://database.windows.net/.default"
IDENTITY_KEYS = ("tenant_id", "service_principal_client_id", "key_vault", "secret_name")
_VAULT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]")
_VAULT_URI = re.compile(r"https://([A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9])\.vault\.azure\.net/?")
_SECRET_NAME = re.compile(r"[0-9A-Za-z-]{1,127}")


def validate_identity(values: dict[str, Any]) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) != set(IDENTITY_KEYS):
        raise ValueError("Environment identity settings must contain exactly: " + ", ".join(IDENTITY_KEYS))
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError("Environment identity settings must be strings.")
    identity = {key: values[key].strip() for key in IDENTITY_KEYS}
    delegated = ("service_principal_client_id", "key_vault", "secret_name")
    if any(identity[key] for key in delegated) and not all(identity[key] for key in delegated):
        raise ValueError(
            "Service principal authentication requires service_principal_client_id, key_vault and "
            "secret_name together. Clear all three to run as the notebook identity."
        )
    for key in ("tenant_id", "service_principal_client_id"):
        if identity[key]:
            try:
                identity[key] = str(UUID(identity[key]))
            except ValueError:
                raise ValueError(f"identity.{key} must be a GUID.") from None
    if identity["service_principal_client_id"]:
        if not identity["tenant_id"]:
            raise ValueError("identity.tenant_id is required for service principal authentication.")
        vault = identity["key_vault"]
        match = _VAULT_URI.fullmatch(vault)
        if match:
            name = match.group(1)
        elif _VAULT_NAME.fullmatch(vault):
            name = vault
        else:
            raise ValueError("identity.key_vault must be a Key Vault name or https://<name>.vault.azure.net.")
        identity["key_vault"] = f"https://{name}.vault.azure.net/"
        if not _SECRET_NAME.fullmatch(identity["secret_name"]):
            raise ValueError("identity.secret_name must be a valid Key Vault secret name.")
    return identity


def uses_service_principal(identity: dict[str, str]) -> bool:
    return bool(identity["service_principal_client_id"])


def fabric_identity(get_library: Callable[[str], Any], library_name: str) -> dict[str, str]:
    print("FAR startup: loading Environment library", flush=True)
    library = get_library(library_name)
    values = {}
    for key in IDENTITY_KEYS:
        print(f"FAR startup: reading Environment setting identity__{key}", flush=True)
        values[key] = library.getVariable(f"identity__{key}")
    print("FAR startup: validating Environment identity settings", flush=True)
    return validate_identity(values)


class NotebookTokenProvider:
    """Keep secrets and the MSAL cache in memory; never fall back from SP to user."""

    def __init__(self, notebookutils: Any, identity: dict[str, Any]) -> None:
        identity = validate_identity(identity)
        self.is_service_principal = uses_service_principal(identity)
        self._get = notebookutils.credentials.getToken
        self._app: Any = None
        if self.is_service_principal:
            import msal

            print("FAR startup: reading configured Key Vault secret", flush=True)
            secret = notebookutils.credentials.getSecret(identity["key_vault"], identity["secret_name"])
            if not isinstance(secret, str) or not secret.strip():
                raise ValueError("The configured Key Vault client secret is empty.")
            print("FAR startup: initializing MSAL client", flush=True)
            self._app = msal.ConfidentialClientApplication(
                identity["service_principal_client_id"],
                authority=f"https://login.microsoftonline.com/{identity['tenant_id']}",
                client_credential=secret,
                timeout=60,
            )

    def get_token(self, scope: str = FABRIC_SCOPE) -> str:
        if self.is_service_principal:
            # MSAL caches by scope and renews expiring application tokens on each call.
            result = self._app.acquire_token_for_client(scopes=[scope])
            token = result.get("access_token")
            if not isinstance(token, str) or not token.strip():
                # Do not log the response: error descriptions can include sensitive context.
                code = result.get("error")
                safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_]+", code) else "unknown"
                raise RuntimeError(f"MSAL service-principal authentication failed ({safe_code}); no identity fallback.")
            return token
        audiences = {
            FABRIC_SCOPE: "pbi",
            POWERBI_SCOPE: "pbi",
            SQL_SCOPE: "https://database.windows.net/",
            "https://storage.azure.com/.default": "storage",
            "https://vault.azure.net/.default": "keyvault",
            "https://management.azure.com/.default": "azuremanagement",
        }
        if scope not in audiences:
            raise ValueError("Unsupported notebook token scope.")
        return self._get(audiences[scope])

    def headers(self, scope: str = FABRIC_SCOPE) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.get_token(scope), "Content-Type": "application/json"}


def ensure_notebook_dependencies(*, sql: bool = False) -> None:
    requirements = ["msal>=1.23,<2"]
    if sql:
        requirements.append("mssql-python>=1.15,<2")
    print("Checking notebook authentication dependencies:", ", ".join(requirements), flush=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *requirements],
            check=True, timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "Notebook dependency provisioning failed. Install " + ", ".join(requirements)
            + " in the notebook's published Fabric Environment and retry in a fresh session."
        ) from exc
    importlib.invalidate_caches()


def notebook_token_provider(
    notebookutils: Any, library_name: str, *, sql: bool = False,
) -> NotebookTokenProvider:
    # Empty binding is for legacy generated callers, not a fallback for library errors.
    identity = (fabric_identity(notebookutils.variableLibrary.getLibrary, library_name)
                if library_name else dict.fromkeys(IDENTITY_KEYS, ""))
    if uses_service_principal(identity):
        ensure_notebook_dependencies(sql=sql)
    provider = NotebookTokenProvider(notebookutils, identity)
    print("FAR startup: authentication provider ready", flush=True)
    return provider

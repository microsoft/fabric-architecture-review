# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Deployment environment settings: how a Fabric run authenticates.

The values live in the ``<prefix>_Environment`` Variable Library. They are read at
run time rather than passed as pipeline parameters because they do not vary between
runs. Secrets are never stored: the library holds only the Key Vault name and the
secret name.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from orchestration.review_config import canonical_id

IDENTITY_KEYS = ("tenant_id", "service_principal_client_id", "key_vault", "secret_name")
_VAULT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]")
_VAULT_URI = re.compile(r"https://([A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9])\.vault\.azure\.net/?")
_SECRET_NAME = re.compile(r"[0-9A-Za-z-]{1,127}")


def validate_identity(values: dict[str, Any]) -> dict[str, str]:
    """Return the normalized identity settings or raise for an unusable combination."""
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
    if identity["tenant_id"]:
        identity["tenant_id"] = canonical_id(identity["tenant_id"], "identity.tenant_id")
    if identity["service_principal_client_id"]:
        if not identity["tenant_id"]:
            raise ValueError("identity.tenant_id is required for service principal authentication.")
        identity["service_principal_client_id"] = canonical_id(
            identity["service_principal_client_id"], "identity.service_principal_client_id",
        )
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
    library = get_library(library_name)
    return validate_identity({key: library.getVariable(f"identity__{key}") for key in IDENTITY_KEYS})


ACCESS_GRANT_ROLES = ("auto", "Contributor", "Member")


def validate_access_grant(values: dict[str, Any]) -> dict[str, Any]:
    """Normalized temporary-access settings; anything unrecognized is rejected rather than guessed."""
    if not isinstance(values, dict) or set(values) != {"enabled", "role"}:
        raise ValueError("Access grant settings must contain exactly: enabled, role.")
    enabled, role = values["enabled"], values["role"]
    if isinstance(enabled, bool):
        flag = enabled
    elif isinstance(enabled, str) and enabled.strip().lower() in {"true", "false", ""}:
        flag = enabled.strip().lower() == "true"
    else:
        raise ValueError("access_grant.enabled must be true or false.")
    role = "auto" if role is None or (isinstance(role, str) and not role.strip()) else role
    matches = [name for name in ACCESS_GRANT_ROLES if isinstance(role, str) and role.strip().casefold() == name.casefold()]
    if not matches:
        raise ValueError("access_grant.role must be auto, Contributor or Member.")
    return {"enabled": flag, "role": matches[0]}


def fabric_access_grant(get_library: Callable[[str], Any], library_name: str) -> dict[str, Any]:
    """Settings from the Environment library. A library from before this setting existed means disabled."""
    library = get_library(library_name)
    values = {}
    for key in ("enabled", "role"):
        try:
            values[key] = library.getVariable(f"access_grant__{key}")
        except Exception:
            values[key] = None
    return validate_access_grant({"enabled": values["enabled"] or "false", "role": values["role"]})


def access_grant_environment(settings: dict[str, Any]) -> dict[str, str]:
    """The process environment the collector reads, so local and Fabric runs share one code path."""
    return {"ACCESS_GRANT_ENABLED": "true" if settings["enabled"] else "false", "ACCESS_GRANT_ROLE": settings["role"]}


def validate_model_security(values: dict[str, Any]) -> dict[str, bool]:
    """Normalized model-security settings; anything unrecognized is rejected rather than guessed."""
    if not isinstance(values, dict) or set(values) != {"role_detail"}:
        raise ValueError("Model security settings must contain exactly: role_detail.")
    value = values["role_detail"]
    if isinstance(value, bool):
        return {"role_detail": value}
    if value is None or (isinstance(value, str) and value.strip().lower() in {"true", "false", ""}):
        return {"role_detail": isinstance(value, str) and value.strip().lower() == "true"}
    raise ValueError("model_security.role_detail must be true or false.")


def fabric_model_security(get_library: Callable[[str], Any], library_name: str) -> dict[str, bool]:
    """Settings from the Environment library. A library from before this setting existed means disabled."""
    library = get_library(library_name)
    try:
        value = library.getVariable("model_security__role_detail")
    except Exception:
        value = None
    return validate_model_security({"role_detail": value})


def model_security_environment(settings: dict[str, bool]) -> dict[str, str]:
    return {"MODEL_SECURITY_ROLE_DETAIL": "true" if settings["role_detail"] else "false"}

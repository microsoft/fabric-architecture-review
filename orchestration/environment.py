# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Deployment environment settings: how a Fabric run authenticates.

The values live in the ``<prefix>_Environment`` Variable Library. They are read at
run time rather than passed as pipeline parameters because they do not vary between
runs. Secrets are never stored: the library holds only the Key Vault name and the
secret name.
"""
from __future__ import annotations

from typing import Any, Callable

from orchestration.notebook_auth import (
    IDENTITY_KEYS as IDENTITY_KEYS,
    fabric_identity as fabric_identity,
    uses_service_principal as uses_service_principal,
    validate_identity as validate_identity,
)


ACCESS_GRANT_ROLES = ("auto", "Contributor", "Member")


def validate_access_grant(values: dict[str, Any]) -> dict[str, Any]:
    """Normalized temporary-access settings; anything unrecognized is rejected rather than guessed."""
    if not isinstance(values, dict) or not {"enabled", "role"} <= set(values) or set(values) - {"enabled", "role", "scope"}:
        raise ValueError("Access grant settings require enabled, role and optionally scope.")
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
    scope = values.get("scope") or "declared_models"
    if not isinstance(scope, str) or scope.strip().lower() not in {"declared_models", "required_evidence"}:
        raise ValueError("access_grant.scope must be declared_models or required_evidence.")
    return {"enabled": flag, "role": matches[0], "scope": scope.strip().lower()}


def fabric_access_grant(get_library: Callable[[str], Any], library_name: str) -> dict[str, Any]:
    """Settings from the Environment library. A library from before this setting existed means disabled."""
    library = get_library(library_name)
    values = {}
    for key in ("enabled", "role", "scope"):
        try:
            values[key] = library.getVariable(f"access_grant__{key}")
        except Exception:
            values[key] = None
    return validate_access_grant({"enabled": values["enabled"] or "false", "role": values["role"], "scope": values["scope"]})


def access_grant_environment(settings: dict[str, Any]) -> dict[str, str]:
    """The process environment the collector reads, so local and Fabric runs share one code path."""
    return {
        "ACCESS_GRANT_ENABLED": "true" if settings["enabled"] else "false",
        "ACCESS_GRANT_ROLE": settings["role"],
        "ACCESS_GRANT_SCOPE": settings.get("scope", "declared_models"),
    }


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

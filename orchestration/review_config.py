# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Validated, run-frozen configuration shared by local and Fabric reviews."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import yaml
from orchestration.project import DEFAULT_OUTPUT_DIR, PROJECT_ID, load_environment, validate_checkout

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 3
ENVIRONMENTS = {"production", "nonproduction", "unknown"}
SOURCE_KEYS = {"enabled", "workspace_id", "lakehouse_id", "max_age_hours"}


def read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration must be a mapping: {path}")
    return value


def canonical_id(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a GUID string.")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be a GUID.") from exc


def validate_thresholds(values: dict[str, Any]) -> None:
    from orchestration.rule_policy import validate_rule_threshold
    defaults = read_yaml(ROOT / "config" / "thresholds.yaml")
    if values.keys() != defaults.keys():
        raise ValueError("Threshold groups must match config/thresholds.yaml.")
    for group, expected in defaults.items():
        actual = values[group]
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError(f"Unknown or missing threshold in {group}.")
        for key, default in expected.items():
            value = actual[key]
            label = f"{group}.{key}"
            if isinstance(default, str):
                if not isinstance(value, str):
                    raise ValueError(f"{label} must be a string.")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label} must be numeric.")
            elif not math.isfinite(value) or value < 0:
                raise ValueError(f"{label} must be finite and nonnegative.")
            elif isinstance(default, int) and int(value) != value:
                raise ValueError(f"{label} must be an integer.")
            elif ("ratio" in key and value > 1) or ("pct" in key and value > 100):
                raise ValueError(f"{label} is outside its valid range.")
            validate_rule_threshold(key, value)


def validate_profiles(values: dict[str, Any]) -> None:
    from orchestration.rule_policy import validate_override
    if set(values) - {"workspaces", "capacities", "rule_profiles", "default_rule_profile"}:
        raise ValueError("Unknown workspace/capacity configuration section.")
    named = values.get("rule_profiles", {})
    if not isinstance(named, dict):
        raise ValueError("rule_profiles must be a mapping.")
    for name, profile in named.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("Invalid rule profile name.")
        if not isinstance(profile, dict) or set(profile) != {"rule_overrides"}:
            raise ValueError("Named profiles require rule_overrides only.")
        if not isinstance(profile["rule_overrides"], dict):
            raise ValueError("Named rule_overrides must be a mapping.")
        for rule_id, override in profile["rule_overrides"].items():
            validate_override(rule_id, override)
    if "default_rule_profile" in values and values["default_rule_profile"] not in named:
        raise ValueError("Unknown default rule profile.")
    for section in ("workspaces", "capacities"):
        entries = values.get(section, [])
        if not isinstance(entries, list):
            raise ValueError(f"{section} must be a list.")
        seen: set[str] = set()
        for entry in entries:
            allowed_entry = {"id", "profile"}
            if section == "capacities":
                allowed_entry.update({"arm_resource_id", "inherit_rules"})
            if not isinstance(entry, dict) or set(entry) - allowed_entry:
                raise ValueError(f"Invalid {section} entry.")
            entity_id = canonical_id(entry.get("id"), section)
            if entity_id in seen:
                raise ValueError(f"Duplicate {section} ID: {entity_id}")
            seen.add(entity_id)
            entry["id"] = entity_id
            if "inherit_rules" in entry and not isinstance(entry["inherit_rules"], bool):
                raise ValueError("inherit_rules must be Boolean.")
            if "arm_resource_id" in entry:
                resource = entry["arm_resource_id"]
                if not isinstance(resource, str) or not re.fullmatch(
                    r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9._()-]+"
                    r"/providers/Microsoft\.Fabric/capacities/[A-Za-z0-9._()-]+", resource, re.IGNORECASE,
                ):
                    raise ValueError("Capacity ARM resource ID must identify Microsoft.Fabric/capacities.")
                canonical_id(resource.split("/")[2], "ARM subscription")
                entry["arm_resource_id"] = resource.lower()
            profile = entry.get("profile", {})
            if not isinstance(profile, dict) or set(profile) - {"environment", "reason"}:
                raise ValueError(f"Unsupported {section} profile field.")
            if "environment" in profile and profile["environment"] not in ENVIRONMENTS:
                raise ValueError(f"Invalid {section} profile environment: {profile['environment']!r}")
            if "reason" in profile and not isinstance(profile["reason"], str):
                raise ValueError("Profile reason must be a string.")


def validate_sources(values: dict[str, Any]) -> None:
    if set(values) != {"fuam", "fca"}:
        raise ValueError("Source configuration must contain fuam and fca.")
    for name, source in values.items():
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise ValueError(f"Invalid {name} connection fields.")
        if not isinstance(source["enabled"], bool):
            raise ValueError(f"{name}.enabled must be Boolean.")
        age = source["max_age_hours"]
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age <= 0:
            raise ValueError(f"{name}.max_age_hours must be finite and positive.")
        for key in ("workspace_id", "lakehouse_id"):
            if source[key]:
                source[key] = canonical_id(source[key], f"{name}.{key}")
            elif source["enabled"] or not isinstance(source[key], str):
                raise ValueError(f"{name}.{key} is required when the source is enabled.")


POLICY_LISTS = {
    "allowed_item_types": "ARCH-001", "allowed_capacity_ids": "ARCH-002", "allowed_storage_modes": "ARCH-003",
    "eligible_admin_ids": "GOV-001", "allowed_principal_ids": "SEC-001", "allowed_roles": "SEC-002",
    "allowed_regions": "ARCH-007", "required_capacity_tags": "GOV-009", "restricted_network_directions": "SEC-021",
    "rls_required_model_ids": "SEC-024", "ols_required_model_ids": "SEC-025",
}
# The policy field each list variable fills when it differs from the variable name.
POLICY_FIELD = {"required_capacity_tags": "required_tag_names", "restricted_network_directions": "restricted_directions",
                "rls_required_model_ids": "required_model_ids", "ols_required_model_ids": "required_model_ids"}
# Lists whose values are matched in lower case.
LOWERCASE_LISTS = frozenset({"restricted_network_directions"})
# Declaring a requirement keeps the rule's built-in scope: ARCH-002 stays production-only.
POLICY_DEFAULTS = {"ARCH-002": {"production_only": True, "require_shared_workspace": True}}
DECLARED_REASON = "Declared in the policy variables."


# Values that can contain spaces (region and tag names) are separated by commas, semicolons or new lines only.
SPACED_LISTS = frozenset({"allowed_regions", "required_capacity_tags"})


def _items(value: Any, label: str, spaced: bool = False) -> list[str]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    separator = r"[\n,;]+" if spaced else r"[\s,;]+"
    return [item.strip() for item in re.split(separator, value.strip()) if item.strip()]


def validate_declarations(values: dict[str, Any]) -> None:
    defaults = read_yaml(ROOT / "config" / "policy.yaml")
    if not isinstance(values, dict) or values.keys() != defaults.keys():
        raise ValueError("Policy groups must match config/policy.yaml.")
    for group, expected in defaults.items():
        actual = values[group]
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError(f"Unknown or missing policy variable in {group}.")


def declared_profiles(values: dict[str, Any]) -> dict[str, Any]:
    """Turn the fixed scope and policy variables into the profiles the rules resolve against."""
    validate_declarations(values)
    scope, policy = values["scope"], values["policy"]
    profiles: dict[str, Any] = {"workspaces": [], "capacities": []}
    for section, kind in (("workspaces", "workspace"), ("capacities", "capacity")):
        seen: set[str] = set()
        for environment in ("production", "nonproduction"):
            label = f"scope.{environment}_{kind}_ids"
            for raw in _items(scope[f"{environment}_{kind}_ids"], label):
                identity = canonical_id(raw, label)
                if identity in seen:
                    raise ValueError(f"{identity} is declared more than once in the scope variables.")
                seen.add(identity)
                entry: dict[str, Any] = {"id": identity, "profile": {"environment": environment, "reason": DECLARED_REASON}}
                if section == "capacities":
                    entry["inherit_rules"] = True
                profiles[section].append(entry)
    capacities = {entry["id"]: entry for entry in profiles["capacities"]}
    for pair in _items(scope["capacity_arm_resource_ids"], "scope.capacity_arm_resource_ids"):
        capacity, separator, resource = pair.partition("=")
        if not separator or not resource:
            raise ValueError("scope.capacity_arm_resource_ids entries must be <capacity-guid>=<arm-resource-id>.")
        identity = canonical_id(capacity, "scope.capacity_arm_resource_ids")
        if identity in capacities and "arm_resource_id" in capacities[identity]:
            raise ValueError(f"{identity} has more than one ARM resource ID.")
        if identity not in capacities:
            capacities[identity] = {"id": identity}
            profiles["capacities"].append(capacities[identity])
        capacities[identity]["arm_resource_id"] = resource
    overrides: dict[str, Any] = {}
    for key, rule_id in POLICY_LISTS.items():
        items = _items(policy[key], f"policy.{key}", spaced=key in SPACED_LISTS)
        if key in LOWERCASE_LISTS:
            items = list(dict.fromkeys(item.lower() for item in items))
        if items:
            overrides[rule_id] = {
                "reason": DECLARED_REASON,
                "policy": {"required": True, **POLICY_DEFAULTS.get(rule_id, {}), POLICY_FIELD.get(key, key): items},
            }
    for rule_id in _items(policy["disabled_rules"], "policy.disabled_rules"):
        overrides[rule_id.upper()] = {"applicability": "disabled", "reason": "Disabled in the policy variables."}
    if overrides:
        profiles["rule_profiles"] = {"declared": {"rule_overrides": overrides}}
        profiles["default_rule_profile"] = "declared"
    validate_profiles(profiles)
    return profiles


def local_configuration(config_dir: Path = ROOT / "config") -> dict[str, Any]:
    return {
        "origin": "local",
        "thresholds": read_yaml(config_dir / "thresholds.yaml"),
        "profiles": declared_profiles(read_yaml(config_dir / "policy.yaml")),
        "sources": read_yaml(config_dir / "sources.yaml"),
        "threshold_environment": {},  # kept for snapshot schema compatibility; always empty
    }


def fabric_configuration(
    get_library: Callable[[str], Any],
    thresholds_library: str,
    connections_library: str,
) -> dict[str, Any]:
    library = get_library(thresholds_library)

    def read(defaults: dict[str, Any]) -> dict[str, Any]:
        return {
            group: {key: library.getVariable(f"{group}__{key}") for key in entries}
            for group, entries in defaults.items()
        }

    thresholds = read(read_yaml(ROOT / "config" / "thresholds.yaml"))
    profiles = declared_profiles(read(read_yaml(ROOT / "config" / "policy.yaml")))
    connections = get_library(connections_library)
    sources = {
        name: {key: connections.getVariable(f"{name}__{key}") for key in sorted(SOURCE_KEYS)}
        for name in ("fuam", "fca")
    }
    return {
        "origin": "fabric", "thresholds": thresholds, "profiles": profiles,
        "sources": sources, "threshold_environment": {},
    }


def validate_configuration(config: dict[str, Any]) -> None:
    if set(config) != {"origin", "thresholds", "profiles", "sources", "threshold_environment"}:
        raise ValueError("Invalid review configuration contract.")
    if config["origin"] not in {"local", "fabric"}:
        raise ValueError("Invalid configuration origin.")
    for key in ("thresholds", "profiles", "sources", "threshold_environment"):
        if not isinstance(config[key], dict):
            raise ValueError(f"{key} must be a mapping.")
    validate_thresholds(config["thresholds"])
    validate_profiles(config["profiles"])
    validate_sources(config["sources"])
    if config["threshold_environment"]:
        raise ValueError("Threshold environment overrides are not supported; thresholds come from configuration files.")


def targeting_source(get_library: Callable[[str], Any], name: str) -> dict[str, str]:
    library = get_library(name)
    sources = {
        source: {key: library.getVariable(f"{source}__{key}") for key in sorted(SOURCE_KEYS)}
        for source in ("fuam", "fca")
    }
    validate_sources(sources)
    if not sources["fuam"]["enabled"]:
        raise ValueError("Targeted review requires an enabled FUAM connection.")
    return {
        "FUAM_WORKSPACE_ID": sources["fuam"]["workspace_id"],
        "FUAM_LAKEHOUSE_ID": sources["fuam"]["lakehouse_id"],
    }


def digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_snapshot(path: Path) -> dict[str, Any]:
    from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
        or value.get("project_id") != PROJECT_ID
        or value.get("profile_id") != PROFILE_ID or value.get("profile_sha256") != PROFILE_SHA256
    ):
        raise ValueError("Unsupported review configuration snapshot.")
    config = value["configuration"]
    validate_configuration(config)
    if value.get("sha256") != digest(config):
        raise ValueError("Review configuration snapshot checksum mismatch.")
    return value


def capture(out_dir: Path, config: dict[str, Any], *, replace: bool = False) -> Path:
    from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256
    validate_configuration(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "review_config.json"
    if path.exists() and not replace:
        read_snapshot(path)
        return path
    value = {
        "schema_version": SCHEMA_VERSION, "project_id": PROJECT_ID,
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": digest(config), "configuration": config,
    }
    temporary = out_dir / "review_config.json.tmp"
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
    return path


def activate(path: Path) -> dict[str, Any]:
    snapshot = read_snapshot(path)
    config = snapshot["configuration"]
    directory = path.parent / "review_config"
    directory.mkdir(exist_ok=True)
    target = directory / "thresholds.yaml"
    target.write_text(yaml.safe_dump(config["thresholds"], sort_keys=False), encoding="utf-8")
    os.environ["THRESHOLDS_CONFIG"] = str(target.resolve())
    os.environ["FAR_CONFIG_SNAPSHOT"] = str(path.resolve())
    return snapshot


def activate_from_environment() -> None:
    path = os.environ.get("FAR_CONFIG_SNAPSHOT")
    if path:
        activate(Path(path))


def main() -> None:
    validate_checkout(ROOT)
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT_DIR))
    parser.add_argument("--config-dir", default=os.environ.get("FAR_CONFIG_DIR", str(ROOT / "config")))
    parser.add_argument("--replace", action="store_true", help="Begin a new local review in the same output directory.")
    args = parser.parse_args()
    path = capture(Path(args.out_dir), local_configuration(Path(args.config_dir)), replace=args.replace)
    print(f"Review configuration frozen: {path}")


if __name__ == "__main__":
    main()

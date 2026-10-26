# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""One architecture-only collection planner for local and Fabric execution."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256, TENANT_RULES
from collectors import fuam_inventory, fuam_sourced, tenant_settings, workspace_inventory
from collectors._common import RUN_SCOPE_ENV, collection_incomplete, load_workspace_inventory
from collectors.fuam_sql import Session
from orchestration.review_config import read_snapshot
from orchestration.rule_policy import evidence_demanded, resolve_rule

DEMAND = {
    "tenant_settings": {"GOV-002", "GOV-010", "SEC-003", "SEC-004", "SEC-005", "SEC-007", "SEC-008", "SEC-013",
                         "SEC-014", "SEC-015", "SEC-016", "SEC-023"},
    "git_integration": {"OPS-001"},
    # ARCH-020 keeps the hosting and storage inventory behind the report pages in every review.
    "capacity_metrics": {"ARCH-002", "ARCH-007", "ARCH-016", "ARCH-020", "OPS-006", "OPS-008", "OPS-009", "REL-001"},
    "semantic_models": {"ARCH-003", "ARCH-020", "SEC-024", "SEC-025", "SEC-026"},
    "semantic_model_definitions": {"ARCH-003", "ARCH-020", "SEC-024", "SEC-025", "SEC-026"},
    "pipelines_notebooks": {"ARCH-004", "ARCH-005", "ARCH-010", "ARCH-011", "ARCH-012", "ARCH-013", "REL-002", "SEC-011"},
    "pipeline_definitions": {"ARCH-004", "ARCH-005", "ARCH-010", "ARCH-011", "ARCH-012", "ARCH-013", "REL-002", "SEC-011"},
    "gateways": {"SEC-010"},
    "deployment_pipelines": {"OPS-010"},
    "onelake_roles": {"SEC-020"},
    "workspace_network": {"SEC-021"},
}
PREREQUISITES = {
    "semantic_model_definitions": {"semantic_models"},
    "pipeline_definitions": {"pipelines_notebooks"},
}
ARTIFACTS = {
    "tenant_settings": "tenant_settings.json", "git_integration": "git_integration.json", "capacity_metrics": "capacity_metrics.json",
    "semantic_models": "semantic_models.json", "semantic_model_definitions": "semantic_model_definitions.json",
    "pipelines_notebooks": "pipelines_notebooks.json", "pipeline_definitions": "pipeline_definitions.json",
    "gateways": "gateways.json", "deployment_pipelines": "deployment_pipelines.json",
    "onelake_roles": "onelake_roles.json", "workspace_network": "workspace_network.json",
}


def plan(snapshot: dict[str, Any], workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    required: set[str] = set()
    rules: set[str] = set()
    applicable: set[str] = set()
    scopes: dict[str, set[str]] = {module: set() for module in DEMAND}
    for workspace in workspaces:
        if not workspace.get("id"):
            continue
        for rule in ACTIVE_RULE_IDS - TENANT_RULES:
            resolved = resolve_rule(snapshot["configuration"], rule, workspace)
            if resolved["applicability"] == "applicable":
                applicable.add(rule)
            if evidence_demanded(rule, resolved):
                rules.add(rule)
                if workspace.get("id"):
                    for module, demand in DEMAND.items():
                        if rule in demand:
                            scopes[module].add(str(workspace["id"]).lower())
    if any(workspace.get("id") for workspace in workspaces):
        # Tenant-wide rules are evaluated once per review, against the default rule profile.
        for rule in TENANT_RULES:
            resolved = resolve_rule(snapshot["configuration"], rule, {})
            if evidence_demanded(rule, resolved):
                rules.add(rule)
            if resolved["applicability"] == "applicable":
                applicable.add(rule)
    for module, demand in DEMAND.items():
        if demand & rules:
            required.add(module)
    for module in tuple(required):
        required.update(PREREQUISITES.get(module, set()))
        for dependency in PREREQUISITES.get(module, set()):
            scopes[dependency].update(scopes[module])
    enabled_sources = [source for source, values in snapshot["configuration"]["sources"].items()
                       if values["enabled"]]
    resolutions = [{
        "collector": module, "rules": sorted(DEMAND[module] & rules),
        "workspace_ids": sorted(scopes[module]),
        "resolution": "fuam" if fuam_sourced.supplies(snapshot, module) else "native_architecture",
        "reason": ("Read from the connected FUAM first, with the admin API adding group detail and replacing FUAM "
                   "entirely when its snapshot is stale, missing or unreadable."
                   if fuam_sourced.supplies(snapshot, module) else
                   "Required architectural metadata; optional enrichment does not replace or expand API collection."),
    } for module in DEMAND if module in required]
    return {
        "schema_version": 1, "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "configuration_sha256": snapshot["sha256"], "active_rule_ids": sorted(applicable),
        "evidence_rule_ids": sorted(rules),
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
        "workspace_ids": sorted({str(ws["id"]).lower() for ws in workspaces if ws.get("id")}),
        "collectors": ["collectors." + module for module in DEMAND if module in required],
        "source_resolutions": resolutions, "enrichment_sources": enabled_sources,
        "collection_mode": "architecture_plus_optional_enrichment",
        "empty_scope": not workspaces,
    }


def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    from collectors import access_grant
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    # An invalid setting stops the run here, before anything is changed.
    grant = access_grant.AccessGrant.from_environment(raw_dir)
    # Collectors are given a narrower WORKSPACE_IDS; this is how they still know the scope of the whole review.
    previous = os.environ.get(RUN_SCOPE_ENV)
    os.environ[RUN_SCOPE_ENV] = os.environ.get("WORKSPACE_IDS", "")
    try:
        # Access an earlier run left behind is removed first, whether or not the setting is still on.
        grant.recover()
        target = _collect(output_dir, grant)
    finally:
        grant.finish()
        grant.write_audit(raw_dir)
        if grant.failures:
            print("Temporary workspace access could not be removed for: " + ", ".join(grant.failures)
                  + ". Remove it by hand; the ledger lists it, and the next run tries again.")
        if previous is None:
            os.environ.pop(RUN_SCOPE_ENV, None)
        else:
            os.environ[RUN_SCOPE_ENV] = previous
    if grant.failures:
        raise RuntimeError("Temporary workspace access could not be removed; the run is failed so it is noticed.")
    return target


def _definitions_with_access(raw_dir: Path, grant: Any, declared: frozenset[str], workspaces: list[dict[str, Any]]) -> Path:
    """Read the model definitions, then re-read the declared models that were refused after a temporary grant."""
    from collectors import semantic_model_definitions
    path = semantic_model_definitions.collect(output_dir=str(raw_dir))
    grant.remediate(Path(path), declared, workspaces, semantic_model_definitions.refetch_model)
    return path


def _collect(output_dir: str | os.PathLike, grant: Any) -> Path:
    config_path = os.environ.get("FAR_CONFIG_SNAPSHOT")
    if not config_path:
        raise ValueError("Collection requires a frozen FAR_CONFIG_SNAPSHOT.")
    snapshot = read_snapshot(Path(config_path))
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    # The raw folder belongs to one collection: nothing from an earlier run may be read as current evidence.
    for stale in raw_dir.glob("*.json"):
        stale.unlink()
    outcomes = []
    failures = []
    # Provenance records the scope of the whole run, not the narrower per-collector demand.
    run_scope = os.environ.get("WORKSPACE_IDS", "")

    def execute(module: str, producer: Callable[[], Path] | None = None) -> None:
        name = module.removeprefix("collectors.")
        try:
            path = producer() if producer else importlib.import_module(module).collect(output_dir=str(raw_dir))
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            incomplete = collection_incomplete(payload, include_errors=True)
            if isinstance(payload, dict):
                payload["_provenance"] = {
                    "source": "enrichment" if name == "far_evidence" else "native",
                    "collector": module, "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
                    "configuration_sha256": snapshot["sha256"],
                    "configuration_captured_at_utc": snapshot["captured_at_utc"],
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                    "workspace_scope": run_scope,
                    "collector_workspace_scope": os.environ.get("WORKSPACE_IDS", ""),
                    "capture_status": "partial" if incomplete else "collected",
                }
                Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            outcome = {"collector": module, "status": "incomplete" if incomplete else "collected"}
            if isinstance(payload, dict) and payload.get("dataSource"):
                outcome["data_source"] = payload["dataSource"]
                if payload.get("inventorySource"):
                    outcome["source_detail"] = payload["inventorySource"]
                if payload.get("reason"):
                    outcome["reason"] = payload["reason"]
            outcomes.append(outcome)
        except Exception:
            # Do not persist exception text: API failures can contain source/secret literals.
            print(f"Collector failed: {module}. Details are withheld because they can contain source literals.")
            failures.append(module)
            outcomes.append({"collector": module, "status": "failed"})

    # One read-only session serves the inventory, the tenant settings and every other FUAM or FCA read.
    sql = Session(snapshot)
    execute("collectors.scanner_api")
    inventory = (lambda: workspace_inventory.collect(
        output_dir=str(raw_dir),
        fuam_reader=lambda ids: fuam_inventory.read(snapshot, sql.query, ids, datetime.now(timezone.utc)),
    )) if fuam_inventory.supplies(snapshot) else None
    execute("collectors.workspace_inventory", inventory)
    try:
        workspaces = load_workspace_inventory(raw_dir)
    except Exception:
        workspaces = []
        failures.append("workspace_inventory_contract")
    execution_plan = plan(snapshot, workspaces)
    selected_names = {module.removeprefix("collectors.") for module in execution_plan["collectors"]}
    for module, filename in ARTIFACTS.items():
        if module not in selected_names:
            (raw_dir / filename).unlink(missing_ok=True)
    from analyzers.model_security_rules import required_models
    declared_models = required_models(snapshot["configuration"], "SEC-024") | required_models(snapshot["configuration"], "SEC-025")
    try:
        for module in execution_plan["collectors"]:
            scope = next(row["workspace_ids"] for row in execution_plan["source_resolutions"]
                         if "collectors." + row["collector"] == module)
            name = module.removeprefix("collectors.")
            producer = ((lambda name=name: fuam_sourced.collect(
                             name, raw_dir, snapshot, sql.query, api_fetch=tenant_settings.fetch_sanitized))
                        if fuam_sourced.supplies(snapshot, name) else None)
            if name == "semantic_model_definitions" and producer is None and grant.settings.enabled and declared_models:
                producer = lambda: _definitions_with_access(raw_dir, grant, declared_models, workspaces)
            original_scope = os.environ.get("WORKSPACE_IDS")
            try:
                os.environ["WORKSPACE_IDS"] = ",".join(scope)
                execute(module, producer)
            finally:
                if original_scope is None:
                    os.environ.pop("WORKSPACE_IDS", None)
                else:
                    os.environ["WORKSPACE_IDS"] = original_scope
    finally:
        sql.close()
    # Connected modules are bounded context reads, not scoring/optimization inputs.
    execute("collectors.far_evidence")
    incomplete = any(outcome["status"] == "incomplete" for outcome in outcomes)
    execution_plan.update(
        captured_at_utc=datetime.now(timezone.utc).isoformat(), outcomes=outcomes,
        collectionComplete=not failures and not incomplete,
        status="failed" if failures else "incomplete" if incomplete else "completed",
    )
    if grant.settings.enabled or grant.audit:
        # Counts and statuses only; the identity that was granted lives in the ledger, not in the evidence.
        summary = grant.summary()
        execution_plan["access_grant"] = {key: summary[key] for key in ("enabled", "role_setting", "granted_count", "removed_count")}
    target = raw_dir / "collection_plan.json"
    target.write_text(json.dumps(execution_plan, indent=2), encoding="utf-8")
    if failures:
        raise RuntimeError("Collection failed: " + ", ".join(failures))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

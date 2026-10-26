# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Materialize only architectural Gold, never optimizer or execution history."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any
from analyzers.far_profile import PROFILE_SHA256, JUDGED_SETTING_NAMES
from analyzers.model_security_rules import (KINDS as SECURITY_KINDS, model_roles, model_state, requirement_outcome,
                                            required_models)
from collectors.enrichment_facts import ENRICHMENT_FAMILIES

from collectors._common import load_workspace_inventory
from collectors._http import HttpError
from collectors.workspace_evidence import merge_workspace_evidence, workspace_items, workspace_users, workspace_items_available, workspace_roles_complete
from collectors.workspace_scope import filter_review_payload
from reports.architecture_contract import PROFILE_ID, architectural_findings, assessment
from reports.powerbi.schema import GOLD_TABLES_BY_NAME, ONTOLOGY_KEYS, SEVERITY_RANK, coerce_value, ontology_key
from reports.rule_guidance import catalog, collection_guidance, explain
from reports.version import build_release_record
from orchestration.review_config import read_snapshot
from orchestration.rule_policy import resolve_rule


def _load(raw_dir: Path, name: str) -> dict:
    path = raw_dir / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _coerce_row(table_name: str, row: dict) -> dict:
    return {
        column.name: coerce_value(column.kind, row.get(column.name), f"{table_name}.{column.name}")
        for column in GOLD_TABLES_BY_NAME[table_name].columns
    }


def _capacity_kind(sku: str | None) -> str:
    value = (sku or "").upper()
    return next((label for prefix, label in (
        ("FT", "Trial"), ("F", "Fabric"), ("PP", "Premium Per User"),
        ("P", "Premium"), ("A", "Embedded")) if value.startswith(prefix)), "Unknown")


def _gold_workspace_inventory(raw: Path) -> list[dict]:
    try:
        collected = load_workspace_inventory(raw)
    except HttpError:
        collected = []
    scanner = _load(raw, "scanner.json")
    identities = [
        {key: row[key] for key in ("id", "name", "displayName", "type", "capacityId") if key in row}
        for row in filter_review_payload(scanner, raw).get("workspaces") or []
    ] if scanner.get("collectionComplete") is False else []
    return merge_workspace_evidence(identities, collected)


# Evidence that lists the items a finding points at, and what to say about each.
ITEM_EVIDENCE = {
    "violating_item_ids": "Not allowed by the declared policy",
    "orphan_report_ids": "Report with no semantic model in the review",
    "unresolved_report_ids": "Report whose semantic model could not be resolved",
    "unused_model_ids": "No report uses it and nothing queried it",
    "unconfirmed_model_ids": "Its use could not be confirmed",
    "linked_item_ids": "Open to the whole organization through a sharing link",
    "published_item_ids": "Published to the web",
    "pipeline_ids_without_failure_path": "No step reacts when another step fails",
    "pipeline_ids_without_retry": "An activity has no retry",
    "pipeline_ids_with_default_timeout": "An activity uses the default 12-hour timeout",
    "lakehouse_ids_without_custom_roles": "No custom OneLake data access role",
    "item_ids_with_resharing_grants": "A direct grant can be shared again",
    "failing_item_ids": "A high share of its capacity operations failed",
    "refresh_failing_item_ids": "A high share of its refreshes failed",
    "heavy_item_ids": "Uses a large share of its capacity's compute",
    "stale_item_ids": "Not modified for a long time",
    "undocumented_item_ids": "No description",
    "misnamed_item_ids": "Name does not match the naming convention",
    "unendorsed_model_ids": "Not promoted or certified",
    "unlabelled_item_ids": "No sensitivity label",
    "dependent_item_ids": "Reads from another workspace",
    "nonproduction_dependent_item_ids": "Production item that reads from a non-production workspace",
    "legacy_item_ids": "A legacy item type",
    "notebook_ids_with_patterns": "Contains a pattern this rule looks for",
    "models_missing_rls_ids": "Required to enforce row-level security, and none is configured",
    "models_missing_ols_ids": "Required to enforce object-level security, and none is configured",
    "models_without_security_ids": "Defines neither row-level nor object-level security",
    "unverified_model_ids": "Its row-level and object-level security could not be inspected",
}
# Rules whose violations are a list of {item_id, ...} rather than a list of IDs.
VIOLATION_ITEMS = {"ARCH-004": "Passes a parameter its notebook does not declare",
                   "ARCH-005": "Its activity dependencies are broken"}


def _ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        result = {str(value[key]).lower() for key in ("workspaceId", "workspace_id") if value.get(key)}
        for child in value.values():
            result.update(_ids(child))
        return result
    if isinstance(value, list):
        return set().union(*(_ids(child) for child in value)) if value else set()
    return set()


def _setting_history(far: dict) -> dict[str, tuple[str | None, int]]:
    """Per setting: when it last changed and how often, from the FUAM transfer history."""
    transfers: dict[str, list[tuple[str, bool]]] = {}
    for row in (far.get("tables") or {}).get("far_evidence_tenant_settings") or []:
        transfers.setdefault(row["settingName"], []).append((row["TransferDateTime"], row["enabled"]))
    history = {}
    for name, rows in transfers.items():
        rows.sort()
        changes = [rows[index][0] for index in range(1, len(rows)) if rows[index][1] != rows[index - 1][1]]
        history[name] = (changes[-1] if changes else None, len(changes))
    return history


def _posture(rule: str | None, setting: dict) -> str:
    """Where a setting stands, for every setting, without guessing which ones are risky."""
    if rule:
        return "Judged by a rule"
    if setting.get("enabled") is not True:
        return "Disabled"
    if (setting.get("enabledSecurityGroupCount") or 0) > 0:
        return "Limited to groups"
    if setting.get("canSpecifySecurityGroups"):
        return "Open to everyone (can be limited)"
    return "Enabled (cannot be limited)"


def _fca_capacities(far: dict, frozen: dict | None) -> dict[str, dict]:
    """Per capacity: the Azure region and tags FCA holds for its resource, and which required tags are missing."""
    result: dict[str, dict] = {}
    for row in (far.get("tables") or {}).get("far_evidence_capacity_resources") or []:
        result[str(row["capacity_id"]).lower()] = {
            "fca_region": row.get("RegionId"), "fca_resource_name": row.get("ResourceName")}
    facts = ((far.get("rule_facts") or {}).get("capacity_tags") or {})
    required = (resolve_rule(frozen, "GOV-009", {})["policy"] or {}).get("required_tag_names") if frozen else None
    wanted = {name.casefold(): name for name in required or []}
    if facts.get("status") == "collected":
        for row in facts.get("rows") or []:
            present = {tag.casefold() for tag in row.get("tag_names") or []}
            result.setdefault(str(row["capacity_id"]).lower(), {}).update(
                tag_names=", ".join(sorted(row.get("tag_names") or [])),
                missing_tags=", ".join(sorted(name for key, name in wanted.items() if key not in present)))
    return result


def _emit_tenant_settings(emit, findings: list[dict], tenant: dict, far: dict, observed_at: str) -> None:
    """Record every tenant setting returned for this run, tied to the control that evaluates it."""
    settings = tenant.get("tenantSettings")
    if tenant.get("collectionComplete") is not True or not isinstance(settings, list):
        return
    rule_of = {name: rule for rule, names in JUDGED_SETTING_NAMES.items() for name in names}
    status_of = {f["rule_id"]: f.get("status") for f in findings}
    history = _setting_history(far)
    for setting in settings:
        name = setting.get("settingName")
        rule = rule_of.get(name)
        last_changed, changes = history.get(name, (None, None))
        emit("gold_tenant_settings", setting_name=name, title=setting.get("title"),
             setting_group=setting.get("tenantSettingGroup"), rule_id=rule,
             status=status_of.get(rule) or "not_evaluated", posture=_posture(rule, setting),
             enabled=setting.get("enabled"),
             is_scoped=(setting.get("enabledSecurityGroupCount") or 0) > 0,
             enabled_group_count=setting.get("enabledSecurityGroupCount"),
             excluded_group_count=setting.get("excludedSecurityGroupCount"),
             can_specify_security_groups=setting.get("canSpecifySecurityGroups"),
             last_changed_at=last_changed, change_count=changes,
             history_source="fuam" if rule and name in history else "none")


def build_gold(findings: list[dict], raw_dir: str | Path, *, run_id: str,
               run_timestamp: str, client_name: str = "", engagement_name: str = "",
               reviewer_name: str = "", deployed_version: str | None = None,
               repo_url: str = "", branch: str = "main", check_remote: bool = True) -> dict[str, list[dict]]:
    raw = Path(raw_dir)
    findings = architectural_findings(filter_review_payload({"findings": findings}, raw)["findings"])
    manifest = _load(raw, "run_manifest.json") or _load(raw.parent, "run_manifest.json")
    profile_hash = manifest.get("profile_sha256")
    configuration_hash = manifest.get("configuration_sha256") or manifest.get("review_configuration_sha256")
    if manifest.get("profile_id") != PROFILE_ID or profile_hash != PROFILE_SHA256 or not configuration_hash:
        findings = []
    meta = {"run_id": run_id, "run_timestamp": run_timestamp, "profile_id": manifest.get("profile_id"),
            "profile_sha256": profile_hash, "configuration_sha256": configuration_hash,
            "client_name": client_name, "engagement_name": engagement_name, "reviewer_name": reviewer_name}
    tables: dict[str, list[dict]] = {name: [] for name in GOLD_TABLES_BY_NAME}

    emitted_keys: set[tuple[str, str]] = set()

    def emit(table: str, **values: Any) -> bool:
        """Append a row; False when it repeats an entity that is already there."""
        row = {**meta, **values}
        for key, parts in ONTOLOGY_KEYS.get(table, {}).items():
            row[key] = ontology_key(run_id, *(row.get(part) or "" for part in parts))
        # An entity is one row per run: a principal who administers several workspaces is emitted once, and a
        # model's security row once, because the model-to-roles relationship needs a unique key on the one side.
        unique = {"gold_owners": "owner_key", "gold_model_security": "model_key"}.get(table)
        if unique and row.get(unique):
            if (table, row[unique]) in emitted_keys:
                return False
            emitted_keys.add((table, row[unique]))
        tables[table].append(_coerce_row(table, row))
        return True

    rule_catalog = {row["rule_id"]: row for row in catalog()}
    for finding in findings:
        emit("gold_findings", **{
            **finding, "severity_rank": SEVERITY_RANK.get(finding.get("severity"), 0),
            "is_fail": int(finding.get("status") == "fail" and finding["is_scored"]),
            "is_scored": int(finding["is_scored"]),
            "affected": ", ".join(sorted(_ids(finding["evidence"]))),
            "workspace_name": finding["evidence"].get("workspace_name")
            or (f"Capacity: {finding['evidence']['capacity_name']}" if finding["evidence"].get("scope") == "capacity" else "Tenant-wide"),
            "evidence_json": json.dumps(finding["evidence"], ensure_ascii=False),
            "rule_description": rule_catalog.get(finding.get("rule_id"), {}).get("what_it_checks", ""),
            **explain(finding),
        })
    if findings:
        for rule in rule_catalog.values():
            emit("gold_rules", **rule)
    emit("gold_run_summary", **assessment(findings), is_latest=True, **{
        severity + "_fail": sum(f.get("severity") == severity and f["is_scored"] and f.get("status") == "fail"
                                for f in findings)
        for severity in ("critical", "high", "medium", "low")
    })
    for dimension in sorted({f.get("dimension") or "architecture" for f in findings}):
        group = [f for f in findings if (f.get("dimension") or "architecture") == dimension]
        emit("gold_dimension_summary", dimension=dimension, **assessment(group),
             worst_severity=max((f.get("severity", "info") for f in group if f["is_scored"] and f.get("status") == "fail"),
                                key=lambda s: SEVERITY_RANK.get(s, 0), default="none"))
    workspaces = _gold_workspace_inventory(raw)
    snapshot_path = os.environ.get("FAR_CONFIG_SNAPSHOT")
    frozen = read_snapshot(Path(snapshot_path))["configuration"] if snapshot_path else None
    cap_raw = filter_review_payload(_load(raw, "capacity_metrics.json"), raw)
    capacities = cap_raw.get("capacities") or filter_review_payload(_load(raw, "capacities.json"), raw).get("capacities") or []
    model_metadata = {
        (str(m.get("workspaceId") or "").lower(), str(m.get("id") or m.get("modelId") or "").lower()): m
        for m in filter_review_payload(_load(raw, "semantic_model_definitions.json"), raw).get("models") or []
    }
    model_inventory = filter_review_payload(_load(raw, "semantic_models.json"), raw).get("datasets") or []
    cap_names = {str(c.get("id") or c.get("capacityId") or "").lower():
                 c.get("displayName") or c.get("name") or "" for c in capacities}
    cap_skus = {str(c.get("id") or c.get("capacityId") or "").lower(): c.get("sku")
                for c in capacities}
    fca = _fca_capacities(_load(raw, "far_evidence.json"), frozen)
    declared_models = {"rls": required_models(frozen, "SEC-024"), "ols": required_models(frozen, "SEC-025")}
    for capacity in capacities:
        cid = str(capacity.get("id") or capacity.get("capacityId") or "").lower()
        members = [w for w in workspaces if str(w.get("capacityId") or "").lower() == cid]
        kind = _capacity_kind(capacity.get("sku"))
        emit("gold_capacities", capacity_id=cid, capacity_name=cap_names[cid], sku=capacity.get("sku"),
             kind=kind, is_dedicated=kind != "Premium Per User" if kind != "Unknown" else None,
             state=capacity.get("state"),
             region=capacity.get("region"),
             admin_count=len(capacity["admins"]) if isinstance(capacity.get("admins"), list) else None,
             assigned_workspace_count=capacity.get("assignedWorkspaceCount", capacity.get("workspaceCount")),
             observed_workspace_count=len(members),
             observed_item_count=sum(len(workspace_items(w)) for w in members),
             workspace_scope_limited=bool(cap_raw.get("workspaceScopeLimited")), **fca.get(cid, {}))
        emit("gold_graph_nodes", node_id=cid, node_name=cap_names[cid], node_type="Capacity",
             capacity_id=cid, capacity_name=cap_names[cid], status="unknown",
             importance=len(members) + 1, kpi_label="Observed workspaces", kpi_value=str(len(members)))
    item_index: dict[str, tuple[str, str, str, str]] = {}
    for workspace in workspaces:
        wid = str(workspace.get("id") or workspace.get("objectId") or "").lower()
        if not wid:
            continue
        name = workspace.get("name") or workspace.get("displayName") or wid
        cid = str(workspace.get("capacityId") or "").lower()
        items = workspace_items(workspace)
        items_known = workspace_items_available(workspace) and any(
            isinstance(workspace.get(key), list) for key in
            ("items", "datasets", "reports", "dataflows", "Dataflow2", "notebooks", "Notebook",
             "DataPipeline", "Lakehouse", "lakehouses", "pipelines", "SQLAnalyticsEndpoint", "Warehouse")
        )
        known_items = {str(item.get("id") or "").lower() for item in items}
        items = [*items, *[{**model, "type": "SemanticModel"} for model in model_inventory
                          if str(model.get("workspaceId") or model.get("groupId") or "").lower() == wid
                          and str(model.get("id") or "").lower() not in known_items]]
        counts = Counter(item.get("type") for item in items)
        users = workspace_users(workspace) or []
        admins = [u for u in users if (u.get("groupUserAccessRight") or u.get("role") or "").lower() == "admin"]
        for family, known in (
            ("workspace_items", items_known), ("workspace_membership", workspace_users(workspace) is not None),
        ):
            emit("gold_source_coverage", workspace_id=wid, workspace_name=name,
                 evidence_family=family, collection_status="collected" if known else "unknown",
                 source_module="collectors.workspace_evidence", source_contract="far-workspace-inventory-v1",
                 freshness_status="unknown",
                 notice="Collected static inventory." if known else "Inventory component unavailable; not evidence of emptiness.")
        environment = resolve_rule(frozen, "GOV-001", {"id": wid, "capacityId": cid})["environment"] if frozen else "unknown"
        declared = environment != "unknown"
        emit("gold_workspaces", workspace_id=wid, workspace_name=name, capacity_id=cid,
             on_capacity=bool(cid), item_count=len(items) if items_known else None,
             admin_count=len(admins) if workspace_roles_complete(workspace) else None,
             description=workspace.get("description"), environment=environment if declared else None,
             profile_reason="declared_frozen_configuration" if declared else "undeclared",
             item_type_counts_json=json.dumps(counts))
        scoped = [f for f in findings if wid in _ids(f["evidence"])]
        failures = [f for f in scoped if f["is_scored"] and f.get("status") == "fail"]
        for finding in scoped:
            guide = explain(finding)
            emit("gold_finding_targets", workspace_id=wid, workspace_name=name,
                 **{k: finding.get(k) for k in ("rule_id", "rule_kind", "dimension", "severity",
                                              "status", "title", "recommendation")},
                 reason_code=guide["reason_code"], status_meaning=guide["status_meaning"],
                 review_suggested=guide["review_suggested"],
                 next_action=guide["next_action"], why_it_matters=guide["why_it_matters"],
                 how_to_fix=guide["how_to_fix"], configure=guide["configure"],
                 severity_rank=SEVERITY_RANK.get(finding.get("severity"), 0),
                 is_fail=int(finding in failures), is_scored=int(finding["is_scored"]))
        risk = min(100, sum(SEVERITY_RANK.get(f.get("severity"), 0) * 10 for f in failures))
        status = "critical" if risk >= 60 else "warning" if failures else "healthy" if any(f["is_scored"] for f in scoped) else "unknown"
        if status == "unknown":
            risk = None
        emit("gold_workspace_risk", workspace_id=wid, workspace_name=name, capacity_id=cid,
             capacity_name=cap_names.get(cid), owner="",
             item_count=len(items) if items_known else None, issue_count=len(failures),
             critical_count=sum(f.get("severity") == "critical" for f in failures),
             high_count=sum(f.get("severity") == "high" for f in failures), risk_score=risk,
             status=status, status_rank=2 if failures else 0,
             **{kind + "_count": counts.get(item_type, 0) if items_known else None for kind, item_type in (
                 ("semantic_model", "SemanticModel"), ("report", "Report"), ("notebook", "Notebook"),
                 ("pipeline", "DataPipeline"), ("lakehouse", "Lakehouse"))})
        emit("gold_graph_nodes", node_id=wid, node_name=name, node_type="Workspace",
             workspace_id=wid, workspace_name=name, capacity_id=cid, capacity_name=cap_names.get(cid),
             status=status, issue_count=len(failures), critical_count=sum(f.get("severity") == "critical" for f in failures),
             risk_score=risk, importance=len(items) + 1, kpi_label="Items",
             kpi_value=str(len(items)) if items_known else "Unknown")
        if cid in cap_names:
            emit("gold_graph_edges", edge_id=hashlib.sha256(f"{cid}|{wid}".encode()).hexdigest(),
                 source_id=cid, source_name=cap_names[cid], source_type="Capacity",
                 target_id=wid, target_name=name, target_type="Workspace",
                 relationship="hosts", is_lineage=False)
        for item in items:
            iid, kind = str(item.get("id") or ""), item.get("type") or "Item"
            if not iid:
                continue
            iname = item.get("name") or iid
            item_index[iid.lower()] = (wid, name, iname, kind)
            emit("gold_capacity_items", capacity_id=cid, capacity_name=cap_names.get(cid), sku=cap_skus.get(cid),
                 workspace_id=wid, workspace_name=name, item_id=iid, item_name=iname, item_type=kind,
                 workspace_scope_limited=bool(cap_raw.get("workspaceScopeLimited")))
            dimensions = {"SemanticModel": ("gold_semantic_models", "model"),
                          "Report": ("gold_reports", "report"), "Notebook": ("gold_notebooks", "notebook"),
                          "DataPipeline": ("gold_pipelines", "pipeline"), "Lakehouse": ("gold_lakehouses", "lakehouse")}
            if kind in dimensions:
                table, prefix = dimensions[kind]
                definition = model_metadata.get((wid, iid.lower()), {})
                modes = definition.get("storageModes") if definition.get("metadataOnly") is True else []
                modes = [m for m in modes or [] if m in {"Import", "DirectLake", "DirectQuery"}]
                mode = "Composite" if len(set(modes)) > 1 else next(iter(modes), None)
                posture: dict[str, Any] = {}
                if kind == "SemanticModel":
                    state = model_state(definition or None)
                    posture = {"rls_status": state["rls"], "ols_status": state["ols"],
                               "security_inspection": state["inspection"]}
                    required = {k: iid.lower() in declared_models[k] for k in SECURITY_KINDS}
                    fresh = emit("gold_model_security", workspace_id=wid, workspace_name=name, capacity_id=cid,
                         capacity_name=cap_names.get(cid), model_id=iid, model_name=iname, **posture,
                         rls_required=required["rls"], ols_required=required["ols"],
                         rls_outcome=requirement_outcome(required["rls"], state, "rls"),
                         ols_outcome=requirement_outcome(required["ols"], state, "ols"),
                         inspection_reason=state["reason"], rls_source=state["rls_source"],
                         role_count=state["role_count"], reader_role_count=state["reader_role_count"],
                         rls_filter_count=state["rls_filter_count"], ols_table_count=state["ols_table_count"],
                         ols_column_count=state["ols_column_count"],
                         roles_without_row_filters=state["roles_without_row_filters"],
                         role_detail=state["role_detail"])
                    for role in model_roles(definition) if fresh else []:
                        emit("gold_model_security_roles", workspace_id=wid, workspace_name=name, capacity_id=cid,
                             capacity_name=cap_names.get(cid), model_id=iid, model_name=iname, **role)
                emit(table, workspace_id=wid, workspace_name=name, **posture,
                     **{prefix + "_id": iid, prefix + "_name": iname},
                     **({"model_id": str(item["datasetId"]).lower()} if kind == "Report" and item.get("datasetId") else {}),
                     storage_mode=mode or (item.get("storageMode") if item.get("storageMode") in {
                         "Import", "DirectLake", "DirectQuery", "Composite", "Abf"} else None))
            emit("gold_graph_nodes", node_id=iid, node_name=iname, node_type=kind,
                 workspace_id=wid, workspace_name=name, capacity_id=cid,
                 status="unknown", issue_count=0, importance=1)
            emit("gold_graph_edges", edge_id=hashlib.sha256(f"{wid}|{iid}".encode()).hexdigest(),
                 source_id=wid, source_name=name, source_type="Workspace",
                 target_id=iid, target_name=iname, target_type=kind, relationship="contains", is_lineage=False)
            if kind == "Report" and item.get("datasetId"):
                emit("gold_lineage_edges", model_id=item["datasetId"], report_id=iid, report_name=iname)
                emit("gold_graph_edges", edge_id=hashlib.sha256(f"{item['datasetId']}|{iid}".encode()).hexdigest(),
                     source_id=item["datasetId"], source_type="SemanticModel", target_id=iid,
                     target_name=iname, target_type="Report", relationship="feeds", is_lineage=True)
        for admin in admins if workspace_roles_complete(workspace) else []:
            owner_id = admin.get("graphId") or admin.get("identifier")
            if owner_id:
                emit("gold_owners", owner_id=owner_id, owner_name=admin.get("displayName") or owner_id)
                emit("gold_owner_edges", owner_id=owner_id, owner_name=admin.get("displayName") or owner_id,
                     workspace_id=wid, workspace_name=name)
    for finding in findings:
        # A signal that does not suggest a review has nothing to look at.
        if finding.get("status") == "info" and finding["evidence"].get("review_recommended") is False:
            continue
        why: dict[str, list[str]] = {}
        for key, reason in ITEM_EVIDENCE.items():
            for item_id in finding["evidence"].get(key) or []:
                if isinstance(item_id, str) and item_id:
                    why.setdefault(item_id.lower(), []).append(reason)
        if finding.get("rule_id") in VIOLATION_ITEMS:
            for violation in finding["evidence"].get("violations") or []:
                if isinstance(violation, dict) and isinstance(violation.get("item_id"), str) and violation["item_id"]:
                    why.setdefault(violation["item_id"].lower(), []).append(VIOLATION_ITEMS[finding["rule_id"]])
        guide = explain(finding) if why else {}
        for item_id, reasons in sorted(why.items()):
            home = (finding["evidence"].get("workspace_id"), finding["evidence"].get("workspace_name"), item_id, "Unknown")
            wid, wname, iname, kind = item_index.get(item_id, home)
            if item_id not in item_index:
                reasons = [*reasons, "Not in the reviewed inventory, so it may have been deleted"]
            emit("gold_finding_items", workspace_id=wid, workspace_name=wname,
                 item_id=item_id, item_name=iname, item_type=kind, reason="; ".join(dict.fromkeys(reasons)),
                 **{k: finding.get(k) for k in ("rule_id", "rule_kind", "dimension", "severity", "status", "title", "recommendation")},
                 severity_rank=SEVERITY_RANK.get(finding.get("severity"), 0),
                 is_fail=int(finding.get("status") == "fail" and finding["is_scored"]),
                 is_scored=int(finding["is_scored"]), review_suggested=guide["review_suggested"])
    far = _load(raw, "far_evidence.json")
    if findings:
        _emit_tenant_settings(emit, findings, _load(raw, "tenant_settings.json"), far, run_timestamp)
    for row in far.get("coverage") or []:
        family = row.get("family") or row.get("table") or row.get("source") or ""
        if family not in {
            "workspaces", "active_items", "capacities", "resources", "tenant_settings", "fuam", "fca",
            *ENRICHMENT_FAMILIES,
        }:
            continue
        notice, action = collection_guidance(row.get("status"), row.get("reason"))
        emit("gold_source_coverage", evidence_family=family, source_module=row.get("source"),
             source_table=row.get("table"), source_contract=f"far-context-v1:{family}",
             collection_status=row.get("status") or "unknown", notice=notice or row.get("reason"),
             next_action=action, observed_at=far.get("captured_at_utc"), freshness_status="unknown")
    plan = _load(raw, "collection_plan.json")
    if plan.get("profile_id") == PROFILE_ID and plan.get("profile_sha256") == PROFILE_SHA256:
        for row in plan.get("outcomes") or []:
            module = row.get("collector")
            if module not in {
                "collectors.scanner_api", "collectors.workspace_inventory", "collectors.git_integration",
                "collectors.capacity_metrics", "collectors.semantic_models", "collectors.semantic_model_definitions",
                "collectors.pipelines_notebooks", "collectors.pipeline_definitions", "collectors.gateways",
                "collectors.far_evidence", "collectors.deployment_pipelines", "collectors.onelake_roles",
                "collectors.workspace_network",
            }:
                continue
            notice, action = collection_guidance(row.get("status"), row.get("reason") or row.get("error"))
            emit("gold_source_coverage", evidence_family=module.removeprefix("collectors."),
                 source_module=row.get("data_source") or module, source_contract="far-native-v1",
                 collection_status=row.get("status"), notice=notice, next_action=action,
                 observed_at=plan.get("captured_at_utc"), freshness_status="capture_time_only")
    # Rules that could not decide are explained on the Action plan; coverage rules report here.
    for finding in findings:
        if finding["rule_kind"] == "coverage":
            guide = explain(finding)
            emit("gold_source_coverage", evidence_family=finding["rule_id"],
                 collection_status=finding.get("status"),
                 notice=rule_catalog.get(finding["rule_id"], {}).get("what_it_checks", guide["status_meaning"]),
                 next_action=guide["next_action"], source_module="FAR")
    severity_groups = Counter((f.get("dimension"), f.get("severity")) for f in findings
                              if f["is_scored"] and f.get("status") == "fail")
    for (dimension, severity), count in severity_groups.items():
        emit("gold_severity_matrix", dimension=dimension, severity=severity,
             severity_rank=SEVERITY_RANK.get(severity, 0), status="fail", issue_count=count,
             weighted_risk=count * SEVERITY_RANK.get(severity, 0))
    emit("gold_release", **build_release_record(deployed_version, repo_url, branch, check_remote=check_remote))
    return tables


def build_gold_from_dir(findings_dir: str | Path, raw_dir: str | Path | None = None, **kwargs: Any) -> dict:
    findings = []
    root = Path(findings_dir)
    paths = [root / "findings.json"] if (root / "findings.json").exists() else sorted((root / "findings").glob("*.json"))
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        findings.extend(payload if isinstance(payload, list) else payload.get("findings") or [])
    return build_gold(findings, raw_dir or root / "raw", **kwargs)

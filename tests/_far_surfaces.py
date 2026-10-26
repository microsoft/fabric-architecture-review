# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Current-profile consumption fixtures, deliberately containing stale subjects."""
import json
from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256

WID = "11111111-1111-1111-1111-111111111111"
MID = "22222222-2222-2222-2222-222222222222"
CID = "33333333-3333-3333-3333-333333333333"
META = {"run_id": "review", "run_timestamp": "2026-09-01T00:00:00Z",
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "configuration_sha256": "configuration"}
FORBIDDEN_TABLES = {"gold_dax_models", "gold_dax_measures", "gold_dax_objects",
                    "gold_dax_object_coverage", "gold_model_tables", "gold_model_columns",
                    "gold_item_executions", "gold_execution_coverage", "gold_cost_metrics",
                    "gold_dataflow_queries", "owner_executions"}


def finding(rule="ARCH-001", status="fail", **extra):
    return {**META, "rule_id": rule, "status": status, "dimension": "architecture",
            "severity": "high", "title": "Declared architecture control",
            "recommendation": "Review the declared boundary.",
            "evidence": {"workspace_id": WID}, **extra}


def raw(path, monkeypatch):
    monkeypatch.delenv("FAR_CONFIG_SNAPSHOT", raising=False)
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    payloads = {
        "run_manifest.json": META,
        "workspace_inventory.json": {"workspaceListComplete": True, "workspaces": [
            {"id": WID, "name": "Architecture", "capacityId": CID, "items": [
                {"id": MID, "name": "Model", "type": "SemanticModel"}], "itemsComplete": True,
             "users": [], "usersComplete": True}]},
        "capacity_metrics.json": {"capacities": [{"id": CID, "displayName": "Hosting", "sku": "F64",
                                                   "utilization": 999, "cost": 12345}]},
        "semantic_model_definitions.json": {"models": [
            {"id": MID, "workspaceId": WID, "metadataOnly": True, "storageModes": ["DirectLake"],
             "expression": "DO-NOT-EXPOSE-EXPRESSION", "parts": []}]},
        "dax_analysis.json": {"expression": "DO-NOT-EXPOSE-EXPRESSION"},
        "vertipaq_stats.json": {"total_size": 9999999},
    }
    for name, payload in payloads.items():
        (path / name).write_text(json.dumps(payload), encoding="utf-8")
    return path


def owner_input():
    return {
        "gold_workspaces": [{**META, "workspace_id": WID, "workspace_name": "Architecture"}],
        "gold_run_summary": [{**META}],
        "gold_semantic_models": [{**META, "workspace_id": WID, "model_id": MID,
                                  "model_name": "Model", "storage_mode": "DirectLake"}],
        "gold_finding_targets": [{**finding(), "workspace_id": WID, "is_scored": 1}],
        "gold_source_coverage": [{**META, "workspace_id": WID, "evidence_family": "definitions",
                                  "collection_status": "partial", "source_contract": "far-native-v1"}],
        "gold_dax_measures": [{"workspace_id": WID, "expression": "DO-NOT-EXPOSE-EXPRESSION"}],
        "gold_item_executions": [{"workspace_id": WID, "duration_ms": 9999999}],
    }

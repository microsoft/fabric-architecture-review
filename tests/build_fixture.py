# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate the committed test fixture as fully-synthetic mock data.

This is a developer convenience, not part of the runtime. It writes a small,
**entirely invented** raw corpus under ``tests/fixtures/sample/raw`` that the
golden-file tests run the analyzers against.

Design goals:
  * No customer data, ever. Every value here is made up from scratch — there
    is no real engagement folder to read and nothing to scrub. The only
    realistic strings are *standard* Microsoft tenant-setting names and Fabric
    enum values, which the analyzers must match literally.
  * Lean. Just enough workspaces / models / events to exercise each analyzer's
    pass / fail / info branches, so the rendered sample report stays compact.
  * Deterministic. Stable fake GUIDs and timestamps so regenerating produces a
    byte-identical fixture (golden stays stable).

Usage:
    python -m tests.build_fixture            # writes tests/fixtures/sample/raw
"""
from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# A fixed "now" so timestamps (and therefore staleness/age findings) never drift.
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


# --- Obviously-synthetic, deterministic GUIDs -------------------------------
def guid(n: int) -> str:
    """Return a stable, clearly-fake GUID for a small integer tag."""
    return f"{n:08x}-0000-4000-8000-{n:012x}"


WS1, WS2, WS3 = guid(11), guid(12), guid(13)          # workspaces
CAP1, CAP2 = guid(21), guid(22)                       # capacities
DS_A, DS_B, DS_C = guid(31), guid(32), guid(33)       # datasets / models
LH1, LH2 = guid(41), guid(42)                         # lakehouses
SG1, SG2 = guid(51), guid(52)                         # security groups
NB1, NB2 = guid(61), guid(62)                         # notebooks
PIPE1 = guid(71)                                       # deployment pipeline
GW1 = guid(81)                                         # gateway

USER1 = "user-0001@contoso.example"
USER2 = "user-0002@contoso.example"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# --- Synthetic notebook + TMDL payloads -------------------------------------
_DIRTY_NB = """# Fabric notebook source

# CELL ********************

%pip install pandas
client_secret = "PLACEHOLDER-NOT-A-REAL-SECRET"
df = spark.read.load("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/t")
rows = df.collect()
dbutils.fs.ls("/dbfs/tmp")
df.write.format("parquet").save("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/out")
"""

_CLEAN_NB = """# Fabric notebook source

# CELL ********************

df = spark.read.table("lakehouse.sales")
df.limit(10).toPandas()
df.write.format("delta").mode("append").saveAsTable("gold.sales")
"""

# Import model with Direct Lake blockers: calculated column, non-friendly M
# connector (Web.Contents), no lakehouse (entityName) binding.
_TMDL_IMPORT_BLOCKED = (
    "table SalesFact\n"
    "\tcolumn Amount\n"
    "\t\tdataType: double\n"
    "\tcolumn Margin = SalesFact[Amount] * 0.1\n"
    "\tpartition SalesFact = m\n"
    "\t\tmode: import\n"
    "\t\tsource = let Source = Web.Contents(\"https://example.invalid/data\") in Source\n"
)

# Import model that is a clean Direct Lake candidate: lakehouse binding via
# entityName + a Direct-Lake-friendly connector, no calculated objects.
_TMDL_IMPORT_CANDIDATE = (
    "table SalesFact\n"
    "\tcolumn Amount\n"
    "\t\tdataType: double\n"
    "\tpartition SalesFact = entity\n"
    "\t\tmode: import\n"
    "\t\tentityName: SalesFact\n"
    "\t\tsource = Lakehouse.Contents([])\n"
)

# Direct Lake model with no explicit directLakeBehavior (implicit automatic).
_TMDL_DIRECTLAKE = (
    "table SalesFact\n"
    "\tcolumn Amount\n"
    "\t\tdataType: double\n"
    "\tpartition SalesFact = entity\n"
    "\t\tmode: directLake\n"
    "\t\tentityName: SalesFact\n"
)


def _tenant_settings() -> dict[str, Any]:
    def s(name: str, enabled: bool, groups: list[str] | None = None) -> dict[str, Any]:
        return {
            "settingName": name,
            "title": name,
            "enabled": enabled,
            "canSpecifySecurityGroups": True,
            "enabledSecurityGroups": groups or [],
            "excludedSecurityGroups": [],
        }

    return {
        "tenantSettings": [
            s("PublishToWeb", True),                       # enabled, unscoped -> fail
            s("ExportData", False),                        # disabled -> pass
            s("CreateFabricItem", True, [SG1]),            # scoped -> pass
            s("ServicePrincipalAccess", True, [SG1]),      # scoped -> pass
            s("AllowExternalDataSharing", True),           # enabled, unscoped -> fail
            s("CustomVisualsTenantSettings", True, [SG1]), # scoped -> pass
            s("RScriptVisualsTenantSettings", False),      # disabled -> pass
            # AllowGuestUserToAccessSharedContent omitted -> info (missing)
        ]
    }


def _scanner() -> dict[str, Any]:
    def user(identifier: str, right: str, ptype: str) -> dict[str, Any]:
        return {
            "identifier": identifier,
            "displayName": identifier.split("@")[0],
            "groupUserAccessRight": right,
            "principalType": ptype,
            "type": ptype,
        }

    def item(name: str, label: str | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {"name": name}
        if label:
            d["sensitivityLabel"] = label
        return d

    ws_bronze = {
        "id": WS1,
        "name": "data-bronze-dev",
        "type": "Workspace",
        "state": "Active",
        "isOnDedicatedCapacity": True,
        "capacityId": CAP1,
        "description": "Bronze ingestion layer. Owner: Data Platform team.",
        "users": [
            user("data-platform-admins", "Admin", "Group"),
            user(USER1, "Admin", "User"),  # individual admin -> SEC-004 fail
        ],
        "datasets": [{"id": DS_A, "name": "SalesModel"}],
        "reports": [{"id": guid(101), "name": "Sales Overview", "datasetId": DS_A}],
        "lakehouses": [{"id": LH1, "displayName": "bronze_lh", "name": "bronze_lh"}],
        "items": [item("SalesModel", "Confidential"), item("Sales Overview")],
    }
    ws_gold = {
        "id": WS2,
        "name": "data-gold-prod",
        "type": "Workspace",
        "state": "Active",
        "isOnDedicatedCapacity": True,
        "capacityId": CAP2,
        "description": "Gold serving layer. Owner: Analytics team.",
        "users": [
            user("analytics-admins", "Admin", "Group"),
            user("platform-admins", "Admin", "Group"),
        ],
        "datasets": [{"id": DS_B, "name": "FinanceModel"}, {"id": DS_C, "name": "DirectLakeModel"}],
        "lakehouses": [{"id": LH2, "displayName": "gold_lh", "name": "gold_lh"}],
        "items": [item("FinanceModel", "Confidential"), item("DirectLakeModel")],
    }
    ws_personal = {
        "id": WS3,
        "name": "My workspace",
        "type": "PersonalGroup",   # -> ARCH-008 fail
        "state": "Active",
        "isOnDedicatedCapacity": False,
        "capacityId": None,
        "description": "",
        "users": [user(USER2, "Admin", "User")],
        "items": [],
    }
    return {
        "_meta": {
            "complete": True,
            "workspaces_collected": 3,
            "workspaces_eligible": 3,
            "batches_total": 1,
            "batches_failed": 0,
            "failed_batch_numbers": [],
            "scoped": False,
        },
        "workspaces": [ws_bronze, ws_gold, ws_personal],
        "misconfiguredDatasourceInstances": [
            {"datasourceId": guid(111), "datasourceType": "Sql"},
        ],
    }


def _workspace_inventory() -> dict[str, Any]:
    return {
        "workspaces": [
            {"id": WS1, "name": "data-bronze-dev", "items": [{"name": "SalesModel"}, {"name": "Sales Overview"}]},
            {"id": WS2, "name": "data-gold-prod", "items": [{"name": "FinanceModel"}, {"name": "DirectLakeModel"}]},
            {"id": WS3, "name": "My workspace", "items": []},
        ]
    }


def _semantic_models() -> dict[str, Any]:
    datasets = [
        {
            "id": DS_A, "name": "SalesModel", "workspaceId": WS1, "workspaceName": "data-bronze-dev",
            "targetStorageMode": "Abf", "sizeInBytes": 2_684_354_560,  # ~2.5 GB -> PERF-003 fail
            "isRefreshable": True,
        },
        {
            "id": DS_B, "name": "FinanceModel", "workspaceId": WS2, "workspaceName": "data-gold-prod",
            "targetStorageMode": "Abf", "sizeInBytes": 524_288_000,  # 500 MB
            "isRefreshable": True,
        },
        {
            "id": DS_C, "name": "DirectLakeModel", "workspaceId": WS2, "workspaceName": "data-gold-prod",
            "targetStorageMode": "DirectLake", "sizeInBytes": 104_857_600,
            "isRefreshable": True,
        },
    ]
    # SalesModel: 1 failed of 2 recent -> PERF-004 fail; last success 5d ago.
    refreshes = {
        DS_A: [
            {"id": guid(201), "status": "Failed", "refreshType": "Scheduled",
             "startTime": _iso(_days_ago(5)), "endTime": _iso(_days_ago(5) + timedelta(minutes=20))},
            {"id": guid(202), "status": "Completed", "refreshType": "Scheduled",
             "startTime": _iso(_days_ago(5)), "endTime": _iso(_days_ago(5) + timedelta(minutes=40))},
        ],
        # FinanceModel: only on-demand, no scheduled -> PERF-006 fail.
        DS_B: [
            {"id": guid(203), "status": "Completed", "refreshType": "OnDemand",
             "startTime": _iso(_days_ago(2)), "endTime": _iso(_days_ago(2) + timedelta(minutes=10))},
        ],
        DS_C: [
            {"id": guid(204), "status": "Completed", "refreshType": "Scheduled",
             "startTime": _iso(_days_ago(1)), "endTime": _iso(_days_ago(1) + timedelta(minutes=5))},
        ],
    }
    return {"datasets": datasets, "refreshes": refreshes}


def _semantic_model_definitions() -> dict[str, Any]:
    def model(mid: str, name: str, ws: str, tmdl: str, mode: str) -> dict[str, Any]:
        return {
            "id": mid, "displayName": name, "workspaceName": ws, "error": None,
            "targetStorageMode": mode,
            "parts": [{"path": "definition/tables/SalesFact.tmdl", "payloadType": "InlineText", "text": tmdl}],
        }

    return {
        "models": [
            model(DS_A, "SalesModel", "data-bronze-dev", _TMDL_IMPORT_BLOCKED, "Abf"),
            model(DS_B, "FinanceModel", "data-gold-prod", _TMDL_IMPORT_CANDIDATE, "Abf"),
            model(DS_C, "DirectLakeModel", "data-gold-prod", _TMDL_DIRECTLAKE, "DirectLake"),
        ]
    }


def _dax_analysis() -> dict[str, Any]:
    expression = "SUMX(FILTER(SalesFact, SalesFact[Amount] > 0), SUMX(CROSSJOIN(Products, Stores), SalesFact[Amount]))"
    return {
        "available": True,
        "metadata_only": True,
        "models_scanned": 3,
        "definition_errors": 0,
        "measures": [{
            "model_id": DS_A,
            "model_name": "SalesModel",
            "workspace_id": WS1,
            "workspace_name": "data-bronze-dev",
            "table_name": "SalesFact",
            "measure_name": "Potentially Expensive Sales",
            "expression": expression,
            "expression_length": len(expression),
            "risk_score": 80,
            "risk_level": "high",
            "signals": [
                {"code": "nested_iterators", "category": "iteration", "points": 25, "message": "Expression contains iterator calls."},
                {"code": "crossjoin", "category": "cardinality", "points": 35, "message": "CROSSJOIN can create a large intermediate row set."},
                {"code": "whole_table_filter", "category": "filtering", "points": 20, "message": "FILTER iterates a whole table."},
            ],
        }],
    }


def _capacity_metrics() -> dict[str, Any]:
    return {
        "capacityCount": 2,
        "capacities": [
            {"id": CAP1, "displayName": "Capacity Dev", "sku": "F2", "state": "Active",
             "region": "North Europe", "assignedWorkspaceCount": 1, "admins": [], "workloads": []},
            {"id": CAP2, "displayName": "Capacity Prod", "sku": "F64", "state": "Active",
             "region": "North Europe", "assignedWorkspaceCount": 2, "admins": [], "workloads": []},
        ],
    }


def _capacity_metrics_app() -> dict[str, Any]:
    def probe(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {"ok": True, "status": 200, "error": None, "rowCount": len(rows or []), "rows": rows or []}

    usage = [{
        "Capacity Id": CAP2, "Health": "Healthy", "Average CU %": 78.0,
        "P95 interactive rejection": 0, "P95 background rejection": 85,  # -> PERF-001 fail
        "P95 interactive delay": 0, "Processed overage": 0, "Overage billing limit": 0,
    }]
    return {
        "datasetLocated": True,
        "dataset": {"workspaceId": WS2, "name": "Fabric Capacity Metrics"},
        "queries": {
            "info_tables": probe([]),
            "info_measures": probe([]),
            "usage_summary_7d": probe(usage),
            "usage_summary_24h": probe(usage),
            "usage_summary_1h": probe(usage),
            "items_throttled": probe([]),
        },
    }


def _gateways() -> dict[str, Any]:
    return {
        "gatewayCount": 1,
        "gateways": [
            {
                "id": GW1, "name": "on-prem-cluster", "gatewayType": "OnPremises",
                "datasourceCount": 2, "memberCount": 1,  # single member + datasources -> SEC-008 fail
                "members": [{"id": guid(91), "name": "gw-member-1", "version": "3000.100", "status": "Live"}],
            }
        ],
    }


def _git_integration() -> dict[str, Any]:
    return {
        "workspaces": [
            {
                "workspaceId": WS2, "workspaceName": "data-gold-prod", "connected": True,
                "gitConnectionState": "ConnectedAndInitialized",
                "gitProviderDetails": {
                    "organizationName": "contoso-org", "projectName": "fabric",
                    "gitProviderType": "AzureDevOps", "repositoryName": "fabric-repo", "branchName": "main",
                },
                "gitSyncDetails": {"head": "0000000000", "lastSyncTime": _iso(_days_ago(3))},
            },
            {"workspaceId": WS1, "workspaceName": "data-bronze-dev", "connected": False,
             "gitConnectionState": "NotConnected"},
        ]
    }


def _activity_logs() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    base = _days_ago(2)
    plan = [
        ("RefreshDataset", WS1, USER1),
        ("RefreshDataset", WS2, USER2),
        ("ViewReport", WS1, USER1),
        ("ViewReport", WS2, USER2),
        ("CreateNotebook", WS1, USER1),
        ("ShareReport", WS2, USER2),
        ("ShareDashboard", WS2, USER1),
        ("UpdateSharePermissions", WS1, USER2),
        ("RunNotebook", WS1, USER1),
        ("RefreshDataset", WS2, USER2),
        ("ViewDashboard", WS2, USER1),
        ("ExportReport", WS1, USER2),
    ]
    for i, (op, ws, who) in enumerate(plan):
        events.append({
            "Id": guid(300 + i),
            "CreationTime": _iso(base + timedelta(hours=i)),
            "Operation": op,
            "Activity": op,
            "UserId": who,
            "UserKey": who,
            "WorkspaceId": ws,
        })
    return {"windowDays": 7, "fetchedAt": _iso(NOW), "eventCount": len(events), "events": events}


def _deployment_pipelines() -> dict[str, Any]:
    return {
        "pipelineCount": 1,
        "pipelines": [
            {
                "id": PIPE1, "displayName": "Sales Pipeline", "stageCount": 2,
                "stages": [
                    {"order": 0, "displayName": "Development", "workspaceId": WS1},
                    {"order": 1, "displayName": "Production", "workspaceId": WS2,
                     "lastDeploymentTime": _iso(_days_ago(10))},
                ],
                "assignedWorkspaceIds": [WS1, WS2],
            }
        ],
    }


def _pipeline_definitions() -> dict[str, Any]:
    def nb(nid: str, name: str, ws: str, src: str) -> dict[str, Any]:
        return {
            "id": nid, "displayName": name, "workspaceId": WS1, "workspaceName": ws,
            "parts": [{"path": "notebook-content.py", "payloadType": "InlineBase64", "payload": _b64(src)}],
        }

    return {
        "pipelines": [],
        "notebooks": [
            nb(NB1, "ProcessSales", "data-bronze-dev", _DIRTY_NB),
            nb(NB2, "PublishGold", "data-gold-prod", _CLEAN_NB),
        ],
    }


def _lakehouse_warehouse() -> dict[str, Any]:
    return {
        "lakehouses": [
            {"id": LH1, "name": "bronze_lh", "displayName": "bronze_lh",
             "workspaceId": WS1, "workspaceName": "data-bronze-dev"},
            {"id": LH2, "name": "gold_lh", "displayName": "gold_lh",
             "workspaceId": WS2, "workspaceName": "data-gold-prod"},
        ],
        "warehouses": [],
        "tables": {
            LH1: [{"name": "sales_fact", "tableName": "sales_fact"}],
            LH2: [{"name": "sales_fact", "tableName": "sales_fact",
                   "shortcutMetadata": {"source": "bronze_lh"}}],
        },
    }


def _best_practices() -> dict[str, Any]:
    """Synthetic BPA / Direct Lake / Delta / capacity health corpus."""
    return {
        "available": True,
        "notes": None,
        "models": [
            {"model_name": "Sales DL", "model_bpa": [{"rule": "Avoid bi-di"}, {"rule": "Hide keys"}],
             "fallback": [{"reason": "GuardrailLimit"}], "delta": [{"table": "fact_sales", "smallFiles": 9}],
             "unused": [{"object": "ColZZ"}]},
            {"model_name": "Finance Import", "model_bpa": [], "fallback": [], "delta": [], "unused": []},
        ],
        "reports": [
            {"report_name": "Exec Dash", "report_bpa": [{"rule": "Too many visuals"}]},
            {"report_name": "Ops", "report_bpa": []},
        ],
        "capacities": [
            {"capacity": "P1-legacy", "sku": "P1", "needs_migration": True},
            {"capacity": "F64", "sku": "F64", "needs_migration": False},
        ],
        "errors": [],
    }


def _azure_capacity_automation() -> dict[str, Any]:
    return {
        "subscriptionsScanned": 1,
        "skipped": False,
        "pauseAutomations": [],
        "pauseCandidates": [],
    }


def _empty_files() -> dict[str, dict[str, Any]]:
    """Files the analyzers do not read but the collectors normally emit."""
    return {
        "vertipaq_stats.json": {"models": []},
        "realtime_intelligence.json": {"eventhouses": [], "kqlDatabases": [], "eventstreams": []},
        "pipelines_notebooks.json": {"pipelines": [], "notebooks": []},
    }


def _corpus() -> dict[str, Any]:
    files = {
        "tenant_settings.json": _tenant_settings(),
        "scanner.json": _scanner(),
        "workspace_inventory.json": _workspace_inventory(),
        "semantic_models.json": _semantic_models(),
        "semantic_model_definitions.json": _semantic_model_definitions(),
        "dax_analysis.json": _dax_analysis(),
        "capacity_metrics.json": _capacity_metrics(),
        "capacity_metrics_app.json": _capacity_metrics_app(),
        "gateways.json": _gateways(),
        "git_integration.json": _git_integration(),
        "activity_logs.json": _activity_logs(),
        "deployment_pipelines.json": _deployment_pipelines(),
        "pipeline_definitions.json": _pipeline_definitions(),
        "lakehouse_warehouse.json": _lakehouse_warehouse(),
        "azure_capacity_automation.json": _azure_capacity_automation(),
        "best_practices.json": _best_practices(),
    }
    files.update(_empty_files())
    return files


def build(out_dir: Path) -> int:
    raw_out = out_dir / "raw"
    raw_out.mkdir(parents=True, exist_ok=True)
    files = _corpus()
    for name, data in sorted(files.items()):
        (raw_out / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tests/fixtures/sample", help="Fixture output folder.")
    args = parser.parse_args()
    n = build(Path(args.out))
    print(f"Wrote {n} synthetic raw file(s) to {args.out}/raw")


if __name__ == "__main__":
    main()

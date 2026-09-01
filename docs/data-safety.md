# Data Safety Statement

`fabric-arch-review` is engineered so that **no customer data is ever read, queried, downloaded, or otherwise touched** by this tool. Every collector reads metadata, configuration, inventory, or aggregate metrics only.

This document lists the **exact** API endpoints and DMVs that are allowed, and the surfaces that are explicitly **forbidden**.

---

## Allowed sources (metadata / configuration / metrics only)

### Fabric / Power BI Admin REST

| Endpoint | Purpose |
|---|---|
| `GET https://api.fabric.microsoft.com/v1/admin/tenantsettings` | Tenant-wide feature configuration |
| `GET https://api.fabric.microsoft.com/v1/admin/capacities` | List of Fabric capacities |
| `GET https://api.fabric.microsoft.com/v1/admin/workspaces` | Workspace inventory |
| `GET https://api.fabric.microsoft.com/v1/admin/workspaces/{wsId}/users` | Workspace role assignments |
| `GET https://api.fabric.microsoft.com/v1/admin/activityevents?startDateTime=...&endDateTime=...` | Admin audit log (last 30d) |

### Power BI Scanner API (metadata only)

| Endpoint | Required parameters |
|---|---|
| `POST https://api.powerbi.com/v1.0/myorg/admin/workspaces/getInfo` | `lineage=true`, **`datasourceDetails=false`**, **`getArtifactUsers=false`**, **`datasetSchema=false`**, **`datasetExpressions=false`** |
| `GET  https://api.powerbi.com/v1.0/myorg/admin/workspaces/scanStatus/{scanId}` | — |
| `GET  https://api.powerbi.com/v1.0/myorg/admin/workspaces/scanResult/{scanId}` | — |

### Workspace-scoped REST (metadata only)

| Endpoint | Purpose |
|---|---|
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items` | Item inventory |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/lakehouses` | Lakehouse inventory |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/lakehouses/{id}/tables` | Table names + metadata (no rows) |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/warehouses` | Warehouse inventory |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items/{id}/jobs/instances` | Job run history (status + duration) |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/git/connection` | Git integration config |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/git/status` | Git sync status |
| `GET https://api.powerbi.com/v1.0/myorg/groups/{gid}/datasets/{did}/refreshes` | Refresh history (metadata) |

### OneLake DFS (filesystem listing only)

| Endpoint | Notes |
|---|---|
| `GET https://onelake.dfs.fabric.microsoft.com/{ws}/{item}/Files?resource=filesystem&recursive=true` | Returns paths + sizes + last-modified only. **No file content is downloaded.** |

### Capacity Metrics

| Source | Notes |
|---|---|
| Azure Monitor metrics for the Fabric capacity resource | Aggregate CU%, throttling counters |
| Fabric Capacity Metrics app dataset (via XMLA) | Schema queries only (`$SYSTEM.TMSCHEMA_*`); never `EVALUATE` against the metrics tables |

### Semantic model DMVs (structure only)

Allowed against the XMLA endpoint `powerbi://api.powerbi.com/v1.0/myorg/<workspace>`:

```text
SELECT * FROM $SYSTEM.TMSCHEMA_MODEL
SELECT * FROM $SYSTEM.TMSCHEMA_TABLES
SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS
SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES
SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS
SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS
SELECT * FROM $SYSTEM.DISCOVER_OBJECT_MEMORY_USAGE
SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS
```

These DMVs describe **how the model is built** (tables, columns, measures, relationships, memory footprint). They do not return business rows.

### DAX definition analysis (`collectors.dax_analysis`)

The DAX Analyzer reads TMDL or `model.bim` measure expressions from semantic-model
definitions already collected through Fabric `getDefinition`. It performs static text
analysis only: it does not execute a measure, issue `EVALUATE`, inspect a query plan, or
read model rows. Consequently its risk score describes potentially expensive syntax
patterns, not observed duration, CU consumption, or runtime cost.

Measure expressions can reveal proprietary business logic or embedded literal values.
Treat `semantic_model_definitions.json`, `dax_analysis.json`, the DAX Gold tables, and
generated expression previews as confidential review metadata. They stay within the
review workspace/output controls and must not be copied into public samples or logs.

### VertiPaq Analyzer statistics (`collectors.vertipaq_stats`, Fabric runs only)

`collectors.vertipaq_stats` uses `semantic-link-labs`' `vertipaq_analyzer` **inside a Fabric notebook** to report each model's storage footprint. By default it reads only the storage-engine DMVs above (`DISCOVER_STORAGE_TABLE_COLUMNS`, `DISCOVER_OBJECT_MEMORY_USAGE`, `TMSCHEMA_*`), which return table/column **sizes, encoding, data type and % of model** — metadata only, no data query.

Exact column **cardinality** (distinct-value counts) cannot be read from a DMV; it requires an aggregate `COUNT`/`DISTINCTCOUNT`-style query over the model. That step is therefore **opt-in**:

| Mode | Env flag | What runs | Returns |
|---|---|---|---|
| Default (metadata-only) | `VERTIPAQ_STATS_READ_DATA` unset / `false` | storage-engine DMVs only | sizes, encoding, data type, % of model |
| Cardinality (opt-in) | `VERTIPAQ_STATS_READ_DATA=true` | + aggregate COUNT-style DAX per column | the above **plus** exact distinct-value counts |

Even in the opt-in mode only **aggregate counts** leave the engine — never row values — and nothing beyond sizes and counts is persisted. Set `VERTIPAQ_STATS_SKIP=true` to disable the collector entirely.

---

## Forbidden — must never appear in this codebase

| Surface | Why it's forbidden |
|---|---|
| DAX `EVALUATE` against semantic model tables / measures | Returns customer rows |
| `SELECT ... FROM <user_table>` against Warehouse SQL endpoint | Returns customer rows |
| `SELECT ... FROM <user_table>` against Lakehouse SQL endpoint | Returns customer rows |
| Spark / notebook execution that reads tables or files | Returns customer rows |
| Downloading files from OneLake (PUT/GET on file content) | Returns customer data |
| Reading notebook cell outputs (`/notebooks/{id}/content` with outputs included) | Cell outputs may contain customer data |
| Scanner API parameters: `getArtifactUsers=true`, `datasetSchema=true`, `datasetExpressions=true`, `datasourceDetails=true` | Returns PII, M/DAX expressions that may include secrets, or credentials |
| Power BI REST `GET /datasets/{id}/executeQueries` | Executes DAX returning rows |
| Pipeline activity input/output payloads | May contain query text or row samples |

A PR that introduces any of the above must be rejected. CI runs the automated contract,
syntax, test, build, and dependency-audit gates; reviewers must also inspect changes to
data-access code in context because permitted metadata collectors can legitimately contain
terms such as `EVALUATE` and `executeQueries`.

---

## Audit-data handling

The Admin Activity Log contains user UPNs and is treated as PII:

- Raw dumps are written under `output/raw/` which is **gitignored**.
- The PDF report aggregates activity (e.g. "8 distinct users created reports in workspace X") rather than enumerating user identities.
- The engagement folder must be deleted or moved to a secure archive at the end of the engagement.

## Statement for the client

> The `fabric-arch-review` framework was used to perform this assessment. It accesses only tenant configuration, workspace and item metadata, inventory, and aggregate performance metrics. **At no point did the assessment read, copy, or transmit any customer data stored in semantic models, lakehouses, warehouses, OneLake files, or notebook outputs.**

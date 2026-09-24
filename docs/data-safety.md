<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Data Safety Statement

`fabric-arch-review` is engineered not to read **customer business rows or files**.
Collectors read metadata, configuration, inventory, or aggregate metrics. The
separately enabled targeted-review feature reads documented monitoring data and
can prepare owner-email records and send native Outlook emails containing
workspace and owner identities and a report link. The separately opt-in
[Workspace Owner report](workspace-owner-report.md) projects only curated
workspace/item technical signals and uses expiring direct-user-admin entitlements.

This document describes permitted data sources and access boundaries. The
collector source identifies the specific requests used by each feature.

## Before collecting or sharing

1. Agree the review scope and executing identity; keep optional collection
   features off unless approved.
2. Restrict access to output folders/Lakehouse artifacts and set retention.
   Metadata, contact details and source definitions can be sensitive.
3. Never commit live outputs, secrets or notebook results. Use synthetic data
   when reporting a problem.
4. Before owner sharing, complete the separate [owner access and live tests](workspace-owner-report.md#live-fabric-acceptance).
   A URL filter or sensitivity label does not establish row-level isolation.

Use [Fabric deployment](../fabric/DEPLOYMENT.md) or [local review](local-review.md)
for the steps to run FAR. The source reference below explains what those steps may read.

---

## Allowed sources (metadata / configuration / metrics only)

### Fabric / Power BI Admin REST

| Endpoint | Purpose |
|---|---|
| `GET https://api.fabric.microsoft.com/v1/admin/tenantsettings` | Tenant-wide feature configuration |
| `GET https://api.powerbi.com/v1.0/myorg/admin/capacities` | Capacity inventory |
| `GET https://api.powerbi.com/v1.0/myorg/admin/groups` | Workspace inventory |
| `GET https://api.powerbi.com/v1.0/myorg/admin/groups/{wsId}/users` | Workspace role assignments |
| `GET https://api.powerbi.com/v1.0/myorg/admin/activityevents?startDateTime=...&endDateTime=...` | Admin audit events; seven-day default, 28-day maximum lookback |

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
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/roleAssignments` | Workspace role assignments where permitted |
| `POST https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items/{id}/getDefinition` | Pipeline/notebook definitions; notebook source only, not cell outputs |
| `POST https://api.fabric.microsoft.com/v1/workspaces/{wsId}/semanticModels/{id}/getDefinition` | TMDL/BIM definitions for static model and DAX analysis |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/dataflows` | Native Dataflow Gen2 inventory; legacy scanner dataflows are not assumed to be Gen2 |
| `POST https://api.fabric.microsoft.com/v1/workspaces/{wsId}/dataflows/{id}/getDefinition` | Supported Gen2 definitions; inspected in memory and not persisted as raw M |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/lakehouses` | Lakehouse inventory |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/lakehouses/{id}/tables` | Table names + metadata (no rows) |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/warehouses` | Warehouse inventory |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items/{id}/jobs/instances` | Job run history (status + duration) |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/git/connection` | Git integration config |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/git/status` | Git sync status |
| `GET https://api.powerbi.com/v1.0/myorg/groups/{gid}/datasets/{did}/refreshes` | Refresh history (metadata) |

### OneLake table APIs (metadata only)

Schema-enabled Lakehouses use these metadata endpoints instead of the legacy
Fabric table-list endpoint. The executing identity needs permission to read
tables in the Lakehouse and a token for `https://storage.azure.com/`.
Only metadata is requested; no table rows or file contents are read.

| Endpoint | Purpose |
|---|---|
| `GET https://onelake.table.fabric.microsoft.com/delta/{wsId}/{itemId}/api/2.1/unity-catalog/schemas?catalog_name={itemId}` | Schema names |
| `GET https://onelake.table.fabric.microsoft.com/delta/{wsId}/{itemId}/api/2.1/unity-catalog/tables?catalog_name={itemId}&schema_name={schema}` | Table names, schema, format and storage location |

Permission or pagination failures remain visible as incomplete coverage; they
are not reported as an empty Lakehouse. Lakehouse schemas do not need to be disabled.

### OneLake DFS (filesystem listing only)

| Endpoint | Notes |
|---|---|
| `GET https://onelake.dfs.fabric.microsoft.com/{ws}/{item}/Files?resource=filesystem&recursive=true` | Returns paths + sizes + last-modified only. **No file content is downloaded.** |

### Capacity Metrics

| Source | Notes |
|---|---|
| Power BI capacity, refreshable and workload REST endpoints | Capacity inventory and configuration; not a CU-utilization time series |
| Fabric Capacity Metrics app dataset | The opt-in standalone collector executes aggregate monitoring DAX; targeted reviews do not use this model |
| FUAM Lakehouse | Targeted reviews read the fixed monitoring contract described below |

The legacy [Workloads API](https://learn.microsoft.com/rest/api/power-bi/capacities/get-workloads)
is not relevant for Gen2 capacities. An unavailable workload probe is recorded
separately and does not discard capacity inventory.

Microsoft does not support [custom consumption of the Capacity Metrics app semantic model](https://learn.microsoft.com/fabric/enterprise/metrics-app#considerations-and-limitations).
The standalone collector is opt-in and dependent on that model's schema. Use
FUAM for targeted-review selection.

### Optional targeted-review orchestration

See [deployment and behavior](targeted-review.md). This feature is disabled by
default and separate from the normal review.

- Only the configured FUAM monitoring Lakehouse may be queried by targeted review,
  using the [supported tables and columns](targeted-review.md#supported-fuam-source-contract),
  not caller-supplied SQL, DAX, arbitrary table names or file paths.
- Selection reads workspace identifiers, monitoring timestamps and aggregate
  CU-seconds/recorded-throttling-minutes metrics. This narrow monitoring exception does **not** authorize
  reading customer business Lakehouse tables or other semantic-model rows.
- The generated parent waits for FAR to succeed before preparing notifications.
  Run the Completion notebook only through that parent; manual execution is not
  a completion check. FAR stores executable packages and run-state files in its
  own Lakehouse. Restrict package write access to trusted deployers.
- When explicitly enabled, owner notifications read workspace role assignments
  through the Power BI admin group-users API. All direct `User`/`Admin` email
  recipients are resolved before preparing any records. Failed/missing owner
  lookup blocks the whole preparation; there is no global fallback recipient,
  group expansion, chat or channel alternative.
- Native Outlook activities send one email per owner per selected workspace,
  sequentially with no automatic retries. Secure inputs/outputs do not remove
  owner contact metadata and report links from all pipeline variables and
  notebook outputs. Treat contact identifiers as personal information; restrict
  artifact and monitoring access and retention according to tenant policy.
- Notifications default off and require an authorized Outlook connection. Setup
  sends no mail and grants no credentials or permissions. Validate mailbox
  authorization and delivery with the intended execution identity using the
  [email setup guide](../fabric/DEPLOYMENT.md#6-optionally-configure-native-outlook-owner-email).
- Audit `emails_prepared` is not delivery proof. A new parent run can duplicate
  messages after uncertain delivery; inspect native monitoring before retrying.
  See [failure and recovery behavior](targeted-review.md#selection-and-failure-behavior).
- Email contains workspace name/ID, a filtered report link, ranking metric/value,
  lookback, a metric caveat and tracking ID, not findings or tasks.
  **Links and filters grant no access and do not enforce RLS.** The central
  report's filter affects workspace-risk visuals, not the entire report.
  Keep it central-only; use the separately validated Workspace Owner report for
  owners and approve their access manually.
- No notification credentials belong in source, pipeline parameters or audit
  files. Notifications off prevents both email-recipient lookup and sending, not
  a bound post-review or independently scheduled owner-access sync. Local
  tests cannot validate connection authorization or mail delivery.

### Optional Workspace Owner report

`DEPLOY_WORKSPACE_OWNER_REPORT="false"` by default is independent of the central
report flag. Optional setup bootstraps owner Gold tables; `04_Gold` detects
enablement through **`owner_access`**. Disabling the setup flag later does not
remove that table or stop projections on an already-enabled installation.
The separate owner model/report does **not** secure or
alter the central governance model, governance report or central Data Agent. Those
remain central-only.

- Owner evidence is a curated technical subset, **not the full checklist or
  central score**. It excludes mixed findings JSON, tenant findings, capacity
  totals, raw DAX, M and notebook code. Evidence requires explicit workspace/item
  IDs matching the same-run inventory; names or URLs never establish attribution.
  Missing detail remains incomplete evidence, not a pass.
- The dynamic `WorkspaceOwner` role matches `USEROBJECTID()` to resource-tenant
  `graphId` for current direct `User`/`Admin` assignments. Groups and service
  principals are excluded; there is no group expansion. Object IDs and workspace
  associations are sensitive personal/organizational metadata.
- Grants expire within 24 hours. Schedule independent daily `07_OwnerAccessSync`
  for all owner-projected workspaces, not only the latest selection or email
  recipients. Sync invalidates old grants before lookup; it does not retain old
  grants as a fallback, backfill reviews or approve readers. Follow
  [daily sync operations](workspace-owner-report.md#daily-entitlement-sync).
- Configure a shared cloud **fixed identity with SSO disabled** and source read
  permissions. Give consumers only item-scoped report/model Read or an owner-only
  app audience, plus manual `WorkspaceOwner` membership and a current grant.
  Grant **no FAR workspace membership, including Viewer**, and no raw
  Lakehouse/OneLake/SQL access or Build. Source-workspace Admin is a different role.
- Apply organizational labels and sharing policies separately; Governance
  settings are not inherited. Disabling deployment does not revoke access:
  follow [retirement and recovery](workspace-owner-report.md#retire-or-recover).

The optional [Workspace Owner Agent](workspace-owner-agent.md) uses only this
secured model. Consumers additionally need query-only Agent access. Instructions,
hidden tables and source selection are not authorization; model RLS and caller
permissions enforce access.

**Complete [live Fabric acceptance](workspace-owner-report.md#live-fabric-acceptance)
before sharing**, using actual read-only consumers to test isolation, export,
revocation, guest IDs and expiry. Deployment and local tests do not certify live
security. Cached visuals may persist until reevaluation; exported/downloaded data
cannot be retroactively revoked.
See the [owner acceptance and operations guide](workspace-owner-report.md) and
Microsoft's [RLS](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security),
[Direct Lake security](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-security-integration)
and [admin group-users API](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/groups-get-group-users-as-admin)
documentation.

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

The DAX Analyzer reads TMDL or `model.bim` expressions from semantic-model
definitions already collected through Fabric `getDefinition`. It performs static text
analysis only: it does not execute a measure, issue `EVALUATE`, inspect a query plan, or
read model rows. Consequently its risk score describes potentially expensive syntax
patterns, not observed duration, CU consumption, or runtime cost.

Measures retain their existing tables and counts. Calculated columns, calculated
tables and calculation items use separate typed evidence and coverage tables;
those Gold tables do not include expression text. Report visual calculations
are not collected.

Measure expressions can reveal proprietary business logic or embedded literal values.
Treat `semantic_model_definitions.json`, `dax_analysis.json`, the DAX Gold tables, and
generated expression previews as confidential review metadata. They stay within the
review workspace/output controls and must not be copied into public samples or logs.

### Native execution and Dataflow Gen2 evidence

Native refresh/job history adds operational metadata: item and execution IDs,
statuses, observed UTC timestamps and nullable millisecond durations. Gold
execution tables do not publish source error payloads. History comes from the
recent records retained by the source APIs; repeated FAR snapshots are not
additional executions and missing records are not a successful empty history.

Dataflow Gen2 collection reads inventory and supported definitions for static
Power Query M inspection. It never executes M or reads query results. Query
evidence contains metadata names, allowlisted signal codes and investigation
guidance, not raw M expressions or connection literals. Names themselves may
still be sensitive. Unsupported or partially parsed definitions remain explicit
coverage gaps.

Collecting this evidence does not authorize additional business-data access.
See [native evidence](native-evidence.md) for interpretation
and the distinction between central access and the curated owner projection.

### VertiPaq Analyzer statistics (`collectors.vertipaq_stats`, Fabric runs only)

`collectors.vertipaq_stats` uses `semantic-link-labs`' `vertipaq_analyzer` **inside a Fabric notebook** to report each model's storage footprint. By default it reads only the storage-engine DMVs above (`DISCOVER_STORAGE_TABLE_COLUMNS`, `DISCOVER_OBJECT_MEMORY_USAGE`, `TMSCHEMA_*`), which return table/column **sizes, encoding, data type and % of model** — metadata only, no data query.

Exact column **cardinality** (distinct-value counts) cannot be read from a DMV; it requires an aggregate `COUNT`/`DISTINCTCOUNT`-style query over the model. That step is therefore **opt-in**:

| Mode | Env flag | What runs | Returns |
|---|---|---|---|
| Default (metadata-only) | `VERTIPAQ_STATS_READ_DATA` unset / `false` | storage-engine DMVs only | sizes, encoding, data type, % of model |
| Cardinality (opt-in) | `VERTIPAQ_STATS_READ_DATA=true` | + aggregate COUNT-style DAX per column | the above **plus** exact distinct-value counts |

Even in the opt-in mode only **aggregate counts** leave the engine — never row values — and nothing beyond sizes and counts is persisted. Set `VERTIPAQ_STATS_SKIP=true` to disable the collector entirely.

---

## Forbidden business-data access

| Surface | Why it's forbidden |
|---|---|
| DAX `EVALUATE` returning customer business rows | Returns customer rows; documented monitoring aggregates and opt-in cardinality counts above are distinct |
| `SELECT ... FROM <user_table>` against Warehouse SQL endpoint | Returns customer rows |
| `SELECT ... FROM <user_table>` against Lakehouse SQL endpoint | Returns customer rows |
| Spark / notebook execution that reads customer business tables or files | Returns customer rows; excludes FAR's own artifacts and documented opt-in FUAM monitoring inputs |
| Downloading customer business files from OneLake | Returns customer data; excludes FAR's own artifacts and documented monitoring inputs |
| Reading notebook cell outputs (`/notebooks/{id}/content` with outputs included) | Cell outputs may contain customer data |
| Scanner API parameters: `getArtifactUsers=true`, `datasetSchema=true`, `datasetExpressions=true`, `datasourceDetails=true` | Returns PII, M/DAX expressions that may include secrets, or credentials |
| Power BI REST `POST /datasets/{id}/executeQueries` against arbitrary business models | Executes DAX returning rows; the documented monitoring-model exception does not authorize general model queries |
| Pipeline activity input/output payloads | May contain query text or row samples |

Changes must preserve these boundaries. For code-review requirements, see the
[contributor guide](../CONTRIBUTING.md).

---

## Audit-data handling

The Admin Activity Log contains user UPNs and is treated as PII:

- Raw dumps are written under `output/raw/` which is **gitignored**.
- Reports and Gold tables can include actor identities for tenant-setting audit
  history as well as aggregate activity. Treat these outputs as sensitive
  metadata, not anonymous data.
- The engagement folder must be deleted or moved to a secure archive at the end of the engagement.

## Statement for the client

> The `fabric-arch-review` framework accesses tenant configuration, workspace and
> item metadata, inventory, and aggregate performance metrics, not customer
> business rows or files. If targeted review was enabled, it also queried the
> configured monitoring source to select workspaces. If owner notifications were
> enabled, prepared email records and native Outlook messages included the
> selected workspace and owner contact identifiers and a FAR report link.

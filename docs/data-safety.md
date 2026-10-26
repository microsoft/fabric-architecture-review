<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Data Safety Statement

The Fabric Architecture Review review is engineered not to read **customer business
rows or files**. Collectors read metadata, configuration and inventory, and reduce
item definitions to sanitized structure before anything is stored. The optional
targeted-review feature additionally reads documented FUAM monitoring data and can
send owner emails containing workspace and owner identities and a report link. The
optional [Workspace Owner report](workspace-owner-report.md) projects only curated
workspace and item technical signals using expiring direct-user-administrator
entitlements.

## Before collecting or sharing

1. Agree the review scope and the executing identity.
2. Restrict access to output folders and Lakehouse artifacts and set retention.
   Metadata, contact details and item structure can be sensitive.
3. Never commit live outputs, secrets or notebook results. Use synthetic data when
   reporting a problem.
4. Before owner sharing, complete the separate
   [owner access and live tests](workspace-owner-report.md#live-fabric-acceptance).
   A URL filter or sensitivity label does not establish row-level isolation.

Use [Fabric deployment](../fabric/DEPLOYMENT.md) or [local review](local-review.md)
to run a review. The sections below describe what those runs may read.

---

## Allowed sources (metadata and configuration only)

### Fabric and Power BI Admin REST

| Endpoint | Purpose |
|---|---|
| `GET https://api.powerbi.com/v1.0/myorg/admin/capacities` | Capacity inventory (identity, SKU, state, region, workspace mapping) |
| `GET https://api.powerbi.com/v1.0/myorg/admin/groups` | Workspace inventory |
| `GET https://api.powerbi.com/v1.0/myorg/admin/groups/{wsId}/users` | Workspace role assignments |

### Optional temporary workspace access (the only write)

Off by default (`access_grant__enabled`). See [auth setup](auth-setup.md#additional-permissions-for-temporary-workspace-access-optional).
These are the only calls that change anything, and they change only workspace membership, never content.

| Endpoint | Purpose |
|---|---|
| `POST https://api.powerbi.com/v1.0/myorg/admin/groups/{wsId}/users` | Adds the review identity as Contributor (or Member) to a workspace that holds a declared model it could not read, and only when it has no role there |
| `DELETE https://api.powerbi.com/v1.0/myorg/admin/groups/{wsId}/users/{user}` | Removes exactly what was added, directly after the read, after a failure, or at the start of the next run |

The ledger names the identity that was granted so it can be removed. It is kept outside the review output and must be
administrator-only. The audit file in the review output holds statuses and counts, no identity. Response bodies are not kept.

### Optional semantic model role detail

Off by default (`model_security__role_detail`). By default only counts of a model's row-level and object-level security are
kept. When enabled, one record per role is added: the role **name**, its permission, how many tables carry a row filter, how many
tables and columns it hides, and whether its filters use the user's identity. Filter text, table names, column names and
role members are still never stored. Role names then reach the central report, the owner report for the owner's own
workspaces, the app and the agents. See [policy configuration](far-policy-configuration.md#role-detail-optional).

### Power BI Scanner API (metadata only)

| Endpoint | Required parameters |
|---|---|
| `POST https://api.powerbi.com/v1.0/myorg/admin/workspaces/getInfo` | `lineage=true`, `getArtifactUsers=true` (reduced to counts before anything is written), **`datasourceDetails=false`**, **`datasetSchema=false`**, **`datasetExpressions=false`** |
| `GET  https://api.powerbi.com/v1.0/myorg/admin/workspaces/scanStatus/{scanId}` | none |
| `GET  https://api.powerbi.com/v1.0/myorg/admin/workspaces/scanResult/{scanId}` | none |

### Workspace-scoped REST (metadata only)

| Endpoint | Purpose |
|---|---|
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items` | Item inventory |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/roleAssignments` | Workspace role assignments where permitted |
| `POST https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items/{id}/getDefinition` | Pipeline and notebook definitions, inspected in memory; only dependency skeletons, parameter names, coverage and heuristic hit counts are persisted |
| `POST https://api.fabric.microsoft.com/v1/workspaces/{wsId}/semanticModels/{id}/getDefinition` | Model definition, inspected in memory; only sanitized storage-mode metadata and row-level/object-level security counts are persisted, never DAX, M, role filters or names |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/git/connection` | Git integration connection state |
| `GET https://api.powerbi.com/v1.0/myorg/gateways` | Gateway clusters, members and versions (no data source credentials) |
| `GET https://api.powerbi.com/v1.0/myorg/admin/pipelines?$expand=stages` (or `GET …/v1/deploymentPipelines` and its stages) | Pipeline and workspace IDs and stage order for declared production workspaces. Names and every other workspace are dropped |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/items/{id}/dataAccessRoles` | How many OneLake data access roles a lakehouse has and how many are custom. No role names, members or paths |
| `GET https://api.fabric.microsoft.com/v1/workspaces/{wsId}/networking/communicationPolicy` (and `…/inbound/firewall`, `…/outbound/connections`, `GET …/managedPrivateEndpoints`) | The default inbound and outbound public access action, and counts: IP firewall rules, rules covering a very wide range, whether outbound protection is on, managed private endpoints. IP addresses, rule names and endpoint targets are read in memory and never stored |
| `GET https://api.fabric.microsoft.com/v1/admin/tenantsettings` | Tenant setting name, title, group, enabled state and the number of included and excluded security groups. Group names and IDs are not stored |

Permission and pagination failures remain visible as incomplete coverage. They are
never reported as an empty workspace or a passed check.

### Optional FUAM and FCA Lakehouse context

When enabled in the Connections configuration, the review reads supported metadata
tables from the configured FUAM and FCA Lakehouses through fixed queries, not
caller-supplied SQL, table names or file paths. This reads monitoring and inventory
metadata, not customer business tables. Each record keeps its source module, table,
observation time and freshness, separately from the time the review captured it.
Evidence from a different review or configuration cannot substitute for this run.

Besides the inventory tables, the enrichment rules read these FUAM and FCA tables with fixed,
aggregating queries: `activities` (the latest activity date per workspace), `active_items` (item
counts and modification dates), `workspaces` (domain), the two capacity-metrics tables (operation
and failure counts, compute totals, throttled days), `widelyshared_publishtoweb` (item and
workspace identity only) and FCA's `focus` table (the resource identifier and `Tags` text of mapped capacities; tag names
are kept, values are discarded in memory, and no cost column is selected). User identities, activity details
and sharer details are not read. [Native evidence](native-evidence.md) lists each read.

The review never runs DAX or best-practice analysis, VertiPaq or XMLA statistics,
refresh or job history, or financial and Azure automation collectors, and it does not
collect activity logs; it reads FUAM's own aggregated data.

### Optional targeted-review orchestration

See [deployment and behavior](targeted-review.md). This feature is disabled by
default and separate from the standard review.

- Only the configured FUAM Lakehouse may be queried, using the
  [supported tables and columns](targeted-review.md#supported-fuam-source-contract).
- Selection reads workspace identifiers, monitoring timestamps and aggregate CU
  seconds or recorded throttling minutes. This narrow monitoring exception does
  **not** authorize reading customer business tables or semantic-model rows.
- The generated parent waits for the review to succeed before preparing
  notifications. Run the Completion notebook only through that parent. Runtime
  packages and run-state files are stored in the Lakehouse; restrict write access
  to trusted deployers.
- When notifications are enabled, owner emails are resolved from workspace role
  assignments through the Power BI admin group-users API. All direct `User`/`Admin`
  recipients are resolved before any record is prepared. A failed or missing lookup
  blocks the whole preparation; there is no global fallback recipient or group expansion.
- Native Outlook activities send one email per owner per selected workspace,
  sequentially with no automatic retries. Contact identifiers are personal
  information: restrict artifact and monitoring access according to tenant policy.
- Notifications default off and need an authorized Outlook connection. Setup sends
  no mail and grants no permissions. Validate with the
  [email setup guide](../fabric/DEPLOYMENT.md#6-optionally-configure-native-outlook-owner-email).
- `emails_prepared` is not delivery proof; a new run can duplicate messages after
  uncertain delivery. See [failure and recovery behavior](targeted-review.md#selection-and-failure-behavior).
- Email contains workspace name and ID, a report link, the ranking metric and value,
  the lookback, a caveat and a tracking ID, not findings. **Links and filters grant
  no access and do not enforce RLS.** Keep the central report central-only; use the
  separately validated Workspace Owner report for owners and approve access manually.
- No notification credentials belong in source, pipeline parameters or audit files.

### Optional Workspace Owner report

`DEPLOY_WORKSPACE_OWNER_REPORT="false"` by default and is independent of the central
report. Optional setup bootstraps the owner tables; `04_Gold` detects enablement
through **`owner_access`**. Disabling the setup flag later does not remove that table
or stop projection on an installation that already enabled it. The owner model does
**not** secure or alter the central model, report or Data Agent.

- Owner evidence is a curated technical subset, **not the full rule set or the
  central score**. It excludes tenant findings, capacity totals, raw DAX, M and
  notebook code. Evidence requires explicit workspace and item IDs matching the same
  run; names or URLs never establish attribution. Missing detail remains incomplete
  evidence, not a pass.
- The dynamic `WorkspaceOwner` role matches `USEROBJECTID()` to the resource-tenant
  `graphId` of current direct `User`/`Admin` assignments. Groups and service
  principals are excluded; there is no group expansion. Object IDs and workspace
  associations are sensitive personal and organizational metadata.
- Grants expire within 24 hours. Schedule the independent daily `07_OwnerAccessSync`
  for all owner-projected workspaces. Follow
  [daily sync operations](workspace-owner-report.md#daily-entitlement-sync).
- Configure a shared cloud **fixed identity with SSO disabled** and source read
  permissions. Give consumers only item-scoped report/model Read or an owner-only
  app audience, plus manual `WorkspaceOwner` membership and a current grant. Grant
  **no review-workspace membership, including Viewer**, and no raw
  Lakehouse/OneLake/SQL access or Build.
- Apply organizational labels and sharing policies separately. Disabling deployment
  does not revoke access: follow [retirement and recovery](workspace-owner-report.md#retire-or-recover).

The optional [Workspace Owner Agent](workspace-owner-agent.md) uses only this
secured model. Consumers additionally need query-only Agent access. Instructions,
hidden tables and source selection are not authorization; model RLS and caller
permissions enforce access.

**Complete [live Fabric acceptance](workspace-owner-report.md#live-fabric-acceptance)
before sharing**, using actual read-only consumers. Deployment and local tests do not
certify live security. Cached visuals may persist until reevaluation, and exported
data cannot be retroactively revoked. See Microsoft's
[RLS](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security),
[Direct Lake security](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-security-integration)
and [admin group-users API](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/groups-get-group-users-as-admin)
documentation.

### Sanitized architectural definitions

Definitions may contain proprietary code, secrets, DAX/M or outputs. They are decoded
and inspected **only in memory** and discarded before any persistence, including
progress checkpoints.

- **Pipelines:** structural activity dependencies, GUID resource references and
  parameter names, never values.
- **Notebooks:** `parameterNames`, `parameterCoverage` and a `signals` mapping from rule
  ID to a safe hit count. No source, base64 or outputs.
- **Models:** allowlisted storage modes and feature names, not expressions or
  connection literals. For row-level and object-level security, only **counts**: the number
  of roles, of row filters that restrict rows (and of constant `TRUE` filters), and of hidden
  tables and columns, plus how completely the definition could be inspected. Filter
  expressions, role, table and column names and role membership are never stored.

Source patterns are heuristics that need confirmation, not verified leaked credentials
or proven violations. Unsupported or partially parsed formats keep unknown or partial
coverage. A row modification date is not proof of a complete, current capture.
See [architectural evidence](native-evidence.md).

---

## Forbidden business-data access

| Surface | Why it's forbidden |
|---|---|
| DAX `EVALUATE` returning customer business rows | Returns customer rows |
| `SELECT ... FROM <user_table>` against a Warehouse or Lakehouse SQL endpoint | Returns customer rows |
| Spark or notebook execution that reads customer business tables or files | Returns customer rows; excludes the suite's own artifacts and the configured FUAM/FCA context tables |
| Downloading customer business files from OneLake | Returns customer data |
| Reading notebook cell outputs | Outputs may contain customer data |
| Scanner API parameters `datasetSchema=true`, `datasetExpressions=true`, `datasourceDetails=true` | Return column schemas, expressions that may include secrets, or credentials |
| Persisting the scanner's item access lists (`getArtifactUsers`) | Return people's identities. Only `accessSummary` counts may be written, and the reduction happens in memory in `reduce_artifact_users` |
| Power BI REST `POST /datasets/{id}/executeQueries` | Executes DAX that can return rows |
| Pipeline activity input and output payloads | May contain query text or row samples |

Changes must preserve these boundaries. For code-review requirements see the
[contributor guide](../CONTRIBUTING.md).

---

## Output handling

Review outputs contain workspace and item identities and, for access rules, principal
identifiers. Treat them as sensitive metadata:

- Local raw output is written under `output/`, which is **gitignored**.
- Reports and Gold tables can include principal identities. Restrict access and set
  retention.
- Delete or securely archive the engagement folder at the end of the engagement.

## Statement for the client

> The Fabric Architecture Review review accesses tenant configuration, workspace and
> item metadata and inventory, and sanitized structural summaries of item definitions.
> It does not access customer business rows or files. If targeted review was enabled,
> it also queried the configured FUAM monitoring source to select workspaces. If owner
> notifications were enabled, prepared email records and native Outlook messages
> included the selected workspace and owner contact identifiers and a report link.

<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Auth Setup

By default, this framework authenticates **as you** locally (interactive user,
OAuth 2.0 authorization-code flow with PKCE) or as the notebook's executing
identity in Fabric. That default requires no service-principal secret or app
registration. Optional service-principal collection is described below.

The identity used for API calls follows the configured authentication mode.
Verify scheduled execution with the intended identity, not just the deployer.

## Choose the access task

| You are setting up... | Do this |
| --- | --- |
| Local collection | Follow [Local setup](#local-setup), then run the [local review](local-review.md) |
| Collection inside Fabric | Check [collector roles](#what-you-need-in-the-client-tenant), then follow [deployment](../fabric/DEPLOYMENT.md) |
| Unattended collection | Use the [optional service-principal path](#standing--unattended-mode--service-principal-optional) and test its actual permissions |
| FUAM selection or email | Check [targeted permissions](#additional-permissions-for-optional-targeted-reviews); Collect's identity settings do not configure these |
| An owner-report reader | Follow [reader approval](workspace-owner-report.md#approve-each-new-reader), **not** the collector-admin role list |

**Success:** the intended identity can complete a scoped test, with no unexplained
access-related evidence gaps. Being able to open the report is a separate permission.

## Authentication modes

The framework acquires tokens for **two separate planes**, and how each is
obtained depends on where you run it. The table describes the **default user /
notebook-identity mode**, not the optional service-principal override.

| Plane | Collectors it serves | Local (PowerShell) | In Fabric (notebook / pipeline) |
|---|---|---|---|
| **Fabric + Power BI** | tenant settings, scanner, workspaces, semantic models, pipelines, gateways, activity logs… (most of the review) | your interactive **user** (`az login` / browser) | the **notebook's executing identity** |
| **Azure ARM** | the opt-in `azure_capacity_automation` (Pause/Resume scan) | the same interactive **user** | **not available** — the scan is local-only |

Key points:

- **Permissions follow the caller.** Grant the configured identity the roles
  required by each enabled collector. Changing authentication mode does not
  grant access or establish that every API supports that mode.
- **Azure ARM is an opt-in add-on.** Only the opt-in `azure_capacity_automation`
  (Pause/Resume) scan touches Azure ARM. Grant the same identity Azure **Reader**
  on the capacity's subscription and it works; skip the scan
  (`CAPACITY_AUTO_PAUSE_CONFIGURED=false`) and no ARM access is needed at all.
- **Cross-tenant:** locally you can target **any** tenant you are a guest/member
  of (set `TENANT_ID`, then `az login --tenant <id>`). In Fabric the token is
  always the workspace's home tenant - run the notebook *inside the tenant you
  are reviewing*. If the Fabric capacity lives in a subscription that identity
  cannot read, run the review from inside the tenant that owns it.

> **Deployment is distinct from authentication.** Base setup creates the FAR
> Lakehouse, notebooks, pipeline and optional reporting artifacts. It does not
> create the target workspace, grant roles or provision sender credentials.
> Review runs write metadata, reports and Gold tables into the FAR Lakehouse.
> See the [deployment walkthrough](../fabric/DEPLOYMENT.md).

## Standing / unattended mode — service principal (optional)

The standalone Collect stage has an opt-in `ClientSecretCredential` path.
Provision the app registration, applicable read-only admin API tenant settings,
and collector-specific permissions yourself; setup does not grant them.

- **Local:** provide `TENANT_ID`, `CLIENT_ID` and `CLIENT_SECRET` through your
  approved secret-management mechanism. Never commit the secret.
- **In Fabric:** set `TENANT_ID`, `SP_CLIENT_ID`, `SP_SECRET_KEYVAULT` (vault name
  or URI) and `SP_SECRET_NAME` on the standalone pipeline. The executing notebook
  identity must be permitted to retrieve that Key Vault secret. Only when all
  three `SP_*` credential settings are nonempty does Collect select this path;
  otherwise it uses the notebook identity.

The secret is retrieved into process memory/environment at runtime, not stored
in the notebook, pipeline definition or repository. Protect the executing
environment and do not print its environment variables.

`SP_CLIENT_ID` alone does **not** switch identity. Creating a named cloud connection
does not activate the collector override; configure the Key Vault settings above. Validate the
enabled collectors with the intended identity before scheduling. This override
does not change the identity used by FUAM selection or the Outlook sender.

## What you need in the client tenant

You must be a **member or guest** in the client's Microsoft Entra tenant, and
depending on which collectors you want to run, you need one of the following
role / permission combinations:

| Collector | Required role |
|---|---|
| `tenant_settings` (admin) | **Fabric Administrator** or **Power BI Administrator** in the client tenant |
| `scanner_api` (admin) | Same as above |
| `workspace_inventory` — admin view (all workspaces) | Same as above |
| `activity_logs` (admin) | Same as above |
| `capacity_metrics` — capacity, refreshable and workload inventory | Fabric/Power BI administrator for tenant-wide inventory, or Capacity Admin for assigned capacities; this collector does not query Azure Monitor |
| `workspace_inventory` — workspace-scoped | Workspace **Member** (or higher) on each in-scope workspace |
| `lakehouse_warehouse` — metadata only | Workspace **Member** (or higher) |
| `semantic_models` — dataset metadata and refresh history | Access to the in-scope workspace and datasets |
| `vertipaq_stats` — storage-engine metadata (Fabric only) | Read access through the semantic model's XMLA endpoint on a supported capacity |
| `semantic_model_definitions` — TMDL/BIM via `getDefinition` | Workspace **Member** (or higher); `getDefinition` requires write access, so **Viewer** is not enough |
| `dataflows` — Gen2 inventory and supported definitions | Item read permission for inventory and read/write permission for `getDefinition`; verify workspace role, item support and the actual execution identity |
| `pipelines_notebooks` — run history | Workspace **Member** (or higher) |
| `pipeline_definitions` — pipeline / notebook source via `getDefinition` | Workspace **Member** (or higher); `getDefinition` requires write access |
| `realtime_intelligence` — RTI + mirroring inventory | Workspace **Viewer** (or higher) |
| `git_integration` | Workspace **Admin** |
| `deployment_pipelines` — Deployment Pipelines inventory | **Admin** on each Power BI Deployment Pipeline (or Fabric Administrator); returns only pipelines you can see |
| `gateways` — data gateway inventory | **Gateway admin** on each on-prem / VNet gateway; returns only gateways you administer |
| `capacity_metrics_app` *(opt-in)* — DAX vs. Capacity Metrics App | **Build** permission on the Fabric Capacity Metrics App semantic model |
| `azure_capacity_automation` *(opt-in)* — ARM scan for Pause/Resume | Azure **Reader** on the subscription(s) hosting the capacity, Automation account, and Logic App |

> **No Fabric Admin role?** You can still complete a workspace-scoped review.
> The framework will surface `401`/`403` from the tenant-wide endpoints; mark
> those checklist items as "evidence not available — request Fabric Admin
> review meeting" in the final report.

## Tenant settings (must be enabled by the client's Fabric admin)

For the XMLA / DMV based collector (`vertipaq_stats`) to work, the Fabric
admin must enable, for either the whole org or a security group containing
your user:

- **XMLA endpoint** -> *Read* or *Read/Write* (Capacity settings -> Power BI
  workload), and
- **Allow XMLA endpoints and Analyze in Excel with on-premises datasets**
  (Tenant settings).

The Scanner / Admin REST endpoints do **not** require any tenant setting
change for a user-context call — they only require the admin role.

## Additional permissions for optional targeted reviews

The [targeted parent](targeted-review.md) uses the execution identity of its
selection and Completion notebooks. The child Collect stage's service-principal
settings do not change the selection identity or Outlook sender.

| Operation | Required access |
| --- | --- |
| Deploy | Create/update notebooks and pipelines in the FAR workspace; write the runtime package to the attached FAR Lakehouse |
| Execute | Read the runtime, run and monitor FAR, and write parent-run audit records |
| Rank FUAM workspaces | Read the [documented tables](targeted-review.md#supported-fuam-source-contract) through the FUAM Lakehouse SQL analytics endpoint |
| Prepare email | Read workspace administrator assignments through the Power BI admin group-users API after the native FAR invocation succeeds |
| Send email | Use an authorized Office 365 Outlook connection and sender mailbox |
| Read the report | Recipient access to the report and its data, including applicable RLS |

The [Spark SQL connector](https://learn.microsoft.com/fabric/data-engineering/spark-data-warehouse-connector#authentication)
supports interactive Microsoft Entra user authentication, not service-principal
authentication. Validate source access with the intended scheduled identity.

For user OAuth, create the Outlook connection through the parent's **Email
workspace administrator** activity in **Settings**. The service-principal-only
**Office 365 Email** form in Manage connections and gateways is not the user-sign-in
route. User email connections are user-scoped, not directly shareable across
authors; test with the account that will execute the pipeline. Other authentication
modes and explicit senders require administrator-approved permissions and mailbox
authorization.

Follow the [email setup and scoped test](../fabric/DEPLOYMENT.md#6-optionally-configure-native-outlook-owner-email)
before enabling notifications. FAR grants no permissions. Keep credentials in
the connection, not notebook or pipeline parameters, and restrict monitoring
access because outputs include owner contact details. A report filter is not
authorization; see [data safety](data-safety.md#optional-targeted-review-orchestration).

## Additional permissions for the optional Workspace Owner report

For the actions to perform in Fabric, use the
[one-time connection walkthrough](workspace-owner-report.md#connect-the-owner-model-click-by-click)
and [new-reader approval steps](workspace-owner-report.md#approve-each-new-reader).
The connection is configured once per owner model, not once per reader. For each
new reader, approve Read and add them to the existing `WorkspaceOwner` role;
07 separately maintains their workspace mapping.

The [owner report](workspace-owner-report.md) uses a **separate** model. It does
not secure the central governance model/report or Data Agent; those remain
central-only. Enable it separately with `DEPLOY_WORKSPACE_OWNER_REPORT="true"`;
the default is `"false"`, independent of `DEPLOY_GOLD_REPORT`.

| Identity / operation | Required boundary |
| --- | --- |
| Owner access-sync execution identity | Admin group-users API lookup, write owner entitlement snapshots, and refresh/reframe the owner model; validate the scheduled identity separately from Collect or the email sender |
| Model's shared cloud connection identity | Fixed identity, **SSO disabled**, with required source read permissions before any consumer sharing |
| Owner consumer | Manually approved **item-scoped report/model Read** or a narrowly scoped owner app audience, plus manual membership in the single `WorkspaceOwner` read role; no FAR workspace membership |
| Source workspace eligibility | Current direct `User`/`Admin` assignment with resource-tenant `graphId`, matching `USEROBJECTID()` and a nonexpired grant |

The sync uses [Power BI `admin/groups/{id}/users`](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/groups-get-group-users-as-admin).
For delegated authentication, the API documents a Fabric administrator and
`Tenant.Read.All` or `Tenant.ReadWrite.All` scope; service-principal authentication
has different documented prerequisites. Do not copy delegated scopes into a
service-principal configuration. Groups and service-principal **owner recipients**
are excluded regardless of the sync caller's identity. No Microsoft Entra Graph
group-membership permissions are required or requested.

**Source workspace Admin is not a FAR workspace role.** Grant consumers **no FAR
workspace membership at any role, including Viewer**. RLS does not restrict
Admin, Member or Contributor roles; Viewer respects owner-model RLS but can expose
other unrestricted central governance artifacts colocated in the same workspace.
Use only item-scoped report/model Read or a narrowly scoped app audience containing
owner artifacts. Do not grant raw Lakehouse/OneLake/SQL access or Build to owner
consumers.
Use [Direct Lake fixed-identity connection guidance](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-security-integration)
and [RLS guidance](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security).
Keep model connection permissions distinct from consumer permissions.

Reader approval and workspace entitlements are separate. A new model's role is
empty (default deny); Read and role membership return no rows without a current
grant. Guests must use their **resource-tenant** object ID, not email or a
home-tenant ID. Email only notifies recipients; it never approves access.

Schedule `07_OwnerAccessSync` daily and run it successfully after the first
owner-enabled review. Grants expire within 24 hours. Follow the
[daily sync procedure](workspace-owner-report.md#daily-entitlement-sync) for
execution permissions, failures and coordination with targeted reviews.
Setup creates no schedule; disabling deployment does not revoke existing access.

Before sharing, apply organizational labels and sharing policies; these are not
inherited from Governance and do not replace RLS. Complete the
[owner rollout checklist](workspace-owner-report.md#live-fabric-acceptance) with
actual read-only consumers, including revocation and expiry. A successful
deployment is not proof of isolation.

## Local setup

### 1. Fill `.env`

```text
TENANT_ID=<client-tenant-id>     # the GUID of the customer's Entra tenant
# CLIENT_ID=                     # leave blank to use the Azure CLI public client
```

### 2. (Recommended) Sign in via Azure CLI

```powershell
az login --tenant <client-tenant-id>
```

The framework uses the existing Azure CLI session through `AzureCliCredential`
when a valid token for the tenant is available.

### 3. First run

If no CLI session is available, the framework falls back to
`InteractiveBrowserCredential`. A browser window opens, you sign in once, and
credentials are cached on disk using Azure Identity's persistence mechanism.
The configuration permits unencrypted storage when platform protection is
unavailable. Protect the operating-system account and cache directory.

Token renewal is automatic. A renewal failure stops authenticated requests
rather than reusing an expired token.

```powershell
.\.venv\Scripts\Activate.ps1
python -m collectors.tenant_settings
```

Expected output:
```
Tenant settings written to: output/raw/tenant_settings.json
```

`AADSTS50105` indicates an application-assignment requirement; ask the tenant
administrator to check enterprise application access. For HTTP `401` or `403`,
check the token's tenant/audience and the identity's endpoint-specific
permissions. These errors do not by themselves identify a missing Fabric role.
For workspace discovery, a denied tenant-admin listing can fall back to the
executing user's workspace-member listing. Server errors are not treated as a
permission fallback. Failed membership collection is missing evidence, not an
empty member list or a successful security check. Resolve the access problem
and rerun Collect before interpreting those checks.

## Footprint in the client's audit log

Authentication and supported API operations can appear in Microsoft Entra and
Fabric/Power BI audit logs under the executing user or application identity.
Available events and retention depend on the service and tenant configuration;
do not assume every API request creates a matching Fabric activity event.

## Conditional Access caveats

If the client tenant enforces Conditional Access policies that require:

- a managed device,
- a compliant device,
- a specific named network location,

...then sign-in requires an environment that meets those policies, such as an
approved customer-issued jump host or session host.

## Cross-engagement isolation

Use a separate output folder per engagement. The browser-token cache is named
by `TENANT_ID` and stored at operating-system user scope; separate repository
clones do **not** create separate credential stores. Azure CLI manages its own
sign-in cache. On shared machines, use isolated operating-system accounts and
your organization's sign-out and credential-cleanup procedures.

## Azure (ARM) Reader for the Pause/Resume scan (local only)

The opt-in `azure_capacity_automation` (Pause/Resume) scan is the only collector
that reads **Azure Resource Manager**. It runs **only on a local machine** —
Fabric's notebook identity cannot mint an ARM token, so leave
`CAPACITY_AUTO_PAUSE_CONFIGURED=false` in Fabric and it skips cleanly. Locally it
needs an Azure identity with **Reader** on the subscription that hosts the
capacity — normally your own `az login` user. This default user-mode path does
not require a service principal or a Key Vault secret.

```bash
az role assignment create --assignee <your-user-object-id> \
  --role "Reader" --scope /subscriptions/<subscription-id>
```

Scope it to the capacity's resource group instead of the whole subscription if
the capacity, Automation accounts, and Logic Apps all live in one RG. **Reader**
is enough because the scan issues only `GET` ARM calls - list capacities, read
Automation runbook content, read Logic App workflow definitions. No
write/contributor role and no data-plane access is involved.

Then set the local environment variable / `.env` value:

| Setting | Value |
| --- | --- |
| `CAPACITY_AUTO_PAUSE_CONFIGURED` | `true` |

When enabled, the local collect stage acquires the ARM token from your `az login`
session automatically. Sign in to the tenant that owns the capacity first if it
lives elsewhere.

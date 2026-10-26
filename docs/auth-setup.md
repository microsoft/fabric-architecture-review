<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Auth Setup

By default the review authenticates **as you** locally (interactive user, OAuth 2.0
authorization-code flow with PKCE) or as the notebook's executing identity in
Fabric. That default needs no service-principal secret or app registration.
An optional service principal is described below.

The identity used for API calls follows the configured authentication mode.
Verify scheduled execution with the intended identity, not just the deployer.

## Choose the access task

| You are setting up... | Do this |
| --- | --- |
| Local collection | Follow [Local setup](#local-setup), then run the [local review](local-review.md) |
| Collection inside Fabric | Check [collector roles](#what-you-need-in-the-client-tenant), then follow [deployment](../fabric/DEPLOYMENT.md) |
| Unattended collection | Use the [service-principal path](#unattended-mode-service-principal-optional) and test its actual permissions |
| FUAM selection or email | Check [targeted permissions](#additional-permissions-for-optional-targeted-reviews) |
| An owner-report reader | Follow [reader approval](workspace-owner-report.md#approve-each-new-reader), **not** the collector-admin role list |

**Success:** the intended identity completes a scoped test with no unexplained
access-related evidence gaps. Being able to open the report is a separate permission.

## Authentication modes

All collectors call the **Fabric and Power BI** REST APIs. How the token is obtained
depends on where you run:

| Where | Default identity |
| --- | --- |
| Local (PowerShell/bash) | Your interactive **user** (`az login` or browser) |
| Fabric notebook or pipeline | The **notebook's executing identity** |

- **Permissions follow the caller.** Grant the configured identity the roles
  required by the collectors that run. Changing the authentication mode does not
  grant access.
- **Cross-tenant:** locally you can target any tenant you are a member or guest of
  (set `TENANT_ID`, then `az login --tenant <id>`). In Fabric the token is always
  the workspace's home tenant: run the notebook *inside the tenant you are reviewing*.

> **Deployment is distinct from authentication.** Setup creates the Lakehouse,
> notebooks, pipeline and optional reporting artifacts. It does not create the target
> workspace, grant roles or provision sender credentials.

## Unattended mode: service principal (optional)

Use this when scheduled runs must not depend on a person.

1. Create an app registration. Grant it the [collector roles](#what-you-need-in-the-client-tenant)
   below and, for tenant-wide admin endpoints, allow service principals to use the
   read-only admin APIs in the Fabric admin portal.
2. Store its client secret in an Azure Key Vault secret and allow the notebook
   identity to read it (*Key Vault Secrets User*).
3. **In Fabric**, edit the `<prefix>_Environment` Variable Library: set
   `identity__tenant_id`, `identity__service_principal_client_id`,
   `identity__key_vault` and `identity__secret_name`. All four must be set; a partial
   configuration is rejected rather than silently using another identity.
4. **Locally**, provide `TENANT_ID`, `CLIENT_ID` and `CLIENT_SECRET` through your
   approved secret-management mechanism. Never commit the secret.

The secret is read into process memory at run time. It is not stored in a notebook,
pipeline definition, Variable Library or repository. Do not print environment
variables. This identity does not change the identity used by FUAM selection or
the Outlook sender. Not every Fabric API supports service principals: validate the
enabled collectors with the intended identity before scheduling.

## What you need in the client tenant

You must be a **member or guest** in the client's Microsoft Entra tenant. The
roles needed depend on which collectors run:

| Collector | Required role |
|---|---|
| `scanner_api` (admin), including who can access each item | **Fabric Administrator** or **Power BI Administrator** |
| `workspace_inventory` — all workspaces | Same as above |
| `workspace_inventory` — workspace-scoped | Workspace **Member** (or higher) on each in-scope workspace |
| `capacity_metrics` — capacity inventory | Fabric/Power BI administrator for tenant-wide inventory, or Capacity Admin for assigned capacities |
| `semantic_models` — dataset metadata | Fabric/Power BI administrator for admin discovery; otherwise access to the in-scope workspace and datasets |
| `semantic_model_definitions` — TMDL/BIM via `getDefinition` | Workspace **Member** (or higher); `getDefinition` requires write access, so **Viewer** is not enough. The Fabric Administrator role does not grant it: the identity needs *read and write* on each model, through a workspace role or an item permission. The same read supplies the row-level and object-level security counts for SEC-024 to SEC-026; a model the identity cannot read (or whose encrypted sensitivity label it cannot remove) is reported as missing evidence, never as having no security |
| `pipelines_notebooks` — item inventory | Workspace **Member** (or higher) |
| `pipeline_definitions` — pipeline and notebook structure via `getDefinition` | Workspace **Member** (or higher) |
| `git_integration` | Workspace **Admin** |
| `gateways` — gateway cluster inventory | **Gateway admin** on each gateway; returns only gateways you administer |
| `tenant_settings` — tenant setting state | **Fabric Administrator** (delegated sign-in also needs the `Tenant.Read.All` scope) |
| `deployment_pipelines` (declared production workspaces) | **Fabric Administrator** lists every pipeline (200 requests an hour); without it only the pipelines you can see are read, and a missing assignment is then missing evidence |
| `onelake_roles` (declared production workspaces) | Read access to each lakehouse |
| `workspace_network` (declared production workspaces) | Workspace **Admin**, **Member** or **Contributor** for the policy. The firewall, outbound and private endpoint reads are best effort: when one is refused its count stays empty and the policy is still reported |

> **No Fabric Admin role?** You can still complete a workspace-scoped review. The
> tenant-wide endpoints return `401`/`403`; those results are reported as missing
> evidence, never as a pass.

Only collectors that an enabled rule needs run; see
[architectural evidence](native-evidence.md) and `raw/collection_plan.json`.

## Additional permissions for optional targeted reviews

The [targeted parent](targeted-review.md) uses the execution identity of its
selection and Completion notebooks. The service-principal settings of the
Environment library do not change the selection identity or Outlook sender.

| Operation | Required access |
| --- | --- |
| Deploy | Create/update notebooks and pipelines in the review workspace; write the runtime package to the attached Lakehouse |
| Execute | Read the runtime, run and monitor the review, and write parent-run audit records |
| Rank FUAM workspaces | Read the [documented tables](targeted-review.md#supported-fuam-source-contract) through the FUAM Lakehouse SQL analytics endpoint |
| Prepare email | Read workspace administrator assignments through the Power BI admin group-users API after the review succeeds |
| Send email | Use an authorized Office 365 Outlook connection and sender mailbox |
| Read the report | Recipient access to the report and its data, including applicable RLS |

The [Spark SQL connector](https://learn.microsoft.com/fabric/data-engineering/spark-data-warehouse-connector#authentication)
supports interactive Microsoft Entra user authentication, not service-principal
authentication. Validate source access with the intended scheduled identity.

For user OAuth, create the Outlook connection through the parent's **Email
workspace administrator** activity in **Settings**. The service-principal-only
**Office 365 Email** form in Manage connections and gateways is not the user-sign-in
route. User email connections are user-scoped; test with the account that will
execute the pipeline.

Follow the [email setup and scoped test](../fabric/DEPLOYMENT.md#6-optionally-configure-native-outlook-owner-email)
before enabling notifications. Keep credentials in the connection, not notebook or
pipeline parameters, and restrict monitoring access because outputs include owner
contact details. A report filter is not authorization; see
[data safety](data-safety.md#optional-targeted-review-orchestration).

## Additional permissions for the optional Workspace Owner report

For the actions to perform in Fabric, use the
[one-time connection walkthrough](workspace-owner-report.md#connect-the-owner-model)
and [new-reader approval steps](workspace-owner-report.md#approve-each-new-reader).
The connection is configured once per owner model, not once per reader. For each new
reader, approve Read and add them to the existing `WorkspaceOwner` role; 07
separately maintains their workspace mapping.

The [owner report](workspace-owner-report.md) uses a **separate** model. It does not
secure the central governance model/report or Data Agent; those remain central-only.
Enable it separately with `DEPLOY_WORKSPACE_OWNER_REPORT="true"`; the default is
`"false"`, independent of `DEPLOY_GOLD_REPORT`.

| Identity / operation | Required boundary |
| --- | --- |
| Owner access-sync execution identity | Admin group-users API lookup, write owner entitlement snapshots, and refresh/reframe the owner model; validate the scheduled identity separately from Collect or the email sender |
| Model's shared cloud connection identity | Fixed identity, **SSO disabled**, with required source read permissions before any consumer sharing |
| Owner consumer | Manually approved **item-scoped report/model Read** or a narrowly scoped owner app audience, plus manual membership in the single `WorkspaceOwner` read role; no review-workspace membership |
| Source workspace eligibility | Current direct `User`/`Admin` assignment with resource-tenant `graphId`, matching `USEROBJECTID()` and a nonexpired grant |

The sync uses [Power BI `admin/groups/{id}/users`](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/groups-get-group-users-as-admin).
For delegated authentication, the API documents a Fabric administrator and
`Tenant.Read.All` or `Tenant.ReadWrite.All` scope; service-principal authentication
has different documented prerequisites. Do not copy delegated scopes into a
service-principal configuration. Groups and service-principal **owner recipients**
are excluded regardless of the sync caller's identity. No Microsoft Entra Graph
group-membership permissions are required or requested.

**Source workspace Admin is not a review-workspace role.** Grant consumers **no
review-workspace membership at any role, including Viewer**. RLS does not restrict
Admin, Member or Contributor roles; Viewer respects owner-model RLS but can expose
other unrestricted central governance artifacts colocated in the same workspace.
Use only item-scoped report/model Read or a narrowly scoped app audience containing
owner artifacts. Do not grant raw Lakehouse/OneLake/SQL access or Build to owner
consumers. Use [Direct Lake fixed-identity connection guidance](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-security-integration)
and [RLS guidance](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security).
Keep model connection permissions distinct from consumer permissions.

Reader approval and workspace entitlements are separate. A new model's role is empty
(default deny); Read and role membership return no rows without a current grant.
Guests must use their **resource-tenant** object ID, not email or a home-tenant ID.
Email only notifies recipients; it never approves access.

Schedule `07_OwnerAccessSync` daily and run it successfully after the first
owner-enabled review. Grants expire within 24 hours. Follow the
[daily sync procedure](workspace-owner-report.md#daily-entitlement-sync) for
execution permissions, failures and coordination with targeted reviews. Setup
creates no schedule; disabling deployment does not revoke existing access.

Before sharing, apply organizational labels and sharing policies; these are not
inherited from Governance and do not replace RLS. Complete the
[owner rollout checklist](workspace-owner-report.md#live-fabric-acceptance) with
actual read-only consumers, including revocation and expiry. A successful deployment
is not proof of isolation.

## Local setup

### 1. Fill `.env.far`

```text
TENANT_ID=<client-tenant-id>     # the GUID of the customer's Entra tenant
# CLIENT_ID=                     # optional service principal; leave blank for your own sign-in
```

### 2. (Recommended) Sign in via Azure CLI

```powershell
az login --tenant <client-tenant-id>
```

The framework uses the existing Azure CLI session through `AzureCliCredential` when a
valid token for the tenant is available.

### 3. First run

If no CLI session is available, the framework falls back to
`InteractiveBrowserCredential`. A browser window opens, you sign in once, and
credentials are cached on disk using Azure Identity's persistence mechanism. The
configuration permits unencrypted storage when platform protection is unavailable.
Protect the operating-system account and cache directory.

Token renewal is automatic. A renewal failure stops authenticated requests rather
than reusing an expired token. Verify access with a scoped inventory:

```powershell
.\.venv\Scripts\Activate.ps1
$env:WORKSPACE_IDS = "<one-workspace-guid>"
python -m collectors.workspace_inventory --output-dir output/far/raw
```

`AADSTS50105` indicates an application-assignment requirement; ask the tenant
administrator to check enterprise application access. For HTTP `401` or `403`, check
the token's tenant/audience and the identity's endpoint-specific permissions. For
workspace discovery, a denied tenant-admin listing can fall back to the executing
user's workspace-member listing. Server errors are not treated as a permission
fallback. Failed membership collection is missing evidence, not an empty member list
or a successful security check. Resolve the access problem and rerun Collect before
interpreting those checks.

## Additional setting for semantic model role detail (optional)

Needs no extra permission: it only decides what the review keeps of the model definition it already reads. Off by default.
Set it in the `<prefix>_Environment` Variable Library (locally in `.env.far`):

| Setting | Meaning |
| --- | --- |
| `model_security__role_detail` (`MODEL_SECURITY_ROLE_DETAIL`) | `true` or `false`. Default `false`. When `true` the review also keeps each role's name, permission, tables with a row filter, hidden tables and columns, and whether the filter is dynamic. See [policy configuration](far-policy-configuration.md#role-detail-optional) |

An environment library created before this setting existed behaves as `false`; add the variable to turn it on.

## Additional permissions for temporary workspace access (optional)

Off by default, and the only part of the review that **writes**. It exists for one situation: you declared models in
`policy__rls_required_model_ids` or `policy__ols_required_model_ids`, the review identity is a **Fabric Administrator**,
and it has no write access to those models, so their roles cannot be read. Turn it on in the `<prefix>_Environment`
Variable Library (locally in `.env.far`):

| Setting | Meaning |
| --- | --- |
| `access_grant__enabled` (`ACCESS_GRANT_ENABLED`) | `true` or `false`. Default `false` |
| `access_grant__role` (`ACCESS_GRANT_ROLE`) | `auto` (default) tries **Contributor** and uses **Member** only if the read is still refused. Or `Contributor` / `Member` to try only that role |

What it does, and what it will not do:

- It acts only on a workspace that holds a **declared** model whose definition read was refused (HTTP 403 or 404). It never
  grants access to the rest of the estate, so models you did not declare are not re-read.
- It never changes or removes a role the identity already has. If the identity is already in the workspace in any role, the
  workspace is skipped and reported. Current membership is checked through the admin API, not inferred from cached FUAM
  membership. If that check fails, no access is granted. It removes only what it added.
- For each such workspace it adds the identity (admin API `POST …/admin/groups/{workspaceId}/users`), re-reads the
  declared models, and removes the identity again (`DELETE …/admin/groups/{workspaceId}/users/{user}`), before
  moving to the next workspace. Removal also runs after an error or an interrupt. A new role can take a short while to take
  effect, so the first read is retried for up to about two minutes per role.
- The identity is the one signed in: its user principal name for a person, or its object ID for a service principal. A
  service principal needs the tenant setting that lets it call the admin APIs and must not hold admin-consent Power BI
  permissions.
- A **ledger** (`_access_grants/ledger.json` beside the run folders in the FAR Lakehouse; `access_grants_ledger.json` beside
  the output folder locally) lists a grant *before* it is made and clears it after removal. A run that is killed cannot
  remove its grant, so the **next run removes whatever the ledger lists first**, even if the setting has since been turned
  off. Keep the ledger administrator-only: it names the identity that was granted. You can also run
  `python -m collectors.access_grant` to remove leftovers without a review.
- **A removal that fails fails the run**, so it is noticed, and the ledger keeps the entry for the next attempt. A grant
  or removal answer that is not a clean success is confirmed against the workspace's users before the ledger forgets it, because
  a request that is retried after a network error can have been applied.
- Limits: the admin APIs allow 200 requests an hour, so one run handles at most 40 workspaces and reports the rest as
  deferred. Each grant, removal and retry is a Fabric audit event under the executing identity.
- While it holds Contributor or Member the identity can edit content in that workspace, for as long as the read takes.
  Use it only where that is acceptable; owners who object keep their models as missing evidence.
- `raw/access_grants.json` records per workspace the status, the role used and whether it was removed, and totals. It holds
  no identity and nothing from inside a model.

## Footprint in the client's audit log

Authentication and supported API operations can appear in Microsoft Entra and
Fabric/Power BI audit logs under the executing user or application identity.
Available events and retention depend on the service and tenant configuration; do not
assume every API request creates a matching Fabric activity event. Temporary workspace
access, when enabled, adds role-assignment events for each grant and removal.

## Conditional Access caveats

If the client tenant enforces Conditional Access policies that require a managed
device, a compliant device or a specific named network location, sign-in requires an
environment that meets those policies, such as an approved customer-issued jump host
or session host.

## Cross-engagement isolation

Use a separate output folder per engagement. The browser-token cache is named by
`TENANT_ID` and stored at operating-system user scope; separate repository clones do
**not** create separate credential stores. Azure CLI manages its own sign-in cache. On
shared machines, use isolated operating-system accounts and your organization's
sign-out and credential-cleanup procedures.

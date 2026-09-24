<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Optional Workspace Owner report

FAR provides two separate reports: **Governance**, for the central review team,
and **Workspace Owner**, for current direct-user administrators of reviewed
source workspaces. Workspace Owner is opt-in and disabled by default. It has
its own semantic model and row-level security (RLS); it is not the Governance
report with a URL filter applied.

> **Before sharing:** complete [live Fabric acceptance](#live-fabric-acceptance)
> with actual read-only consumers. Creating artifacts is not proof of reader
> isolation. Keep central reports, the central Data Agent and raw sources central-only.

For owner chat, optionally deploy the separate
[Workspace Owner Agent](workspace-owner-agent.md) through **08_OwnerAgent**.
It uses this same secured model and current access grants, not the central Agent's
sources. Setup provisions its notebook only; publication and reader approval are
separate steps.

## Start here: first owner-report deployment

Configure the owner model's data connection once, approve each reader once,
and schedule `07_OwnerAccessSync` to maintain current workspace entitlements.
Do not create a connection or RLS role per workspace.

| Action | When |
| --- | --- |
| Enable owner reporting and configure the model connection | Initial setup |
| Run FAR through Gold | Each review |
| Run `07_OwnerAccessSync` | After the first review, after later reviews as needed, and daily |
| Approve report/model Read and `WorkspaceOwner` membership | Once per approved reader |
| Validate actual reader access | Before sharing and after relevant security changes |

Use [base setup](../fabric/setup.ipynb), not `06_TargetedReviewSetup`, to deploy:

| Parameter | Default |
| --- | --- |
| `DEPLOY_WORKSPACE_OWNER_REPORT` | `"false"`; set to `"true"` to opt in |
| `OWNER_SEMANTIC_MODEL_NAME` | `"Fabric Arch Review - Workspace Owner Model"` |
| `OWNER_REPORT_NAME` | `"Fabric Arch Review - Workspace Owner"` |

The generated notebook is `<NOTEBOOK_PREFIX>_07_OwnerAccessSync` (normally
`FabricArchReview_07_OwnerAccessSync`). The flag is independent of `DEPLOY_GOLD_REPORT`.

1. Deploy while consumers remain unshared. Confirm the seven owner tables,
   separate owner model/report and access-sync notebook exist in the FAR workspace.
   The tables are `owner_workspaces`, `owner_reviews`, `owner_findings`,
   `owner_details`, `owner_executions`, `owner_coverage` and `owner_access`.
2. Attach the notebook to the FAR output Lakehouse and configure the
   [owner model connection](#connect-the-owner-model-click-by-click).
3. Run a scoped FAR review through Gold, then run access sync and wait for its
   model refresh to finish. A new workspace has no grant until sync succeeds.
4. Complete reader approval, live acceptance and organizational sharing checks.
5. Schedule access sync daily with its intended execution identity. Setup does
   not create a schedule. Prevent overlap between daily, targeted and manual runs.

`04_Gold` detects owner enablement through the bootstrapped `owner_access`
table. Turning the deployment flag off later does not stop projections or
revoke access; see [retirement](#retire-or-recover).

**Redeployment:** rerun setup with the same names. Compatible models are reused;
recognized older FAR models upgrade automatically without extra parameters.
The model/report IDs, role members, source connection and review history remain.
Upgrades require capacity XMLA Read Write and save an administrator-only recovery
snapshot in the output Lakehouse before changing metadata.
Unexpected schema, source, security or calculation changes still fail visibly,
as does a report bound to a different model. Do not delete tables or bypass
these checks. See prerequisites and recovery in
[model compatibility troubleshooting](../fabric/DEPLOYMENT.md#existing-owner-model-mismatch).
Use unused artifact names only for an explicitly approved replacement, with
connection and actual-reader validation before sharing.

## From a finding to a tested improvement

| Page | Reader workflow |
| --- | --- |
| **My workspaces** | Select an authorized workspace; check its latest review and evidence coverage before interpreting counts. |
| **Findings** | Filter severity/assessment area, select an action, and read its separate recommendation panel. Clear selection to show all guidance. |
| **Technical details** | Inspect measure, non-measure DAX, Dataflow query, notebook, model-statistics or pipeline-structure evidence by category and exact item ID. |
| **Executions** | Inspect native refresh, pipeline and notebook observations: status, execution ID, UTC start/end and integer milliseconds. |
| **Coverage** | Check collection status, observed/flagged counts and bounded scope. Select a row for collection notes and discrepancies. |

Workspace selections follow page navigation. Item selections are page-specific;
select exact IDs when names repeat. Selecting a finding does not automatically
filter the separate technical-details table. Clear earlier item/category/metric
filters when investigating another item. Use Focus mode for long guidance.

The **Executions** and **Coverage** grids retain workspace, item and review IDs
to distinguish identical names and repeated observations. Scroll horizontally
to inspect the diagnostic identifiers and review context.
Workspace-level execution inventory gaps remain visible in **Coverage** without
representing individual items: these rows use the workspace's identity. They do
not represent executions or inspected technical evidence.

Open the source item using your existing source-workspace permissions. Confirm
the signal, record a representative baseline, test a candidate change for both
correctness and runtime impact, then ask the operator to rerun FAR.

### Interpret evidence correctly

- **Static signals are not measured performance.** DAX pattern points, Dataflow
  `Table.Buffer` / `Table.StopFolding` / `Value.NativeQuery` signals and pipeline
  dependency checks are investigation prompts, not measured query duration or CU.
- DAX measure counts remain measure-only. Calculated columns, calculated tables
  and calculation items are separate object types. A failed model refresh is
  an execution finding, not a DAX finding. Dataflow signals are informational,
  not counted as failed findings.
- `ARCH-016` reports attributable pipeline dependency defects, not runtime
  reliability. Signal-free rows describe coverage only; partial inspection can
  still reveal a concrete structural defect.
- Native execution history is a bounded retained snapshot, not a complete
  ledger. Counts use distinct execution keys in each workspace's latest review.
  Duration `125` means 125 milliseconds; missing timing stays blank.
- `empty` means a successful native response with no observed runs, not a
  collection gap or a passing assessment. `inspected` and `complete` describe
  static extraction, not successful execution, folding or good performance.
- If safely projected counts disagree with collector counts, coverage becomes
  `partial` and explains the discrepancy. Missing inventory means unknown
  coverage, not proof that no items exist.
- **Zero findings or no rows never means pass.** These are partial owner
  assessments, not the full FAR checklist or a tenant/capacity score.

## Scope and data boundary

All owner tables independently enforce workspace RLS. Attribution requires
explicit same-run workspace/item IDs matching collected inventory, never names
or URLs. The projection reconstructs allowlisted recommendations and notices;
it does not copy raw expressions, source code, service errors, literals, mixed
findings JSON, tenant metadata or central scores.

The five pages show the latest review **per workspace**, not the latest global
run. All cards and charts preserve RLS and slicers. Historical reviews remain
secured in the model, but the report is not a history browser. Review freshness
and the 24-hour access TTL are separate concerns.

A workspace reviewed last week remains visible if it is not selected this week,
provided the reader's access is still approved and refreshed by daily 07 sync.
Reviewing that workspace again moves its pages to the new review; it does not
erase its earlier history.

Only owner-enabled reviews populate these tables; central governance history
is not automatically backfilled. Explicit historical Gold reruns still require
trustworthy source IDs. Access sync does not collect reviews or discover more
workspaces.

Setup preserves existing history and rejects unexpected schema changes.
Replaying a run replaces only that run's evidence. Missing grants do not delete
history. Publication can fail partway through; report the failure and rerun the
retained `RUN_ID` to repair it.

### Executions or Coverage missing for a run

Both general and targeted reviews populate these pages; Dataflows are not required.
Compare counts for the **same `run_id`** in `gold_workspaces`,
`gold_item_executions`, `gold_execution_coverage`, `owner_reviews`,
`owner_executions` and `owner_coverage`. Existing owner rows can belong to older runs.

Check collection warnings and same-run native/Scanner inventory. Failed
workspace lookups leave coverage incomplete; inventory never grants reader
access. If no workspace can be attributed safely, owner publication stops before
changing owner tables.

To rebuild a retained run, use a fresh notebook session and rerun `04_Gold` with
the original `RUN_ID` and intended code ref. Usable inventory (`scanner.json` or
native workspace metadata), raw execution snapshots and findings must remain.
Verify the printed owner row counts. Workspaces absent from both inventories
require a new Collect run; do not delete tables or change RLS to recover evidence.

## Identity and eligibility

The single dynamic read role, `WorkspaceOwner`, matches `USEROBJECTID()` to
resource-tenant object IDs for current **direct-user source-workspace Admins**.
Groups and service-principal admins are excluded. Guests require their
resource-tenant object ID, not their home-tenant ID or email.

Every table requires a nonblank matching workspace/user grant, issuance not in
the future, and an unexpired interval of at most 24 hours. `owner_access` is
disconnected from fact relationships and independently secured. Hiding a table
or applying a URL filter is not authorization.

**Never give owner consumers FAR workspace membership, including Viewer.**
Admin/Member/Contributor bypass model RLS; Viewer can expose other unrestricted
central artifacts. Use item-scoped Read or an app audience limited to owner
artifacts. Do not give readers raw Lakehouse/OneLake/SQL access, central Governance
or Data Agent access, Build, Write or Reshare permissions to make this report work.

## Connection and manual access approval

### Connect the owner model: click-by-click

As an authorized maintainer:

1. In the FAR workspace, open **... > Settings** on the **Workspace Owner
   semantic model**, not the report or Governance model.
2. Under **Gateway and cloud connections**, use the source's **Maps to** control
   to select or create a connection to the FAR output Lakehouse's SQL analytics
   endpoint and database.
3. Configure an approved, least-privilege source-reading identity. For an
   approved OAuth sandbox connection, sign in with that account. Keep Microsoft
   Entra **SSO disabled**; verify its saved setting in **Manage connections and
   gateways** if it is not visible in the creation pane.
4. Apply the connection mapping and refresh the owner model. Confirm completion.
   Keep `DirectLakeOnly`; do not enable fallback or grant readers source access
   to bypass credential failures.
5. Test with an actual read-only consumer. A successful maintainer refresh alone
   does not prove reader isolation.

The fixed connection identity reads the source; `USEROBJECTID()` still identifies
the actual reader for RLS. Disabling source SSO does not disable reader sign-in.
See Microsoft's [connection workflow](https://learn.microsoft.com/en-us/power-bi/connect-data/service-connect-cloud-data-sources#create-a-new-shareable-cloud-connection)
and [Direct Lake security guidance](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-security-integration#connection-configuration).

### Approve each new reader

1. Confirm current direct-user Admin eligibility and a successful sync.
2. Approve effective **Read** on both owner report and owner model, or use a
   narrowly scoped owner-only app audience. Disable Build and Reshare.
3. On the owner model's **... > Security**, add the person to the existing
   **WorkspaceOwner** role. Do not create per-workspace roles or change its rules.
4. Have the reader open the report with their own account and verify the exact
   workspace set. Read plus role membership grants no rows without a current
   entitlement; an entitlement alone does not share the report.

See Microsoft's [RLS role-membership instructions](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security#manage-security-on-your-semantic-model).

### Labels are a separate policy step

Apply organizational sensitivity-label and sharing requirements before broader
sharing. Labels do not configure the connection, grant role membership or filter
rows; owner deployment does not copy Governance labels or endorsement.

## Daily entitlement sync

`07_OwnerAccessSync` processes all retained owner-projected workspaces, not only
the latest review selection or email recipients. Its execution identity needs
admin group-user lookup, entitlement-write and owner-model refresh permissions.
Reader permissions are separate.

Each sync:

1. Clears old grants, refreshes the owner model and waits for completion.
2. Queries current direct-user admins for every owner-projected workspace.
3. Replaces the complete grant snapshot with 24-hour entitlements.
4. Refreshes the owner model again and waits before reporting success.

HTTP 404 denies that workspace, logs its ID and warnings, retains history, and
allows other workspaces to synchronize. Other lookup, write and refresh failures
propagate; there is no old-grant fallback. If invalidation or refresh cannot be
confirmed, stop consumer sharing until repaired and revalidated. Do not restore
old grants or extend TTLs to hide failures.

For HTTP 404, check workspace existence, tenant and execution identity rather
than assuming deletion. If the final refresh fails, fresh grants may already
be stored, but the sync has not succeeded.

The admin endpoint permits 200 requests/hour; requests are spaced 20 seconds
apart. Allow time and throttling headroom for large estates. Schedule daily and
prevent overlapping daily, targeted or manual runs; do not redeploy during sync.
Monitor completion and expiry, not just the schedule. Invalidation causes an
intentional deny window, and delayed/failed runs may leave readers denied.
Revocation is not instantaneous.

A targeted parent bound to the existing 07 notebook runs it after successful
FAR and before notification preparation, even when notifications are off.
Empty selections skip that step. Standalone FAR is unchanged: run 07 manually
after review or use the daily schedule. Email lookup is not entitlement sync,
and sync does not approve Read access or role membership.

## Native email links

The owner model's `gold_workspace_risk` alias exposes only `owner_workspaces`,
not the central score table. The native `gold_workspace_risk/workspace_id` URL
filter is navigation, never authorization.

After access validation, set the targeted parent's `FAR_REPORT_URL` to the
**owner report URL**. Targeted setup does not create that report or change the
URL automatically. Never share Governance with owners as a shortcut.
See [native email setup](targeted-review.md#3-optionally-configure-native-outlook-owner-email).

## Live Fabric acceptance

Record results in approved private operational records. Test actual item-scoped
read-only consumers, not only **Test as role**, builders or FAR administrators.

| Test | Required result |
| --- | --- |
| No role, no grant, expired grant | No owner data and no central fallback |
| Single/multiple authorized workspaces | Exact eligible set across all five pages and latest-per-workspace counts |
| URL tampering, cleared filters, export, drillthrough | No broader access or hidden raw/central route |
| Revocation and delayed/failed sync | Removed admins lose access after validated invalidation/refresh or expiry; failure is visible |
| Guest identity | Resource-tenant object ID is used |
| Connection and permissions | Fixed identity, SSO disabled, no consumer FAR workspace/source/central access |
| Compatible redeployment and mismatches | Members/bindings preserved; schema/security/calculation or report-binding mismatch refuses overwrite |
| Native and static evidence | Milliseconds/nulls preserved; repeated names stay separate; static signals never imply runtime success |
| Review history and recovery | One latest review per workspace; same-run replay repairs evidence without duplicates or loss of other runs |
| Membership lookup 404 | Workspace denied with warnings; history retained; other workspaces synchronize |
| Targeted post-review sync | Sync completes before email preparation; failure prevents email; empty selections skip |

Include fresh and already-open/cached sessions in expiry/revocation tests.
`UTCNOW()` is evaluated on query/formula reevaluation, not continuously on cached
visuals. Validate actual model/cache behavior; TTL does not guarantee an open
visual disappears at expiry. Previously exported/downloaded data cannot be
retroactively revoked; apply organizational export and retention policies.
Local schema and synthetic runtime tests do not certify live Fabric behavior.

## Retire or recover

Setting `DEPLOY_WORKSPACE_OWNER_REPORT="false"` only skips optional deployment.
It does not revoke sharing, remove grants, stop schedules or disable projections
on an already-enabled installation. To retire, remove consumer access and role
membership, invalidate entitlements and confirm model refresh, then disable the
schedule and retire artifacts under tenant policy.

For recovery, stop overlapping work, investigate the explicit failure, repair
the intended connection or data state, and rerun the retained review/sync as
appropriate. Revalidate security before resuming sharing. Do not bypass the
compatibility guards or delete history to make setup succeed.

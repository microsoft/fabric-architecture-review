<!--
Copyright (c) Microsoft Corporation. All rights reserved.
Licensed under the MIT License. See LICENSE.TXT in the project root for full license information.
-->

# Workspace Owner architectural report

The optional owner report shows approved workspace administrators the findings,
inventory and evidence coverage for their own workspaces. It uses a separate,
secured model. Follow this guide to connect the model, approve readers and verify access.

## What owners see

- Findings for their workspaces, with recommended actions and the settings that control each check.
- Item inventory, storage choices and observed semantic-model RLS/OLS.
  Optional [role details](far-policy-configuration.md#role-detail-optional) include role names,
  permissions and counts, never filter text or role membership.
- Evidence coverage, including incomplete or unavailable inspections.
- The latest compatible review for each workspace. Tenant-wide and capacity-wide findings
  are not included.

Owner reviews have no global score. Signals and evidence gaps are not failures,
and zero failures does not prove complete coverage.

## Authorization

Deploy the owner artifacts separately from the central artifacts. Do not give the governance model,
the central Agent, the app or the Lakehouse to an owner audience. Report audience selection and slicers
are not authorization.

Row-level security (RLS) enforces each reader's current workspace grants.
Run **07_OwnerAccessSync** daily; grants expire after 24 hours. Missing, expired or
revoked grants return no owner data. Reader approval and role membership are separate
steps. Report filters and inventory ownership do not grant access.

### Identity and eligibility

The single read role, `WorkspaceOwner`, matches `USEROBJECTID()` to the resource-tenant object IDs of the
current **direct-user Admins** of a workspace. Groups and service-principal admins are excluded. Guests need
their resource-tenant object ID, not their home-tenant ID or email.

Hiding a table or applying a URL filter is not authorization.

**Never give owner readers membership of the FAR workspace, not even Viewer.** Admin, Member and
Contributor bypass RLS, and Viewer can expose other central artifacts. Use item-scoped Read, or an app
audience limited to the owner artifacts. Do not give readers raw Lakehouse, OneLake or SQL access, access to
the central governance model or Data Agent, or Build, Write or Reshare permission to make the report work.

## Deploy and connect

Owner deployment and synchronization are explicit opt-ins (`DEPLOY_WORKSPACE_OWNER_REPORT="true"` in setup).
Setup creates the owner model and report and the notebooks **07_OwnerAccessSync** and **08_OwnerAgent**. It
grants no permission and shares nothing. Resolve any setup validation errors before sharing.

### Connect the owner model

As an authorized maintainer:

1. In the FAR workspace, open **... > Settings** on the **Workspace Owner semantic model**, not the
   report or the governance model.
2. Under **Gateway and cloud connections**, use the source's **Maps to** control to select or create a
   connection to the SQL analytics endpoint and database of the FAR output Lakehouse.
3. Configure an approved, least-privilege identity that reads the source. For an approved OAuth sandbox
   connection, sign in with that account. Keep Microsoft Entra **SSO disabled**; if it is not visible in the
   creation pane, check the saved setting in **Manage connections and gateways**.
4. Apply the mapping and refresh the owner model, then confirm that it completed. Keep `DirectLakeOnly`; do not
   enable fallback or give readers source access to work around a credential failure.
5. Test with a real read-only reader. A successful refresh by the maintainer does not prove reader isolation.

The fixed connection identity reads the source; `USEROBJECTID()` still identifies the actual reader for RLS.
Disabling source SSO does not disable reader sign-in. See Microsoft's
[connection workflow](https://learn.microsoft.com/power-bi/connect-data/service-connect-cloud-data-sources#create-a-new-shareable-cloud-connection)
and [Direct Lake security guidance](https://learn.microsoft.com/fabric/fundamentals/direct-lake-security-integration#connection-configuration).

### Approve each new reader

1. Confirm that the person is a current direct-user Admin and that the last sync succeeded.
2. Approve effective **Read** on both the owner report and the owner model, or use an app audience limited to
   the owner artifacts. Disable Build and Reshare.
3. On the owner model's **... > Security**, add the person to the existing **WorkspaceOwner** role. Do not create
   per-workspace roles or change its rules.
4. Have the reader open the report with their own account and check the exact set of workspaces. Read access
   and role membership return no rows without a current entitlement, and an entitlement alone does not share
   the report.

See Microsoft's [RLS role-membership instructions](https://learn.microsoft.com/fabric/security/service-admin-row-level-security#manage-security-on-your-semantic-model).

### Labels

Apply your sensitivity-label and sharing requirements before broader sharing. Labels do not configure the
connection, grant role membership or filter rows, and deployment does not copy labels or endorsement from the
governance model.

## Daily entitlement sync

`07_OwnerAccessSync` processes every owner-projected workspace, not only the latest review selection or the
email recipients. Its identity needs admin group-user lookup, entitlement-write and owner-model refresh
permission. Reader permissions are separate.

Each sync:

1. Clears old grants, refreshes the owner model and waits for it to complete.
2. Queries the current direct-user admins of every owner-projected workspace.
3. Replaces the whole grant snapshot with 24-hour entitlements.
4. Refreshes the owner model again and waits before reporting success.

An HTTP 404 denies that workspace, logs its ID and a warning, keeps its history and lets the other workspaces
synchronize. Any other lookup, write or refresh failure stops the sync; there is no fallback to old grants. If
invalidation or the refresh cannot be confirmed, stop sharing with readers until it is repaired and revalidated.
Do not restore old grants or extend the lifetime to hide a failure.

For a 404, check that the workspace exists, the tenant and the execution identity instead of assuming it was
deleted. If the final refresh fails, fresh grants may already be stored, but the sync has not succeeded.

The admin endpoint allows 200 requests an hour, so requests are spaced 20 seconds apart. Allow time for large
estates. Schedule the sync daily and prevent overlapping daily, targeted and manual runs; do not redeploy during
a sync. Monitor completion and expiry, not only the schedule. Invalidation causes an intentional window in which
readers are denied, a delayed or failed run can leave readers denied, and revocation is not instantaneous.

A targeted parent bound to 07 runs it after a successful review and before notifications are prepared, even
when notifications are off, and skips it for an empty selection. A standalone review does not run it: run 07
after the review or on its daily schedule. Email lookup is not entitlement sync, and sync does not approve Read
access or role membership.

## Email links

The owner model's `gold_workspace_risk` exposes only `owner_workspaces`, never the central score table. The
`gold_workspace_risk/workspace_id` URL filter is navigation, not authorization. After access is validated, set
the targeted parent's `FAR_REPORT_URL` to the **owner report URL**; setup does not create the report or set the
URL for you. Never share the governance report with owners as a shortcut. See
[native email setup](targeted-review.md#3-optionally-configure-native-outlook-owner-email).

## Live Fabric acceptance

Record the results in your approved operational records. Test with real item-scoped read-only readers, not only
**Test as role**, builders or administrators.

| Test | Required result |
| --- | --- |
| No role, no grant, expired grant | No owner data and no central fallback |
| One or several authorized workspaces | Exactly the eligible set on all five pages, with the latest review per workspace |
| URL tampering, cleared filters, export, drill-through | No broader access and no hidden raw or central route |
| Revocation and a delayed or failed sync | Removed admins lose access after a validated invalidation and refresh, or at expiry; a failure is visible |
| Guest identity | The resource-tenant object ID is used |
| Connection and permissions | Fixed identity, SSO disabled, no reader access to the FAR workspace, source or central items |
| Redeployment and mismatches | Members and bindings are kept; a schema, security, calculation or report-binding mismatch refuses to overwrite |
| Review history and recovery | One latest review per workspace; replaying a run repairs evidence without duplicates or losing other runs |
| Membership lookup 404 | The workspace is denied with a warning, its history is kept and the others synchronize |
| Targeted post-review sync | Sync completes before email preparation; a failure prevents email; an empty selection skips it |

Include fresh and already-open sessions in the expiry and revocation tests. `UTCNOW()` is evaluated when a query or
formula is reevaluated, not continuously on a cached visual, so a lifetime does not guarantee that an open visual
disappears at expiry. Data that was exported or downloaded cannot be revoked afterwards; apply your export and
retention policies. Local tests of the schema and the runtime do not certify live Fabric behavior.

## Retire or recover

Setting `DEPLOY_WORKSPACE_OWNER_REPORT="false"` only skips the optional deployment. It does not revoke sharing,
remove grants, stop schedules or disable the projection of an installation that already has it. To retire the
report, remove reader access and role membership, invalidate the entitlements and confirm a model refresh, then
disable the schedule and retire the artifacts under your tenant policy.

To recover, stop overlapping work, find the explicit failure, repair the intended connection or data state and
rerun the retained review or sync. Revalidate security before sharing again. Do not bypass the compatibility
guards or delete history to make setup succeed.

The optional [owner Agent](workspace-owner-agent.md) uses this same verified model.

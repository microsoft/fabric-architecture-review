<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Optional targeted reviews

**Goal:** let FUAM choose the workspaces FAR reviews, optionally notifying their
administrators afterwards. For the shortest setup path, follow
[deployment step 5](../fabric/DEPLOYMENT.md#5-optionally-enable-fuam-targeted-reviews).
This page is the detailed parameter and operations reference.

| Task | Go to |
| --- | --- |
| Upgrade an existing parent | [Redeployment](#redeployment) |
| Change scope, count or lookback | [Runtime parameters](#runtime-parameters) |
| Diagnose FUAM source or selection failures | [Source contract](#supported-fuam-source-contract) and [failure behavior](#selection-and-failure-behavior) |
| Configure or troubleshoot email | [Outlook email](#3-optionally-configure-native-outlook-owner-email) |
| Confirm the deployment works | [Verification](#deployment-verification) |

Targeted review is an orchestration layer around FAR, not a replacement for the
standalone review. Base setup deploys disabled `06_TargetedReviewSetup` but does
not start monitoring queries, create a schedule, or send messages. The setup
order is **base setup -> opt-in 06 -> targeted parent**. Configure optional native
Outlook email directly in the generated parent.

For a workspace-owner audience, independently opt in to the
[Workspace Owner report](workspace-owner-report.md) through **base setup**.
`06_TargetedReviewSetup` never creates that report. Existing central governance
model/report and Data Agent remain central-only and are **not secured** by this
feature. Existing pipeline bindings do not change.

## 1. Deploy standalone FAR

1. Deploy FAR using [standard setup](../fabric/setup.ipynb). For a step-by-step
   walkthrough from an empty workspace, follow the [Fabric deployment guide](../fabric/DEPLOYMENT.md).
2. Run the normal FAR pipeline for a test workspace and confirm the report is
   available before adding monitoring-driven selection.

## 2. Enable FUAM-based selection

Open the deployed `06_TargetedReviewSetup` notebook. Change only these basic settings:

```python
DEPLOY_TARGETED_REVIEW = "true"
FUAM_WORKSPACE_ID = "<FUAM workspace GUID>"
FUAM_LAKEHOUSE_ID = "<FUAM Lakehouse GUID>"
CAPACITY_ID_OR_NAME = ""  # All; or one name/GUID; or '["Capacity A", "Capacity B"]'
RANKING_METRIC = "cu_seconds"
LOOKBACK_DAYS = "7"
TOP_N = "5"
```

Its **advanced wiring** (FAR workspace, output Lakehouse, child pipeline and
repository settings) is stamped by base setup. Normally leave that section alone.
The FAR output Lakehouse remains attached; it is not the FUAM source Lakehouse.
FUAM is the supported monitoring source.

When base setup deploys the optional owner report, it stamps the existing 07
notebook's GUID into **`OWNER_ACCESS_NOTEBOOK_ID`** in 06's advanced wiring.
Normally leave it unchanged. A blank value omits post-review access sync;
a nonblank value must identify an existing notebook in the FAR workspace or
setup fails. This is a setup binding, not a parent run parameter, and does not
change the standalone FAR pipeline. Rerun base setup and then all cells of 06
to apply this binding to an existing targeted parent.

1. Run the notebook. It creates a selection **Runner**, a **Completion** notebook
   that prepares email records only, and a native same-workspace targeted-review
   parent. On a new deployment the parent's native email activity is inactive,
   has no connection, and `NOTIFICATIONS_ENABLED="false"`. Setup sends no messages.
   The original four-stage FAR pipeline is not modified.
2. **Success:** the output lists the parent pipeline, selection runner and
   completion notebook IDs, plus the bound access-sync notebook ID when enabled.
   In **Review selected workspaces**, confirm **Sync owner access** follows
   **Record FAR run ID** before enabling notifications.
3. **Next:** run the parent manually with a test-workspace `WORKSPACE_IDS` allow-list.
   Confirm its selection and successful child run before configuring a weekly
   schedule through Fabric.

Five is the default maximum, not a fixed limit: `TOP_N` accepts 1-100. The
lookback window is separate from the pipeline's schedule frequency.

**Capacity scope:** select one capacity, a JSON list of capacities, or leave the
filter blank for all FUAM capacities. `TOP_N` selects workspaces across the
combined scope, not per capacity. High consumption is a prioritization signal,
not proof of a performance problem. Capacity filtering does not grant report access.

### Redeployment

Rerunning 06 preserves the existing Outlook connection, optional **From**
sender, email activation state and `FAR_REPORT_URL`, but deliberately resets the
parent's `NOTIFICATIONS_ENABLED` default to `"false"`. Generated **To**, **Subject**
and **Body** are restored; manual recipient overrides are not retained. Other
setup defaults are regenerated from the notebook settings. Existing schedule overrides may still
enable notifications; inspect them before resuming.

Before upgrading, pause the parent schedule and let active runs finish. Rerun
**all cells** of 06 against the intended repository branch/ref, including after a
partial deployment. Keep the same artifact names and IDs: setup updates the
existing parent and stage notebooks. There is
no need to delete the FAR Lakehouse or standalone pipeline. Do not
run the parent until 06 completes: both stage notebooks and the parent parameter
contracts must be upgraded together.

Setup validates saved email settings before provisioning. Only one operator
should edit or redeploy the parent at a time.

Both stage notebooks run the deployed code stored in the FAR Lakehouse.
No repository download or GitHub token is required at run time.
The deployment identity needs write access to that Lakehouse; the execution
identity needs read access. Treat these runtime packages as executable artifacts:
only trusted deployers should have write access.

## Runtime parameters

The parent exposes all parameters from the deployed child pipeline, preserving
their defaults except `WORKSPACE_IDS`. That parent allow-list starts blank so
FUAM selection does not inherit a standalone review's scope. The standalone
pipeline itself is unchanged. Re-run optional setup after changing the child's
parameter contract.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `FUAM_WORKSPACE_ID` | blank | Workspace containing FUAM monitoring data |
| `FUAM_LAKEHOUSE_ID` | blank | Explicit FUAM source Lakehouse; required if its workspace has more than one Lakehouse |
| `CAPACITY_ID_OR_NAME` | blank | One GUID/exact FUAM capacity name, or a JSON list of names/GUIDs; blank ranks across all capacities |
| `RANKING_METRIC` | `cu_seconds` | CU-seconds or `recorded_throttling_minutes`; never substitutes operation counts or another metric |
| `LOOKBACK_DAYS` | `7` | Last 1-28 complete reporting dates ending before today's UTC date; FUAM retention still applies |
| `TOP_N` | `5` | Maximum workspace count, 1-100 |
| `WORKSPACE_IDS` | blank | Optional allow-list applied **before** ranking; blank ranks all FUAM candidates; selected IDs replace this value for the child |
| `NOTIFICATIONS_ENABLED` | `false` | Boolean string `true`/`false`; enables the success-only owner-email branch after configuring and activating its native email activity |
| `FAR_REPORT_URL` | blank | Fabric/Power BI report link used in owner messages |

A nonblank `WORKSPACE_IDS` allow-list must contain at least one valid workspace
GUID. Separator-only values such as `,` are rejected rather than treated as an
unrestricted selection.

After owner live acceptance, manually set `FAR_REPORT_URL` to the **owner
report**, rather than granting recipients access to the central report.
The owner model supports the native email URL filter without exposing the
central risk table or full FAR score. URL filters are navigation, never authorization.

Workspace, pipeline and runner IDs are deployment wiring, not user-overridable run
parameters. All other FAR parameters, including engagement labels, collection
flags and analysis thresholds, pass through to the child.

Setting `NOTIFICATIONS_ENABLED="true"` alone does not configure email. Enabled
runs validate `FAR_REPORT_URL`; if the email activity is reached before it
is activated, it fails rather than silently appearing sent. Configure the connection
and optional sender in the parent's native activity, not the FAR child.

Enter ordinary strings in the parent parameters, leaving optional values blank
when appropriate. Run the parent rather than its stage notebooks directly; it
supplies the required parameter bindings. If a stage reports missing or malformed
injected parameters, rerun all cells of 06 to deploy a consistent parent and
runtime package. Do not bypass validation by changing stage notebook defaults.

For an unexpected skip, check the run's effective parameters, including saved
schedule overrides, and the source/selection logs:

- Confirm the FUAM workspace/Lakehouse, metric and UTC window (`window_start`
  inclusive, `window_end_exclusive` exclusive).
- `source_rows` counts aggregated workspace rows, not raw operations. Zero points
  to the source or date window; nonzero rows with no `selected_workspaces` point
  to zero metrics, exclusions or a restrictive `WORKSPACE_IDS` allow-list.
- Clear the parent's allow-list for automatic top-N selection. The FAR hosting
  workspace and FUAM workspace are always excluded, even if allow-listed.

### Select capacities

Set `CAPACITY_ID_OR_NAME` in 06 to save the parent's default, or override it on
a parent run or schedule. Blank (including whitespace), omitted values and null
mean all capacities. For multiple capacities, enter a JSON list **as a string**:

```python
CAPACITY_ID_OR_NAME = '["Capacity A", "Capacity B", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]'
```

In the pipeline parameter UI, enter `["Capacity A", "Capacity B"]` without the
outer Python single quotes. Names and GUIDs may be mixed. Duplicate references
to the same capacity do not double-count usage. An empty list (`[]`), malformed
JSON, or an invalid entry fails; only blank selects all capacities.

A name must match `dbo.capacities.displayName` exactly after
trimming and case-insensitive comparison; partial matching is not used. Unknown
names and names matching multiple distinct capacity GUIDs fail. Use a GUID to
disambiguate. Names are resolved from the configured FUAM Lakehouse, not live
Fabric workspace assignments.

The resolved GUIDs filter `CapacityId` on **metric rows before workspace
aggregation and ranking**, for either metric. A workspace that moved capacities
contributes only the usage recorded on the selected capacities during the window.
Usage is summed per workspace across the selected set, then one global `TOP_N`
is taken; it is not `TOP_N` per capacity.
`WORKSPACE_IDS` remains an additional candidate allow-list; hosting/FUAM
exclusions, positive-metric ranking, tie-breaking and `TOP_N` still apply.
The child reviews the selected workspaces normally; capacity targeting does not
restrict individual items collected within them.

GUID input is validated and queried directly, without requiring a current
capacity inventory entry. A GUID with no positive in-window metrics contributes
no candidates (including an unknown but syntactically valid GUID). If the entire
scope has no eligible metrics, the review is skipped; check the GUIDs and FUAM
retention if unexpected. Any name-lookup, schema or permission failure stops
the entire selection rather than silently dropping a capacity or broadening scope.
The source log includes the resolved capacity GUIDs; the audit retains the
requested and resolved capacity scope, including for skipped runs. Changing the
capacity parameter does not allow selection to be repeated within the same
parent run.

### Pipeline canvas

1. **Select workspaces** runs the short selection notebook and persists its scope.
2. **Review selected workspaces** is a nonempty-scope condition. Open its true
   branch to see **Invoke FAR**, a native same-workspace `ExecutePipeline`
   activity with `waitOnCompletion=true`, followed by **Record FAR run ID**.
   When 07 is bound, this branch also runs **Sync owner access** after FAR
   succeeds and before notifications. Normal FAR has no such added step.
3. **Notify owners if enabled** requires a successful child run and
   `NOTIFICATIONS_ENABLED="true"`. Its branch runs **Prepare owner emails**
   (the Completion notebook), then **Store owner emails** to save the complete
   recipient list in `FAR_OWNER_EMAILS`.
4. **Send owner emails** is a sequential `ForEach` on the main canvas, after the
   notification condition succeeds. It runs **Email workspace administrator**
   (`Office365Email`) once per prepared record. The array starts empty, so disabled
   notifications or an empty selection produce no iterations. Preparation or
   storage failure prevents the loop from running. Preparation, storage, native
   child invocation and native mail activities use secure inputs and outputs.
   Mail retry is `0`.

The native invocation needs no additional Fabric connection, service principal,
or workspace identity. Selection finishes before FAR starts. The generated
activity timeout is 12 hours with zero automatic retries.
Inspect the parent's **Invoke FAR** activity in monitoring after
timeout or cancellation rather than assuming the child stopped.

The Completion notebook does not send email. Native mail activities use the
manually authorized Office365 Outlook connection. With notifications off, the
completion branch is skipped and no recipients are resolved.

## Supported FUAM source contract

The adapter uses the documented
[Fabric Spark SQL connector](https://learn.microsoft.com/fabric/data-engineering/spark-data-warehouse-connector)
to query the configured FUAM Lakehouse SQL analytics endpoint across workspaces.
It reads only:

- `dbo.capacity_metrics_by_item_by_operation_by_day`: `CapacityId`, `WorkspaceId`, `Date`,
  `TotalCUs` (CU-seconds), or `ThrottlingInMin` (minutes).
- `dbo.workspaces`: `WorkspaceId` and `WorkspaceName`, joined by GUID after
  aggregation, never by display name.
- `dbo.capacities`: `CapacityId` and `displayName`, only when resolving a capacity
  name. The bounded inventory is matched locally; input names are never embedded
  in SQL. The metrics predicate contains only a validated GUID.

These contracts were verified against the
[FUAM source at c38ea357](https://github.com/microsoft/fabric-toolbox/tree/c38ea357b804b335d1ed3b558dda38cda778cf70/monitoring/fabric-unified-admin-monitoring).
The stock [capacity inventory definition](https://github.com/microsoft/fabric-toolbox/blob/c38ea357b804b335d1ed3b558dda38cda778cf70/monitoring/fabric-unified-admin-monitoring/src/FUAM_Core_SM.SemanticModel/definition/tables/capacities.tmdl)
and [operation/day metrics definition](https://github.com/microsoft/fabric-toolbox/blob/c38ea357b804b335d1ed3b558dda38cda778cf70/monitoring/fabric-unified-admin-monitoring/src/FUAM_Core_SM.SemanticModel/definition/tables/capacity_metrics_by_item_by_operation_by_day.tmdl)
confirm these capacity columns and `dbo` tables.
FUAM is a community/open-source monitoring solution, not a Microsoft-supported
product. This feature uses documented Fabric platform APIs and does not modify
FUAM or promise compatibility with arbitrary FUAM schema changes.

**Recorded throttling is potentially incomplete.** Stock FUAM extraction filters
out item/operation/day groups with zero CU, so their throttling/rejection metrics
can be absent. The option is labelled recorded minutes in parameters and audit
output. It is neither a count of throttled operations nor proof of the cause of
capacity overload. Missing columns, permissions or Lakehouses fail explicitly.

**Why no direct Capacity Metrics adapter:** Microsoft states that
[custom consumption of the Capacity Metrics semantic model is unsupported](https://learn.microsoft.com/fabric/enterprise/metrics-app#considerations-and-limitations).
Targeted review does not use that model. If FUAM is unavailable, configure it
first or run standalone FAR.

## Selection and failure behavior

- Before ranking, match FUAM workspace GUIDs against the complete current Power BI
  admin workspace inventory (`$top`/`$skip` pagination). The execution identity
  needs permission to read this tenant inventory; there is no member-scope fallback.
  Unmatched IDs are excluded with an explicit warning, **not classified as deleted**.
  The selection audit's `workspace_inventory` records the source, completeness,
  inventory count, `unmatched_count`, `unmatched_ids`, and reason. Known unsupported
  workspace metadata is also excluded and recorded as `unsupported_count`/`unsupported_ids`;
  absent policy fields alone do not exclude a matched workspace.
  These exclusions happen before `TOP_N`, so the next eligible workspaces fill
  the slots within the existing capacity and allow-list scope. If none remain,
  FAR and email are safely skipped. Inventory authentication, service, malformed
  response, or pagination errors stop selection rather than fabricating emptiness.
- Exclude the FAR hosting workspace (`CHILD_WORKSPACE_ID`) and the configured
  FUAM workspace (`FUAM_WORKSPACE_ID`) by GUID before ranking and taking `TOP_N`.
  These exclusions override the candidate allow-list and apply to both metrics;
  remaining eligible workspaces fill the available slots. No name-based discovery
  or additional parameter is needed. Standalone FAR's scope is unchanged.
- Rank by a positive metric, descending, with workspace GUID as a deterministic
  tie-breaker. Aggregate multiple rows for the same workspace.
- FUAM metric groups with a null, empty or whitespace-only `WorkspaceId` cannot
  identify a review target. They are excluded with a warning, and their count
  is saved as `unattributed_workspace_groups` in the selection audit. Their
  consumption is not assigned by workspace name or redistributed to other
  workspaces. Nonblank malformed IDs still stop selection. If no eligible
  workspaces remain, FAR and notifications are skipped.
- High consumption is a selection signal, **not proof of poor performance**.
  Throttling attribution is different from identifying which workload caused a
  capacity overload.
- Query/authentication/schema errors stop the parent. They must never be
  reinterpreted as an empty successful query or silently downgrade to CU ranking.
- An empty selection is a logged skip. It never passes an empty workspace list to
  FAR (which would otherwise mean tenant-wide collection).
- The parent invokes FAR with a native pipeline activity and waits for completion.
  Success-only dependencies gate email preparation; a failed/cancelled child
  cannot trigger notifications. The Completion notebook validates the recorded
  selection and resolves owners; it does not make a separate child-status request.
  Run it only through the generated parent, never as a standalone notebook.
- A workspace can disappear or become inaccessible after selection. Owner lookup
  still fails all email preparation in that case; no partial recipient list is sent.
  Failures include a sanitized service code/request ID, never the response body.
  Check the execution tenant, current workspace identity and FUAM source; a 404
  alone does not prove deletion. Reconcile the prior run and start a new parent
  run after correction rather than bypassing the notification replay guard.
- Avoid overlapping scheduled runs. Test on the intended capacity before
  scheduling, including source access and the existing FAR stages.
- Automatic activity retries are disabled. A saved selection blocks re-execution
  of the selection stage for the same parent ID. An email-preparation replay
  guard blocks re-preparation for that parent run. These safeguards are
  **not** an exactly-once guarantee: manually rerunning from the native child
  activity can bypass selection. Inspect monitoring before manual recovery.

Audit records are in `Files/far-orchestration/runs/<parent-run-id>.json`. They retain
the selected workspace IDs and metric values, source, lookback and fixed child
pipeline IDs. **Fabric pipeline monitoring is authoritative for child status.**
With notifications disabled, the selection audit remains `selected`, even after
FAR succeeds; this does not mean the child is still running. Enabled completion
preparation adds the native `child_run_id` and preparation state. Records do not store
the complete parameter dictionary, tokens, or raw source operation rows.

Audit `emails_prepared` means records were prepared, **not that mail was
delivered**. Inspect native mail activity outcomes and actual delivery before
retrying. A new parent run can duplicate messages; do not bypass the replay guard
or enable automatic retries.

## 3. Optionally configure native Outlook owner email

Configure email after the parent and child complete a scoped review with
notifications off. Saving configuration does not send mail.

### Required one-time Outlook configuration

Follow the [Outlook setup and scoped test](../fabric/DEPLOYMENT.md#6-optionally-configure-native-outlook-owner-email)
in the deployment guide: authorize the activity's connection, activate it, save,
and set `FAR_REPORT_URL`. After setup, `NOTIFICATIONS_ENABLED` is the run-time
switch for owner lookup and sending.

The email activity is initially **Inactive** with **Mark activity as Failed**.
[Inactive activities are excluded from Fabric validation](https://learn.microsoft.com/en-us/fabric/data-factory/activity-overview#deactivate-an-activity),
so a connection is not required for notification-disabled runs. If an enabled
run reaches the activity before activation, it fails rather than reporting
successful sending.

The email is a **notification, not a findings or task list**. It includes workspace
name/ID, a filtered report link, rank, metric/value, lookback, a metric caveat and
tracking ID. The central report's filter affects workspace-risk visuals, not the
entire report; keep that report central-only.

For owners, use the validated **Workspace Owner report**, whose RLS restricts its
curated evidence to authorized workspaces. Recipients can select **Request access**
on the linked report, or contact its owner if unavailable. Operators must complete
[reader approval](workspace-owner-report.md#approve-each-new-reader).
Links never grant access. Do not grant FAR workspace membership, including
Viewer, raw Lakehouse/SQL access or Build to make the link work.

Recipients are **current direct-user workspace administrators** (`User`/`Admin`),
resolved using the Power BI admin group-users API, not business owners or a
FUAM-maintained email list. There is no global fallback
recipient, group expansion, or chat/channel alternative. Failed owner lookup,
missing usable owner email, or a workspace with no eligible direct-user admin
blocks preparation for **all** workspaces before any mail activity starts.
Completion prepares all records only after the parent's native success dependency
is satisfied; it never sends mail itself.

The Outlook account and optional **From** select the sender, not the recipients.
There is no test-recipient override. Use a dedicated test workspace rather than
changing production permissions to test mail.

### Owner access freshness is not the review schedule

The optional owner report requires **daily** `07_OwnerAccessSync`, scheduled
independently of reviews or email. It covers **all owner-projected workspaces**,
not just `TOP_N` or the current recipients. Sync does not discover the full tenant
or backfill governance history.

When 06 is bound to the existing 07 notebook, **only the targeted parent** runs
access sync after successful FAR/Gold and before preparing email. It runs even
when notifications are disabled; an empty selection skips it. Sync failure
blocks email preparation. Without that binding, the parent retains its original
flow. The normal FAR pipeline remains unchanged.

New workspaces have no grants until sync succeeds. Grants expire within 24 hours,
so weekly reviews cannot replace daily sync. Avoid overlapping daily, targeted
and manual runs; follow [daily sync operations](workspace-owner-report.md#daily-entitlement-sync)
for permissions, throttling and recovery. Neither sync nor email-recipient lookup
approves report Read or role membership.

Before sharing, configure the owner model's fixed-identity connection with SSO
disabled and complete [live Fabric acceptance](workspace-owner-report.md#live-fabric-acceptance).
Disabling the deployment flag does not revoke sharing.

The sequential native mail loop sends one email per owner per workspace, not
one tenant-wide summary. Secure inputs/outputs on preparation, child invocation
and native mail activities do not eliminate the contact
metadata and report link in parent/notebook outputs. Restrict access to those
outputs and pipeline monitoring according to tenant policy; do not put
credentials into notebook or pipeline parameters.

### Official definition and activity references

Native mail uses activity type `Office365Email`. Configure it through the
pipeline UI and preserve its generated dynamic fields.

- [Fabric DataPipeline REST item definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/datapipeline-definition)
- [Invoke pipeline activity: legacy wait-on-completion behavior](https://learn.microsoft.com/en-us/fabric/data-factory/invoke-pipeline-activity#invoke-pipeline-legacy-settings)
- [Office 365 Outlook activity: connection, email fields and authentication](https://learn.microsoft.com/en-us/fabric/data-factory/outlook-activity)
- [Microsoft MCP DataPipeline definition schema](https://github.com/microsoft/mcp/blob/main/tools/Fabric.Mcp.Tools.Docs/src/Resources/item-definitions/datapipeline-definition.md)

## Deployment verification

Before enabling a recurring schedule, verify the deployment in your environment:

1. Confirm standalone FAR produces a report for an explicit test workspace.
2. Run the parent with a candidate allow-list and notifications off. Reconcile
   the selected IDs, ranking metric and date window with FUAM, and confirm the
   child receives the intended workspace IDs and FAR parameters.
3. If enabling email, complete the scoped delivery test in the deployment guide.
   Check recipient report access as well as mail activity success.
4. Record the deployed Git ref/SHA and verify scheduled execution with the
   intended identity. Check effective parameter overrides before enabling a
   schedule or resuming it after redeployment.

Validate tenant permissions, capacity availability, FUAM compatibility and
Outlook delivery in the intended environment; deployment success alone is not sufficient.

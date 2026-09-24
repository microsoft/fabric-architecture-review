<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Deploy FAR in Microsoft Fabric

**First deployment: do steps 1-3.** Add only the optional features you need.
No workstation installation is required.

| You want | Continue with |
| --- | --- |
| A central architecture review and report | Steps 1-3 |
| A restricted report for workspace administrators | Step 4 |
| FUAM to choose the highest-CU workspaces | Step 5 |
| Email notifications after each targeted review | Step 6 |
| Recurring runs | Step 7, after a successful manual test |

Already installed? See [Upgrading](#upgrading) or
[Existing owner model mismatch](#existing-owner-model-mismatch).

## 1. Prepare the workspace and access

- Use a workspace **in the tenant being reviewed**, on an active Fabric capacity.
- The executing account must be able to deploy/run FAR items and read the review
  sources. Tenant-wide admin collection requires the appropriate administrator
  role; see the [permission checklist](../docs/auth-setup.md).
- Changing `TENANT_ID` does not switch the notebook's signed-in tenant.
- Never put credentials in notebook or pipeline parameters.

## 2. Import and configure setup

1. Download [setup.ipynb](setup.ipynb) from the version you intend to deploy.
   In the Fabric workspace, choose **Import > Notebook** and open it.
2. In its parameters cell, set:

   | Parameter | First-run choice |
   | --- | --- |
   | `WORKSPACE_ID` | Blank: use this notebook's workspace |
   | `GITHUB_REPO_URL` | Public project: `https://github.com/microsoft/fabric-architecture-review.git`; use your fork URL when testing a branch |
   | `GITHUB_BRANCH` / `RELEASE_TAG` | The version you intend to run; prefer a published tag for production |
   | `LAKEHOUSE_NAME`, `NOTEBOOK_PREFIX` | Keep defaults unless naming policy requires otherwise |
   | `DEPLOY_GOLD_REPORT` | `"true"` for central governance reporting |
   | `DEPLOY_WORKSPACE_OWNER_REPORT` | `"true"` only if you also want the separate restricted owner report |
   | `OWNER_AGENT_NAME` | Separate owner chat item name; default: `Fabric Arch Review - Workspace Owner Agent` |
   | `DEPLOY_ONTOLOGY` | Set `"false"` for the first deployment; setup defaults to `"true"` and requires the [ontology tenant setting](REFERENCE.md#-build-the-estate-graph-fabric-iq-ontology) |

3. **Run all cells.** Check the output for failed optional deployments.

**Success:** the workspace contains the FAR Lakehouse, notebooks **01-06**, and
**Fabric Arch Review Pipeline**. Reporting items appear when enabled; owner
reporting also provisions **07_OwnerAccessSync** and **08_OwnerAgent**. Keep the
**FAR Lakehouse** attached to the review and access-sync notebooks. Run 08
separately only if you want to publish owner chat.

Setup deploys artifacts. It does **not** run a review, share reports or enable schedules.

### Workspace folders

Setup creates or reuses these exact **root** folders and moves FAR items in place:

| Folder | FAR artifacts |
| --- | --- |
| Reporting | Central and optional owner reports and semantic models |
| Pipelines | Standalone review and optional targeted-review pipelines |
| Ontology | The configured FAR ontology item |
| Agents | Central and optional owner DataAgents |
| Notebooks | Deployed notebooks 01-08, including 06_TargetedReviewSetup, and targeted Runner/Completion notebooks |

Only the FAR Lakehouse and imported setup notebook stay at workspace root.
Rerun **all setup cells** to organize existing installations,
including optional artifacts whose deployment flags are now off. Keep the
configured artifact names aligned with your installation: setup resolves only
those exact deployment identities, rejects duplicates, and moves their verified
IDs, not every item of a type. Renamed or separately named deployments require
their corresponding configuration. Unrelated items and ontology-generated
backing artifacts are not independently selected or moved.

Standalone **05**, **08** and enabled **06** also apply folder placement to newly
deployed items. Folder operations do not delete/recreate items or change IDs,
report bindings, schedules, connections, RLS membership or sharing.

These operations require workspace **Contributor or higher** and the Fabric
`Workspace.ReadWrite.All` delegated scope. Fabric's folder APIs are **Preview**;
permission, unsupported-workspace and malformed-response errors stop the folder
step rather than silently skipping it. Earlier successful deployment/move steps
are not rolled back; resolve the error and rerun. See the REST contracts for
[listing folders](https://learn.microsoft.com/en-us/rest/api/fabric/core/folders/list-folders),
[creating root folders](https://learn.microsoft.com/en-us/rest/api/fabric/core/folders/create-folder)
and [moving an existing item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/move-item).

## 3. Run your first scoped review

1. Open **Fabric Arch Review Pipeline** and select **Run**.
2. Set `WORKSPACE_IDS` to **one known test workspace GUID**. Set your
   `CLIENT_NAME`, `ENGAGEMENT_NAME` and `REVIEWER_NAME`; keep other defaults initially.
3. Wait for **Collect -> Analyze -> Report -> Gold** to succeed.
4. Open the governance report. The written review is in the FAR Lakehouse under
   `Files/fabric-arch-review/<run-id>/report.md`.

**Blank `WORKSPACE_IDS` on normal FAR means everything the executing identity can
access**, potentially the whole tenant. Always use an explicit scope for testing.
Missing evidence means a check was not established, not that it passed.

**Central-team chat:** after Gold, run **05_Agent** and follow
[agent setup](REFERENCE.md#-ask-the-data-agent-conversational-qa).

## 4. Optionally finish the Workspace Owner report

Skip this section if owner reporting is disabled. If you now want it, enable
`DEPLOY_WORKSPACE_OWNER_REPORT="true"` in setup, rerun setup, then run a scoped review.
The owner report is separate from the central governance report, app and central Data Agent.

### Connect its data once

1. Find the **owner semantic model** (default: **Fabric Arch Review - Workspace Owner Model**).
2. Open **... > Settings > Gateway and cloud connections**.
3. Under **Maps to**, select an approved shared cloud connection for the FAR source,
   or choose **Create a connection**. Keep the FAR SQL endpoint/database.
4. Use an approved source-reading identity; for a sandbox, **OAuth 2.0** sign-in.
   Keep **Microsoft Entra SSO disabled**. Apply the connection and verify model refresh.

**Why:** the model reads the Lakehouse using that connection; readers do not need
raw data access. This is not the Outlook connection. [Detailed connection steps](../docs/workspace-owner-report.md#connect-the-owner-model-click-by-click).

### Synchronize, then approve a reader

**Classic FAR stops after Gold; it does not run 07.** Run 07 yourself after the
first owner-enabled review. **06 is not required** for this sequence.
Run 07 using an approved Fabric administrator identity with permission to refresh
the owner model; being an administrator of a source workspace alone is not enough.

1. Run **07_OwnerAccessSync** after the owner-enabled review and wait for completion.
   If `owner_workspaces` already contains the test workspace from an earlier
   owner-enabled review, reuse it; old governance-only tables are not sufficient.
2. On the **owner report**, approve item-scoped **Read** and verify effective Read
   on its model. Do **not** grant Build or Reshare.
3. On the **owner semantic model**, open **... > Security > WorkspaceOwner**,
   add the approved reader, then **Save**.
4. Test with that person's actual account. They must be a **current direct-user
   administrator of a reviewed source workspace**, with **no FAR workspace role**.
5. Complete [live acceptance](../docs/workspace-owner-report.md#live-fabric-acceptance)
   and apply organizational labels/sharing policies before broader sharing.

**For each new reader, repeat approval only.** Use the same connection and RLS
role; 07 maintains their workspace mapping. RLS is enforced by the model when
they query the report. No mapping or an expired grant means no owner data.
Never give owners FAR workspace membership (even Viewer) or raw Lakehouse/SQL access.

### Optionally add owner chat

Enabling owner reporting in base setup also provisions **08_OwnerAgent**,
stamped with the deployed owner model ID. Setup does not run the notebook,
publish an Agent or approve consumers.

After the owner model, scoped review and 07 are ready, run 08 separately following
the [Workspace Owner Agent guide](../docs/workspace-owner-agent.md). Its only source
is the secured owner semantic model. Approve query-only access to the published
Agent, owner-model **Read** and `WorkspaceOwner` membership; **Build is not required**.
Complete the guide's actual-reader acceptance before broader sharing.
Model RLS enforces access; Agent instructions do not. Do not share the central
Agent as a substitute.

## 5. Optionally enable FUAM targeted reviews

Use this instead of choosing the review workspaces yourself.
You need an existing, populated FUAM Lakehouse and permission to read its
[supported source tables](../docs/targeted-review.md#supported-fuam-source-contract).

1. Open **06_TargetedReviewSetup** and set:

   ```python
   DEPLOY_TARGETED_REVIEW = "true"
   FUAM_WORKSPACE_ID = "<FUAM workspace GUID>"
   FUAM_LAKEHOUSE_ID = "<FUAM Lakehouse GUID>"
   CAPACITY_ID_OR_NAME = ""  # All; or one name/GUID; or '["Capacity A", "Capacity B"]'
   RANKING_METRIC = "cu_seconds"
   LOOKBACK_DAYS = "7"
   TOP_N = "5"
   ```

2. Leave **advanced wiring** unchanged. Base setup fills the FAR IDs and, when
   owner reporting is enabled, `OWNER_ACCESS_NOTEBOOK_ID` for 07.
3. **Run all cells of 06.** Then open the generated **targeted parent pipeline**.
4. Test with `WORKSPACE_IDS` set to a candidate test-workspace GUID,
   `TOP_N="1"` and `NOTIFICATIONS_ENABLED="false"`.
5. Confirm selection, **Invoke FAR**, and the bound **Sync owner access** succeed.

```text
FUAM selection -> FAR (through Gold) -> 07 when bound -> optional email
No candidates  -> skip review, access sync and email
```

**06 creates/updates the parent; it does not run a review.** Normal FAR is unchanged.
Blank `WORKSPACE_IDS` on the parent ranks all eligible FUAM candidates; it never
turns an empty selection into a tenant-wide FAR run. FAR/FUAM hosting workspaces
are excluded. Source errors fail rather than falling back.
`CAPACITY_ID_OR_NAME` filters source metrics **before** workspace ranking for
either metric. Blank includes all capacities; a single name/GUID selects one;
a JSON list selects a set. `TOP_N` is across that combined set, not per capacity.
Any unknown or ambiguous name fails the selection instead of widening the
review. No eligible metrics in the selected scope skips the review.

The parent runs bound 07 even with notifications off. A blank
`OWNER_ACCESS_NOTEBOOK_ID` omits sync; a configured invalid ID fails setup.
See the [targeted reference](../docs/targeted-review.md) for other metrics,
parameters and monitoring.

## 6. Optionally configure native Outlook owner email

Do this **only after the review and intended readers' report access work**.

1. Pause parent schedules. In its main canvas, open **Send owner emails ->
   Email workspace administrator -> Settings**.
2. Create/sign in to the Outlook connection **inside that activity**, using
   **User authentication (OAuth)**. Do not use the service-principal-only
   Office 365 Email form in Manage connections and gateways.
3. Leave dynamic **To / Subject / Body** unchanged. Set **General > Activity state**
   to **Activated**, then **Save**.
4. Set parent `FAR_REPORT_URL` to the actual report URL. For owners, use the
   **validated owner report**, not the unrestricted governance report.
5. Test a FUAM-ranked workspace where you are the **sole direct-user admin**:
   `WORKSPACE_IDS="<test GUID>"`, `TOP_N="1"`, `NOTIFICATIONS_ENABLED="true"`.
   Use the Outlook connection's account for the first run. Verify delivery and access.

Mail goes to current direct-user workspace administrators, not groups or a
fallback recipient. It contains a report link, not a generated task list.
The email grants no access. Notifications off means no recipient lookup or mail.
See [email troubleshooting](../docs/targeted-review.md#3-optionally-configure-native-outlook-owner-email)
if authentication or delivery fails; do not blindly replay uncertain deliveries.

## 7. Schedule and operate

- Schedule **normal FAR** for a fixed scope, or the **targeted parent** for FUAM
  selection. Verify the time zone, execution identity and schedule parameter overrides.
- For owner reporting, separately schedule **07 daily**; grants expire after
  **24 hours**. Weekly reviews alone are not enough.
- Do not overlap daily, targeted or manual access syncs. Monitor completion,
  warnings and model refreshes, not just pipeline submission.
- A 404 during 07 denies that workspace and warns; other workspaces continue.
  Other API/write/refresh failures stop sync. There is no stale-grant fallback.
- After timeout/cancellation, inspect native child and mail activity status before retrying.

## Upgrading

Routine setup reuses a compatible owner model **with the same name** or upgrades
a recognized older model in place, preserving its approved members and connection
binding. It also updates the report and 07.
Keep the same artifact names when rerunning setup, including after an interrupted
deployment. Updates wait for Fabric to confirm completion before setup continues.

1. Pause review and access-sync schedules; let active runs finish.
2. Re-import the updated setup notebook and select the intended code version.
   Restart its notebook session, then run all cells.
3. If using targeted reviews, rerun **all cells of 06** to update the parent binding.
4. Verify connections, report URL, scope and schedule overrides. 06 resets
   notification defaults to `"false"`; existing schedule overrides can still enable email.
5. After a successful scoped review through Gold, rerun **05** if central chat is
   enabled and **08** if owner chat is enabled. Setup alone does not update
   published Agent instructions or source selections. A changed owner model ID
   requires a new owner Agent name and reader revalidation.
6. Test with notifications off, validate owners if enabled, then resume schedules.

See [redeployment details](../docs/targeted-review.md#redeployment) for what 06 preserves.

### Existing owner model mismatch

Rerun setup with the same artifact names. Recognized older FAR owner models
upgrade automatically in place; **no additional setup parameters** are required.
Compatible models are reused without a model write. The existing model and
report IDs, role memberships, source connection and Lakehouse history are retained.

An in-place upgrade requires **XMLA Read Write** on the capacity, model write
permission, and permission to read the bound connection's settings. Setup does
not enable these settings. Run only one setup at a time, with review/access-sync
schedules paused; setup verifies model compatibility, memberships and connection
settings before continuing.

Before changing a model, setup saves and verifies a recovery snapshot under
`Files/_far/owner-model-backups` in the FAR output Lakehouse. Keep this Lakehouse
administrator-only. Snapshots contain model definitions, role memberships and
connection metadata, not credentials or a complete item-permission backup.
Do not share them with consumers or attach them to support requests.
If verification fails, setup stops and prints the snapshot path; there is
**no automatic rollback**. Restrict owner access and use administrator-assisted
recovery before resuming schedules.

**Do not delete Lakehouse tables.** An unrecognized schema, changed source or
customized security/calculation still stops deployment. A report bound to a
different model is not silently rebound. These errors do not establish that
your review data is corrupt.

Check the intended version, Lakehouse and model names, then use the error's
**First mismatch** path to identify the difference. Do not bypass the check.
For unexpected differences, follow [support guidance](../SUPPORT.md).
If replacement is necessary, follow [owner recovery and retirement](../docs/workspace-owner-report.md#retire-or-recover):
permissions do not copy automatically. Before restoring sharing or schedules,
run access sync, revalidate actual readers and republish owner chat if enabled.

## Troubleshooting the first deployment

| Symptom | Next action |
| --- | --- |
| Notebooks cannot start | Check active capacity and workspace assignment |
| Wrong Lakehouse | Attach the FAR output Lakehouse, then restart the notebook session |
| Missing collection evidence | Check the executing identity's [permissions](../docs/auth-setup.md) |
| Owner model contract differs | Check [model compatibility](#existing-owner-model-mismatch); do not delete tables |
| Owner report is empty/inaccessible | Check model connection, completed 07, item Read and RLS membership |
| FUAM source missing or no candidates | Check source IDs, source ingestion, dates and positive CU within scope |
| Email fails | Check native activity state/connection, recipient lookup and report URL |

More detail: [Fabric reference](REFERENCE.md#-troubleshooting),
[owner operations](../docs/workspace-owner-report.md), [targeted operations](../docs/targeted-review.md).

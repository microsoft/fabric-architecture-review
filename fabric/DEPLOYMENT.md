<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Deploy FAR in Microsoft Fabric

Complete steps 1-3 for a central review and report. Add optional capabilities only
after a successful scoped review. No workstation installation is required.

| Goal | Continue with |
| --- | --- |
| Central architecture review | Steps 1-3 |
| Restricted workspace-owner report | Step 4 |
| FUAM-based workspace selection | Step 5 |
| Email notifications | Step 6 |
| Recurring reviews | Step 7 |
| Update an existing installation | [Upgrading](#upgrading) |

## 1. Prepare the workspace and access

- Use a workspace in the tenant being reviewed, on an active Fabric capacity.
- The deploying identity needs workspace **Contributor or higher** and the
  delegated `Workspace.ReadWrite.All` scope. Tenant-wide collection needs additional
  administrator access; follow the [permission checklist](../docs/auth-setup.md).
- Use the Environment Variable Library for unattended identities. Never place
  credentials in public notebooks or pipeline parameters.
- Before updating an installation, pause its schedules and let active runs finish.

## 2. Import and configure setup

1. Download [setup.ipynb](setup.ipynb) from the version you intend to deploy.
   In the Fabric workspace, select **Import > Notebook**.
2. Configure the parameters:

   | Parameter | Recommended setting |
   | --- | --- |
   | `WORKSPACE_ID` | Leave blank to use the notebook's workspace |
   | `GITHUB_REPO_URL` | Repository containing your selected release |
   | `RELEASE_TAG` | Release tag, such as `v2026.10.1`; leave blank only to use `GITHUB_BRANCH` |
   | Artifact names | Keep defaults for a new installation; retain existing names when upgrading |
   | `DEPLOY_GOLD_REPORT` | `"true"` |
   | `DEPLOY_WORKSPACE_OWNER_REPORT` | `"false"` unless you need restricted owner reporting |
   | `DEPLOY_ONTOLOGY` | `"false"` for a first run; its default is `"true"` and requires the [preview tenant setting](REFERENCE.md#ontology) |

3. Select **Run all**. Resolve errors before continuing.

Setup creates the Lakehouse, notebooks **01-06**, **Fabric Arch Review Pipeline**,
and three Variable Libraries. Reports and models are created when enabled.
Owner reporting also adds **07_OwnerAccessSync** and **08_OwnerAgent**.

Setup does **not** run a review, publish Agents, share reports or enable schedules.
Keep the FAR Lakehouse attached to the deployed notebooks.

### Configure the review

Open the Variable Libraries in Fabric:

| Library | Configure |
| --- | --- |
| `FAR_Environment` | Review identity; optional temporary workspace access and model-role details |
| `FAR_Connections` | Optional FUAM and FCA workspace/Lakehouse IDs |
| `FAR_Thresholds` | Production scope, requirements and rule thresholds |

Setup preserves your values and adds new settings with their defaults.
`FAR_CONFIG_PREFIX` changes the library-name prefix. Follow the
[FAR policy guide](../docs/far-policy-configuration.md) for examples.

### Workspace folders

FAR organizes reports, pipelines, ontology, Agents and notebooks into corresponding
workspace folders. The Lakehouse and imported setup notebook remain at the root.
Folder placement preserves item IDs, connections, sharing and schedules.
If a folder step fails, correct the reported permission or service issue and rerun setup.

## 3. Run your first scoped review

1. Open **Fabric Arch Review Pipeline** and select **Run**.
2. Set `WORKSPACE_IDS` to **one known test-workspace GUID**. Set `CLIENT_NAME`,
   `ENGAGEMENT_NAME` and `REVIEWER_NAME` for the report.
3. Wait for **Collect -> Analyze -> Report -> Gold** to complete.
4. Open **Fabric Arch Review - Governance**. Confirm the workspace, review date
   and evidence coverage before interpreting the score.

**Blank `WORKSPACE_IDS` includes every eligible workspace the identity can access.**
The FAR hosting workspace and Capacity Metrics app workspaces are excluded,
even when explicitly listed. Capacity Metrics is identified from its standard
linked report and semantic-model metadata, not its workspace name.
Missing evidence means a check could not be established, not that it passed.
The written report is available at `Files/far/<run-id>/report.md`.

FAR collects architectural metadata, not business rows or raw source expressions.
Optional FUAM/FCA connections enable additional evidence; disconnected sources
do not trigger replacement monitoring or financial collection.

For central-team chat, run **05_Agent** after a successful review.
See [Data Agent setup](REFERENCE.md#data-agent).

## 4. Optionally finish the Workspace Owner report

Enable `DEPLOY_WORKSPACE_OWNER_REPORT="true"` in setup, rerun setup, and complete
a scoped review. Then:

1. Open the **Workspace Owner semantic model > Settings > Gateway and cloud connections**.
   Map it to an approved identity that can read the FAR source. Keep
   **Microsoft Entra SSO disabled** and verify refresh.
2. Run **07_OwnerAccessSync** using an approved Fabric administrator identity
   with permission to refresh the owner model.
3. Grant the approved reader item-scoped **Read** on the owner report and model.
   Do not grant **Build** or **Reshare**.
4. Add the reader to the model's **Security > WorkspaceOwner** role.
5. Test with the reader's actual account. They must be a current direct-user
   administrator of a reviewed source workspace.

The standard review ends after Gold; run 07 separately. Targeted reviews can run
it automatically when configured. Schedule 07 daily: access grants expire after
24 hours.

**Never give owner readers a FAR workspace role, even Viewer, or direct Lakehouse/SQL access.**
Do not share the central report, app or Agent as a substitute.
Complete the [owner access acceptance checks](../docs/workspace-owner-report.md#live-fabric-acceptance)
before broader sharing.

### Optionally add owner chat

After the owner report and access synchronization work, run **08_OwnerAgent**.
Approve query-only Agent access, owner-model **Read**, and `WorkspaceOwner`
membership. Build permission is not required. The secured model enforces access.
Follow the [Workspace Owner Agent guide](../docs/workspace-owner-agent.md).

## 5. Optionally enable FUAM targeted reviews

Use an existing, populated FUAM Lakehouse that the review identity can read.

1. In `FAR_Connections`, set `fuam__enabled=true` and the FUAM workspace and Lakehouse IDs.
2. Open **06_TargetedReviewSetup** and set:

   ```python
   DEPLOY_TARGETED_REVIEW = "true"
   CAPACITY_ID_OR_NAME = ""  # All capacities; or a name, GUID, or JSON list
   RANKING_METRIC = "cu_seconds"
   LOOKBACK_DAYS = "7"
   TOP_N = "5"
   SKIP_REVIEWED_WITHIN_DAYS = "0"
   ```

3. Leave deployment IDs unchanged and run all cells.
4. Open the generated targeted pipeline. Test with one candidate workspace,
   `TOP_N="1"` and `NOTIFICATIONS_ENABLED="false"`.
5. Confirm selection, review, and owner access synchronization when enabled.

The capacity filter applies before ranking; `TOP_N` is across the selected capacities.
An empty selection skips the review and notifications. Source errors stop the run.
FAR/FUAM hosting workspaces and Capacity Metrics app workspaces are excluded
before `TOP_N`; the next eligible workspaces fill the available slots.
Optionally set `SKIP_REVIEWED_WITHIN_DAYS="7"` to exclude workspaces from
successful targeted reviews in the past seven days before taking `TOP_N`.
The default `0` disables this cooldown. History starts after redeploying 06;
standalone and pre-upgrade reviews do not count.

See [targeted-review guidance](../docs/targeted-review.md) for supported FUAM tables,
other ranking options and troubleshooting.

## 6. Optionally configure native Outlook owner email

Configure email only after the review and intended readers' report access work.

1. Pause targeted-pipeline schedules. Open **Send owner emails >
   Email workspace administrator > Settings**.
2. Create the Outlook connection **inside the activity**, using **User authentication (OAuth)**.
   Do not use the service-principal-only Office 365 Email form.
3. Leave dynamic **To / Subject / Body** unchanged. Set **Activity state**
   to **Activated**, then save.
4. Set `FAR_REPORT_URL` to the validated owner report.
5. Test with a workspace where you are the sole direct-user administrator,
   `TOP_N="1"` and `NOTIFICATIONS_ENABLED="true"`. Verify delivery and report access.

Notifications go to current direct-user administrators, not groups. Email grants
no access. When notifications are off, no recipients are queried and no email is sent.
See [email troubleshooting](../docs/targeted-review.md#3-optionally-configure-native-outlook-owner-email)
before retrying uncertain deliveries.

## 7. Schedule and operate

- Schedule the standard pipeline for a fixed scope, or the targeted pipeline for
  FUAM-based selection. Verify the execution identity, time zone and scope.
- Schedule **07 daily** if owner reporting is enabled. Do not overlap access-sync runs.
- Monitor completed stages, evidence warnings and model refreshes, not just submission.
- If temporary workspace access is enabled, run only one review at a time.
- After a timeout or cancellation, inspect review and email activity status before retrying.

## Upgrading

1. Pause review and access-sync schedules; let active runs finish.
2. Import the release's setup notebook. Retain your artifact names and select the release tag.
3. Restart the notebook session and run all cells.
4. If using targeted reviews, rerun all cells of **06**. Verify connections, scope,
   report URL and schedule overrides. Notification defaults return to `"false"`.
5. Run a scoped review. Republish central chat with **05** and owner chat with **08** if used.
6. Revalidate owner access before resuming schedules.

Setup keeps configured Variable Library values. Do not delete Lakehouse tables to
resolve an upgrade error. Compare only results from the same review version and
configuration; run a fresh review after upgrading.

### Model security (RLS and OLS)

Declare required models in `FAR_Thresholds` using `policy__rls_required_model_ids`
and `policy__ols_required_model_ids`. The review identity needs model write access;
unreadable definitions appear as missing evidence.

Optional temporary workspace access for Fabric administrators and role details are
off by default in `FAR_Environment`. Enable them only after reviewing the
[permissions](../docs/auth-setup.md#additional-permissions-for-temporary-workspace-access-optional)
and [policy guidance](../docs/far-policy-configuration.md#model-security-rls-and-ols).
Role names are visible in the reports, app and Agents when role details are enabled.

### Owner model upgrade

Recognized owner models upgrade in place, preserving IDs, role memberships and source
connections. Upgrades require **XMLA Read Write**, model write permission, and access
to the connection settings. Customized or unrecognized models require review.

Setup saves recovery snapshots in `Files/_far/owner-model-backups`. Restrict access:
they contain model definitions, role memberships and connection metadata.
If verification fails, stop sharing and scheduling until an administrator resolves
the issue. There is no automatic rollback. Follow the
[recovery guide](../docs/workspace-owner-report.md#retire-or-recover); do not bypass checks.

## Troubleshooting the first deployment

| Symptom | Next action |
| --- | --- |
| Notebook cannot start | Verify the active capacity and attached FAR Lakehouse |
| Missing collection evidence | Check the review identity's [permissions](../docs/auth-setup.md) |
| Report has no data | Complete a review through Gold; verify semantic-model refresh |
| Owner report is empty or inaccessible | Check the connection, completed 07, item Read and RLS membership |
| Owner model upgrade stops | Follow [owner model upgrade](#owner-model-upgrade); do not delete tables |
| No FUAM candidates | Check source IDs, ingestion, dates, capacity filter and positive CU |
| Email fails | Check the native activity state, connection, recipient and report URL |

For more help, see the [Fabric reference](REFERENCE.md#troubleshooting) and [support](../SUPPORT.md).

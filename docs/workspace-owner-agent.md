<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Workspace Owner Agent

The optional Workspace Owner Agent answers questions about an approved reader's
FAR technical findings, investigation guidance, execution observations and
coverage. It uses **only the separate secured owner semantic model**.

It is not the central Data Agent deployed by 05, and it does not change the
Fabric app's central chat. No additional Gold tables are required.

## Deploy on demand

Use a supported paid F2-or-higher Fabric capacity, or P1-or-higher Premium
capacity with Fabric enabled, and the required
[Data Agent tenant settings](https://learn.microsoft.com/fabric/data-science/data-agent-tenant-settings).
The deployment identity needs permission to manage the Agent and retrieve the
owner model definition; consumer permissions are listed separately below.

1. In [base setup](../fabric/setup.ipynb), set:

   ```python
   DEPLOY_WORKSPACE_OWNER_REPORT = "true"
   OWNER_AGENT_NAME = "Fabric Arch Review - Workspace Owner Agent"
   ```

2. Run setup. Enabling owner reporting also creates
   **`<NOTEBOOK_PREFIX>_08_OwnerAgent`**, stamped with the exact deployed owner
   model ID and repository ref. It does not execute 08, publish an Agent,
   approve readers or create schedules.
3. Complete [owner reporting setup](workspace-owner-report.md): configure the
   fixed-identity source connection with SSO disabled, run a scoped review through
   Gold, and run **07_OwnerAccessSync** successfully.
4. Open 08. Run the pinned SDK install cell. After any kernel restart, run the
   parameters and deployment cells. Do not replace the owner model ID with a
   governance model ID.
5. Confirm successful publication **and source verification**, then complete
   [actual-reader acceptance](#actual-reader-acceptance) before wider sharing.

08 verifies deployment configuration, not answer quality. It does not run 05's
automated evaluation or write `gold_agent_eval`. A failed post-publication
verification does not unpublish the Agent; restrict sharing until resolved.

Deployment validates the owner model, including its RLS, and rejects incompatible
models, unexpected Agent sources or unsaved configuration. It does not repair or
replace model security, members, connections or sharing.

Compatible reruns reconcile the same Agent. Use a separate name if an existing
Agent has other sources. Disabling owner reporting in setup does not delete an
existing Agent or revoke access. After changing the owner model ID, use a new Agent name
and repeat acceptance; do not silently rebind a shared Agent.

Normal setup upgrades recognized older owner models in place, keeping their ID.
After an upgrade, rerun 08 with the same Agent name to publish the updated
instructions and examples, then repeat actual-reader acceptance.

## Approve a reader

Approve all of the following:

- **Query-only access to the published owner Agent.** Do not enable View details,
  Edit or resharing for ordinary consumers.
- Item-scoped **Read** on the owner semantic model.
- Membership in that model's **WorkspaceOwner** role.
- A current workspace entitlement from 07: the reader must be a direct-user
  administrator of an owner-reviewed source workspace. Grants expire within
  24 hours, so maintain and monitor the independent daily sync.

**Build is not required for semantic-model queries through a Fabric Data Agent.**
Do not grant FAR workspace membership, even Viewer, or raw Lakehouse/SQL access.
Do not share the deployment notebook with consumers. Apply your organization's
labels and sharing policies separately.

Fabric and model RLS enforce the caller's data access. Instructions, hidden
tables, table selection and supplied workspace IDs are **not authorization**.
The Agent excludes the entitlement table from its selected grounding and is
instructed not to answer entitlement, raw-code, secret or tenant-wide questions;
this is conversational guidance, not an additional object-level security policy.
Anyone with a model write role or other RLS-bypassing privileges is not a valid
test of the owner-consumer boundary.

## Ask useful questions

- Which reviewed workspaces can I see?
- What should I investigate first in my latest reviews?
- Show DAX measure and calculated-object evidence from my latest reviews.
- Show my Dataflow Gen2 query signals.
- Show observed executions in my latest reviews.
- Where is evidence missing or incomplete?
- Which of my pipelines have dependency findings?

Answers should identify workspace/item IDs, review context, coverage limits,
the recommendation and a validation step. Current questions use each authorized
workspace's latest review. Execution duration is in milliseconds, not CU usage;
DAX and M patterns are static signals, not proof of runtime cost.

Historical execution answers must account for repeated snapshots before
calculating counts or comparing status. **Semantic-model sources do not support
datasource few-shot queries or datasource instructions**; owner-specific examples
are provided in the Agent instructions instead.

## Actual-reader acceptance

Test with actual **read-only consumer accounts**, not the deployment author or an
admin's role simulation. Local tests do not establish live authorization. Use
fresh conversations after each permission/grant change.

| Test | Required result |
| --- | --- |
| Owner A has access only to reviewed workspace A | Answers and direct requests contain no workspace B evidence |
| Owner B has access only to B | No workspace A evidence, even when A's name or GUID is supplied |
| Approved role member has no current grant | No owner evidence; no fallback source or inferred denied-workspace details |
| Grant expires or is revoked and 07 refreshes | A fresh query no longer returns that workspace's evidence |
| Consumer lacks model Read or Agent query permission | Access fails; no author-identity or service-identity fallback |
| Prompt asks to ignore rules, expose raw code, list grants or query the tenant | No unauthorized data, alternative source or impersonation |
| Known latest-review DAX, Gen2, pipeline, execution and coverage questions | Match the same user's owner report and correctly state evidence limits |
| Two workspaces/items share a display name | Resolve using authorized IDs rather than joining by name |

Record the tested accounts, permissions, review IDs and outcomes through your
approved internal process. Do not publish user identifiers or test transcripts
containing customer metadata in the repository. Keep sharing restricted until
all applicable tests pass.

## References

- [Fabric Data Agent sharing and semantic-model Read permissions](https://learn.microsoft.com/fabric/data-science/data-agent-sharing)
- [Datasource configuration and semantic-model limitations](https://learn.microsoft.com/fabric/data-science/data-agent-source-control)
- [Owner report access operations](workspace-owner-report.md)
- [Evidence scope and interpretation](native-evidence.md)

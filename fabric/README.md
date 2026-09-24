<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# FAR in Microsoft Fabric

Review Fabric metadata, configuration and metrics without reading customer
business rows. No workstation installation is needed.

## Start here

**Follow [Deploy FAR](DEPLOYMENT.md) from step 1.** It is the installation checklist.

```text
Import setup -> Run setup -> Run a scoped FAR review -> Open the results
```

Setup deploys a Lakehouse, stage notebooks and the FAR pipeline. The pipeline runs
**Collect -> Analyze -> Report -> Gold**. Governance reporting is enabled by default;
owner reporting and FUAM targeting are optional.

## Choose what you need

| Goal | What to do |
| --- | --- |
| Review workspaces you choose | Run the normal FAR pipeline with explicit `WORKSPACE_IDS` |
| Let FUAM choose the top workspaces | Configure **06**, then run its targeted parent pipeline |
| Give workspace administrators a restricted report | Enable owner reporting in setup, connect its model, run **07**, then approve readers |
| Notify those administrators | Configure Outlook in the targeted parent **after** report access is tested |
| Ask questions about the central review | Run **05_Agent** after Gold; [agent instructions](REFERENCE.md#-ask-the-data-agent-conversational-qa) |
| Let approved owners ask about their findings | Owner reporting provisions **08_OwnerAgent**; run it manually after Gold and **07**, then [approve owner chat](../docs/workspace-owner-agent.md) |

**06 configures a pipeline; it does not run a review.**
**07 synchronizes owner access; it does not collect findings or share the report.**
When owner reporting is bound, the targeted parent runs 07 after FAR and before
email. The normal FAR pipeline does not. Keep a separate daily 07 schedule.

## Three boundaries to keep clear

- **Scope:** normal FAR with blank `WORKSPACE_IDS` reviews everything the execution
  identity can access. Targeted FAR with no FUAM candidates skips the review.
- **Audience:** the governance report, FAR app and central Data Agent are
  central-team tools. The separate owner report and owner Agent use the secured
  `WorkspaceOwner` model.
- **Access:** give approved owners report/model Read plus `WorkspaceOwner`
  membership, not FAR workspace membership or raw Lakehouse/SQL access.

## Already deployed?

- [Upgrade safely](DEPLOYMENT.md#upgrading) or
  [recover an owner-model mismatch](DEPLOYMENT.md#existing-owner-model-mismatch).
  **Do not delete tables to fix a model compatibility error.**
- [Owner access, connection setup and acceptance tests](../docs/workspace-owner-report.md).
- [FUAM parameters and notification operations](../docs/targeted-review.md).
- [Full technical reference](REFERENCE.md): parameters, Gold, reports, optional
  features and troubleshooting.

<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Fabric Architecture Review in Microsoft Fabric

Review workspace boundaries, hosting, storage, static dependencies, promotion,
access design and evidence coverage. Results are available in a Power BI report,
a Data Agent and an optional estate ontology.

## Get started

1. Follow the [deployment guide](DEPLOYMENT.md).
2. Import and run the [setup notebook](setup.ipynb).
3. Edit the configuration in the workspace: authentication identity in the
   Environment Variable Library, optional FUAM/FCA Lakehouses in the Connections
   library, and thresholds plus the production scope and allowed values in the
   Thresholds library. See [Configure the review](../docs/far-policy-configuration.md).
4. Run the review pipeline with the workspace IDs you want to assess.
5. Open the report. Deploy the Agent after the curated result tables are populated.

```text
Setup -> Configure -> Collect -> Analyze -> Report -> Gold -> Explore results
```

Setup creates the Lakehouse, stage notebooks, review pipeline
and three Variable Libraries (Environment, Connections, Thresholds). Owner reporting, targeted selection and
notifications are optional.

## Collection

- The baseline collects the metadata required by the architectural review.
- Configured FUAM/FCA Lakehouses provide additional supported metadata.
- Missing optional modules do not trigger API collection of their monitoring,
  optimizer or financial data.
- A configured source that cannot be read produces a visible error or coverage
  gap. Missing evidence is not a passing architectural result.
- Notebook and pipeline definitions are reduced to sanitized structural evidence;
  customer business rows and raw source literals are not stored.

## Optional experiences

| Goal | Entry point |
| --- | --- |
| Select workspaces using FUAM consumption | **06** targeted setup and its parent pipeline |
| Give approved workspace administrators a restricted report | Owner reporting, **07** access synchronization and reader approval |
| Ask questions about central results | **05_Agent** |
| Give approved owners a restricted chat experience | **08_OwnerAgent** and the secured owner model |
| Send result notifications | Outlook configuration in the targeted parent |

**06** configures orchestration; **07** synchronizes access. Neither runs a review
or shares a report. Test owner access before enabling notifications.

## Access and operations

The central report and Agent are central-team tools. Owner tools use a separate
secured model. Approved owners need report/model Read and the `WorkspaceOwner`
role, not access to the raw Lakehouse or review workspace.

See the [technical reference](REFERENCE.md), [owner access guide](../docs/workspace-owner-report.md)
and [targeted-review operations](../docs/targeted-review.md).

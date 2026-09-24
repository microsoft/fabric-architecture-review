<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Fabric Architecture Review Accelerator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.TXT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

FAR reviews Microsoft Fabric **metadata, configuration, source definitions and
metrics** and turns them into prioritized findings. It does not read customer
business rows.

It covers architecture, performance, security, governance, operations, cost,
tenant settings and best practices. DAX findings are **static investigation
signals**, not measured query latency or CU consumption.

Native refresh/job history, Dataflow Gen2 Power Query evidence and typed
non-measure DAX objects help identify the next investigation step. See
[where to find this evidence](docs/native-evidence.md#where-to-find-the-evidence)
in the reports, app and Data Agent.

## Choose your task

| I want to... | Follow this guide | Success looks like |
| --- | --- | --- |
| Run FAR inside Fabric | [Fabric deployment](fabric/DEPLOYMENT.md), steps 1-3 | Pipeline completes and the governance report shows the scoped review |
| Run FAR on my workstation | [Local review](docs/local-review.md) | Collected evidence, findings and a written report in the output folder |
| Review the highest-CU workspaces each week | [Targeted reviews](fabric/DEPLOYMENT.md#5-optionally-enable-fuam-targeted-reviews) | Parent selects workspaces and waits for FAR to finish |
| Give workspace administrators their own restricted report | [Owner report setup](fabric/DEPLOYMENT.md#4-optionally-finish-the-workspace-owner-report) | An actual approved reader sees only their authorized workspaces |
| Let approved owners ask questions about their review | [Workspace Owner Agent](docs/workspace-owner-agent.md) | A separately published agent queries only the secured owner model |
| Email those administrators after a review | [Outlook notifications](fabric/DEPLOYMENT.md#6-optionally-configure-native-outlook-owner-email) | Controlled test email arrives and opens an accessible report |
| Try or deploy the interactive FAR app | [App guide](fabric/app/README.md) | Anonymous preview or a live Fabric-hosted app |
| Ask the central review data questions | [Data Agent setup](fabric/REFERENCE.md#-ask-the-data-agent-conversational-qa) | Published agent answers from populated FAR data |
| Understand a finding and decide what to change | [Interpret results](docs/methodology.md#use-the-results) | Evidence, affected item and a tested next action |
| Investigate executions, Gen2 queries or calculated DAX objects | [Native evidence](docs/native-evidence.md) | Coverage, affected object and the right validation step |
| Upgrade or recover setup | [Upgrade/recovery](fabric/DEPLOYMENT.md#upgrading) | Updated artifacts verified before schedules resume |

**First timer:** choose **Fabric deployment** or **Local review**, not both.
Get one scoped review working before adding targeting, owner sharing or email.

## What you get

- **Written review:** prioritized findings and supporting evidence in Markdown;
  local runs can also render PDF when the renderer is installed.
- **Fabric governance report:** Gold-backed overview, trends and technical
  drilldowns for the central review team.
- **Optional owner report:** a separate, RLS-protected technical subset.
- **Optional owner Agent:** on-demand conversational access through that same secured model.
- **Optional app and central Data Agent:** central-team exploration and conversational access.

The core flow is **Collect -> Analyze -> Report**; Fabric adds **Gold**.
Deployment creates artifacts. A review run populates their data.

## Keep these boundaries clear

- **Scope:** blank `WORKSPACE_IDS` on normal FAR means no workspace filter within
  the execution identity's permissions. Use one explicit workspace for the first test.
  An empty workspace selection skips the targeted review.
  FAR excludes observed Pro/shared workspaces (`isOnDedicatedCapacity=false`),
  PPU workspaces/capacities (including virtual SKU `PP3`), deleted/inactive
  workspaces, and built-in `AdminWorkspace` review targets. Explicit IDs cannot
  override these exclusions. Real Fabric/dedicated capacities remain supported;
  missing metadata, workspace names, and a user's Pro license are not exclusions.
  Workspace/capacity metadata discovery and tenant settings remain available;
  excluded resources receive no item/model/workload/refreshable probes. Scope
  identities are retained in raw metadata to filter historical evidence and Gold.
  An eligible Admin monitoring workspace may still supply operational metrics.
- **Access:** permissions determine evidence coverage. A missing-evidence result
  is not a pass. Use the [access checklist](docs/auth-setup.md).
- **Sharing:** central reports, the app and central Data Agent are not owner-isolated.
  Owner consumers need the separate model, item Read and `WorkspaceOwner` membership;
  owner chat also needs query-only access to the separate owner Agent. Never grant
  them FAR workspace membership to use owner artifacts.
- **Data handling:** metadata and source definitions can still be sensitive.
  Read [data safety](docs/data-safety.md) before collection or sharing.

## Reference and help

| Need | Document |
| --- | --- |
| Rule IDs and severity | [Checklist reference](docs/checklist-reference.md) |
| Scoring, applicability and thresholds | [Methodology](docs/methodology.md) |
| Local configuration, individual stages and extension points | [Review reference](REFERENCE.md) |
| Fabric parameters, Gold and optional features | [Fabric reference](fabric/REFERENCE.md) |
| Targeting sources, replay and notifications | [Targeted-review reference](docs/targeted-review.md) |
| Owner RLS, access operations and acceptance tests | [Owner reference](docs/workspace-owner-report.md) |
| Example output | [Synthetic sample report](samples/report.md) |
| Changes in each version | [Changelog](CHANGELOG.md) |
| Bugs, questions or setup help | [Support](SUPPORT.md) |
| Contribute a change | [Contributing](CONTRIBUTING.md) |
| Report a vulnerability privately | [Security policy](SECURITY.md) |

Licensed under [MIT](LICENSE.TXT). Contributions follow the
[Microsoft Open Source Code of Conduct](CODE_OF_CONDUCT.md).

<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Fabric Architecture Review (FAR)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.TXT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Assess the architecture, governance and security of your Microsoft Fabric estate.
FAR reviews workspace design, configuration and access, then explains each finding
and the next action to take. It does not query customer business rows.

Explore results in Power BI, the FAR app, a Data Agent or local Markdown/PDF reports.
Optional workspace-owner reports restrict each reader to their authorized workspaces.

## Get started

| Run in | Start here |
| --- | --- |
| Microsoft Fabric | [Deploy FAR](fabric/DEPLOYMENT.md): import setup, configure the review and run the pipeline |
| Your workstation | [Run a local review](docs/local-review.md): install dependencies, create `.env.far` and run the three stages |

Start with one test workspace. **An empty workspace scope includes all workspaces
the review identity can access.**

See the [2026.10.2 release notes](CHANGELOG.md#2026102) for this version.

## What FAR reviews

70 rules cover five dimensions. Only assessed controls affect the score.
Signals highlight areas to investigate; missing evidence is never a pass.

| Dimension | Examples |
| --- | --- |
| Architecture | Workspace boundaries, hosting, storage modes, pipeline/notebook contracts and dependencies |
| Governance | Administrator continuity, naming, documentation, endorsement and sensitivity labels |
| Operations | Source control, deployment pipelines, capacity state and optional activity signals |
| Reliability | Production concentration, pipeline failure paths and dependencies on non-production |
| Security | Sharing, direct access, tenant settings, network controls, OneLake roles and semantic-model RLS/OLS |

Declare production workspaces and required policies before interpreting results.
FAR does not infer your requirements from workspace names. See the
[rule catalogue](docs/checklist-reference.md) and [scoring guide](docs/methodology.md).

## Configure the review

| Setting | Microsoft Fabric | Local |
| --- | --- | --- |
| Review identity | `FAR_Environment` Variable Library | `.env.far` |
| Optional FUAM/FCA connections | `FAR_Connections` Variable Library | [sources.yaml](config/sources.yaml) |
| Thresholds and policy | `FAR_Thresholds` Variable Library | [thresholds.yaml](config/thresholds.yaml), [policy.yaml](config/policy.yaml) |
| Workspace scope and report labels | Pipeline parameters | `.env.far` |

Setup preserves configured values. Follow the [FAR policy guide](docs/far-policy-configuration.md)
for examples, semantic-model security checks and optional role details.

## Optional capabilities

- **FUAM and FCA:** enrich the review with activity, capacity and sharing evidence.
  Without these connections, their rules are not applicable; FAR does not collect
  replacement monitoring or financial data.
- **Workspace-owner reporting:** provide a separate, secured view for approved
  administrators. Follow the [owner access guide](docs/workspace-owner-report.md).
- **Targeted reviews:** use FUAM to select workspaces and optionally notify their
  administrators. See [targeted reviews](docs/targeted-review.md).
- **Agents, app and ontology:** explore results conversationally or visually.
  See [Fabric results](fabric/REFERENCE.md) and [app setup](fabric/app/README.md).

## Use the results safely

Start with the report's **Action plan**. Resolve evidence gaps, review failures,
and use **Items to review** to identify affected items. Re-run the same scope after
making changes. Compare results only when the review version and configuration match.

The central report, app and Agent are for the review team, not an owner-only audience.
Review metadata can be sensitive even though business rows, source code and filter
expressions are not collected. Read the [permissions guide](docs/auth-setup.md) and
[data-safety guidance](docs/data-safety.md) before deployment or sharing.

Validate access with actual users before production use.
For assistance, see [support](SUPPORT.md).

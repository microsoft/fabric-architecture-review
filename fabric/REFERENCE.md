<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Explore FAR results

Use the [deployment guide](DEPLOYMENT.md) to install FAR and complete a first review.
This guide explains how to use the reports, Agents, app and ontology.

## Power BI report

Open **Fabric Arch Review - Governance** after a successful review:

| Page | Use it to |
| --- | --- |
| Overview | Understand the scope, score and assessment coverage |
| Action plan | Prioritize failures and resolve missing policy or evidence |
| Findings / Items to review | Identify affected items and recommended actions |
| Workspaces / Capacities & storage | Inspect hosting, capacity and storage choices |
| Model security | Review configured RLS/OLS and inspection coverage |
| Tenant settings | Review tenant-wide posture and available change history |
| Dependencies | Inspect relationships between items |
| Rule catalogue | Understand each rule, its requirements and configuration |
| Evidence coverage / Trends | Check available sources and compare compatible reviews |

Only assessed controls affect the score. Signals indicate areas to investigate.
Missing evidence is not a pass; **No assessable controls** means no valid score.
Compare reviews only when their version and configuration match.

When [role details](../docs/far-policy-configuration.md#role-detail-optional) are enabled,
right-click a model on **Model security** and select **Drill through > Model roles**.

## Gold tables and the governance model

FAR keeps review history in its Lakehouse and makes results available through the
governance semantic model. Use that model for analysis of findings and scores.
Do not grant source access merely to let someone view a report.

| Information | Includes |
| --- | --- |
| Review results | Scope, scores, findings, affected items and recommendations |
| Estate inventory | Workspaces, capacities, items, storage and dependencies |
| Model security | Observed RLS/OLS posture and optional role details |
| Evidence coverage | Collection status, source and freshness |
| Rule catalogue | Purpose, requirements and remediation guidance |

Raw expressions, customer business rows and model-role membership are not collected.
Role names are stored only when role details are enabled.
See [data safety](../docs/data-safety.md) for access and retention considerations.

## Data Agent

After a successful review, run **05_Agent** to deploy and publish central-team chat.
Your capacity and tenant must support Fabric Data Agents, and reviewers need access
to the published Agent and its data sources.

Try questions such as:

- "Which failed controls should we address first?"
- "Which findings are missing evidence or policy declarations?"
- "Which models require RLS, and could their definitions be inspected?"

Check the review date, evidence coverage and cited sources. An Agent answer is not
an independent security or performance assessment. Republish the Agent after upgrading
FAR. Selecting Agent sources does not grant users permission to read them.

In **05_Agent**, `DATA_AGENT_RUNTIME` defaults to `"standard"`. To try the preview
runtime, use a separately named test agent and set it to `"preview"` before running
the deployment. The setting is verified before publishing; it does not change
sources or permissions. Published agents retain the runtime selected at publication.

## Owner report and Agent

Use the separate owner report for approved workspace administrators. The secured
model limits readers to currently authorized workspaces. Access synchronization must
run daily; expired grants return no owner data.

Do not share the central report, app, Agent or Lakehouse with an owner-only audience.
Follow the [owner report guide](../docs/workspace-owner-report.md) and
[owner Agent guide](../docs/workspace-owner-agent.md) for approval and actual-user testing.

## Ontology

The optional Fabric IQ ontology shows capacities, workspaces, items and their
relationships. Enable **Users can create Ontology (preview) items** in tenant
settings before deploying it.

After a review, open the ontology and select **Manage graph > Materialize**.
Repeat materialization when you want the graph to reflect a later review.
The graph describes the estate; use the secured report for findings and scores.
Publishing an ontology does not widen data access.

## App

The FAR app provides findings, a 3D estate map, model security, source coverage and
central Data Agent chat. Follow [app setup](app/README.md) to bind it to your
governance model and published Agent.

The app is for central reviewers. Filters are navigation, not access controls.
Chat does not inherit page filters: include the workspace or model in your question.
Local preview is an empty demonstration, not a live review.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No data after setup | Run the pipeline through Gold and check model refresh |
| `unknown` or `missing_evidence` | Open the Action plan or Rule catalogue for the required setting or permission |
| Variable Library validation fails | Correct the setting or type reported by the error; rerun setup without discarding your values |
| Stage notebook cannot download the source | Verify the configured repository/ref and run the pipeline rather than an unstamped notebook |
| Ontology is unavailable | Check preview availability, capacity and the tenant setting |
| Ontology has no nodes | Complete a review, then select **Manage graph > Materialize** |
| Agent answers use an older review | Check the latest successful review and republish the Agent after an upgrade |
| App cannot connect | Verify the model, Agent and workspace IDs, then test as an intended reviewer |

For collection failures, inspect the run output and evidence coverage.
See [support](../SUPPORT.md) before sharing logs or review data.

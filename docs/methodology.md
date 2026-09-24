<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Review Methodology

The Fabric Architecture Review is aligned to the [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/) and adapts the WAF pillars to Microsoft Fabric workloads.

## Use the results

**Goal:** turn a finding into an evidence-backed action, not just improve a score.

1. Select the intended **run and workspace**. Check evidence coverage first.
2. For `missing_evidence` or `unknown`, resolve collection/access gaps and rerun
   Collect before drawing a conclusion.
3. For a failed check, open its affected items, evidence and recommendation.
   Look up the rule ID in the [checklist](checklist-reference.md) when needed.
4. Agree the next action, owner and validation with the workload team. FAR
   does not automatically remediate or create a task list.
5. Test the change for correctness and performance, then rerun the same review scope.

**Example: potentially expensive DAX measures.** Open **DAX Analyzer** in the
central report, or **Findings / Technical details** in the owner report. Select
the workspace/model and inspect the named measures and pattern guidance.
Benchmark relevant queries before and after changing a measure. A static flag is
not proof of slow execution, and removing it is not proof of a performance gain.

The sections below explain scoring and interpretation. For installation, use
[Fabric deployment](../fabric/DEPLOYMENT.md) or [local review](local-review.md).

## WAF pillar mapping

| WAF Pillar | Review Dimension(s) | Examples of what we look at |
|---|---|---|
| Reliability | Architecture, Performance | Capacity headroom, refresh failure rate, pipeline retry behavior, Git integration enabling rollback |
| Security | Security, Tenant Settings | Tenant-wide vs scoped settings, workspace role membership, sensitivity labels, guest access |
| Cost Optimization | Cost, Performance | SKU right-sizing vs sustained CU%, pause/resume on non-prod, orphaned items |
| Operational Excellence | Operational Excellence, Governance, Architecture | ALM maturity: deployment-pipeline coverage, Git source control, dev→prod promotion path; naming conventions, monitoring |
| Performance Efficiency | Performance | Throttling events, semantic model size, refresh SLOs, small-file problem, VertiPaq footprint, metadata-only DAX definition risk patterns |

## Phases

1. **Scope & access** — Identify in-scope workspaces and capacities, then grant the executing identity the permissions required by the enabled collectors. FAR uses metadata-reading operations, but Fabric Administrator is a privileged role, not a read-only role. Optional service-principal collection requires separate configuration. See [auth-setup.md](auth-setup.md).
2. **Collect** — Gather metadata, configuration, definitions, inventory, and metrics, not business-data rows. Definitions can contain literals or credentials; treat raw collection artifacts as sensitive.
3. **Analyze** — Apply checklist rules from `config/review-checklist.yaml` against thresholds in `config/thresholds.yaml`. Emit findings as structured JSON.
4. **Report** — Render central-review Markdown with executive summary, detailed findings (grouped by dimension), and a prioritized roadmap. The local workflow can also generate a PDF; Fabric produces Markdown and materializes Gold for Power BI.
5. **Review & handover** — Walk the client through findings; capture decisions; archive the engagement folder.

The DAX Analyzer parses TMDL or `model.bim` measure definitions already returned by
Fabric `getDefinition`. It assigns deterministic, explainable signals for patterns such
as nested iterators, broad virtual tables, whole-table filters, and complex row context.
These are prioritization hints only: no DAX is executed, and a signal is never presented
as measured duration, query-plan cost, capacity usage, or proof that a measure is slow.
Definition failures reduce coverage through `DAX-002` instead of being treated as clean models.

Calculated columns, calculated tables and calculation items are separate typed
evidence, not additional measures or inputs to measure-only DAX scoring.
Dataflow Gen2 signals describe static Power Query M, not DAX or measured folding
failures. Native execution history is a bounded observation, not a complete
ledger. See [native evidence](native-evidence.md) for coverage, deduplication
and validation guidance.

Collection failures and incomplete pagination remain explicit evidence gaps.
They do not become empty successful inventories or passing checks. The scanner
integrity rule `ARCH-013` reports collection failure directly. See
[outcomes and thresholds](#applicability-outcomes-and-thresholds) before scoring.

### Optional monitoring-driven scope selection

For the **normal FAR pipeline**, an empty `WORKSPACE_IDS` means **no
workspace filter**: tenant-wide where the executing identity has the required
admin access, otherwise the workspaces its permissions expose. Scope selection
does not grant additional access. This is distinct from the targeted parent's empty
selection, which must skip the review.

[Targeted review](targeted-review.md) adds a parent pipeline before these phases.
It ranks the configured FUAM monitoring records by CU-seconds (default) or
explicitly labelled **recorded throttling minutes**, then passes up to `TOP_N`
workspace GUIDs (default five, configurable from 1-100) into the normal review. Existing FAR parameters and finding
semantics do not change. An optional workspace allow-list constrains candidates
before ranking; an empty selection skips rather than runs tenant-wide.

High CU usage is not itself a finding. Recorded throttling minutes can be
incomplete because stock FUAM may omit zero-CU item/operation/day groups; they are
not a throttled-operation count or causal attribution of capacity overload.
Source failures stop the parent; there is no direct Capacity Metrics fallback.
The parent waits for a successful FAR review before optional owner notification.

Optional email links direct-user workspace administrators to the report after a
successful review. It does not perform remediation, export a task list or change
finding severity. Recipients need their own report permissions; a workspace
filter is navigation, not authorization.

The governance link scopes workspace-risk visuals, not the whole central
report. Keep central artifacts central-only; use the separately validated
[Workspace Owner report](workspace-owner-report.md) for owner consumers.

### Optional owner technical projection

The owner report is a curated subset, not a reproduction of the full checklist
or central score. All owner assessments remain **incomplete**; zero owner
failures is not a pass. Notebook signals and model-table statistics require
authoritative workspace/item IDs; name-only or ambiguous evidence is excluded.
Owner review history starts at enablement
unless retained historical runs are explicitly reprojected.

Review freshness and access freshness are separate. Owner grants expire within
24 hours and require independent daily `07_OwnerAccessSync`, even when reviews
run weekly. A new workspace needs a successful sync before readers receive rows.
Follow the [owner operating and live acceptance guide](workspace-owner-report.md)
for scheduling, reader approval and security validation.

Selection and email-preparation audits describe orchestration state, not review
quality or confirmed delivery. Fabric monitoring is authoritative for child-run
and mail-activity outcomes. For setup, validation and recovery, use the
[deployment guide](../fabric/DEPLOYMENT.md); for recipient privacy and permissions,
see [data safety](data-safety.md#optional-targeted-review-orchestration).

## Scoring

Each finding has one of five severities: `critical`, `high`, `medium`, `low`, `info`.

- **Critical** — Causes data loss, security incident, or sustained service degradation. Address immediately.
- **High** — Significant risk or sustained inefficiency. Address immediately.
- **Medium** — Best-practice deviation. Address within the quarter.
- **Low** — Hygiene / convention. Address opportunistically.
- **Info** — Observation; no action required.

## Applicability, outcomes, and thresholds

Before evaluation, workspace-scoped rules classify each workspace from its item composition,
environment signals, and optional workspace-ID overrides in `config/workspaces.yaml`. This
prevents production ALM controls from firing on personal, development, sandbox, or specialized
workspaces where the control does not apply.

Every enabled rule resolves to one of six outcomes:

- **pass** — the metric is within the acceptable boundary; no action needed.
- **fail** — a real, actionable problem crossed a threshold a reviewer would act on.
- **info** — context worth surfacing, but not a defect and not part of pass/fail scoring.
- **not_applicable** — the control does not apply to the classified workload or run mode.
- **unknown** — evidence exists, but it is insufficient or ambiguous for a defensible decision.
- **missing_evidence** — a required collector artifact is unavailable, so the rule was not evaluated.

An unsuccessful API call is not an empty successful result. Collectors preserve
HTTP failures and incomplete pagination as collection errors; affected checks
must not become `pass` because their result list is empty. A successful response
with no rows remains valid evidence. If a membership list or user identity is
unavailable, workspace-access checks report missing evidence rather than
assuming there are no direct administrators or guests. Observed violations can
still be reported as failures when other membership evidence is missing.

After correcting a permission or transient API problem, rerun Collect before
Analyze. Reanalyzing the failure artifact cannot recover the missing evidence.

Only `pass` and `fail` are scored. Assessment coverage is the scored rule count divided by
the rules that should have been evaluated; zero evaluated rules never produce a perfect score.

The boundary between pass and fail is intentionally conservative so reports highlight
genuine issues instead of noise. Soft signals (empty/orphaned/streaming inventory, a
single transient job failure, a point-in-time gauge below the critical line) are emitted
as **info**, not **fail**. Missing required input is **missing_evidence**, not a passing or
informational result. Hygiene rules that are evaluated as coverage (descriptions,
sensitivity labels, naming, Git) exclude personal ("My workspace") and empty workspaces
so they reflect real practice rather than structural clutter.

Every numeric pass/fail boundary is defined in **[`config/thresholds.yaml`](../config/thresholds.yaml)** —
the single place to tune the review to a client's SLOs and maturity. Each entry is
documented inline with the rule ID it drives and its pass/fail meaning. Values resolve
with the precedence **environment variable › `thresholds.yaml` › built-in default**, so
CI pipelines and the Fabric notebook parameters can still override any value without
editing the file. A missing or malformed threshold value uses its built-in default. Analyzer
artifacts are validated independently; malformed or partial finding output fails the stage
rather than being published.

## Data-safety guardrails

FAR reads metadata, definitions and documented monitoring aggregates, not
customer business rows, files or notebook outputs. Definitions and audit records
can still contain sensitive logic, literals and identities. Restrict output
access and retention; do not publish live artifacts.

See [data-safety.md](data-safety.md) for allowed sources, opt-in aggregate queries
and sharing boundaries.

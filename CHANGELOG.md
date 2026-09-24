<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Changelog

All notable changes to this project are documented in this file.

## 2026.09.2

### Native evidence and actionable reviews

- Native refresh/job execution observations with explicit collection coverage,
  stable execution identities and duration units.
- Dataflow Gen2 definition coverage and conservative static Power Query M signals.
- Typed calculated-column, calculated-table and calculation-item DAX evidence,
  separate from existing measure counts and runtime metrics.
- Pipeline activity dependency findings attributable to the affected pipeline.
- Governance evidence pages, scoped Fabric app views and central Data Agent
  queries distinguish static risk, observed execution duration and unavailable
  evidence.

### Workspace-owner reporting

- Optional separate report with **My workspaces**, **Findings**, **Technical details**, **Executions** and **Coverage** pages, workspace-specific review summaries and actionable investigation guidance.
- Workspace-level access for approved direct-user administrators through the `WorkspaceOwner` role. Daily access synchronization maintains 24-hour grants; sharing and role membership require manual approval.
- Curated semantic-model, DAX, notebook, Dataflow Gen2, execution and pipeline-dependency evidence with explicit coverage, not the full FAR checklist or score. Central governance reports, the Fabric app and the central Data Agent remain restricted to the central review team.
- Optional Workspace Owner Agent deployed on demand through **08_OwnerAgent**. Its sole source is the verified secured owner semantic model; reader permissions and model RLS, not instructions, enforce access.

### Targeted reviews and notifications

- Optional selection of the highest-CU workspaces, defaulting to five, with configurable count, scope and lookback. Recorded throttling minutes are also available as a ranking signal.
- Optional capacity scope by exact name or ID, or a JSON list of names/IDs, applied before ranking for either metric. Blank includes all capacities; top N applies across the combined selection. Invalid or ambiguous selectors fail without broadening scope.
- FUAM consumption without a workspace ID is excluded with a visible warning and an audit count. Malformed nonblank workspace IDs still stop selection rather than assigning consumption to another workspace.
- FUAM candidates are matched to a complete current workspace inventory before top-N ranking. Unmatched IDs are excluded with warnings and audit details; inventory lookup failures still stop selection.
- A parent pipeline runs FAR for the selected workspaces and, when configured, synchronizes owner access before notifications. Empty selections skip the review; source failures stop it.
- Optional Outlook notifications to direct-user workspace administrators after successful reviews. Email includes review context and a report link, not a task list or an access grant.

### Reliability and deployment

- Local PowerShell/Bash collection enforces the existing skip flags for
  Fabric-only VertiPaq and Semantic Link BPA/health analysis, without importing
  or installing their SDK. Skipped snapshots replace stale evidence. Fabric
  notebooks and shared collector code are unchanged.
- Improved long-running collection, workspace-membership evidence and review-history recovery. Missing evidence is distinguished from a successful empty result.
- Setup reuses compatible owner models, upgrades recognized older contracts in place, and accepts equivalent report bindings without recreating artifacts. Unrecognized contract or binding changes still stop deployment.
- Setup organizes FAR-owned items into Reporting, Pipelines, Ontology, Agents and Notebooks folders without recreating them. Only the Lakehouse and imported setup notebook remain at the root; 06 joins the other deployed notebooks in Notebooks, including after standalone reruns.
- Partial workspace inventories retain usable pipeline/notebook definitions and execution evidence, including items from successful pages before a later page fails. Inventory warnings identify the affected workspace and service error code; incomplete coverage cannot produce an all-clear.
- Capacity and native-item collection isolates component failures, retaining other observations. Collection summaries include per-model/probe errors, and HTTP diagnostics expose service error codes without response bodies.
- Corrected the capacity Workloads endpoint and restored report BPA discovery from Scanner inventory.
- Schema-enabled Lakehouses use metadata-only OneLake table discovery, preserving schema identities and partial results without unsupported legacy table-list requests.
- Known shared/Pro, PPU, inactive/deleted and built-in Admin monitoring workspaces are excluded from review collection and downstream evidence. Admin monitoring can still serve as an explicitly configured metrics source.
- Workspace discovery follows all admin API pages before applying review scope, and fresh capacity assignments replace stale PPU exclusion metadata.
- Unknown execution durations remain unavailable in Gold and owner evidence rather than becoming measured zeroes.
- Recognized standard TMDL metadata no longer marks otherwise complete DAX object coverage as partial.
- Central and owner Data Agent deployment supports grouped SDK schema elements while preserving explicit source and table selection.
- Core stage notebooks reload framework modules after checkout and log the running revision when Git metadata is available; source snapshots do not require it. Unavailable analysis libraries are distinguished from explicit opt-outs in collection summaries.
- Targeted redeployment preserves configured email settings but resets the notification default to off.
- Task-based deployment and operating guides cover scoped testing, recurring reviews, access approval and recovery.

See [deployment](fabric/DEPLOYMENT.md), [native evidence](docs/native-evidence.md)
and [owner access validation](docs/workspace-owner-report.md#live-fabric-acceptance)
for setup and operating requirements.

## 2026.09.1

- Added tenant-setting audit history from observed Activity Events, with actor, timestamp, operation, setting, and available before/after values across Gold, the Direct Lake report, Data Agent, and Rayfin app.
- Clamped activity-log collection to the supported 28-day maximum while retaining the seven-day default.
- Enriched capacity cost evidence with capacity, workspace, and item detail across Gold, the report, Data Agent, and Rayfin app.
- Added affected capacity or workspace rows for every Cost finding in the report; selecting one filters the adjacent capacity-contents table while preserving the complete default inventory.
- Prevented `COST-005` from scoring partial workspace scans as tenant-wide evidence and surfaced the scope limitation in downstream experiences.

## 2026.09.0

- Added metadata-only DAX definition collection from TMDL and `model.bim`, with explicit missing/error coverage and static pattern-risk rules `DAX-001` and `DAX-002`.
- Added a dedicated DAX Analyzer report page and a capacity-first, semantic-model-second DAX lens in the Fabric app.
- Added DAX model and measure contracts across Gold tables, Direct Lake semantic metadata, Fabric IQ ontology, the Data Agent, Rayfin queries, and deterministic agent evaluation.
- Hardened semantic-model definition collection with token renewal, bounded retries, and resumable checkpoints for long tenant scans.
- Pinned the preview Fabric Data Agent SDK and stamped the resolved repository branch or release ref into the standalone deployed agent notebook.

## 2026.08.2

- Added contextual workspace classification and explicit applicability overrides.
- Added six-state finding outcomes and assessment-coverage scoring.
- Reconciled rule metadata, predicates, thresholds, and superseded rule IDs.
- Hardened local and Fabric stages against stale, missing, malformed, or partial artifacts.
- Added traceable run manifests and retry-safe Gold writes by `RUN_ID`.
- Added full TypeScript production checking, bounded query behavior, schema-based Data Agent discovery, throttling retries, and deliberate vendor chunking.
- Added GitHub-hosted CI gates for Python, notebooks, shell scripts, frontend tests, lint, builds, and package vulnerability audits.
- Updated repository, Fabric, app, methodology, checklist, and contribution documentation.

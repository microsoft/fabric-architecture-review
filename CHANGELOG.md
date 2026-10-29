<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Changelog

All notable changes to this project are documented in this file.

## 2026.10.2

### Targeted-review cooldown

- Add `SKIP_REVIEWED_WITHIN_DAYS` to targeted-review setup and parent run
  parameters. The default `0` keeps existing selection behavior; a positive
  value excludes workspaces from successful targeted reviews in the preceding
  N rolling days, before Top-N selection, for either ranking metric.
- Refill Top-N from eligible workspaces within the existing capacity and
  allow-list scope. Cooldown exclusions override the allow-list; an empty
  selection safely skips FAR and email.
- Persist successful targeted child completion independently of notifications,
  before owner-access sync and email. Failed or unfinished child reviews do not
  start a cooldown, and a later sync or email failure does not erase completion.
- Record cooldown exclusions in selection audits. Unreadable or malformed
  completion history stops an enabled selection instead of silently bypassing
  the cooldown.
- Standalone reviews are unchanged. History starts after redeploying the
  targeted parent; standalone and pre-upgrade reviews are not backfilled.

### Upgrade and testing

Pause targeted schedules, finish active runs, and rerun **all cells of 06** from
the updated deployment ref. This updates the parent and both runtime notebooks
together. Confirm the new **Record successful review** activity, then test with
notifications off. Leave the cooldown at `0` to disable exclusions or set it to
the required number of days. Successful targeted reviews are recorded either way.
Keep the existing independent daily owner-access sync schedule.

See the [cooldown guide](docs/targeted-review.md#recently-reviewed-workspace-cooldown)
for history retention, failure handling and the rolling UTC boundary. Sequential
Fabric runs verified successful completion recording and exclusion of the first
run's workspaces from the second selection. Validate the upgraded flow in your
own workspace before resuming schedules.

## 2026.10.1

### Architecture reviews

- 70 rules across architecture, governance, operations, reliability and security,
  with actionable recommendations and a searchable rule catalogue.
- Production scope and requirements are explicitly configured. Only assessed controls
  affect the score; signals and missing evidence remain separate.
- Optional FUAM and FCA connections enrich reviews with activity, capacity and sharing
  evidence. Unconnected sources do not trigger additional monitoring or financial collection.
- Exclude FAR hosting and Capacity Metrics app workspaces from reviews. Targeted
  reviews skip excluded candidates before Top-N selection and use the next eligible workspaces.

### Semantic model security

- RLS and OLS checks for declared semantic models, with coverage for missing models
  and a signal for models that define neither mechanism.
- A Model security view in the report and app, with corresponding owner-report and Agent support.
- Optional role details and temporary workspace access for Fabric administrators.
  Both are off by default; filter expressions and role membership are never stored.
- Scoped reviews let Fabric administrators discover models without workspace membership. Temporary
  access checks current membership before granting, rather than relying on cached FUAM membership.

### Reports and deployment

- Updated central and workspace-owner reports, actionable findings, estate navigation,
  Data Agents and optional Fabric IQ ontology.
- Consistent FAR names for deployment artifacts, configuration, environment settings and local files.
- Three Variable Libraries for identity, source connections and review policy. Setup preserves
  configured values and adds new settings with their defaults.
- Updated deployment, configuration and local-review guides.
- Deployment defaults and notebook documentation links target the Microsoft
  repository's `main` branch. Use a release tag for repeatable deployments.
- App builds refresh the semantic-model binding, so changing the configured model
  does not leave the dashboard connected to a previous deployment.
- App chat shows request progress and actionable failures, avoids repeating slow
  failed requests, and offers only targeted, bounded suggested questions.
- Update app and build dependencies to address the reported npm security advisories.
- Patch transitive authentication, request parsing, URI, schema-validation and HTTP
  client dependencies without downgrading Rayfin or disabling the dependency audit.
- Add explicit standard/preview runtime selection to central and owner Agent
  deployment notebooks, defaulting to standard and verifying the setting before publication.

### Upgrade

Rerun setup, verify the configuration, and complete a scoped review before resuming schedules.
Use `.env.far` for local runs. Results from different review versions are not compared.
See the [upgrade guide](fabric/DEPLOYMENT.md#upgrading).

## 2026.09.2

### Hotfix: consistent workspace evidence

- Reconcile eligible Scanner and REST workspace inventories by immutable ID
  instead of discarding one source when the other is populated.
- Normalize and deduplicate legacy Scanner, native PascalCase Scanner, and flat
  REST items across workspace rules, collectors, and reporting.
- Preserve independently collected memberships and distinguish unavailable
  users/items from collected empty lists. Count distinct administrator principals
  and report conflicting role evidence as incomplete.
- Correct production administrator, governance metadata, inventory, and ALM
  evaluations; honor environment profiles consistently in production checks.
  Basic REST item listings no longer imply absent labels or endorsements.
- Add regression coverage for mixed evidence, partial child collections,
  workspace-scope exclusions, duplicate items/principals, and report projections.

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

- Correct tenant-setting assessments to use current Fabric API names, check
  Excel and CSV export controls independently, and avoid treating missing,
  unrelated, or exclusions-only settings as evidence of policy compliance.
- Refresh Fabric app and deployment-tool dependencies to resolve npm audit
  advisories without major-version upgrades.
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

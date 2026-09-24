<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Native operational and expression evidence

Use execution history, definition coverage and static expression signals to
identify the affected item, choose a corrective action and validate the result.

## Where to find the evidence

| Experience | Audience and purpose | Evidence |
| --- | --- | --- |
| Governance report | Central review team; estate-wide investigation | **Execution history**, **Dataflow Gen2** and **DAX objects** pages; pipeline dependency findings in **Architecture** |
| Workspace Owner report | Approved workspace administrators; restricted to their authorized workspaces | Curated findings and technical details, plus **Executions** and **Coverage** pages |
| Fabric app | Central review team; investigation within the selected review and scope | **Native evidence** lenses: **Observed executions**, **Dataflow Gen2**, **Calculated DAX objects**; pipeline defects in **Findings** |
| Central Data Agent | Central review team; questions over approved review tables | Queries for execution observations, collection gaps, Dataflow signals, typed DAX objects and affected pipelines |
| Workspace Owner Agent | Approved owners; questions over the secured owner model | Curated findings, technical evidence, execution observations and coverage for authorized workspaces |

The owner report uses its own secured model. Its access controls do not protect
the central report, app, central Data Agent or Lakehouse. The separate
[Workspace Owner Agent](workspace-owner-agent.md) uses only that secured owner model.

In the app, use capacity, workspace and item filters to narrow the selected
review. The **Review agent** chat does not inherit those page filters: name the
workspace, item or review in the question. Current-state Agent queries use each
workspace's latest review; history questions use execution observations.

The app's **FAR review run** identifies one pipeline run, not a live monitoring
session. **Collected in this FAR review** shows execution observations saved
during that run; the executions themselves can have started earlier.
**Collected across reviews up to this one** combines saved observations from
that run and earlier reviews, keeping the latest observation of each execution.
Neither option guarantees a complete execution log.

## Choose the evidence

| Question | Open | What the evidence establishes |
| --- | --- | --- |
| Which refresh or job should I investigate? | Governance **Execution history** | Observed execution identity, status, timestamps and duration, with collection coverage |
| Which Dataflow Gen2 query needs inspection? | Governance **Dataflow Gen2** | Available definition coverage and conservative static Power Query M signals |
| Which calculated object needs inspection? | Governance **DAX objects** | Typed calculated columns, calculated tables and calculation items, with static signals |
| Which measure needs inspection? | Governance **DAX Analyzer** | Measure-only static signals; non-measure objects do not inflate its counts |
| Does a pipeline have a broken dependency graph? | Governance **Architecture** findings | Literal activity dependency problems attributable to the affected pipeline |

On Home, select **Native evidence & execution history**. The evidence pages
link to each other and to the measure view. On a coverage table, select an item
to filter its detail table. Use immutable IDs to distinguish identical display
names.

Native evidence grids retain workspace, item and review identifiers so rows with
identical display names remain distinguishable. Scroll horizontally for the
diagnostic identifiers.

Non-measure DAX detail and coverage rows resolve missing workspace names by
workspace ID from the same run's workspace or scanner inventory. A collected
name is preserved; an unknown workspace ID stays unnamed rather than borrowing
a name from another workspace or an older run. Identical model display names
never determine workspace identity.

## Workspace review scope

FAR excludes observed Pro/shared workspaces (`isOnDedicatedCapacity=false`),
PPU workspaces (including capacity SKU `PP3`), deleted/inactive workspaces,
and built-in workspaces typed `AdminWorkspace`. Explicit `WORKSPACE_IDS`
cannot override exclusions. Excluded resources receive no item, model,
refresh/job-history, capacity workload, or refreshable probes, and their
known identities are filtered from review evidence and Gold.

Workspace/capacity identity discovery and tenant settings remain available.
`excludedWorkspaces` retains each excluded ID plus available policy fields:
`type`, `state`, `capacityId`, `capacitySku`, `isOnDedicatedCapacity`, and
`isOnPremiumPerUserCapacity`. Capacity inventory retains excluded records in
`excludedCapacities`; `review_scope.json` preserves discovered policy metadata.
These identities also filter historical raw evidence without rewriting it.
Required discovery failures are reported as incomplete, not as empty success.
Power BI workspace discovery exhausts `$top`/`$skip` pages before policy
filtering or child probes; failed or repeated pages are not complete evidence.
Fresh capacity assignments invalidate cached capacity-derived exclusions,
including after a PPU-to-Fabric migration. Missing assignment metadata does
not erase previously observed exclusions.

Real Fabric/dedicated capacities remain supported. Missing metadata, workspace
names, and users' Pro licenses never establish exclusions. Personal workspaces
observed on shared capacity are excluded before probing; otherwise existing
personal-workspace handling still applies. An `AdminWorkspace` may supply
operational Capacity Metrics App evidence **only if it is not shared, PPU, or
inactive**. That exception does not make its items review targets and cannot
override capacity exclusions, even with explicit Metrics App source IDs.

## Read execution history correctly

FAR observes the **recent history retained by the native APIs** when a review
runs. It does not install continuous monitoring or promise every event since
the previous review. API retention, permissions, paging limits and collection
failures can leave gaps. Running FAR weekly does not guarantee a complete week
of execution history.

FAR requests up to **10 refresh-history records per semantic model** and bounds
Fabric job collection to **five pages / 1,000 records per item**. Power BI's
refresh-history API excludes OneDrive refresh history. Inspect collection
coverage rather than treating these bounds as a complete time window.

An execution coverage row without an item ID describes an inventory gap, not an
execution. The app retains these gaps so missing inventory remains visible.
Pipeline/notebook inventory warnings identify the workspace and service error
code. Items from successful pages remain usable when a later page fails;
`WorkspaceTypeNotSupported` in an otherwise eligible workspace remains an
explicit coverage gap, not an empty workspace. Observed `AdminWorkspace` types
are excluded instead of producing such a warning. The failed-job count includes
only retained `Failed` executions within
the configured window, filtered locally rather than relying on an undocumented
server-side filter.

- `run_timestamp` is the FAR observation time; `start_time` and `end_time`
  describe the observed execution.
- `duration_ms` is milliseconds. The report's longest-duration card converts
  this to seconds. Neither value represents CU, cost or individual DAX timing.
- The same `execution_key` can appear in several review snapshots. The report
  counts distinct execution identities, while the detail table retains the
  observations. A distinct-ID count alone does not resolve status changes across
  snapshots. For historical status totals, select the **latest observation
  per execution key**, with a deterministic review-ID tie-break.
- Failed, cancelled, active and unknown executions are different outcomes.
  Missing history is not zero failures; an unavailable end time is not a
  zero-duration execution.
- Select a review in the execution page's optional snapshot slicer to inspect
  what that review observed. Other native static-evidence pages open on the
  latest review run, which may cover only a targeted subset of workspaces.

Start with the coverage row, then open the identified refresh or job in Fabric
to inspect its detailed diagnostics. Confirm changes against representative
runs before claiming an improvement.

## Interpret static evidence

**Dataflow Gen2 uses Power Query M, not DAX.** A buffering or explicit
folding-barrier signal identifies code to examine. It does not prove that a
query should fold, that folding failed, or that removing the step is safe.
Check source support, query dependencies and refresh behavior in the authoring
experience. Unsupported or partial definitions remain coverage gaps.

`DFLOW-001` is informational even when syntax signals are present; it does not
score them as measured failures. `DFLOW-002` reports collection/inspection
coverage. `inspected` means selected lexical checks completed, not full M
validation. `inventory_unavailable` rows have no dataflow ID and describe an
inventory gap, not a Dataflow artifact.

**Non-measure DAX objects keep their real types.** For calculated columns and
tables, investigate processing time and model size. For calculation items,
test representative consuming queries. Do not apply a measure rewrite blindly
to a calculated table or column. Report visual calculations are not collected.
Format-string expressions and other unsupported expression surfaces are not
included. `complete` describes extraction coverage for supported object types,
not semantic validation or runtime health. `DAX-001` and `DAX-002` keep their
measure-only scoring; non-measure detail and coverage are presented separately.

**Pipeline dependency checks are structural.** Duplicate names within an
activity scope, missing literal dependency targets, self-dependencies and
cycles can justify a finding. A dynamic or unresolved dependency is not proof
of a broken pipeline. These checks do not execute the pipeline or infer its
runtime performance.

## Data, permissions and access

The normal collection identity needs access to the relevant inventory,
definitions and native history endpoints. Tenant inventory visibility alone
does not guarantee access to every item's definition or history. Check
[authentication and permissions](auth-setup.md) and the
[source contracts](data-safety.md) before collection.

Execution observations, static Gen2 queries and non-measure DAX objects each
have separate detail and coverage tables in central Gold. Evidence is identified
by review, workspace and item IDs, not display names; measure-only counts remain
separate.

The Gen2 and non-measure DAX evidence tables exclude raw expressions and
connection literals. Existing raw model, pipeline and notebook definitions,
measure expression previews and central metadata remain sensitive.

Republish the central Data Agent after deploying this evidence.
**Central Agent/Lakehouse access is not protected by owner-model RLS.**
Give workspace consumers only the separate
[owner experience](workspace-owner-report.md), never access to central raw
tables as a shortcut.

## Deploy and validate

1. Follow [deployment upgrade steps](../fabric/DEPLOYMENT.md#upgrading), with
   schedules paused and existing runs finished.
2. Deploy the updated governance model/report and collection stages. Run a
   scoped normal FAR review through successful **04 Gold**.
3. If using owner reporting, configure its fixed-identity connection and follow
   the [owner access validation](workspace-owner-report.md#live-fabric-acceptance).
   Do not bypass an incompatible-model warning.
4. When owner reporting is enabled, run **07_OwnerAccessSync** for the owner
   model and maintain its separate daily schedule. If using targeted review, rebind through **06** before
   resuming schedules or notifications.
5. Republish the standalone **05 Agent** after the new tables are available.
   Test questions about coverage, execution duration, M signals and typed DAX
   objects. A central Agent answer is not an owner-isolation test.
6. If owner chat is enabled, deploy it separately through **08_OwnerAgent** and
   complete its [actual-reader acceptance](workspace-owner-agent.md) before sharing.

Before rollout, compare representative execution, Gen2 and typed DAX evidence
with the source. Confirm coverage gaps remain visible, repeated snapshots are
not counted as new executions, and identically named items stay distinct.

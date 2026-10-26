<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Architectural evidence and coverage

The review collects metadata from the Fabric and Power BI APIs. It does not depend
on FUAM or FCA, and it does not replace them.

## Evidence collected

| Evidence | Used by |
| --- | --- |
| Workspaces, items, principals, roles and workspace state | ARCH-001, ARCH-002, ARCH-006, ARCH-008, ARCH-009, ARCH-015, ARCH-017, ARCH-020, GOV-001, GOV-003, GOV-004, OPS-006, SEC-001, SEC-002, SEC-009, SEC-017, SEC-018 |
| Tenant settings (names, state and group counts; never group names or IDs) | GOV-002, GOV-010, SEC-003, SEC-004, SEC-005, SEC-007, SEC-008, SEC-013, SEC-014, SEC-015, SEC-016, SEC-023 |
| Capacity metadata (SKU, state, region, hosting, assigned workspaces) | ARCH-002, ARCH-007, ARCH-016, REL-001 and the capacity pages of the report |
| Administrator scan: endorsement, sensitivity-label presence, upstream links, reports and their models, and counts of who can access each item | GOV-005, SEC-012, ARCH-014, ARCH-018, ARCH-019, REL-003, SEC-022 |
| Semantic model storage modes | ARCH-003 and the storage pages of the report |
| Semantic model row-level and object-level security: counts of roles, row filters and hidden tables and columns, and whether the definition could be inspected completely (the dataset's effective-identity roles flag is kept as corroboration). With the optional role detail setting, also each role's name, permission and per-role counts | SEC-024, SEC-025, SEC-026, SEC-027 and the Model security page of the report |
| Pipeline and notebook structural summaries, including activity retry and timeout | ARCH-004, ARCH-005, ARCH-010, ARCH-011, ARCH-012, ARCH-013, REL-002, SEC-011 |
| Git connection per workspace | OPS-001 |
| Deployment pipeline stages, OneLake data access role counts and workspace network policy, firewall rule counts and managed private endpoint counts (declared production workspaces only) | OPS-010, SEC-020, SEC-021 |
| Gateway clusters and versions | SEC-010 |

Only rules that are enabled and applicable request their evidence. Definitions are
reduced to sanitized structure before they are stored.

## Optional enrichment

When FUAM or FCA are connected, supported Lakehouse data is read through fixed,
aggregating queries. Each record keeps its source module, table, observation time and
freshness, so the **Evidence coverage** page shows what came from where. The data enables
the FUAM and FCA rules below. It never replaces the architectural baseline that FUAM does
not hold: without the module those rules are not applicable.

**Read from FUAM first, completed by the API.** The review reads these from FUAM when it is
connected and calls the API only for what FUAM does not cover:

- *Tenant settings.* State, title, group and delegation come from FUAM's latest
  `tenant_settings` snapshot, and the admin API adds the security-group detail FUAM lacks
  (including excluded groups). If the API is denied the FUAM values stand and the excluded
  count is unknown. If the snapshot is older than `max_age_hours` plus one day, missing or
  unreadable, the full admin API call is used instead, with the reason recorded.
- *Workspace items and members.* Read per workspace from `active_items` and
  `workspaces_scanned_users`. A workspace for which FUAM returns no items, or no members, is read
  from the API rather than assumed empty, and every workspace is read from the API when the
  snapshot is stale, missing or unreadable. Each workspace records its source and the run records
  the FUAM snapshot time. FUAM holds a daily snapshot, so an item or member added since its last
  transfer is not in it. The workspace list, capacity assignment, Git connections, model and
  pipeline definitions and gateways are always read from the APIs.

Every family is read independently. If one cannot be read (a missing table, an unexpected
column set, a stale transfer) only the rules that need it report `missing_evidence`, with
a reason code; error text is never stored.

| Module | Table read | What is read | Used by |
| --- | --- | --- | --- |
| FUAM | `workspaces`, `active_items`, `capacities` | Workspace, item and capacity inventory for cross-checking coverage | Evidence coverage |
| FUAM | `tenant_settings`, `tenant_settings_enabled_security_groups` | The latest snapshot of every setting (state, title, group, delegation) and how many groups each is limited to. Read before the admin API, which adds the group detail | Tenant-setting controls, Tenant settings page |
| FUAM | `tenant_settings` | When each rule-relevant tenant setting last changed and how often in 90 days | Tenant settings page |
| FUAM | `activities` | Per workspace: the date of the latest activity, and the earliest and latest date FUAM holds. No users or events | GOV-007 |
| FUAM | `active_items` | Per workspace: item count and how many were last modified before `stale_item_days` | GOV-008 |
| FUAM | `workspaces` | Per workspace: whether it has a domain | GOV-006 |
| FUAM | `capacity_metrics_by_item_by_operation_by_day` | Per item over `window_days`: operations, failed operations and compute (CU); per capacity: total compute | OPS-002, OPS-003, OPS-007 |
| FUAM | `capacity_metrics_by_item_kind_by_day` | Per capacity: days observed and days with throttling | OPS-004 |
| FUAM | `widelyshared_publishtoweb`, `active_items` | Items in the latest transfer of published-to-web items, joined to their workspace. No sharer identity | SEC-006 |
| FUAM | `widelyshared_organization_links`, `active_items` | Items in the latest transfer of organization-wide sharing links, joined to their workspace. No sharer identity | SEC-019 |
| FUAM | `capacity_metrics_by_item_by_operation_by_day` | Per semantic model over `window_days`: `Query` and `XMLA Read` operations only | ARCH-019 |
| FUAM | `active_items` | Whether a semantic model that a report points at exists in another workspace (IDs only) | ARCH-018 |
| FUAM | `capacity_refreshable_details` | Per item over `window_days`: completed and failed refreshes. No item names or request identifiers | OPS-005 |
| FUAM | `active_items`, `workspaces_scanned_users` | Items (ID, name, type, description) and members (type, role, identifier) per workspace; read before the per-workspace API calls, which fill the gaps | Workspace inventory |
| FCA | `resources` | Azure region and placement of mapped capacities | Capacity coverage |
| FCA | `focus` | Only `ResourceId` and the `Tags` text of mapped capacities. Tag names are kept; values and every cost column are not read or stored | GOV-009 |

A result is accepted only when the data is no older than the connection's `max_age_hours`
(daily metrics may lag by one more day). FUAM's activity history is kept for a configurable
period, so GOV-007 reports `missing_evidence` when FUAM holds less history than `inactive_workspace_days`.

## Reading coverage

- `collected` evidence was read completely.
- `partial` evidence was incomplete; dependent results are `unknown` or
  `missing_evidence`, never a pass.
- A configured module that cannot be read is shown as an evidence gap.

## Audiences

Central teams use the Power BI report, the Data Agent and the optional ontology.
Approved workspace owners can use a restricted owner report and Agent. See the
[owner report guide](workspace-owner-report.md).

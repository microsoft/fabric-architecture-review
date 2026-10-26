<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Rule reference

The review evaluates 70 architecture rules. Workspace rules produce one finding per
workspace in scope; **capacity** rules (OPS-004, OPS-008, OPS-009) produce one finding per
capacity, which every workspace on that capacity points to; **tenant-wide** rules produce one
finding per review. The
machine-readable catalogue is [config/review-checklist.yaml](../config/review-checklist.yaml).

IDs are contiguous within a dimension (`ARCH`, `GOV`, `OPS`, `REL`, `SEC`). The original rules are ordered
controls first, then signals, then coverage; rules added later (SEC-024 to SEC-027) keep every earlier
identifier stable and follow them.

| Kind | Meaning |
| --- | --- |
| **Control** | Can pass or fail. Only controls contribute to the architecture score. |
| **Signal** | Non-scored context that suggests where to look. Never a failure. |
| **Coverage** | Reports whether the evidence the review needs was available. |

Statuses are `pass`, `fail`, `info` (signals and coverage), `not_applicable`,
`unknown` (a requirement was not declared or cannot be proven) and
`missing_evidence` (collection was incomplete). **Unknown and missing evidence are
never counted as a pass.** With no assessable control the score is blank, not 100 or 0.

How a rule becomes assessable:

- **Default** controls run without any declaration. Where noted they run only for
  workspaces you declare as production. Names never establish production.
- **Declared** controls run only when you declare their requirement in the
  [policy variables](far-policy-configuration.md). Until then they report `unknown`.
- **Tenant** rules evaluate the recorded tenant settings or tenant inventory and need no declaration.
- **FUAM / FCA** rules (marked below) read the connected module's data. They are
  `not_applicable` when the module is not connected, and `missing_evidence` when its data
  is unreadable or older than `max_age_hours`. The rest of the review never depends on them.

## Architecture

| ID | Kind | How it is evaluated | Result means |
| --- | --- | --- | --- |
| ARCH-001 | Control (declared) | `allowed_item_types`, complete item inventory | Every item is of an allowed type |
| ARCH-002 | Control (default, production) | Shared rather than personal workspace; `allowed_capacity_ids` when declared | Production is shared-owned and, if declared, on an approved capacity |
| ARCH-003 | Control (declared) | Semantic model storage modes against `allowed_storage_modes` | Models use only allowed modes |
| ARCH-004 | Control (default) | Pipeline notebook activities pass only parameters the notebook declares | Static parameter contracts hold |
| ARCH-005 | Control (default) | Pipeline activity dependencies: duplicates, missing references, self-dependencies, cycles | Pipeline dependency graph is sound |
| ARCH-006 | Control (default, production) | The production workspace's capacity is not declared non-production and hosts no workspace you declared non-production | Production does not share a capacity with non-production work. Compared within the review scope only |
| ARCH-007 | Control (declared) | The workspace's capacity region against `allowed_regions` | Capacities are in approved regions. The region of the capacity, not of every item's data |
| ARCH-008 | Signal | Item count against `monolith_max_items` | A large workspace may mix responsibilities |
| ARCH-009 | Signal | Descriptions on the workspace and its items against `description_coverage_min_ratio` | Purpose and ownership are documented |
| ARCH-010 | Signal | Sanitized pattern count for runtime-specific notebook code | Portability limits worth knowing; intentional code is expected |
| ARCH-011 | Signal | Sanitized count of literal storage paths, workspace or item identifiers and server names in notebook code | Code is tied to one environment and blocks promotion between environments |
| ARCH-012 | Signal | Sanitized count of package installs with no pinned version or requirements file | The runtime can change between runs |
| ARCH-013 | Signal | Execution activities in pipelines with no retry, or the default 12-hour timeout | A transient failure stops the pipeline; a hung activity blocks it. Names the pipelines by ID. Dynamic values are not judged |
| ARCH-014 | Signal | Semantic models and dataflows that depend on items in other workspaces, against `cross_workspace_dependency_max` | Tight coupling between workspaces. Sometimes intended |
| ARCH-015 | Signal | Items of a legacy type (today Datamart) | Candidates for migration |
| ARCH-016 | Signal (tenant) | Capacities with no assigned workspace | Unused capacity. Evaluated only when the review is not limited to chosen workspaces |
| ARCH-017 | Signal | Workspaces that hold no items | A leftover or unused workspace. A workspace can be reserved on purpose |
| ARCH-018 | Signal | Power BI reports whose semantic model exists nowhere in the tenant | Reports that cannot show data. With a fresh FUAM a model in another workspace is found and not counted; without it such a report is listed as unresolved. Paginated reports are not counted |
| ARCH-019 | Signal | Semantic models no report or other model uses, with no query or XMLA read in `window_days` (FUAM) | Models that may be unused. With FUAM a queried model is never flagged; without it Excel and Analyze in Excel use is not seen. A data store's default model is skipped, and a model that is not on a capacity has no recorded usage |
| ARCH-020 | Coverage | Item and principal inventory collected completely | The review of the workspace is complete |

## Governance

| ID | Kind | How it is evaluated | Result means |
| --- | --- | --- | --- |
| GOV-001 | Control (default) | Independent individual administrators against `min_admins`; `eligible_admin_ids` restricts who counts | Administration survives a departure. A group-only result is `unknown`, not assumed independent |
| GOV-002 | Control (tenant) | Tenant setting that lets users create Fabric items | Item creation is disabled or limited to security groups |
| GOV-003 | Control (default) | Workspace state reported by the administrator inventory | The workspace has an administrator. `Orphaned` fails. Needs the administrator inventory |
| GOV-004 | Signal | Names against your `naming_pattern`; share against `naming_coverage_min_ratio` | Naming is consistent with your convention (only when a pattern is set) |
| GOV-005 | Signal | Share of a production workspace's semantic models that are Promoted or Certified against `endorsement_min_ratio` | Production models are endorsed |
| GOV-006 | Signal (tenant, FUAM) | Share of reviewed workspaces assigned to a domain against `domain_coverage_min_ratio` | The estate is organized into domains |
| GOV-007 | Signal (FUAM) | Last recorded activity against `inactive_workspace_days` | The workspace may be abandoned. Needs enough activity history, otherwise missing evidence |
| GOV-008 | Signal (FUAM) | Share of items last modified over `stale_item_days` ago against `stale_item_max_ratio` | The workspace holds many untouched items. The date is the last modification, not the last use |
| GOV-009 | Signal (tenant, FCA) | Azure capacity tags against `required_capacity_tags` | Capacities carry the tags you require. Tag values are never read |
| GOV-010 | Coverage (tenant) | Whether the tenant settings were recorded | The tenant-setting rules rest on recorded evidence |

## Operations

| ID | Kind | How it is evaluated | Result means |
| --- | --- | --- | --- |
| OPS-001 | Control (default, production) | Git connection of the workspace | Production has source control. Not a statement about review or branching process |
| OPS-002 | Signal (FUAM) | Items whose failed share of operations over `window_days` exceeds `failure_max_ratio`, judged above `failure_min_events` | Unreliable items. Capacity operations per item, not individual job runs |
| OPS-003 | Signal (FUAM) | An item's share of its capacity's compute over `window_days` against `expensive_item_cu_max_ratio` | An item dominates its capacity. Relative to the capacity, so no absolute CU figure |
| OPS-004 | Signal (FUAM) | Share of days in `window_days` the workspace's capacity was throttled against `throttled_day_max_ratio` | The capacity is under pressure. One finding per capacity that every workspace on it points to |
| OPS-005 | Signal (FUAM) | Items whose failed share of completed and failed refreshes over `window_days` exceeds `refresh_failure_max_ratio`, judged above `refresh_min_events` | Data that is not refreshing. Counts only; cancelled or unknown outcomes are not counted |
| OPS-006 | Signal (declared, production) | Capacity type of a declared production workspace: Trial (FT SKU), Premium per user or Embedded (A, EM) | Production runs on a capacity not meant for production. Premium-per-user workspaces can be outside the review scope |
| OPS-007 | Signal (FUAM) | The workspace's share of its capacity's compute over `window_days` against `workspace_cu_max_ratio` | A workspace dominates a shared capacity. A workspace alone on its capacity is reported without a recommendation |
| OPS-008 | Signal | The capacity the workspace runs on is not active (paused, suspended) | Everything on it is unavailable. One finding per capacity that every workspace on it points to |
| OPS-009 | Signal | Capacity administrators against `min_capacity_admins` | A departure can leave the capacity without anyone to manage it. Counts only; one finding per capacity |
| OPS-010 | Signal (declared, production) | Whether a declared production workspace is assigned to a deployment pipeline stage | Production has a tested path. Release through Git is not seen; pipelines the reviewer cannot see make a missing assignment missing evidence |

## Reliability

| ID | Kind | How it is evaluated | Result means |
| --- | --- | --- | --- |
| REL-001 | Signal (tenant, declared production) | The share of declared production workspaces on the largest capacity, and the capacities and regions behind them, against `capacity_concentration_max_ratio` | One capacity is one failure, pause and throttling domain. A second capacity that is paused or nearly empty does not spread the risk. Production workspaces in the review scope only |
| REL-002 | Signal | Pipelines with two or more service-calling activities where no activity runs when another fails or completes | A failed step ends the run unnoticed by the pipeline. Names the pipelines by ID. A heuristic; alerts set up outside the pipeline are not seen |
| REL-003 | Signal (declared, production) | Semantic models and dataflows of a production workspace that read from a workspace declared non-production | Production changes or stops when a test workspace does. Undeclared upstream workspaces are counted, not judged |

Reliability is also covered where the data lives: source control (OPS-001), deployment pipelines (OPS-010),
capacity state, administrators and throttling (OPS-008, OPS-009, OPS-004), refresh and item failures (OPS-005,
OPS-002), production isolation (ARCH-006), pipeline retry and timeout (ARCH-013), gateway redundancy (SEC-010) and
administrator continuity (GOV-001).

## Security

| ID | Kind | How it is evaluated | Result means |
| --- | --- | --- | --- |
| SEC-001 | Control (declared) | Direct principals against `allowed_principal_ids` (object IDs) | Only approved principals have direct access. Group membership is not expanded |
| SEC-002 | Control (declared) | Direct roles against `allowed_roles` | Only approved roles are used. Item-level permissions are not evaluated |
| SEC-003 | Control (tenant, critical) | Tenant setting **Publish to web** | Public publishing is disabled or limited to security groups |
| SEC-004 | Control (tenant) | Tenant setting **External data sharing** | Cross-tenant sharing is disabled or limited to security groups |
| SEC-005 | Control (tenant) | Tenant setting **Service principals can use Fabric APIs** | Service principal API access is disabled or limited to a security group |
| SEC-006 | Control (FUAM, critical) | Items in the latest FUAM transfer of published-to-web items | No item in the workspace is public on the web. Needs a published-to-web transfer in FUAM |
| SEC-007 | Control (tenant) | Tenant setting **Users can invite guest users to collaborate through item sharing and permissions** (`ExternalSharingV2`) | Guest invitations are disabled or limited to security groups |
| SEC-008 | Control (tenant) | Tenant setting **Block ResourceKey Authentication** (`BlockResourceKeyAuthentication`) | Resource keys cannot bypass sign-in. Passes when the protective setting is **enabled** |
| SEC-009 | Signal | Direct non-group principals against `broad_access_max_principals` | Access may be easier to govern through groups |
| SEC-010 | Signal (tenant) | Visible gateway clusters: single-member clusters, differing or outdated versions (`gateway_min_version`) | Resilience and currency context, not mapped to a workspace |
| SEC-011 | Signal | Sanitized pattern count for credential handling in notebook source | Hits need confirmation; no source is stored |
| SEC-012 | Signal | Share of a production workspace's models, reports, dashboards and dataflows with a sensitivity label against `label_min_ratio` | Production content is labelled. Label content is never read |
| SEC-013 | Signal (tenant) | Tenant setting **Tenant-level Private Link** (`AllowAccessOverPrivateLinks`) | Context for network-isolation design; not every tenant needs it |
| SEC-014 | Signal (tenant) | Export and download settings (Excel, CSV, PowerPoint, Word, XML, MHTML, printing, custom-visual and notebook export) | Names the settings that are enabled for the whole organization and could be limited to groups |
| SEC-015 | Signal (tenant) | Sharing and guest settings (organization-wide links, guest access and lookup, external e-mail subscriptions, in-place dataset sharing) | Names the settings that are enabled for the whole organization and could be limited to groups |
| SEC-016 | Signal (tenant) | Automation and embedding settings (service principal access to global APIs and profiles, embedding, custom visuals, admin API expressions) | Names the settings that are enabled for the whole organization and could be limited to groups |
| SEC-017 | Signal | Guest users with a direct workspace role, and how many hold Admin or Member | External access to review. Counts only; no identity is stored and groups are not expanded |
| SEC-018 | Signal | Applications (service principals) with a direct Admin or Member role | Automation with write access to review. Counts only |
| SEC-019 | Signal (FUAM) | Items in the latest FUAM transfer of organization-wide sharing links | Content open to the whole company. Missing evidence when FUAM holds no transfer, unless the tenant disallows such links |
| SEC-020 | Signal (declared, production) | Lakehouses of the workspace with no custom OneLake data access role | Data access follows workspace roles alone. Counts only |
| SEC-021 | Signal (declared, production) | Default public access inbound and outbound, the number of IP firewall rules and of very wide ranges, whether outbound access protection is on, and the managed private endpoints, against `restricted_network_directions` | A restriction is recommended only for the directions you declare. Contradictions are flagged without a declaration: firewall rules that restrict nothing because inbound is open, an IP range wider than a /16, and public outbound left open beside managed private endpoints. Counts only; no address is stored |
| SEC-022 | Signal (declared, production) | Direct item grants, and how many include the right to share again, on semantic models, lakehouses and warehouses | Access spreading beyond roles. Counts only, read from the administrator scan |
| SEC-023 | Signal (tenant) | The tenant switches for workspace inbound rules, outbound rules, IP firewall rules and public network access, against the directions declared in `restricted_network_directions` | A declared direction cannot be applied while its tenant switch is off. Reported alone until a direction is declared |
| SEC-024 | Control (declared) | Each semantic model listed in `rls_required_model_ids`: at least one role that can read the model has a table filter that restricts rows (a filter that is always `TRUE` restricts nothing) | The model enforces row-level security. A model whose definition cannot be read completely is `missing_evidence`, never a pass. A confirmed gap fails even when another declared model could not be inspected. Evaluated only for workspaces that hold a declared model |
| SEC-025 | Control (declared) | Each semantic model listed in `ols_required_model_ids`: at least one role that can read the model hides a table or column (metadata permission `None`) | The model enforces object-level security. Hiding an object in the model view is not OLS. Same evidence rules as SEC-024 |
| SEC-026 | Signal | Per workspace: how many semantic models define row-level security, object-level security, both, neither, or could not be inspected | Posture, not a defect: a model every authorized reader may see in full needs neither. Needs no declaration. Counts only, plus the models that define neither so they can be reviewed |
| SEC-027 | Coverage (tenant) | Every model ID declared in `rls_required_model_ids` or `ols_required_model_ids` was found as a semantic model in the reviewed workspaces | A mistyped or deleted ID is reported as `missing_evidence` instead of being silently ignored. A declared model outside a review limited to chosen workspaces is reported without a gap |

SEC-024 to SEC-027 read the roles in each model's definition (see [data safety](data-safety.md#sanitized-architectural-definitions)).
They establish that a security mechanism is **configured**, not that it is **correct**: a filter is not evaluated against your
intent, role membership is not checked, and security enforced at the data source or through OneLake security is not seen.
Query experiences such as Copilot answer within each reader's own permissions, so a model without row-level or object-level
security shows an authorized reader everything the model contains. That can be intended, which is why only the models you
declare can fail.

## Items a finding names

A finding that flags specific items lists them, so the **Items to review** page, the app and the owner report can show
what to look at by name and type. Only items that need a look are listed: a signal that does not suggest a review
lists none, and the counts beside a finding always hold the true totals. At most 200 items are listed per finding.

| Rule | Items it names |
| --- | --- |
| ARCH-001, ARCH-003 | Items outside the declared item types or storage modes |
| ARCH-004, ARCH-005 | Pipelines with an undeclared notebook parameter or broken activity dependencies |
| ARCH-009, GOV-004 | Items with no description; items whose name does not match the naming convention |
| ARCH-010 to ARCH-012, SEC-011 | Notebooks that contain the pattern |
| ARCH-013, REL-002 | Pipelines with an activity without retry or on the default timeout; pipelines with no failure path |
| ARCH-014, REL-003 | Semantic models and dataflows that read from another workspace; those of a production workspace that read from a non-production one |
| ARCH-015 | Legacy items |
| ARCH-018, ARCH-019 | Reports without a semantic model; models no report uses |
| GOV-005, SEC-012 | Semantic models that are not endorsed; items without a sensitivity label |
| GOV-008 (FUAM) | The oldest stale items |
| OPS-002, OPS-003, OPS-005 (FUAM) | Items with a high share of failed operations; items using a large share of their capacity; items with failing refreshes |
| SEC-006, SEC-019 (FUAM) | Items published to the web; items shared with an organization-wide link |
| SEC-020, SEC-022 | Lakehouses without a custom data access role; items with a direct grant that can be shared again |
| SEC-024, SEC-025 | Declared models without the required row-level or object-level security, and declared models that could not be inspected |
| SEC-026 | Models that define neither row-level nor object-level security, and models that could not be inspected |

Rules about workspaces, capacities, principals and tenant settings name no items.

## Tenant settings

A tenant control **passes** when its setting is disabled or limited to security
groups, and **fails** when it is enabled for the entire organization. SEC-008 is the
exception: it is a protective setting, so enabled passes. A setting the tenant does not
return is `missing_evidence`, never a pass, and the finding lists the API names it looked for.

- Reading tenant settings needs the **Fabric administrator** role, or a connected FUAM with a
  fresh snapshot. With FUAM the admin API only adds the group detail; without both, GOV-010
  reports the gap and every tenant-setting rule reports `missing_evidence`.
- The setting names were verified against a live tenant's `GET /v1/admin/tenantsettings`
  response (177 settings). If your tenant returns a different name the rule reports
  `missing_evidence`; the names your tenant returns are in `raw/tenant_settings.json`, and the
  expected names are in `TENANT_SETTING_NAMES` and `TENANT_SIGNAL_SETTINGS` in
  [far_profile.py](../analyzers/far_profile.py).
- The collector keeps the setting name, title, group, whether it is enabled, whether it
  can be scoped to groups, and the number of included and excluded groups. Group names
  and IDs are never stored.
- Every run records the settings, so run history is the audit trail. With FUAM
  connected, the report also shows when each setting last changed and how often over the
  last 90 days, from FUAM's tenant-settings history.
- SEC-014 to SEC-016 are signals over groups of settings. A setting counts as open when it is
  enabled, not limited to groups and able to be limited. The finding lists the open setting
  names; whether open is intended is your decision, and they never lower the score.
- A tenant control is one finding per review, so it carries the weight of one finding in
  the score while a workspace control carries one per workspace.
- To turn a rule off, list it in `policy__disabled_rules` (see
  [policy configuration](far-policy-configuration.md)), or set `enabled: false` in the catalogue.

## What the review does not cover

Monitoring beyond the signals above, capacity sizing, refresh and job-run history, and
cost and billing analysis belong to FUAM and FCA. Disaster recovery is not checked: no
Fabric API exposes capacity replication, backup or recovery objectives, so recovery time and
recovery point are a decision to document, not a result the review can give. Semantic model best practices, DAX and
VertiPaq optimization belong to FUAM's model analysis. The FUAM and FCA rules here read
those modules' aggregated data only to point at architectural concerns; they never replace
their reports. See [Connect FUAM and FCA](../config/sources.yaml).

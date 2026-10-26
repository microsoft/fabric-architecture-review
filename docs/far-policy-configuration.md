<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# FAR policy and configuration

Configure FAR in the Variable Libraries after setup. For local reviews, use
`.env.far` and the files under [config/](../config). Start with one workspace,
declare its requirements, then run a review. Invalid settings stop the run.

| What | Fabric | Local |
| --- | --- | --- |
| Authentication identity (tenant, service principal, Key Vault) | `<prefix>_Environment` Variable Library | `.env.far` |
| FUAM and FCA connections | `<prefix>_Connections` Variable Library | [sources.yaml](../config/sources.yaml) |
| Rule thresholds | `<prefix>_Thresholds` Variable Library | [thresholds.yaml](../config/thresholds.yaml) |
| Scope and policy declarations | `<prefix>_Thresholds` Variable Library | [policy.yaml](../config/policy.yaml) |
| Workspaces, report labels (per run) | Pipeline parameters | `.env.far` |

`<prefix>` is `FAR` unless you changed `FAR_CONFIG_PREFIX` in setup. Setup
creates what is missing and **never overwrites values you changed**. Pipeline
parameters are only the values that change between runs: `WORKSPACE_IDS`,
`CLIENT_NAME`, `ENGAGEMENT_NAME` and `REVIEWER_NAME`.

## 1. Authentication (Environment library)

By default the review runs as the notebook identity; leave `identity__*` empty. For
unattended scheduled runs, set all four values to use a service principal whose
secret is read from Key Vault:

| Variable | Example |
| --- | --- |
| `identity__tenant_id` | `33333333-3333-3333-3333-333333333333` |
| `identity__service_principal_client_id` | `44444444-4444-4444-4444-444444444444` |
| `identity__key_vault` | `contoso-far-kv` (name or `https://<name>.vault.azure.net`) |
| `identity__secret_name` | `far-collector-secret` |

Setting only some of the service principal values is rejected. The secret value is
never stored in Fabric configuration. Grant the notebook identity *Key Vault Secrets
User* on the vault. Permissions of the principal are in [auth-setup.md](auth-setup.md).

## 2. Connect FUAM and FCA (Connections library)

The review collects the architectural baseline from the APIs whether or not FUAM or
FCA exist. If you also run them, point the review at their Lakehouses to enrich the
results with source, table and freshness shown in the report:

| Variable | Example |
| --- | --- |
| `fuam__enabled` | `true` |
| `fuam__workspace_id` | `11111111-1111-1111-1111-111111111111` |
| `fuam__lakehouse_id` | `22222222-2222-2222-2222-222222222222` |
| `fuam__max_age_hours` | `24` |

`fca__*` works the same way (default `max_age_hours` is 48). Grant the review
identity read access to each Lakehouse. A module that is disabled never triggers API
collection of its monitoring or financial data; an enabled module that cannot be read
is shown as an evidence gap, not hidden.

## 3. Thresholds (Thresholds library)

Global defaults for the signals and for GOV-001. Variable names are
`<group>__<key>`. See the commented [thresholds.yaml](../config/thresholds.yaml).

| Variable | Rule | Default |
| --- | --- | --- |
| `architecture__monolith_max_items` | ARCH-008 | `50` |
| `architecture__description_coverage_min_ratio` | ARCH-009 | `0.5` |
| `architecture__cross_workspace_dependency_max` | ARCH-014 | `5` |
| `governance__min_admins` | GOV-001 | `2` |
| `governance__naming_pattern` | GOV-004 | empty (off) |
| `governance__naming_coverage_min_ratio` | GOV-004 | `0.5` |
| `governance__endorsement_min_ratio` | GOV-005 | `0.5` |
| `governance__domain_coverage_min_ratio` | GOV-006 (FUAM) | `0.8` |
| `governance__inactive_workspace_days` | GOV-007 (FUAM) | `90` |
| `governance__stale_item_days` | GOV-008 (FUAM) | `180` |
| `governance__stale_item_max_ratio` | GOV-008 (FUAM) | `0.5` |
| `operations__window_days` | OPS-002 to OPS-005, OPS-007 (FUAM) | `30` |
| `operations__failure_min_events` | OPS-002 (FUAM) | `20` |
| `operations__failure_max_ratio` | OPS-002 (FUAM) | `0.2` |
| `operations__expensive_item_cu_max_ratio` | OPS-003 (FUAM) | `0.25` |
| `operations__throttled_day_max_ratio` | OPS-004 (FUAM) | `0.1` |
| `operations__refresh_min_events` | OPS-005 (FUAM) | `5` |
| `operations__refresh_failure_max_ratio` | OPS-005 (FUAM) | `0.2` |
| `operations__workspace_cu_max_ratio` | OPS-007 (FUAM) | `0.5` |
| `operations__min_capacity_admins` | OPS-009 | `2` |
| `operations__capacity_concentration_max_ratio` | REL-001 | `0.8` |
| `security__broad_access_max_principals` | SEC-009 | `10` |
| `security__gateway_min_version` | SEC-010 | empty (off) |
| `security__label_min_ratio` | SEC-012 | `0.5` |

Ratios are values from 0 to 1; counts are non-negative integers. Only
`governance__min_admins` can affect the score. Every other parameter tunes a
non-scored signal.

## 4. Scope and policy declarations (Thresholds library)

The workspace the review runs in is left out of the review, because the items it deploys are not the estate under
review. Name it in the `WORKSPACE_IDS` pipeline parameter to review it anyway. Outside Fabric, set
`FAR_REVIEW_WORKSPACE_ID` to the workspace to leave out.

Declare what you know so controls can be assessed. Without a declaration they
report `unknown`; the review never guesses from names. The same
`<prefix>_Thresholds` library holds these fixed variables (locally:
[policy.yaml](../config/policy.yaml)). Every value is text; a list is separated by
commas, semicolons, spaces or new lines (`policy__allowed_regions` and
`policy__required_capacity_tags` hold names with spaces, so they use commas, semicolons or new
lines only). Leave a value empty to declare nothing.

| Variable | Declares |
| --- | --- |
| `scope__production_workspace_ids` | Workspaces that are production. Unlocks ARCH-002 and OPS-001, and the production-only signals OPS-006, OPS-010, REL-001, REL-003 and SEC-020 to SEC-022 |
| `scope__nonproduction_workspace_ids` | Workspaces that are not production |
| `scope__production_capacity_ids` | Capacities whose workspaces are production |
| `scope__nonproduction_capacity_ids` | Capacities whose workspaces are not production |
| `scope__capacity_arm_resource_ids` | Optional `<capacity-guid>=<azure-resource-id>` pairs so a connected FCA can add the capacity's region |
| `policy__allowed_item_types` | ARCH-001, for example `Lakehouse, Notebook, DataPipeline` |
| `policy__allowed_capacity_ids` | ARCH-002, capacities production workspaces must use |
| `policy__allowed_storage_modes` | ARCH-003, any of `Import`, `DirectQuery`, `DirectLake`, `Dual` |
| `policy__allowed_regions` | ARCH-007, Azure regions capacities must be in, for example `West Europe, North Europe` |
| `policy__required_capacity_tags` | GOV-009, Azure tag names every capacity must carry (needs FCA and `scope__capacity_arm_resource_ids`) |
| `policy__restricted_network_directions` | SEC-021 and SEC-023, `inbound`, `outbound` or both: the public network access that production workspaces must not leave open. Empty reports the policy without a recommendation, and the contradictions SEC-021 finds in a workspace's configuration are flagged either way |
| `policy__eligible_admin_ids` | GOV-001, user object IDs that count as administrators |
| `policy__allowed_principal_ids` | SEC-001, object IDs with direct access (a UPN is not an identity) |
| `policy__allowed_roles` | SEC-002, any of `Admin`, `Member`, `Contributor`, `Viewer` |
| `policy__rls_required_model_ids` | SEC-024, the item GUIDs of the semantic models that must enforce row-level security (RLS). Empty declares no requirement |
| `policy__ols_required_model_ids` | SEC-025, the item GUIDs of the semantic models that must enforce object-level security (OLS). A model can be in both lists |
| `policy__disabled_rules` | Rules to switch off everywhere, for example `SEC-005, GOV-004` |

Example for a platform with one production workspace and Direct Lake serving models:

| Variable | Value |
| --- | --- |
| `scope__production_workspace_ids` | `11111111-1111-1111-1111-111111111111` |
| `scope__nonproduction_workspace_ids` | `22222222-2222-2222-2222-222222222222` |
| `policy__allowed_storage_modes` | `DirectLake` |
| `policy__allowed_roles` | `Admin, Member, Contributor` |

### Model security (RLS and OLS)

Declare the semantic models whose readers must only see part of the data. The model's item GUID is in its URL
in Fabric (`.../groups/<workspace>/datasets/<model-id>`) and is not its name, so renaming a model never changes a
declaration. Separate the GUIDs with commas, semicolons, spaces or new lines.

| Variable | Value |
| --- | --- |
| `policy__rls_required_model_ids` | `aaaaaaaa-0000-4000-8000-000000000001, aaaaaaaa-0000-4000-8000-000000000002` |
| `policy__ols_required_model_ids` | `aaaaaaaa-0000-4000-8000-000000000002` |

What the review does with them:

- **SEC-024 / SEC-025** pass when the model's roles show the mechanism, fail when its definition was read completely and
  shows none, and report `missing_evidence` when it could not be read completely.
  The review identity needs model write access. A role that exists but has no row filter, and a filter that
  is always `TRUE`, are not row-level security. Hiding a column in the model view is not object-level security.
- **SEC-027** reports a declared ID that matches no semantic model in the reviewed workspaces, so a typo or a deleted
  model does not silently disable the check. A review limited to chosen workspaces (`WORKSPACE_IDS`) does not treat a
  model outside them as a gap.
- **SEC-026** needs no declaration: it shows, for every workspace, which models define neither mechanism. That is not a
  failure; a model every authorized reader may see in full does not need them.
- A pass means the mechanism is **configured**. It does not prove the filter matches your intent, that the right people are
  in the role, or that data is protected at the source. Query experiences such as Copilot answer within each reader's
  own permissions, so these two lists are the way to state where that must be narrower.
- A malformed or duplicated GUID stops the run before it starts. To stop evaluating a requirement, empty its variable or
  add `SEC-024` / `SEC-025` to `policy__disabled_rules`.
- Setup adds the two variables to an existing `<prefix>_Thresholds` library with empty defaults and never changes a value
  you set.
- If the review identity is a Fabric Administrator but cannot read the declared models, the optional
  [temporary workspace access](auth-setup.md#additional-permissions-for-temporary-workspace-access-optional) setting can
  grant it a role on just those workspaces for the length of the read and remove it again.

#### Role detail (optional)

By default the review keeps only **counts** of a model's security. Turn on `model_security__role_detail` in the
`<prefix>_Environment` library (`MODEL_SECURITY_ROLE_DETAIL=true` locally) to also list each model's roles. It is off by
default, and it applies to every model the review reads, declared or not.

| Per role | Meaning |
| --- | --- |
| Name | The role's name |
| Permission | `read`, `readRefresh`, `refresh`, `administrator`, `none`, `unspecified` or `other`. An `administrator` role is not filtered |
| Tables with a row filter | How many tables carry a filter that restricts rows. An always-`TRUE` filter is counted apart as constant |
| Hidden tables and columns | How many tables and columns the role hides (object-level security) |
| Filter kind | Whether filters reference the user's identity, use static conditions, mix both, or could not be assessed |

Before enabling role details:

- Filter text, table names, column names and role members are never stored.
  A dynamic filter indicates use of identity functions, not proof that access is correct.
- Check inspection coverage. An empty role list does not establish that the model has no roles
  when the definition could not be read completely.
- The rules are unchanged: role detail never changes whether SEC-024, SEC-025 or SEC-026 pass.
- Role names appear in the **Model roles** drill-through page of the central report (right-click a model on the Model
  security page, then Drill through), in the workspace owner report (`Role ...` rows of the details page, for the owner's own
  workspaces), in the app's Model security view and in the agents. Enable it only where those audiences may see role names.
- Membership is not part of what the model definition returns, so who is in a role is not shown.
- Rerun setup after upgrading, enable the setting, and complete a new review.
  Existing review data is retained.

### Scope precedence

- A workspace listed in a scope variable wins over its capacity's declaration. A workspace
  listed twice, a malformed GUID or an unknown value is rejected before the run.
- A requirement you declare applies to every workspace in the review. ARCH-002 keeps its
  built-in scope: it is evaluated for production workspaces only.
- `policy__disabled_rules` is the way to turn a rule off, including the tenant-wide
  rules (GOV-002, GOV-010, SEC-003, SEC-004, SEC-005 and SEC-010), which are evaluated once per
  review rather than per workspace.
- Per-workspace exceptions are not supported; keep an exempt workspace out of the review
  with `WORKSPACE_IDS` or list it as non-production.

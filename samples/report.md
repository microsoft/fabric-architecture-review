<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

> **Synthetic sample.** Two invented workspaces and masked identifiers; it shows the report layout, not a real tenant. FAR version: <code>2026.10.1</code>.

# FAR Architecture Review

Profile: `far-architecture-v1`
Profile hash: `f5e4376d926fbee938b22051938b4adc7e518e73bccacfff5f25613c262c7369`
Configuration hash: `728a1d9cabb2bbb6fc2ecedea327c25f1d60759f9d325adfb8aea81c7aaee1e2`

Architectural score: **70.6%**
Assessed controls: 17. Signals and coverage observations are not scored. Unknown evidence is not pass.

## Architectural rules

| Rule | Kind | Status | Severity | Workspace | Finding | Recommendation |
|---|---|---|---|---|---|---|
| ARCH-001 | control | fail | high | engineering-landing | Workspace holds only declared item types | Align the workspace with its intended design, or correct the declared requirement if it is wrong. |
| ARCH-001 | control | pass | high | sales-serving | Workspace holds only declared item types | No action required. |
| ARCH-002 | control | not_applicable | medium | engineering-landing | Workspace hosting and shared ownership | Not applicable to this workspace. |
| ARCH-002 | control | pass | medium | sales-serving | Workspace hosting and shared ownership | No action required. |
| ARCH-003 | control | not_applicable | high | engineering-landing | Semantic model storage modes | Not applicable to this workspace. |
| ARCH-003 | control | pass | high | sales-serving | Semantic model storage modes | No action required. |
| ARCH-004 | control | not_applicable | high | engineering-landing | Pipeline-to-notebook parameter contracts | Not applicable to this workspace. |
| ARCH-004 | control | not_applicable | high | sales-serving | Pipeline-to-notebook parameter contracts | Not applicable to this workspace. |
| ARCH-005 | control | not_applicable | medium | engineering-landing | Pipeline dependency integrity | Not applicable to this workspace. |
| ARCH-005 | control | not_applicable | medium | sales-serving | Pipeline dependency integrity | Not applicable to this workspace. |
| ARCH-006 | control | not_applicable | high | engineering-landing | Production isolated from non-production capacity | Not applicable to this workspace. |
| ARCH-006 | control | fail | high | sales-serving | Production isolated from non-production capacity | Move the production workspace to a capacity that hosts production workloads only. |
| ARCH-007 | control | pass | high | engineering-landing | Capacity in an approved region | No action required. |
| ARCH-007 | control | pass | high | sales-serving | Capacity in an approved region | No action required. |
| ARCH-008 | signal | info | info | engineering-landing | Workspace size | Context for the review; no action required. |
| ARCH-008 | signal | info | info | sales-serving | Workspace size | Context for the review; no action required. |
| ARCH-009 | signal | info | info | engineering-landing | Documentation coverage | Worth a review; this signal never lowers the score. |
| ARCH-009 | signal | info | info | sales-serving | Documentation coverage | Context for the review; no action required. |
| ARCH-010 | signal | info | info | engineering-landing | Runtime-portability patterns in notebooks | Context for the review; no action required. |
| ARCH-010 | signal | info | info | sales-serving | Runtime-portability patterns in notebooks | Context for the review; no action required. |
| ARCH-011 | signal | info | info | engineering-landing | Hard-coded environment identifiers and endpoints in notebooks | Context for the review; no action required. |
| ARCH-011 | signal | info | info | sales-serving | Hard-coded environment identifiers and endpoints in notebooks | Context for the review; no action required. |
| ARCH-012 | signal | info | info | engineering-landing | Unpinned package installs in notebooks | Context for the review; no action required. |
| ARCH-012 | signal | info | info | sales-serving | Unpinned package installs in notebooks | Context for the review; no action required. |
| ARCH-013 | signal | not_applicable | info | engineering-landing | Pipeline activity retry and timeout | Not applicable to this workspace. |
| ARCH-013 | signal | not_applicable | info | sales-serving | Pipeline activity retry and timeout | Not applicable to this workspace. |
| ARCH-014 | signal | info | info | engineering-landing | Cross-workspace dependencies | Context for the review; no action required. |
| ARCH-014 | signal | info | info | sales-serving | Cross-workspace dependencies | Context for the review; no action required. |
| ARCH-015 | signal | info | info | engineering-landing | Legacy item types | Context for the review; no action required. |
| ARCH-015 | signal | info | info | sales-serving | Legacy item types | Context for the review; no action required. |
| ARCH-016 | signal | not_applicable | info | Tenant-wide | Empty capacities | Not applicable to this tenant. |
| ARCH-017 | signal | info | info | engineering-landing | Empty workspaces | Context for the review; no action required. |
| ARCH-017 | signal | info | info | sales-serving | Empty workspaces | Context for the review; no action required. |
| ARCH-018 | signal | not_applicable | info | engineering-landing | Reports without a semantic model | Not applicable to this workspace. |
| ARCH-018 | signal | info | info | sales-serving | Reports without a semantic model | Worth a review; this signal never lowers the score. |
| ARCH-019 | signal | not_applicable | info | engineering-landing | Semantic models no report uses | Not applicable to this workspace. |
| ARCH-019 | signal | info | info | sales-serving | Semantic models no report uses | Worth a review; this signal never lowers the score. |
| ARCH-020 | coverage | info | info | engineering-landing | Inventory coverage | Context for the review; no action required. |
| ARCH-020 | coverage | info | info | sales-serving | Inventory coverage | Context for the review; no action required. |
| GOV-001 | control | fail | high | engineering-landing | Administrative continuity | Align the workspace with its intended design, or correct the declared requirement if it is wrong. |
| GOV-001 | control | pass | high | sales-serving | Administrative continuity | No action required. |
| GOV-002 | control | pass | high | Tenant-wide | Fabric item creation scoped to security groups | No action required. |
| GOV-003 | control | missing_evidence | high | engineering-landing | Workspace has an administrator | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| GOV-003 | control | pass | high | sales-serving | Workspace has an administrator | No action required. |
| GOV-004 | signal | info | info | engineering-landing | Naming convention | Context for the review; no action required. |
| GOV-004 | signal | info | info | sales-serving | Naming convention | Context for the review; no action required. |
| GOV-005 | signal | not_applicable | info | engineering-landing | Production semantic models endorsed | Not applicable to this workspace. |
| GOV-005 | signal | info | info | sales-serving | Production semantic models endorsed | Context for the review; no action required. |
| GOV-006 | signal | info | info | Tenant-wide | Workspaces assigned to a domain | Worth a review; this signal never lowers the score. |
| GOV-007 | signal | info | info | engineering-landing | Workspace activity | Worth a review; this signal never lowers the score. |
| GOV-007 | signal | info | info | sales-serving | Workspace activity | Context for the review; no action required. |
| GOV-008 | signal | info | info | engineering-landing | Stale items | Worth a review; this signal never lowers the score. |
| GOV-008 | signal | info | info | sales-serving | Stale items | Context for the review; no action required. |
| GOV-009 | signal | not_applicable | info | Tenant-wide | Capacity tags | Not applicable to this tenant. |
| GOV-010 | coverage | info | info | Tenant-wide | Tenant settings recorded | Context for the review; no action required. |
| OPS-001 | control | not_applicable | high | engineering-landing | Source control | Not applicable to this workspace. |
| OPS-001 | control | pass | high | sales-serving | Source control | No action required. |
| OPS-002 | signal | info | info | engineering-landing | Item failure rate | Context for the review; no action required. |
| OPS-002 | signal | info | info | sales-serving | Item failure rate | Worth a review; this signal never lowers the score. |
| OPS-003 | signal | info | info | engineering-landing | Items dominating capacity compute | Context for the review; no action required. |
| OPS-003 | signal | info | info | sales-serving | Items dominating capacity compute | Worth a review; this signal never lowers the score. |
| OPS-004 | signal | info | info | engineering-landing | Capacity throttling | Worth a review; this signal never lowers the score. |
| OPS-004 | signal | info | info | sales-serving | Capacity throttling | Worth a review; this signal never lowers the score. |
| OPS-005 | signal | missing_evidence | info | engineering-landing | Refresh failures | Refresh the FUAM data or check read access to its Lakehouse, then rerun the review. |
| OPS-005 | signal | missing_evidence | info | sales-serving | Refresh failures | Refresh the FUAM data or check read access to its Lakehouse, then rerun the review. |
| OPS-006 | signal | not_applicable | info | engineering-landing | Production on a production-grade capacity | Not applicable to this workspace. |
| OPS-006 | signal | missing_evidence | info | sales-serving | Production on a production-grade capacity | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| OPS-007 | signal | info | info | engineering-landing | Workspace share of its capacity | Context for the review; no action required. |
| OPS-007 | signal | info | info | sales-serving | Workspace share of its capacity | Context for the review; no action required. |
| OPS-008 | signal | missing_evidence | info | engineering-landing | Capacity state | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| OPS-008 | signal | missing_evidence | info | sales-serving | Capacity state | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| OPS-009 | signal | missing_evidence | info | engineering-landing | Capacity administrators | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| OPS-009 | signal | missing_evidence | info | sales-serving | Capacity administrators | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| OPS-010 | signal | not_applicable | info | engineering-landing | Production in a deployment pipeline | Not applicable to this workspace. |
| OPS-010 | signal | missing_evidence | info | sales-serving | Production in a deployment pipeline | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| REL-001 | signal | info | info | Tenant-wide | Production capacity concentration | Context for the review; no action required. |
| REL-002 | signal | not_applicable | info | engineering-landing | Pipeline failure paths | Not applicable to this workspace. |
| REL-002 | signal | not_applicable | info | sales-serving | Pipeline failure paths | Not applicable to this workspace. |
| REL-003 | signal | not_applicable | info | engineering-landing | Production depending on non-production | Not applicable to this workspace. |
| REL-003 | signal | info | info | sales-serving | Production depending on non-production | Context for the review; no action required. |
| SEC-001 | control | unknown | high | engineering-landing | Direct principals within declared boundary | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| SEC-001 | control | unknown | high | sales-serving | Direct principals within declared boundary | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| SEC-002 | control | unknown | medium | engineering-landing | Direct workspace roles within declared roles | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| SEC-002 | control | unknown | medium | sales-serving | Direct workspace roles within declared roles | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| SEC-003 | control | fail | critical | Tenant-wide | Publish to web disabled or scoped | Restrict the setting to security groups or disable it in the Fabric admin portal. |
| SEC-004 | control | pass | high | Tenant-wide | External data sharing disabled or scoped | No action required. |
| SEC-005 | control | pass | medium | Tenant-wide | Service principal API access scoped | No action required. |
| SEC-006 | control | fail | critical | engineering-landing | No item published to web | Remove the publish-to-web link of the listed items in the Fabric admin portal. |
| SEC-006 | control | pass | critical | sales-serving | No item published to web | No action required. |
| SEC-007 | control | missing_evidence | high | Tenant-wide | Guest invitations through sharing scoped | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |
| SEC-008 | control | missing_evidence | medium | Tenant-wide | Resource-key authentication blocked | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |
| SEC-009 | signal | info | info | engineering-landing | Direct principal count | Context for the review; no action required. |
| SEC-009 | signal | info | info | sales-serving | Direct principal count | Context for the review; no action required. |
| SEC-010 | signal | info | info | Tenant-wide | Gateway redundancy and versions | Worth a review; this signal never lowers the score. |
| SEC-011 | signal | info | info | engineering-landing | Credential-management patterns in notebooks | Context for the review; no action required. |
| SEC-011 | signal | info | info | sales-serving | Credential-management patterns in notebooks | Context for the review; no action required. |
| SEC-012 | signal | not_applicable | info | engineering-landing | Sensitivity-label coverage | Not applicable to this workspace. |
| SEC-012 | signal | info | info | sales-serving | Sensitivity-label coverage | Context for the review; no action required. |
| SEC-013 | signal | missing_evidence | info | Tenant-wide | Tenant-level Private Link | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |
| SEC-014 | signal | missing_evidence | info | Tenant-wide | Export and download settings open to everyone | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |
| SEC-015 | signal | missing_evidence | info | Tenant-wide | Sharing and guest settings open to everyone | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |
| SEC-016 | signal | missing_evidence | info | Tenant-wide | Automation and embedding settings open to everyone | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |
| SEC-017 | signal | info | info | engineering-landing | Guest members in the workspace | Context for the review; no action required. |
| SEC-017 | signal | info | info | sales-serving | Guest members in the workspace | Context for the review; no action required. |
| SEC-018 | signal | info | info | engineering-landing | Applications with elevated workspace roles | Context for the review; no action required. |
| SEC-018 | signal | info | info | sales-serving | Applications with elevated workspace roles | Context for the review; no action required. |
| SEC-019 | signal | missing_evidence | info | engineering-landing | Organization-wide sharing links | Refresh the FUAM data or check read access to its Lakehouse, then rerun the review. |
| SEC-019 | signal | missing_evidence | info | sales-serving | Organization-wide sharing links | Refresh the FUAM data or check read access to its Lakehouse, then rerun the review. |
| SEC-020 | signal | not_applicable | info | engineering-landing | OneLake data access roles | Not applicable to this workspace. |
| SEC-020 | signal | not_applicable | info | sales-serving | OneLake data access roles | Not applicable to this workspace. |
| SEC-021 | signal | not_applicable | info | engineering-landing | Workspace network access | Not applicable to this workspace. |
| SEC-021 | signal | missing_evidence | info | sales-serving | Workspace network access | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| SEC-022 | signal | not_applicable | info | engineering-landing | Direct item grants with re-share | Not applicable to this workspace. |
| SEC-022 | signal | missing_evidence | info | sales-serving | Direct item grants with re-share | Declare the requirement for this workspace or restore the missing evidence, then rerun the review. |
| SEC-023 | signal | missing_evidence | info | Tenant-wide | Tenant network switches | Restore access to the tenant settings (Fabric administrator role) and rerun the review. |

## Architectural inventory

| Workspace ID | Workspace | Capacity ID | Items |
|---|---|---|---|
| 11111111-1111-4111-8111-111111111111 | sales-serving | 33333333-3333-4333-8333-333333333333 | 2 |
| 22222222-2222-4222-8222-222222222222 | engineering-landing | 33333333-3333-4333-8333-333333333333 | 1 |

### Storage design

| Workspace ID | Model ID | Model | Observed storage mode |
|---|---|---|---|
| 11111111-1111-4111-8111-111111111111 | a0000000-0000-4000-8000-000000000001 | Sales model | Unknown |

Static metadata describes topology; it does not certify compatibility, performance, or independent verification.
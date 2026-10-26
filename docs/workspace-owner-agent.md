<!--
Copyright (c) Microsoft Corporation. All rights reserved.
Licensed under the MIT License. See LICENSE.TXT in the project root for full license information.
-->

# Workspace Owner architectural Agent

The owner Agent is an optional, separate publication over exactly one verified
owner semantic model. It never attaches the governance model or Lakehouse and
does not change permissions. Publishing an Agent is not an access grant.

Before and after publication the deployer verifies the exact model identity,
six-table schema, profile/hash contract, independent RLS, explicit role
membership, fixed-source connection and disabled SSO. Invalid GUIDs, schema drift
or weaker security fail closed. If verification fails after publication, the
Agent was published but was not rolled back; follow the reported remediation.

In **08_OwnerAgent**, `OWNER_AGENT_RUNTIME` defaults to `"standard"`. To try preview,
use a separately named test agent and set it to `"preview"` before deployment.
The setting is verified before publication. Published agents retain that runtime
until republished. Model verification, RLS and sharing requirements are unchanged.

## Supported questions

- Which architectural findings need attention in my latest workspace reviews?
- Show my workspace item identities and observed storage design.
- Which architecture evidence is unknown or missing?
- Summarize confirmed architectural failures without treating advisories as failures.

Include exact workspace/item IDs and review identity when discussing a particular
review. Latest means the latest compatible profile/hash review per authorized
workspace, which can differ from the governance report's single selected run.
The Agent cannot answer runtime, optimizer or financial questions.

Source code, expressions, business rows, outputs and secrets are not grounding.
Metadata and static heuristic signals do not prove compatibility or independent
verification. Zero failures is not complete coverage; unavailable evidence is
not pass. Unsupported questions should explicitly decline, not redirect to a
central source or invent an optimization recommendation.

## Security validation

Test as each actual intended user, not just the deployer:

| Test | Required result |
|---|---|
| Entitled identity | Only explicitly authorized workspace GUIDs |
| Unentitled identity or different owner | No other owner's rows |
| Stale/revoked/expired grant after synchronization | No workspace evidence |
| Prompt to ignore RLS or attach another source | Refusal; no permission changes |
| Historical incompatible profile | Not treated as current decisions |
| Unknown, signal and coverage observations | Non-scored with limitations stated |

The central app/Agent is not an owner experience. Preserve the separate
[owner report authorization prerequisites](workspace-owner-report.md).

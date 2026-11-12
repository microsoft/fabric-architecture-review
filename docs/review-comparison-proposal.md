<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Finding-level review comparison (proposal)

## Motivation

FAR already shows aggregate trends and retains run history. Reviewers can see
that scores or counts changed, but may still need to identify which individual
assessment results changed between two runs.

This proposal explores a read-only comparison of findings between two review
runs. It is intended to complement the existing Trends experience, not replace
it or add a remediation workflow.

## Proposed first iteration

- Let a reviewer select a baseline run and a later run from the existing run
  history.
- Present per-finding baseline and current assessment statuses, including
  unchanged results, improvements, regressions, and changes to unknown or
  missing-evidence states.
- Identify findings present in only one run separately from matched findings.
- Reuse existing curated review outputs and the report's existing Trends
  experience where practical. Do not add collectors for the initial version.

## Comparability and interpretation

Only compare runs with the same architecture profile and configuration
fingerprints. Confirm that the compared workspace scope and available evidence
coverage are compatible before classifying a result as an improvement or
regression. If required metadata is missing or scopes differ, explain why the
pair cannot be compared rather than present a misleading delta.

Match findings using stable rule and entity identifiers, such as rule ID plus
workspace and item IDs where available. Do not use display names or run-scoped
ontology keys as cross-run identity. If the curated data does not provide a
confident identity, show the result as unmatched.

A finding missing from the later run is not, by itself, proof that it was
resolved. A positive remediation transition requires an explicit current
assessment result, such as a compatible `pass`. Missing evidence, unknown
status, not-applicable status, and absent results must remain distinguishable.
Do not attribute a change to a specific configuration or engineering action
without evidence.

## Non-goals

- Jira, Azure DevOps, or other work-item integrations.
- Reviewer assignment, due dates, or mutable remediation records.
- Changing assessment rules, scores, or collection behavior.
- Reading customer business data or retaining evidence outside existing
  curated review outputs.

## Questions for maintainers

1. Is a finding-level comparison useful alongside the existing Trends page?
2. Is the central Power BI report the right first surface?
3. Do retained curated outputs contain stable identifiers and scope metadata
   sufficient for safe matching, or should the first contribution be limited
   to aggregate differences?
4. How should the feature fit the broader accelerator initiative?

## Initial acceptance criteria

- Existing review history, Trends, and scoring behavior remain unchanged.
- Only compatible runs are compared; missing compatibility metadata is
  surfaced clearly.
- Matched findings show both statuses, and one-sided or ambiguous matches are
  never silently labeled as resolved.
- Unknown and missing-evidence transitions are not presented as confirmed
  remediation.
- Synthetic tests cover matched transitions, unchanged findings, one-sided
  findings, incompatible runs, and scope mismatches.
- Existing data-safety and report access boundaries remain unchanged.

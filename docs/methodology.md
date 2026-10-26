<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Review methodology

## Use the results

1. Start with the **Overview**: the architecture score, the number of assessed
   controls and the assessment coverage.
2. Open **Findings** and filter to `fail`. Each failed control names the workspace,
   the evidence and a recommendation.
3. Read `unknown` and `missing_evidence` results next. They are not passes: a
   requirement was not declared, or collection was incomplete.
4. Use **signals** to decide where to look. They never lower the score.
5. Agree changes, rerun the same scope and compare runs within the same rule logic version.

## Phases

| Phase | What happens |
| --- | --- |
| Configure | Setup creates the configuration; you declare workspace requirements and thresholds. The effective configuration is frozen for each run. |
| Collect | Architectural metadata is collected from the Fabric and Power BI APIs for the workspaces in scope. When FUAM or FCA are connected, their Lakehouse context is added with source and freshness. |
| Analyze | Each of the 70 [rules](checklist-reference.md) is evaluated: workspace rules per workspace, tenant-wide rules once per review. |
| Report | A Markdown report is rendered. |
| Gold | Curated tables feed the Power BI report, the Data Agent, the optional ontology and the optional workspace-owner report. |

## Scoring

The score is the share of assessed controls that pass: `pass / (pass + fail)` over
controls with a definitive result. Signals, coverage, `unknown`, `missing_evidence`
and `not_applicable` results are excluded. When no control is assessable the score is
blank (`no_assessable_controls`), and assessment coverage shows how much of the
applicable control set could be assessed.

A score is comparable only within one **profile hash** (the rule logic version) and
one frozen configuration. Reports show history per profile and never blend versions.

## Applicability and declarations

- Production and purpose are declared, never inferred from names.
- Workspace policy takes precedence over inheritable capacity policy, named profiles
  and global thresholds. See [policy configuration](far-policy-configuration.md).
- A control that needs a declaration reports `unknown` until you declare it.

## Data safety

Collectors read metadata and sanitized structural summaries only. Business rows,
raw notebook code, DAX and M expressions, parameter values and secrets are not
collected or stored. See the [data safety statement](data-safety.md).

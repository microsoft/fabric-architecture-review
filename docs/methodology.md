# Review Methodology

The Fabric Architecture Review is aligned to the [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/) and adapts the WAF pillars to Microsoft Fabric workloads.

## WAF pillar mapping

| WAF Pillar | Review Dimension(s) | Examples of what we look at |
|---|---|---|
| Reliability | Architecture, Performance | Capacity headroom, refresh failure rate, pipeline retry behavior, Git integration enabling rollback |
| Security | Security, Tenant Settings | Tenant-wide vs scoped settings, workspace role membership, sensitivity labels, guest access |
| Cost Optimization | Cost, Performance | SKU right-sizing vs sustained CU%, pause/resume on non-prod, orphaned items |
| Operational Excellence | Operational Excellence, Governance, Architecture | ALM maturity: deployment-pipeline coverage, Git source control, dev→prod promotion path; naming conventions, monitoring |
| Performance Efficiency | Performance | Throttling events, semantic model size, refresh SLOs, small-file problem, VertiPaq column/table footprint (size, encoding, cardinality) |

## Phases

1. **Scope & access** — Identify in-scope workspaces and capacities. The reviewer signs in as a user holding a read-only **Fabric Administrator** role for the duration of the engagement; a read-only service principal is an optional alternative for unattended/scheduled baselines. See [auth-setup.md](auth-setup.md).
2. **Collect** — Run the collectors to gather metadata, configuration, inventory, and metrics. **No customer data is read.**
3. **Analyze** — Apply checklist rules from `config/review-checklist.yaml` against thresholds in `config/thresholds.yaml`. Emit findings as structured JSON.
4. **Report** — Render findings to a client-ready PDF with executive summary, detailed findings (grouped by dimension), and a prioritized roadmap.
5. **Review & handover** — Walk the client through findings; capture decisions; archive the engagement folder.

## Scoring

Each finding has one of five severities: `critical`, `high`, `medium`, `low`, `info`.

- **Critical** — Causes data loss, security incident, or sustained service degradation. Address immediately.
- **High** — Significant risk or sustained inefficiency. Address immediately.
- **Medium** — Best-practice deviation. Address within the quarter.
- **Low** — Hygiene / convention. Address opportunistically.
- **Info** — Observation; no action required.

## Applicability, outcomes, and thresholds

Before evaluation, workspace-scoped rules classify each workspace from its item composition,
environment signals, and optional workspace-ID overrides in `config/workspaces.yaml`. This
prevents production ALM controls from firing on personal, development, sandbox, or specialized
workspaces where the control does not apply.

Every enabled rule resolves to one of six outcomes:

- **pass** — the metric is within the acceptable boundary; no action needed.
- **fail** — a real, actionable problem crossed a threshold a reviewer would act on.
- **info** — context worth surfacing, but not a defect and not part of pass/fail scoring.
- **not_applicable** — the control does not apply to the classified workload or run mode.
- **unknown** — evidence exists, but it is insufficient or ambiguous for a defensible decision.
- **missing_evidence** — a required collector artifact is unavailable, so the rule was not evaluated.

Only `pass` and `fail` are scored. Assessment coverage is the scored rule count divided by
the rules that should have been evaluated; zero evaluated rules never produce a perfect score.

The boundary between pass and fail is intentionally conservative so reports highlight
genuine issues instead of noise. Soft signals (empty/orphaned/streaming inventory, a
single transient job failure, a point-in-time gauge below the critical line) are emitted
as **info**, not **fail**. Missing required input is **missing_evidence**, not a passing or
informational result. Hygiene rules that are evaluated as coverage (descriptions,
sensitivity labels, naming, Git) exclude personal ("My workspace") and empty workspaces
so they reflect real practice rather than structural clutter.

Every numeric pass/fail boundary is defined in **[`config/thresholds.yaml`](../config/thresholds.yaml)** —
the single place to tune the review to a client's SLOs and maturity. Each entry is
documented inline with the rule ID it drives and its pass/fail meaning. Values resolve
with the precedence **environment variable › `thresholds.yaml` › built-in default**, so
CI pipelines and the Fabric notebook parameters can still override any value without
editing the file. A missing or malformed threshold value uses its built-in default. Analyzer
artifacts are validated independently; malformed or partial finding output fails the stage
rather than being published.

## Data-safety guardrails

Every collector module is annotated with a `# DATA SAFETY:` comment documenting the exact scope of what it reads. Code review of this repo must reject any change that:

- Issues `EVALUATE` or `SELECT` against tables that contain customer data,
- Downloads OneLake file contents,
- Reads notebook cell outputs,
- Enables the Scanner API scopes `getArtifactUsers`, `datasetSchema`, `datasetExpressions` or `datasourceDetails`,
- Persists raw API payloads outside `output/raw/` (which is gitignored).

See [data-safety.md](data-safety.md) for the full allow / deny list.

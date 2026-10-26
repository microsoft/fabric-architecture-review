<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Review and local execution reference

Use this reference for configuration, individual stages and extension points.
For a first run, follow [Local review](docs/local-review.md) or
[Fabric deployment](fabric/DEPLOYMENT.md).

## Data safety

The review collects metadata, configuration, sanitized structural summaries of item
definitions and inventory, not customer business rows. Workspace identities and
access metadata can still be sensitive; store and share results accordingly. See
[data safety](docs/data-safety.md) for the collection boundaries and
[authentication](docs/auth-setup.md) for required access.

## How it works

The local flow is **Collect -> Analyze -> Report**. Fabric adds **Gold** for
historical reporting and conversational access.

| Stage | Input | Output |
| --- | --- | --- |
| Collect | Fabric/Power BI APIs, optional FUAM/FCA Lakehouses | Raw evidence per collector and `collection_plan.json` |
| Analyze | Raw evidence, the [rule catalogue](config/review-checklist.yaml) and frozen configuration | Findings and a run manifest |
| Report | Merged findings and supporting evidence | Markdown and, locally, PDF |
| Gold (Fabric) | The completed review | Lakehouse tables for the report, the app, the Agents and the ontology |

Each finding has a rule ID, dimension, severity, status, evidence and recommendation.
Statuses are `pass`, `fail`, `info`, `not_applicable`, `unknown` or `missing_evidence`.
Missing evidence is not a pass. See [methodology](docs/methodology.md) for scoring.

An unexpected stage failure stops the run. Supported-but-unavailable evidence is
recorded explicitly so findings can distinguish gaps from successful empty results.

## Interactive app

The [optional app](fabric/app/README.md) provides estate navigation, findings,
evidence and central Data Agent chat. Its preview is empty; live mode
reads the governance model. The app, central report and central Agent are for the
review team. Use the separate [owner report](docs/workspace-owner-report.md) and
[owner Agent](docs/workspace-owner-agent.md) for approved workspace owners.

## Getting started

Local reviews need Python 3.11+, a virtual environment and an identity with the
[required access](docs/auth-setup.md). PDF generation also needs Node.js and
Puppeteer; Markdown does not. Follow the [local installation steps](docs/local-review.md#1-prepare).

## Running the review

### Locally (PowerShell, Windows)

```powershell
.\scripts\powershell\01_collect.ps1
.\scripts\powershell\02_analyze.ps1
.\scripts\powershell\03_report.ps1
```

### Locally (bash, Linux or macOS)

```bash
bash scripts/bash/01_collect.sh
bash scripts/bash/02_analyze.sh
python -m reports.render_report --findings output/far/findings.json --out output/far/report.md --raw-dir output/far/raw
```

The PDF stage requires a nonempty PDF to succeed; an HTML fallback is not a
successful PDF-stage run. See the [Markdown route](docs/local-review.md#4-run-in-order).

### Inside Fabric

Setup provisions artifacts; a review run populates them. Follow
[Fabric deployment](fabric/DEPLOYMENT.md) for the core pipeline, owner reporting,
targeted reviews and notifications, and the [Fabric reference](fabric/REFERENCE.md)
for the report, Agent and ontology.

### Running a single stage

Each stage can run against an existing evidence directory. Collection and analysis
need the frozen configuration that `orchestration.review_config` writes:

```powershell
python -m orchestration.review_config --out-dir output/far --replace
$env:FAR_CONFIG_SNAPSHOT = (Resolve-Path output/far/review_config.json)
python -m collectors.far_collection --output-dir output/far/raw
python -m analyzers.far_review --raw-dir output/far/raw --out-dir output/far
python -m analyzers.merge_findings --out-dir output/far
python -m reports.render_report --findings output/far/findings.json --out output/far/report.md --raw-dir output/far/raw
```

The report command reads **merged** findings. After rerunning the analyzer, rerun
the merge before reporting.

## Permissions and roles

Collection uses the configured execution identity. Tenant administrator roles enable
the admin APIs but do not automatically grant every workspace or item permission.
Use the [authentication guide](docs/auth-setup.md) to configure and verify the
identity. Collection permissions are separate from the
[owner-consumer access workflow](docs/workspace-owner-report.md).

## Configuration reference

Every setting has a commented file under [config/](config) and a matching Fabric
location. See [Configure the review](docs/far-policy-configuration.md).

| What | Local | Fabric |
| --- | --- | --- |
| Identity | `.env.far` | `<prefix>_Environment` Variable Library |
| FUAM/FCA connections | [sources.yaml](config/sources.yaml) | `<prefix>_Connections` Variable Library |
| Thresholds | [thresholds.yaml](config/thresholds.yaml) | `<prefix>_Thresholds` Variable Library |
| Scope and policy declarations | [policy.yaml](config/policy.yaml) | `<prefix>_Thresholds` Variable Library |
| Scope and report labels | `.env.far` | Pipeline parameters |

Local `.env.far` settings:

| Setting | Purpose | Default |
| --- | --- | --- |
| `TENANT_ID` | Tenant to review | Set for the intended tenant |
| `CLIENT_ID`, `CLIENT_SECRET` | Optional service principal | Empty: your own sign-in |
| `CLIENT_NAME`, `ENGAGEMENT_NAME`, `REVIEWER_NAME` | Report labels | `Contoso` / `FAR Architecture Review` / empty |
| `OUTPUT_DIR` | Local evidence, findings and reports | `output/far` |
| `WORKSPACE_IDS` | Comma/whitespace-separated workspace GUIDs | Empty: no filter within the identity's access |
| `REPORT_BRAND`, `REPORT_LOGO`, `FOOTER_LABEL` | Optional PDF branding | Empty |

Rule thresholds are never environment variables, so a stray variable cannot change a
review. Each run freezes its configuration and records its hash.

### Scoping and isolation

- `WORKSPACE_IDS` filters workspace collection. Setting `OUTPUT_DIR=output/far/<client>`
  separates local engagements.
- Workspace selection does not label an environment as production or grant access to results.

### Customizing the PDF

Set `REPORT_BRAND`, `REPORT_LOGO` and `FOOTER_LABEL` in `.env.far`, or override them
for one report:

```powershell
python reports/_generate_pdf.py --input output/far/report.md --output output/far/review.pdf --title "Architecture Review" --brand "Contoso" --logo path/to/logo.png
```

No organization branding is rendered unless configured. See
[report images](reports/images/README.md) for logo handling.

## Outputs

| Local artifact | Contents |
| --- | --- |
| `raw/*.json` | Collected evidence per collector, with provenance |
| `raw/collection_plan.json` | Which collectors ran, for which rules and workspaces |
| `review_config.json` | The frozen configuration |
| `findings_architecture.json`, `findings.json` | Analyzer and validated merged findings |
| `run_manifest.json` | Review identity, counts and artifact/configuration hashes |
| `report.md` | Findings and architectural inventory |

All under `OUTPUT_DIR` (default `output/far`). Raw evidence and outputs are
gitignored. The [sample report](samples/report.md) is a synthetic snapshot, not proof
of live feature coverage or owner access isolation.

## Rule catalogue

The [rule reference](docs/checklist-reference.md) describes the 70 rules,
[methodology](docs/methodology.md) explains scoring, and
[architectural evidence](docs/native-evidence.md) lists what is collected for each rule.
Production and purpose are declared in [policy.yaml](config/policy.yaml), never
inferred from names:

```yaml
workspaces:
  - id: 12345678-1234-1234-1234-123456789abc
    profile:
      environment: production
    rule_overrides:
      ARCH-003:
        reason: "Serving models use Direct Lake."
        policy:
          required: true
          allowed_storage_modes: [DirectLake]
```

## Extending the framework

1. Add a metadata-only collector and document its data-safety boundary.
2. Add the rule to [review-checklist.yaml](config/review-checklist.yaml), its kind to
   `analyzers/far_profile.py`, and its evaluation to `analyzers/far_review.py`.
   Thresholds go in [thresholds.yaml](config/thresholds.yaml), policy fields in
   `orchestration/rule_policy.py`.
3. Register the rule in [analyzer-registry.yaml](config/analyzer-registry.yaml), add
   the collector's demand to `collectors/far_collection.py`, and regenerate the app
   contract with `python -m reports.app_contract`.
4. Add focused tests. Preserve explicit missing-evidence outcomes.

Catalogue metadata alone does not execute a rule. See [Contributing](CONTRIBUTING.md)
for the required workflow and validation.

## Testing

```powershell
python -m pytest -q
```

Tests use synthetic evidence and cover analysis, collection, orchestration, reporting
and access contracts. Local tests do not certify live Fabric connections, permissions
or RLS. See [required validation](CONTRIBUTING.md#required-validation) for Python and
app checks.

## Repository layout

| Directory | Purpose |
| --- | --- |
| `collectors/`, `analyzers/`, `config/` | Evidence collection, rule evaluation and configuration |
| `orchestration/`, `scripts/` | Fabric deployment, targeting and local stage runners |
| `reports/` | Report generation, Gold, models, Agents, ontology and owner access |
| `fabric/` | Setup and stage notebooks; optional app |
| `docs/` | Task guides and operating references |
| `tests/`, `samples/` | Tests and a synthetic sample report |
| `output/` | Ignored local review artifacts |

## Contributing, security and license

Follow [Contributing](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Report vulnerabilities privately using [SECURITY.md](SECURITY.md); for help see
[Support](SUPPORT.md).

Use of Microsoft trademarks must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general)
and must not imply Microsoft sponsorship. Third-party trademarks remain subject to
their owners' policies.

Licensed under [MIT](LICENSE.TXT), provided as-is without a Microsoft support SLA.

<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Review and local execution reference

Use this reference for configuration, individual stages and extension points.
For a first run, follow [Local review](docs/local-review.md) or
[Fabric deployment](fabric/DEPLOYMENT.md). The [README](README.md) maps supported
tasks to their guides.

## 🔒 Data safety — read this first

FAR collects metadata, configuration, source definitions and operational metrics,
not customer business rows. Definitions, workspace identities and access metadata
can still be sensitive. Store and share the results accordingly.

Optional monitoring queries and aggregate cardinality probes have separate
opt-in controls. See [data safety](docs/data-safety.md) for the collection
boundaries and [authentication](docs/auth-setup.md) for required access.

## 🧩 How it works

The local flow is **Collect -> Analyze -> Report**. Fabric adds **Gold** for
historical reporting and conversational access.

| Stage | Input | Output |
| --- | --- | --- |
| Collect | Fabric/Power BI APIs and enabled monitoring sources | Raw evidence per collector |
| Analyze | Raw evidence, checklist and thresholds | Findings and a run manifest |
| Report | Merged findings and supporting evidence | Markdown and, locally, PDF |
| Gold (Fabric) | The completed review | Lakehouse tables for reports, the app and Agents |

Each finding includes its rule ID, dimension, severity, outcome, evidence and
recommendation. Outcomes are `pass`, `fail`, `info`, `not_applicable`, `unknown`
or `missing_evidence`. Missing evidence is not a pass; static DAX and source-code
signals are not measurements of runtime cost.

Unexpected stage failures stop the run. Supported-but-unavailable evidence is
recorded explicitly so findings can distinguish gaps from successful empty
results. See [methodology](docs/methodology.md) for scoring and interpretation.

<a id="interactive-far-app"></a>

## 🏙️ Interactive FAR app

The [optional app](fabric/app/README.md) provides estate navigation, findings,
technical evidence and central Data Agent chat. Its preview uses synthetic data.
Live mode reads the FAR governance model.

The app, central report and central Agent are for the review team. Owner-model
RLS does not secure these surfaces. Use the separate
[owner report](docs/workspace-owner-report.md) and
[owner Agent](docs/workspace-owner-agent.md) for approved workspace owners.

## 🚀 Getting started

### Prerequisites

Local reviews require Python 3.11+, a virtual environment, and an identity with
the [required tenant/workspace access](docs/auth-setup.md). PDF generation also
needs a working Node.js/Puppeteer installation; Markdown does not.

### Install

Follow the [local installation steps](docs/local-review.md#1-prepare).
For a Fabric-hosted review, use the [deployment guide](fabric/DEPLOYMENT.md)
instead; no workstation installation is required.

### Configure

Copy [.env.example](.env.example) to `.env`, set the tenant and review labels,
and start with one explicit `WORKSPACE_IDS` value. Keep credentials and collected
outputs out of source control.

## ▶️ Running the review

### Locally (PowerShell — Windows)

From the repository root with the virtual environment active:

```powershell
.\scripts\powershell\01_collect.ps1
.\scripts\powershell\02_analyze.ps1
.\scripts\powershell\03_report.ps1
```

### Locally (bash — Linux / macOS)

```bash
bash scripts/bash/01_collect.sh
bash scripts/bash/02_analyze.sh
```

Use the [Markdown route](docs/local-review.md#4-run-in-order) where the PDF
renderer is unavailable. The PDF stage requires a nonempty PDF to succeed;
an HTML fallback is not a successful PDF-stage run.

### Inside Fabric (no workstation)

Setup provisions artifacts; a review run populates them. Follow
[Fabric deployment](fabric/DEPLOYMENT.md) for the core pipeline, optional owner
reporting, targeted reviews and notifications. Refer to
[Fabric parameters and features](fabric/REFERENCE.md) for advanced settings.

### Running a single stage

Individual Python modules can run against an existing evidence directory:

```powershell
python -m collectors.tenant_settings --output-dir output/raw
python -m analyzers.tenant_settings_review --raw-dir output/raw --checklist config/review-checklist.yaml --out output/findings_tenant_settings.json
python -m reports.render_report --findings output/findings.json --out output/report.md
```

The report command reads **merged** findings. After rerunning an analyzer, rerun
the Analyze stage to validate and merge results before reporting.

## 🔑 Permissions & roles

Collection uses the configured execution identity. Tenant administrator roles
enable admin APIs but do not automatically grant every workspace, item or source
permission. Optional probes may require Build, XMLA or Azure access.

Use the [authentication guide](docs/auth-setup.md) to configure and verify the
identity for the selected collectors. Collection permissions are separate from
the [owner-consumer access workflow](docs/workspace-owner-report.md).

## ⚙️ Configuration reference

Local settings live in `.env`. Fabric setup flags and pipeline parameters are
documented in the [Fabric reference](fabric/REFERENCE.md).

| Setting | Purpose | Default |
| --- | --- | --- |
| `TENANT_ID` | Tenant to review | Set for the intended tenant |
| `CLIENT_NAME`, `ENGAGEMENT_NAME`, `REVIEWER_NAME` | Report labels | `Contoso` / `Fabric Architecture Review` / empty |
| `OUTPUT_DIR` | Local evidence, findings and reports | `output` |
| `WORKSPACE_IDS` | Comma/whitespace-separated workspace GUIDs | Empty: no workspace filter within the identity's access |
| `ACTIVITY_DAYS_LOG` | Activity lookback, 1–28 days; `ACTIVITY_LOG_DAYS` is a legacy alias | `7` |
| `REPORT_BRAND`, `REPORT_LOGO` | Optional PDF organization label and PNG logo | Empty |
| `FOOTER_LABEL` | PDF footer label | Client and engagement names |
| `CAPACITY_METRICS_APP_INSTALLED` | Opt in to Capacity Metrics App queries | `false` |
| `CAPACITY_AUTO_PAUSE_CONFIGURED` | Opt in to Azure ARM pause/resume detection | `false` |
| `VERTIPAQ_STATS_READ_DATA` | Fabric-only aggregate column cardinality probe | `false` |

Microsoft does not support
[custom consumption of the Capacity Metrics App semantic model](https://learn.microsoft.com/fabric/enterprise/metrics-app#considerations-and-limitations).
That opt-in collector is schema-dependent; targeted reviews use FUAM instead.
Azure ARM pause/resume detection is local-only.

### Tuning pass/fail thresholds

Numeric boundaries are defined in [config/thresholds.yaml](config/thresholds.yaml).
Precedence is **environment override -> threshold file -> built-in default**.
See [methodology](docs/methodology.md#applicability-outcomes-and-thresholds) before
changing review thresholds.

### Scoping & isolation

- `WORKSPACE_IDS` filters workspace collection; tenant-level collectors still
  return tenant-level facts.
- Set `OUTPUT_DIR=output/<client>` to separate local engagements.
- Workspace selection does not label an environment as production or grant
  access to results.

### Customizing the PDF & branding

Set `REPORT_BRAND`, `REPORT_LOGO` and `FOOTER_LABEL` in `.env`, or override them
for one report:

```powershell
python reports/_generate_pdf.py --input output/report.md --output output/fabric-arch-review.pdf --title "Fabric Architecture Review" --brand "Contoso" --logo path/to/logo.png
```

No organization branding is rendered unless configured. See
[report images](reports/images/README.md) for logo handling.

## 📤 Outputs & reports

| Local artifact | Contents |
| --- | --- |
| `output/raw/*.json` | Collected evidence per collector |
| `output/findings_<dimension>.json` | Analyzer findings |
| `output/findings.json` | Validated merged findings |
| `output/run_manifest.json` | Review identity, counts and artifact/configuration hashes |
| `output/report.md` | Executive summary, findings and recommendations |
| `output/fabric-arch-review.pdf` | PDF, when the renderer is available |

Reports include diagrams when supporting evidence is available. Raw evidence and
outputs are gitignored. The [sample report](samples/report.md) and
[sample PDF](samples/fabric-arch-review-sample.pdf) are synthetic snapshots, not
proof of live feature coverage or owner access isolation.

## 📋 Rule catalog & status

[Checklist reference](docs/checklist-reference.md) describes the rules and their
Microsoft Learn references. [Methodology](docs/methodology.md) explains outcomes,
applicability and scoring. [Native evidence](docs/native-evidence.md) explains
execution history, Dataflow Gen2 and typed DAX evidence.

### Workspace classification and production scope

FAR infers workload archetype from item composition and environment from
workspace-name markers. Unresolved applicability remains `unknown`.
Use immutable workspace IDs in [config/workspaces.yaml](config/workspaces.yaml)
to supply authoritative profiles or rule overrides:

```yaml
workspaces:
  - id: 12345678-1234-1234-1234-123456789abc
    profile:
      archetype: warehouse_analytics
      environment: production
    rule_overrides:
      OPS-002:
        applicability: applicable
        reason: "Production finance workspace."
```

Capacity profiles use immutable capacity IDs in the same file.
`WORKSPACE_IDS` selects scope; it does not assign production status.

## 🧱 Extending the framework

1. Add a metadata-only collector and document its data-safety boundary.
2. Implement rule evaluation in an analyzer. Put rule metadata in
   [review-checklist.yaml](config/review-checklist.yaml) and numeric thresholds in
   [thresholds.yaml](config/thresholds.yaml).
3. Register the analyzer in [analyzer-registry.yaml](config/analyzer-registry.yaml)
   and wire required stages into the PowerShell, Bash and Fabric entry points.
4. Add focused tests and update the synthetic fixture only for intended behavior
   changes. Preserve explicit missing-evidence outcomes.

Checklist metadata alone does not execute a rule. See
[Contributing](CONTRIBUTING.md) for the required workflow and validation.

## 🧪 Testing

```powershell
python -m pytest -q
```

Tests use synthetic evidence and cover analyzer outcomes, collection,
orchestration, reporting and access contracts. To update synthetic artifacts
after an intentional behavior change:

```powershell
python -m tests.build_fixture
python -m tests.gen_golden
python -m tests.gen_sample_report
```

Review generated changes rather than accepting new golden results automatically.
See [required validation](CONTRIBUTING.md#required-validation) for Python and app
checks. Local tests do not certify live Fabric connections, permissions or RLS.

## 🗂️ Repository layout

| Directory | Purpose |
| --- | --- |
| `collectors/`, `analyzers/`, `config/` | Evidence collection, rule evaluation and configuration |
| `orchestration/`, `scripts/` | Fabric deployment/targeting and local stage runners |
| `reports/` | Report generation, Gold, models, Agents and owner access |
| `fabric/` | Setup and stage notebooks; optional app |
| `docs/` | Task guides and operating references |
| `tests/`, `samples/` | Tests, synthetic fixtures and sample reports |
| `output/` | Ignored local review artifacts |

## 📚 Documentation

Use the [README task index](README.md#choose-your-task) to find the appropriate
guide rather than reading every reference before a first run.

## 🤝 Contributing

Follow [Contributing](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md).

## 🛡️ Security

Report vulnerabilities privately using [SECURITY.md](SECURITY.md).
For setup help and other questions, see [Support](SUPPORT.md).

## ™️ Trademarks

Use of Microsoft trademarks must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general)
and must not imply Microsoft sponsorship. Third-party trademarks remain subject
to their owners' policies.

## 📄 License

Licensed under [MIT](LICENSE.TXT), provided as-is without a Microsoft support SLA.

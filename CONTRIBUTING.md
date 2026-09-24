<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Contributing

Thank you for your interest in contributing to the **Fabric Architecture Review Accelerator**!

## Contributor License Agreement (CLA)

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit <https://cla.opensource.microsoft.com>.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide a
CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

## Code of Conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/)
or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## How to contribute

1. **Open an issue first** for anything beyond a trivial fix, so we can align on the approach.
2. **Fork** the repository and create a topic branch from `main`.
3. Make your change with clear, focused commits.
4. **Run the automated tests** against the tracked synthetic fixture in
   `tests/fixtures/sample/raw/`. If collector behavior changes, also run the local pipeline
   against a tenant where you are authorized to perform the review.
5. Open a **pull request** describing the change and the motivation.

## The data-safety contract (mandatory)

This accelerator's core promise is that **it never reads customer business data** — only
metadata, configuration, inventory, and metrics. Any contribution is automatically
rejected if it:

- Issues `EVALUATE` / `SELECT` returning customer business rows. The opt-in,
  fixed-contract FUAM monitoring query is governed separately by the
  [targeted-review source contract](docs/targeted-review.md#supported-fuam-source-contract),
- Downloads customer business OneLake file contents (FAR's own artifacts and
  runtime packages are not customer business data),
- Reads notebook cell **outputs**,
- Enables Scanner API scopes that return PII (`getArtifactUsers`, `datasetSchema`,
  `datasetExpressions`, `datasourceDetails`),
- Commits live tenant payloads, credentials, notebook execution outputs or private
  test harnesses. Keep operational artifacts in approved local output folders or
  the FAR Lakehouse, with appropriate access controls.

Every collector module must include a `DATA SAFETY:` comment or docstring
documenting what it reads. See [data safety](docs/data-safety.md) for the
collection boundaries.

## Adding a rule, collector, or analyzer

See **[Extending the framework](REFERENCE.md#-extending-the-framework)** for the
end-to-end pattern (collector → analyzer → checklist → thresholds). Key
points:

- Numeric pass/fail boundaries belong in [config/thresholds.yaml](config/thresholds.yaml),
  resolved via `analyzers._common.threshold()` — do not hard-code magic numbers.
- Rule metadata (id, dimension, severity, description, Learn URL) belongs in
  [config/review-checklist.yaml](config/review-checklist.yaml).
- Findings are dicts with `rule_id`, `dimension`, `severity`, `status`
  (`pass`, `fail`, `info`, `not_applicable`, `unknown`, or `missing_evidence`),
  `title`, `evidence`, and `recommendation`.
- Add or update focused tests. Every enabled checklist ID must be emitted by the
  synthetic analyzer registry; disabled IDs must declare an active `superseded_by`.

## Code style

- Python 3.11+, standard library + the dependencies in `requirements.txt`.
- Include the existing Microsoft copyright and MIT notice in authored source,
  documentation and notebook headers. Keep JSON data/manifests valid; their
  license is covered by [LICENSE.TXT](LICENSE.TXT), not comment injection.
- Keep the [Fabric overview](fabric/README.md) brief, setup steps in the
  [deployment guide](fabric/DEPLOYMENT.md), and detailed orchestration contracts
  in the [targeted-review reference](docs/targeted-review.md). Link rather than
  copying those contracts into every guide.
- Keep collectors metadata-only. Represent supported-but-unavailable evidence explicitly
  so analyzers can emit `missing_evidence`; unexpected collector/analyzer failures must
  terminate the stage rather than publish stale or partial findings.

## Required validation

Owner-report changes must preserve the
[curated projection, provenance and access contract](docs/workspace-owner-report.md).
Local schema, projection and report tests cannot certify Fabric RLS, connection
bindings or consumer isolation. Record live acceptance privately before consumer
publication; never add tenant identifiers, access snapshots or secrets to fixtures.

Use a Python virtual environment and Node.js 22 with its bundled npm 10.
Before opening a pull request, run these commands from the repository root.
The guarded copies prepare public build placeholders without replacing live settings:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pip_audit -r requirements.txt
cd fabric/app
npm ci
if (-not (Test-Path fabric.yaml)) { Copy-Item fabric.example.yaml fabric.yaml }
if (-not (Test-Path rayfin/.env)) { Copy-Item rayfin/.env.example rayfin/.env }
npm test -- --run
npm run lint
npm run build
npm audit --audit-level=high
```

The repository CI repeats these gates on pull requests and pushes to `main` using
GitHub-hosted runners; contributors do not need to provision a self-hosted runner.
Dependency audits include development and deployment tooling. When changing
dependency overrides, verify the full audit, frontend tests, production build
and Rayfin CLI startup.

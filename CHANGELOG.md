# Changelog

All notable changes to this project are documented in this file.

## 2026.09.0

- Added metadata-only DAX definition collection from TMDL and `model.bim`, with explicit missing/error coverage and static pattern-risk rules `DAX-001` and `DAX-002`.
- Added a dedicated DAX Analyzer report page and a capacity-first, semantic-model-second DAX lens in the Fabric app.
- Added DAX model and measure contracts across Gold tables, Direct Lake semantic metadata, Fabric IQ ontology, the Data Agent, Rayfin queries, and deterministic agent evaluation.
- Hardened semantic-model definition collection with token renewal, bounded retries, and resumable checkpoints for long tenant scans.
- Pinned the preview Fabric Data Agent SDK and stamped the resolved repository branch or release ref into the standalone deployed agent notebook.

## 2026.08.2

- Added contextual workspace classification and explicit applicability overrides.
- Added six-state finding outcomes and assessment-coverage scoring.
- Reconciled rule metadata, predicates, thresholds, and superseded rule IDs.
- Hardened local and Fabric stages against stale, missing, malformed, or partial artifacts.
- Added traceable run manifests and retry-safe Gold writes by `RUN_ID`.
- Added full TypeScript production checking, bounded query behavior, schema-based Data Agent discovery, throttling retries, and deliberate vendor chunking.
- Added GitHub-hosted CI gates for Python, notebooks, shell scripts, frontend tests, lint, builds, and package vulnerability audits.
- Updated repository, Fabric, app, methodology, checklist, and contribution documentation.

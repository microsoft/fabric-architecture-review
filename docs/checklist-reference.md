# Checklist Reference

Each rule below is evaluated by an analyzer module. The canonical source of
truth is [`config/review-checklist.yaml`](../config/review-checklist.yaml);
this page is the reader-friendly index with Microsoft Learn references.

Numeric pass/fail boundaries live in [`config/thresholds.yaml`](../config/thresholds.yaml) — the single,
documented place to tune the review to a client's SLOs. See
[methodology.md](methodology.md#applicability-outcomes-and-thresholds) for applicability,
the six-state outcome contract, scoring, and override precedence. Rows marked superseded
retain historical IDs but are disabled and do not emit findings.

## Architecture

| ID | Severity | Description | Reference |
|---|---|---|---|
| ARCH-001 | high | Workspaces follow a medallion layering convention (e.g. *-bronze / *-silver / *-gold or equivalent) | [Learn](https://learn.microsoft.com/fabric/onelake/onelake-medallion-lakehouse-architecture) |
| ARCH-002 | medium | Each workspace is assigned to a Fabric capacity (no items in "My workspace" or on Pro-only capacities for production data assets) | [Learn](https://learn.microsoft.com/fabric/enterprise/licenses) |
| ARCH-003 | medium | Cross-workspace data access uses OneLake shortcuts rather than data duplication | [Learn](https://learn.microsoft.com/fabric/onelake/onelake-shortcuts) |
| ARCH-004 | medium | **Superseded by OPS-002 (disabled).** Workspaces are connected to source control for production items | [Learn](https://learn.microsoft.com/fabric/cicd/git-integration/intro-to-git-integration) |
| ARCH-005 | medium | No single workspace exceeds the monolithic threshold (default 50 items) | [Learn](https://learn.microsoft.com/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines) |
| ARCH-006 | low | Every workspace has a description that documents purpose and ownership | [Learn](https://learn.microsoft.com/fabric/get-started/create-workspaces) |
| ARCH-007 | info | Workspaces containing no items should be archived or repurposed | [Learn](https://learn.microsoft.com/fabric/governance/governance-overview) |
| ARCH-008 | medium | Personal (My workspace / PersonalGroup) workspaces are deprecated for production assets; migrate content to a shared workspace on a Fabric capacity | [Learn](https://learn.microsoft.com/fabric/get-started/workspaces) |
| ARCH-009 | high | **Superseded by OPS-001 (disabled).** Production workspaces are governed by a Fabric / Power BI Deployment Pipeline | [Learn](https://learn.microsoft.com/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines) |
| ARCH-010 | medium | Real-Time Intelligence assets (Eventhouse, KQL Database, Eventstream, Reflex / Activator) and Mirrored Databases are inventoried | [Learn](https://learn.microsoft.com/fabric/real-time-intelligence/overview) |
| ARCH-011 | high | Semantic-model storage-mode mix is healthy | [Learn](https://learn.microsoft.com/fabric/get-started/direct-lake-overview) |
| ARCH-012 | high | ExecuteNotebook / TridentNotebook activities inside Data Pipelines pass parameters that match the parameter names declared by the target notebook (Papermill-style `parameters`-tagged cell) | [Learn](https://learn.microsoft.com/fabric/data-factory/notebook-activity) |
| ARCH-013 | high | The Scanner API inventory is complete for this run | [Learn](https://learn.microsoft.com/rest/api/power-bi/admin/workspace-info-post-workspace-info) |
| ARCH-014 | medium | Deployment-pipeline stages are kept in sync | [Learn](https://learn.microsoft.com/fabric/cicd/deployment-pipelines/understand-the-deployment-process) |
| ARCH-015 | medium | Legacy Dataflow Gen1 (Power BI dataflows) are migrated to Dataflow Gen2 | [Learn](https://learn.microsoft.com/fabric/data-factory/dataflows-gen2-overview) |

## Performance

| ID | Severity | Description | Reference |
|---|---|---|---|
| PERF-001 | high | Capacities are not actively throttling | [Learn](https://learn.microsoft.com/fabric/enterprise/throttling) |
| PERF-002 | high | Average CU utilization stays below the capacity warning threshold across the review window | [Learn](https://learn.microsoft.com/fabric/enterprise/optimize-capacity) |
| PERF-003 | medium | Semantic models stay within the size threshold; large Import models should be evaluated for Direct Lake or aggregation strategies | [Learn](https://learn.microsoft.com/fabric/get-started/direct-lake-overview) |
| PERF-004 | medium | Scheduled refreshes complete within the duration SLO and refresh failure rate is below threshold | [Learn](https://learn.microsoft.com/power-bi/connect-data/refresh-troubleshooting-refresh-scenarios) |
| PERF-005 | medium | Lakehouse tables do not exhibit a small-file problem | [Learn](https://learn.microsoft.com/fabric/data-engineering/lakehouse-table-maintenance) |
| PERF-006 | medium | Datasets reported as refreshable have a configured refresh schedule and recent successful refresh; otherwise the model is stale or manually refreshed only | [Learn](https://learn.microsoft.com/power-bi/connect-data/refresh-data) |
| PERF-007 | medium | Refresh runs complete in under two hours on average | [Learn](https://learn.microsoft.com/power-bi/connect-data/incremental-refresh-overview) |
| PERF-008 | medium | Data pipeline jobs succeed reliably | [Learn](https://learn.microsoft.com/fabric/data-factory/pipeline-runs) |
| PERF-009 | medium | Spark notebook jobs complete within a reasonable duration and without recurring failures | [Learn](https://learn.microsoft.com/fabric/data-engineering/spark-job-definition-api) |
| PERF-010 | high | Semantic models do not show consecutive refresh failures | [Learn](https://learn.microsoft.com/power-bi/connect-data/refresh-troubleshooting-refresh-scenarios) |
| PERF-011 | medium | Capacity autoscale (or planned manual scale-out) is configured for capacities that regularly approach the throttling threshold | [Learn](https://learn.microsoft.com/fabric/enterprise/scale-capacity) |
| PERF-012 | medium | Import-mode semantic models are audited for DirectLake-migration feasibility | [Learn](https://learn.microsoft.com/fabric/get-started/direct-lake-overview) |
| PERF-013 | medium | Direct Lake semantic models declare an explicit fallback behaviour | [Learn](https://learn.microsoft.com/fabric/get-started/direct-lake-overview#fallback-behavior) |
| PERF-014 | low | Scheduled semantic-model refreshes in the same workspace do not pile up into overlapping windows | [Learn](https://learn.microsoft.com/power-bi/connect-data/refresh-scheduled-refresh) |

## Governance

| ID | Severity | Description | Reference |
|---|---|---|---|
| GOV-001 | high | Every production workspace has at least two admins (no single owner / orphan risk) | [Learn](https://learn.microsoft.com/fabric/get-started/roles-workspaces) |
| GOV-002 | medium | Items have not been inactive beyond the orphaned-item threshold; orphaned items should be archived or deleted | [Learn](https://learn.microsoft.com/fabric/governance/governance-overview) |
| GOV-003 | medium | Sensitivity labels are applied to semantic models, reports, lakehouses and warehouses | [Learn](https://learn.microsoft.com/fabric/governance/information-protection) |
| GOV-004 | low | Workspaces follow a documented naming convention (prefix/suffix for env and layer) | [Learn](https://learn.microsoft.com/fabric/governance/governance-overview) |
| GOV-005 | medium | Sharing activity is within expected volume | [Learn](https://learn.microsoft.com/fabric/admin/track-user-activities) |
| GOV-006 | medium | Workspaces show recent activity in the admin activity log | [Learn](https://learn.microsoft.com/fabric/admin/track-user-activities) |
| GOV-007 | medium | The Microsoft Fabric Capacity Metrics app is installed in the tenant | [Learn](https://learn.microsoft.com/fabric/enterprise/metrics-app-install) |
| GOV-008 | low | A healthy share of discoverable items (semantic models, reports, dataflows, lakehouses, warehouses) are endorsed (Promoted / Certified) | [Learn](https://learn.microsoft.com/fabric/governance/endorsement-overview) |
| GOV-009 | medium | Production semantic models are Certified so consumers can trust the sanctioned source of truth | [Learn](https://learn.microsoft.com/fabric/governance/endorsement-certify) |

## Operational Excellence

Scoped to *production* workspaces only — dev / test / sandbox / personal workspaces are never flagged for lacking ALM controls.

| ID | Severity | Description | Reference |
|---|---|---|---|
| OPS-001 | high | Production workspaces are covered by a Fabric deployment pipeline for controlled dev→test→prod promotion | [Learn](https://learn.microsoft.com/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines) |
| OPS-002 | high | Production workspaces are connected to Git source control so changes are versioned and reviewable | [Learn](https://learn.microsoft.com/fabric/cicd/git-integration/intro-to-git-integration) |
| OPS-003 | medium | At least one multi-stage deployment pipeline exists, evidencing a real dev→prod promotion path | [Learn](https://learn.microsoft.com/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines) |

## Security

| ID | Severity | Description | Reference |
|---|---|---|---|
| SEC-001 | critical | Tenant setting "Publish to web" is disabled or restricted to a specific security group | [Learn](https://learn.microsoft.com/power-bi/admin/service-admin-portal-export-sharing#publish-to-web) |
| SEC-002 | high | Tenant setting "Export data" is restricted to a security group, not enabled tenant-wide | [Learn](https://learn.microsoft.com/power-bi/admin/service-admin-portal-export-sharing) |
| SEC-003 | high | Guest users cannot access the tenant unless explicitly scoped via security group | [Learn](https://learn.microsoft.com/fabric/admin/service-admin-portal-export-sharing) |
| SEC-004 | medium | Workspace access uses security groups (not individual users) and least privilege roles | [Learn](https://learn.microsoft.com/fabric/get-started/roles-workspaces) |
| SEC-005 | medium | Workspaces with very broad direct access (more than 10 individual principals) should be reviewed; convert to security-group-based access | [Learn](https://learn.microsoft.com/fabric/get-started/roles-workspaces) |
| SEC-006 | high | Datasources flagged as misconfigured in the Scanner API must be remediated (broken gateways, expired credentials, missing OAuth consent) | [Learn](https://learn.microsoft.com/power-bi/connect-data/service-gateway-onprem) |
| SEC-007 | high | External (guest) users granted workspace access should be reviewed and removed when no longer required | [Learn](https://learn.microsoft.com/entra/external-id/external-identities-overview) |
| SEC-008 | high | On-premises data gateway clusters have at least two member gateways | [Learn](https://learn.microsoft.com/data-integration/gateway/service-gateway-high-availability-clusters) |
| SEC-009 | medium | Private connectivity is configured for cloud datasources where required - either via a Virtual Network data gateway or Fabric trusted-workspace access | [Learn](https://learn.microsoft.com/fabric/security/security-network-overview) |
| SEC-010 | medium | On-premises / VNet data gateway cluster members run a current, consistent version | [Learn](https://learn.microsoft.com/data-integration/gateway/service-gateway-update) |
| SEC-011 | medium | Data sources avoid personal-mode gateways and stored single-user credentials | [Learn](https://learn.microsoft.com/fabric/security/security-managed-identities) |

## Cost

| ID | Severity | Description | Reference |
|---|---|---|---|
| COST-001 | high | Capacity SKU is right-sized relative to sustained CU% — neither chronically idle (< 20% average) nor chronically saturated (> 85% average) | [Learn](https://learn.microsoft.com/fabric/enterprise/optimize-capacity) |
| COST-002 | medium | Non-production capacities use Pause / Resume or autoscale to avoid 24x7 charges | [Learn](https://learn.microsoft.com/fabric/enterprise/pause-resume) |
| COST-003 | medium | **Superseded by COST-002 (disabled).** Non-production capacities use Pause/Resume to avoid 24x7 charges | [Learn](https://learn.microsoft.com/fabric/enterprise/pause-resume) |
| COST-004 | medium | Production-like workspaces (prod / production in name) are not hosted on PPU (per-user) capacities, which do not scale and are tied to a single user license | [Learn](https://learn.microsoft.com/power-bi/enterprise/service-premium-per-user-faq) |
| COST-005 | medium | Large capacities (F64 and above) with very few assigned workspaces (< 5) are candidates for SKU downgrade or workspace consolidation | [Learn](https://learn.microsoft.com/fabric/enterprise/optimize-capacity) |
| COST-006 | medium | Trial and Embedded capacities are not hosting real workspaces (trials expire; Embedded is sized for app embedding, not interactive use) | [Learn](https://learn.microsoft.com/fabric/enterprise/licenses) |
| COST-007 | low | Several small dedicated F-SKUs (F1-F16) can often be consolidated into one right-sized capacity for shared CU smoothing and simpler governance | [Learn](https://learn.microsoft.com/fabric/enterprise/optimize-capacity) |

## Tenant Settings

| ID | Severity | Description | Reference |
|---|---|---|---|
| TENANT-001 | high | "Users can create Fabric items" is scoped to a specific security group, not enabled for the entire organization | [Learn](https://learn.microsoft.com/fabric/admin/fabric-switch) |
| TENANT-002 | medium | "Service principals can use Fabric APIs" is enabled and scoped to an automation security group (required for this assessment and for CI/CD) | [Learn](https://learn.microsoft.com/fabric/admin/metadata-scanning-enable-read-only-apis) |
| TENANT-003 | high | External sharing tenant settings ("Allow sharing to external users", "Invite external users to your organization") are disabled or scoped to a security group | [Learn](https://learn.microsoft.com/fabric/admin/service-admin-portal-export-sharing) |
| TENANT-004 | medium | Uncertified / custom visuals tenant settings are scoped | [Learn](https://learn.microsoft.com/power-bi/admin/service-admin-portal-visuals) |
| TENANT-005 | medium | R and Python visual / script settings are scoped or disabled | [Learn](https://learn.microsoft.com/power-bi/admin/service-admin-portal-r-and-python-visuals) |

## Notebook code (heuristic)

Heuristic regex scan over decoded notebook source. Each rule is filed under a WAF dimension (shown below); findings reference notebook name + cell index only.

| ID | Severity | Dimension | Description | Reference |
|---|---|---|---|---|
| NBCODE-001 | high | Security | Notebook source contains patterns that look like hard-coded secrets (account keys, SAS tokens, bearer tokens, client_secret assignments, password literals) | [Learn](https://learn.microsoft.com/fabric/data-engineering/microsoft-spark-utilities) |
| NBCODE-002 | medium | Architecture | Notebooks install packages inline via %pip install / !pip install | [Learn](https://learn.microsoft.com/fabric/data-engineering/environment-manage-library) |
| NBCODE-003 | medium | Performance | Notebooks call .collect() / .toPandas() on Spark DataFrames without a bound (.limit(N), .first(), .head(N), .take(N), or an aggregation) | [Learn](https://learn.microsoft.com/fabric/data-engineering/spark-job-best-practices) |
| NBCODE-004 | medium | Architecture | Notebooks reference Databricks-only APIs (dbutils, /dbfs, mlflow Databricks tracking URI) | [Learn](https://learn.microsoft.com/fabric/data-engineering/microsoft-spark-utilities) |
| NBCODE-005 | low | Architecture | Notebook code embeds fully qualified abfss:// paths or workspace / lakehouse GUIDs as string literals | [Learn](https://learn.microsoft.com/fabric/onelake/onelake-overview) |
| NBCODE-006 | low | Performance | Notebooks write data with .format("parquet") or .format("csv") instead of Delta | [Learn](https://learn.microsoft.com/fabric/data-engineering/lakehouse-and-delta-tables) |

## Best Practices (Fabric-only)

| ID | Severity | Description | Reference |
|---|---|---|---|
| BPA-001 | high | Semantic models pass the Best Practice Analyzer for storage, performance and modelling rules | [Learn](https://learn.microsoft.com/fabric/data-warehouse/semantic-link-labs) |
| BPA-002 | high | Reports do not bind to missing fields and have no orphaned visuals | [Learn](https://learn.microsoft.com/power-bi/create-reports/) |
| BPA-003 | high | Direct Lake models do not fall back to DirectQuery | [Learn](https://learn.microsoft.com/fabric/get-started/direct-lake-overview) |
| BPA-004 | medium | Reports pass the Report Best Practice Analyzer (visual count, slow visuals, layout) | [Learn](https://learn.microsoft.com/fabric/data-warehouse/semantic-link-labs) |
| BPA-005 | medium | Delta tables are healthy: V-Ordered, few small files, reasonable row groups | [Learn](https://learn.microsoft.com/fabric/data-engineering/delta-optimization-and-v-order) |
| BPA-006 | medium | Models have no unused columns, tables or measures | [Learn](https://learn.microsoft.com/fabric/data-warehouse/semantic-link-labs) |
| BPA-007 | medium | Capacities run on Fabric F-SKUs rather than legacy Premium P-SKUs | [Learn](https://learn.microsoft.com/fabric/enterprise/capacity-settings) |

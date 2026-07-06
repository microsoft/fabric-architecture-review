# Running the Review Inside Microsoft Fabric

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
[![Runs: In-Fabric](https://img.shields.io/badge/runs-In--Fabric-00BCF2.svg)](#-deploy-it-from-inside-fabric-no-workstation-needed)
[![Pattern: single--notebook](https://img.shields.io/badge/pattern-single--notebook-blueviolet.svg)](#-deploy-it-from-inside-fabric-no-workstation-needed)
[![Data: metadata-only](https://img.shields.io/badge/data-metadata--only-success.svg)](../README.md#-data-safety-read-this-first)

Run the **Fabric Architecture Review Accelerator** entirely **from inside a Microsoft Fabric
workspace** — no workstation, no local install. You import **one** setup notebook, run it once,
and it deploys a Lakehouse, five notebooks, an orchestration pipeline, and a Direct Lake
governance report for you. Then you trigger the pipeline (on demand or on a schedule) and read a
client-ready `report.md` plus an interactive Power BI report.

This guide complements the [main README](../README.md), which covers the **local (PowerShell)**
workflow, the full collector/role matrix, the rule catalog, and the data-safety contract. Read
that first if you have not yet — everything there applies here too.

> **Data safety:** like every run mode, the in-Fabric pipeline reads **metadata, configuration,
> inventory, and metrics only** — never customer business data. See
> [Data safety](../README.md#-data-safety-read-this-first) in the main README.

---

## 📑 Table of contents

- [Which tenant can each run mode reach?](#-which-tenant-can-each-run-mode-reach)
- [Deploy it from inside Fabric (no workstation needed)](#-deploy-it-from-inside-fabric-no-workstation-needed)
- [Pipeline parameters (selectable at run time)](#-pipeline-parameters-selectable-at-run-time)
- [The gold layer + Direct Lake governance report](#-the-gold-layer--direct-lake-governance-report)
- [Ask the data agent (conversational Q&A)](#-ask-the-data-agent-conversational-qa)
- [Build the estate graph (Fabric IQ ontology)](#-build-the-estate-graph-fabric-iq-ontology)
- [Optional: Azure (ARM) auth for capacity Pause/Resume detection](#-optional-azure-arm-auth-for-capacity-pauseresume-detection)
- [Workspace logo (optional)](#-workspace-logo-optional)
- [Versioning & updates](#-versioning--updates)
- [Troubleshooting](#-troubleshooting)

---

## 🧭 Which tenant can each run mode reach?

The two run modes differ in **which tenant you can review**, because they authenticate differently:

| | **Local (PowerShell)** | **In Fabric (notebook / pipeline)** |
| --- | --- | --- |
| Identity | Your signed-in user (`az login` / browser), via `azure.identity` | The notebook's executing identity, via `notebookutils` |
| Tenant targeted | **Any tenant** you have guest/member access to — set by `TENANT_ID` in `.env` | **Only the tenant that owns the Fabric workspace** — `TENANT_ID` is a label, it cannot redirect the token |
| Cross-tenant review | ✅ Yes — `az login --tenant <client>` then set `TENANT_ID=<client>` | ❌ No — run the notebook *inside the client's tenant* |
| Best for | Consultant reviewing a client tenant from their own machine | Client running it themselves, or a tenant-resident reviewer; unattended/scheduled runs |

**Key point:** locally you can point the framework at any tenant where your account is a guest/member
with the right Fabric roles (the `TENANT_ID` env var drives `az login`/the token). In Fabric, the
token is always issued for the workspace's home tenant — so to review a client tenant you import and
run the notebook **inside that client's Fabric workspace** with an identity that holds the roles there.

> The one exception is the opt-in Azure (ARM) Pause/Resume scan — see
> [Optional: Azure (ARM) auth](#-optional-azure-arm-auth-for-capacity-pauseresume-detection) below.

---

## 🚀 Deploy it from inside Fabric (no workstation needed)

This uses a single-notebook deploy pattern: you import **one** setup
notebook, run it once, and it deploys everything else for you. The only prerequisite you create by
hand is the **Fabric workspace** itself. Remember: the review targets the tenant that owns the
workspace (see the table above).

1. **Import the setup notebook** — Fabric workspace → *New* → *Import notebook* → upload [setup.ipynb](setup.ipynb).
2. **Set its parameters** (parameters cell): the GitHub repo/branch to clone, `WORKSPACE_ID` (leave blank to use the current workspace), and the baked-in pipeline defaults (`TENANT_ID`, `CLIENT_NAME`, `ENGAGEMENT_NAME`, `REVIEWER_NAME`, optional `WORKSPACE_IDS`, the `CAPACITY_*` flags). These become the **pipeline's default parameter values** — every one stays overridable at run time (see [Pipeline parameters](#-pipeline-parameters-selectable-at-run-time)).
3. **Run all cells.** Using the Fabric REST API, the setup notebook:
   - creates (or reuses) the Lakehouse `fabric_arch_review_lh` that holds the run output,
   - deploys the four stage notebooks — `FabricArchReview_01_Collect`, `FabricArchReview_02_Analyze`, `FabricArchReview_03_Report`, `FabricArchReview_04_Gold` — plus a standalone `FabricArchReview_05_Agent` notebook, each pre-attached to that Lakehouse,
   - creates (or updates) the **Fabric Arch Review Pipeline** that chains *Collect → Analyze → Report → Gold* (each step depends on the previous one succeeding), passing a shared `RUN_ID` so all stages read/write the same run folder,
   - deploys a **Direct Lake semantic model + Power BI report** named *Fabric Arch Review - Governance* over the gold-layer Delta tables. Set `DEPLOY_GOLD_REPORT="false"` to skip this,
   - prepares a **Fabric data agent** (*Fabric Arch Review - Data Agent*) so the team can ask the review results in plain English — grounded on both the semantic model and the gold lakehouse. It is deployed by the standalone **`FabricArchReview_05_Agent`** notebook that runs the [fabric-data-agent-sdk](https://pypi.org/project/fabric-data-agent-sdk/) **after the pipeline** (so the backend enumerates the datasource schema against populated tables — the only reliable way to reference tables). Optionally set `ENDORSE_MODEL` (`Promoted`/`Certified`) and `SENSITIVITY_LABEL_ID` (a label GUID) to endorse + label the deployed items.
   - deploys a **Fabric IQ estate ontology** (`Fabric_Arch_Review_Estate_Ontology`) — a knowledge graph whose entity types mirror the real estate: **Capacity**, **Workspace**, **SemanticModel**, **ModelTable**, **Report**, **Notebook**, **Pipeline**, **Lakehouse**, **Owner** and **Finding**, connected by the estate relationships (`HostedOnCapacity`, `InWorkspace`, `BelongsToModel`, `ReportInWorkspace`, `NotebookInWorkspace`, `PipelineInWorkspace`, `LakehouseInWorkspace`, `OwnerAdministersWorkspace`, `ModelFeedsReport`, `AffectsWorkspace`). `AffectsWorkspace` links each **Finding** to the workspaces it triggers (by reverse-mapped id), so you can walk from a workspace to its exact failing rules. Set `DEPLOY_ONTOLOGY="false"` to skip it. *(Ontology item names allow only letters, numbers and underscores.)*

   > **Grounding the data agent on the ontology (optional, manual).** Adding an ontology as a data-agent source is a Fabric preview whose item-definition API isn't documented, so setup does **not** wire it automatically. To do it: run the pipeline once so the ontology has data, then open the **Fabric Arch Review - Data Agent** → **＋ Data source** → select `Fabric_Arch_Review_Estate_Ontology` → pick its entity types → **Save** and **Publish**. The agent already answers estate/lineage questions from the semantic model + lakehouse; the ontology source is a graph-reasoning enhancement.

   > **⚠️ Ontology prerequisite (tenant admin).** The ontology is a preview feature, so before running you must enable **"Users can create Ontology (preview) items"** in the Fabric **Admin portal → Tenant settings** (scoped to your identity/security group). Without it the ontology deploy returns `403 FeatureNotAvailable` — the rest of the setup (notebooks, pipeline, model, report) still succeeds and the ontology step is skipped best-effort. Re-run the setup once the setting has propagated (can take a few minutes).

   It is idempotent — re-running it upserts the notebooks (including the `05_Agent` deployer), pipeline, model, report **and ontology** instead of duplicating them. (The data agent itself is deployed later, by running the `05_Agent` notebook.)
4. **Run the pipeline.** Open *Fabric Arch Review Pipeline* → *Run*. The Run dialog lists every parameter (pre-filled with the defaults you set in step 2) so you can adjust scope per run, or *Schedule* it for unattended runs. The collect stage clones this repo, installs `requirements.txt`, authenticates as the **executing identity** via `notebookutils` (no `az login`), gathers metadata into the Lakehouse, then analyze, report, and gold stages run in turn.
5. **Read the output** two ways: the consultant-style **`report.md`** in `Files/fabric-arch-review/<run-id>/` (raw JSON alongside in `raw/`), and the interactive **Fabric Arch Review - Governance** report (open it from the workspace).

> **Auth in Fabric:** the executing identity needs the same roles the framework documents (Fabric Administrator for tenant-wide collectors; Workspace Member+ for per-workspace ones). The Power BI token audience covers both the Fabric and Power BI admin REST endpoints.
>
> **PDF stage:** the branded PDF needs Node.js + Puppeteer and is **skipped** in Fabric — the pipeline produces `report.md`. Generate the PDF later on a workstation with `python reports/_generate_pdf.py` if you need it.

---

## 🎛️ Pipeline parameters (selectable at run time)

The setup notebook promotes every engagement value to a **pipeline-level parameter** with a default,
so you do **not** have to redeploy to re-scope a run. Open *Fabric Arch Review Pipeline* → *Run* and the
dialog pre-fills these (and the same parameters are available on a *Schedule* trigger). Each stage reads
them via `@pipeline().parameters.*`, exactly the way the shared `RUN_ID` is resolved.

| Parameter | Default | What it does |
| --- | --- | --- |
| `GITHUB_REPO_URL` | this repo's clone URL | Repo the stages clone to get the analyzer code — change it if you forked |
| `GITHUB_BRANCH` | `main` | Branch read to discover the latest `VERSION` and used as the fallback if no release tag exists |
| `GITHUB_REF` | resolved release tag (`v<VERSION>`) | Immutable ref every stage clones at runtime — set automatically at deploy so runs stay pinned to the deployed release (not editable per run) |
| `SP_CLIENT_ID` / `SP_CONNECTION_NAME` | blank / `sp-fabric-arch-review` | *Optional* read-only **service principal** for unattended/scheduled baselines. You create the cloud connection **manually** (setup just prints a reminder); the secret never touches setup or pipeline params. Optionally use `SP_SECRET_KEYVAULT` + `SP_SECRET_NAME` for a Key Vault secret instead. Blank = run as the notebook's executing identity. See [auth-setup.md](../docs/auth-setup.md). |
| `TENANT_ID` | blank | Label recorded in the report (does **not** redirect the token in Fabric) |
| `WORKSPACE_IDS` | blank | Comma-separated workspace GUIDs to restrict the review to (blank = tenant-wide) |
| `ACTIVITY_DAYS_LOG` | `7` | Admin Activity Log lookback window in days (1–30 per Fabric Admin API) |
| `CLIENT_NAME` | `Contoso` | Client name on the report cover |
| `ENGAGEMENT_NAME` | `Fabric Architecture Review` | Engagement title on the report cover |
| `REVIEWER_NAME` | blank | Reviewer name on the report cover |
| `CAPACITY_METRICS_APP_INSTALLED` | `false` | `true` enables the opt-in Capacity Metrics App DAX collector |
| `CAPACITY_AUTO_PAUSE_CONFIGURED` | `false` | Keep `false` in Fabric (ARM scan is local-only — see below) |
| `VERTIPAQ_STATS_READ_DATA` | `false` | `true` adds exact column cardinality (aggregate COUNT DAX); default = sizes/encoding metadata only |

> **Tip:** the defaults baked in at deploy time are the safe, generic ones above. Override only what a
> given run needs (e.g. set `WORKSPACE_IDS` to scope a spot-check) and leave the rest.
>
> **Threshold tuning:** every numeric pass/fail boundary (the ~16 keys documented in
> [../config/thresholds.yaml](../config/thresholds.yaml)) is *also* promoted to an optional pipeline
> parameter — leave them blank to use the curated defaults, or set one to re-tune the review to a
> client's SLOs without redeploying. See
> [Tuning pass/fail thresholds](../README.md#tuning-passfail-thresholds) in the main README.

---

## 🥇 The gold layer + Direct Lake governance report

The `04_Gold` stage turns each run's `findings.json` + raw metadata into **gold-layer Delta tables** in
the Lakehouse `Tables/` folder, and **appends** one partition of history per run so you can trend
best-practice posture over time. The tables (built by [../reports/gold_layer.py](../reports/gold_layer.py)
from the shared schema in [../reports/powerbi/schema.py](../reports/powerbi/schema.py)) are:

| Table | Grain | Backs |
| --- | --- | --- |
| `gold_findings` | one row per evaluated rule per run | every page (cards, findings table, measures) |
| `gold_run_summary` | one row per run | the run slicer + headline scorecard |
| `gold_dimension_summary` | one row per dimension per run | the *Overview* maturity radar + severity heatmap |
| `gold_capacities` | capacities at scan time | *Cost* / *Performance* pages |
| `gold_workspaces` | workspaces in scope (+ admin count, last activity, inactive flag) | *Governance* page + data agent |
| `gold_semantic_models` | models + storage mode + VertiPaq size / column counts | *Architecture* + *Semantic Models* pages |
| `gold_model_tables` | one row per model table (VertiPaq) | *Model detail* page |
| `gold_model_columns` | one row per model column (size, encoding, data type, cardinality) | *Model detail* page |
| `gold_model_partitions` | one row per model partition (mode, record/segment counts) | *Model internals* page |
| `gold_model_relationships` | one row per model relationship (cardinality, used size) | *Model internals* page |
| `gold_model_hierarchies` | one row per user hierarchy | *Model internals* page |
| `gold_notebook_smells` | per-notebook NBCODE matches | *Notebooks* page |
| `gold_workspace_risk` | one row per workspace (item mix, issue/risk score, status) | *Overview* top-risk bar + *Estate Map* |
| `gold_severity_matrix` | one row per dimension × severity | *Overview* + *Estate Map* severity heatmap |
| `gold_bpa_violations` | one row per individual BPA / Direct Lake / Delta / health violation | *Best Practices* page |
| `gold_graph_nodes` | estate entities (capacity, workspace, items, owners) | *Estate Map* inventory |
| `gold_graph_edges` | relationships between estate entities | *Estate Map* relationships |
| `gold_agent_eval` | one row per data-agent evaluation case per run (question, gold-derived expected answer, agent answer, passed) | *Agent Eval* page + accuracy KPI |

The `gold_semantic_models`, `gold_model_*` tables are populated from `vertipaq_stats.json` (the
**Fabric-only** `collectors.vertipaq_stats` collector). When that collector did not run — or no models
were resident — those per-model tables are simply empty and the *Semantic Models* / *Model detail* /
*Model internals* pages render without data. The estate-graph and risk tables (`gold_graph_*`,
`gold_workspace_risk`, `gold_severity_matrix`) are derived from the scanner inventory + findings, so
they populate on every run.

The **Best Practices** dimension (`BPA-001..007` — model/report BPA, Direct Lake fallback, Delta health,
unused objects, capacity SKU readiness) is Fabric-only via `semantic-link-labs`; it feeds
`gold_dimension_summary`, `gold_severity_matrix`, and `gold_bpa_violations`, and gets its own report
page automatically. Outside Fabric the collector degrades to `available:false` and the page renders as
informational.

The **Fabric Arch Review - Governance** semantic model
([../reports/powerbi/semantic_model.py](../reports/powerbi/semantic_model.py)) binds to these tables in
**Direct Lake** mode (no import, no scheduled refresh) and the report
([../reports/powerbi/report.py](../reports/powerbi/report.py)) is a **17-page platform-assessment
dashboard**:

| Page | What it shows |
| --- | --- |
| **Home** | Branded navigation hero — click through to any page |
| **Overview** | Executive cockpit: platform-maturity **radar** by dimension, best-practice-score **gauge**, fails-by-severity **donut**, dimension × severity **heatmap**, top-risk-workspace **ranked bar**, and the full findings table |
| **Trends** | Run-over-run history — best-practice score, fails by severity, and per-dimension posture trended across every pipeline run |
| **Estate Map** | Workspace-risk hotspots (scatter), failures by dimension & severity, and the estate inventory / relationships tables |
| **Architecture, Performance, Cost, Governance, Operational Excellence, Security, Tenant Settings** | One page per review dimension — KPI cards, a severity donut, a ranked bar of failing checks, a dimension-specific detail table, and the dimension-filtered findings list |
| **Best Practices** | Fabric-only BPA outcomes — a violation-count KPI and a breakdown of model/report BPA, Direct Lake fallback, Delta health, unused objects, and capacity SKU readiness |
| **Semantic Models** | Per-model VertiPaq burden (size, column / calculated-column counts, storage mode) with a size-by-model bar and a model-hotspot **scatter** (size vs. refresh, sized by columns) |
| **Model detail** | Pick a model + table from slicers and inspect every column the way DAX Studio's VertiPaq Analyzer does — data type, encoding, cardinality, size |
| **Model internals** | Per-model partitions, relationships, and user hierarchies |
| **Notebooks** | NBCODE code-smell matches with a severity donut and a top-notebooks ranked bar |
| **Agent Eval** | Data-agent accuracy — pass-rate KPI, passed-by-category bar, and the per-case table (question, gold-derived expected answer, agent answer, pass/fail) for the latest run |

> **Custom visual note:** the *Overview* maturity radar uses the Microsoft-certified **Radar Chart**
> public custom visual (declared via `publicCustomVisuals`). If your tenant blocks custom visuals it
> renders a placeholder — every other visual on every page uses standard core visuals, so the rest of the
> report is unaffected.

> **First run:** the model + report are deployed empty. They light up after the pipeline runs **once**
> (the `04_Gold` stage creates the Delta tables the Direct Lake model reads). On a brand-new Lakehouse the
> setup notebook waits for the SQL analytics endpoint to provision before deploying the model.

---

## 🤖 Ask the data agent (conversational Q&A)

Alongside the report, setup prepares a **Fabric data agent** named *Fabric Arch Review - Data Agent*
so anyone on the team can ask the review results in plain English instead of reading pages of findings.
It is deployed by the standalone **`FabricArchReview_05_Agent`** notebook, which uses the `fabric-data-agent-sdk` and must
run **after the pipeline's first run**: the SDK lets the Fabric backend *enumerate* each datasource's
schema, so the agent references real, populated tables (a hand-authored table list gets flagged
*"table has been deleted or you don't have permission"*). Run the pipeline once, then run the `05_Agent` notebook.

**Grounded on two sources** (up to five are allowed; the agent picks the right one per question):

- the **Direct Lake governance semantic model** — governed measures + rich column descriptions drive
  natural-language → DAX for scores, counts and roll-ups;
- the **gold lakehouse tables** — natural-language → SQL for open drill-down, with ~17 built-in
  example question/query pairs (few-shots are only honoured on non-semantic-model sources).

**Ask things like:**

- *“What's our best-practice score and the top critical findings?”*
- *“How can I improve the architecture design / reduce cost?”*
- *“How do I improve the semantic model ‘X’ / the notebook ‘Y’?”*
- *“Are we being throttled — do we need a bigger or smaller capacity?”* (PERF-001/002/011)
- *“Which workspaces are unused and could be closed?”* (empty workspaces + GOV-006)
- *“Which workspaces have only one admin?”* (GOV-001)

**Enterprise posture**

- **Read-only.** The agent only generates read queries; it never writes or changes data.
- **Data-safe.** Its model contains *only* the review's findings and metadata — never customer
  business data — and the instructions tell it not to surface the reviewer's identity or raw
  evidence UPNs.
- **Governed.** Answers stay within the caller's Fabric permissions and any Microsoft Purview
  policies (row/column-level security and sensitivity labels are enforced upstream); interactions
  are auditable via Purview.
- **Traceable.** The published agent is stamped with the deployed FAR release version.
- **Endorsable.** Optionally endorse (Promoted / Certified) and apply a sensitivity label to the
  model, report and agent via the `ENDORSE_MODEL` and `SENSITIVITY_LABEL_ID` setup parameters
  (off by default; requires admin rights and a valid tenant label GUID).
- **Prerequisites:** a paid **F2+** (or P1+) capacity and the **cross-geo processing/storing for AI**
  tenant setting enabled (Fabric admin portal). Re-running setup re-publishes the agent idempotently.

> **First run:** the agent answers meaningfully only after the pipeline has run **once** (it reads the
> same gold tables as the report). Deploy it now; it lights up with the first run.

### Self-checking accuracy (deterministic eval)

The agent ships with a **deterministic, self-checking evaluation**
([../reports/agent/evaluate.py](../reports/agent/evaluate.py)): a fixed set of questions whose
**expected answers are computed from the same gold tables the agent is grounded on**, so the ground
truth is re-derived each run and can never drift. There is **no LLM judge and no hand-maintained answer
key** — numbers are matched by value, names by substring, and a prompt-injection case checks the agent
*refuses*. Results land in `gold_agent_eval` and surface on the report's **Agent Eval** page (pass-rate
KPI + per-case table). The **`05_Agent`** notebook runs this evaluation right after it publishes the
agent (it calls the published agent via the `fabric-data-agent-sdk`, scores each answer, and writes
`gold_agent_eval`), so the Agent Eval page populates as soon as you run that notebook.

---

## 🕸️ Build the estate graph (Fabric IQ ontology)

Setup deploys a **Fabric IQ ontology** item (`Fabric_Arch_Review_Estate_Ontology`) with the entity
types and relationships already defined. **Fabric IQ does not auto-materialise the graph from the item
definition** — the graph is *built* in the ontology editor (a preview, UI-only step). So after the
pipeline has run once, do this one-time build:

**Prerequisites**

- The **"Users can create Ontology (preview) items"** tenant setting is enabled (Fabric admin portal).
- The **pipeline has run at least once**, so the gold tables hold data (an empty ontology can't build).

**Steps (≈2 minutes, one time)**

1. Open **`Fabric_Arch_Review_Estate_Ontology`** → switch to the **Model** mode → **Get data** and select
   **`gold_graph_nodes`** and **`gold_graph_edges`** from the **`fabric_arch_review_lh`** lakehouse.
   These two tables model the whole estate as one nodes table + one edges table (edges reference nodes
   by the same `node_id`), so you only define **one node** and **one edge**:
2. **Add node** → `EstateNode`:

   | Field | Value |
   |---|---|
   | Source table | `gold_graph_nodes` |
   | Key | `node_id` *(add `run_id` as a 2nd key column if you've run the pipeline more than once)* |
   | Properties | `node_name`, `node_type`, `status`, `workspace_name`, `capacity_name`, `risk_score`, `issue_count` |

3. **Add edge** → `RelatedTo`:

   | Field | Value |
   |---|---|
   | Source table | `gold_graph_edges` |
   | Source | `EstateNode` on `source_id` → `node_id` |
   | Target | `EstateNode` on `target_id` → `node_id` |
   | Properties | `relationship`, `source_type`, `target_type`, `is_lineage` |

4. **Save**, then switch to **Query** mode → select the **EstateNode** / **RelatedTo** components →
   **Run query**. The graph materialises (the internal store fills on save/run — that's expected; the
   source tables stay in `fabric_arch_review_lh`).

**Explore it**

- **Path query** for lineage: Start `EstateNode` filter `nodeType = Capacity` → End `EstateNode`
  (optionally filter `nodeType = Report`), Direction `->`, Trail, **1–4 hops** → draws the
  Capacity → Workspace → SemanticModel → Report chains.
- **Add filter** to declutter the full graph: `nodeType`, `status = red`, or `risk_score > 20`;
  filter edge `relationship` (`hosts`/`contains`/`administers`/`feeds`).

**Notes**

- Styling is **per node type**. Because `EstateNode` is a single type, all nodes share one colour;
  use **filters** / **path queries** to focus. For a colour-per-type map, model each `nodeType`
  (Capacity, Workspace, SemanticModel, …) as its own node against its dimension table
  (`gold_capacities`, `gold_workspaces`, …) — more setup, prettier result.
- The data is **run-partitioned**: `gold_graph_edges` holds edges for every run, so multiple runs
  overlay. Filter to a single run (or rebuild the node/edge on a latest-run view) to keep it a single
  clean estate.

---

## ⚙️ Optional: Azure (ARM) auth for capacity Pause/Resume detection

The opt-in Pause/Resume scan
([../collectors/azure_capacity_automation.py](../collectors/azure_capacity_automation.py), enabled with
`CAPACITY_AUTO_PAUSE_CONFIGURED=true`) reads **Azure Resource Manager** — subscriptions,
`Microsoft.Fabric/capacities`, Automation runbooks, and Logic App workflows — to verify that an
auto-pause/auto-resume schedule exists. That control plane is **not** reachable with the Fabric/Power BI
token, so it needs an Azure identity with *Reader* on the subscription that hosts the capacity.

> **⚠️ This scan only works when you run the framework on a local machine** (the CLI /
> `scripts/powershell/01_collect.ps1` flow), **not inside Fabric.** Microsoft Fabric's notebook identity cannot mint
> an Azure ARM token — `notebookutils.credentials.getToken("azuremanagement")` returns
> `400 REQUEST_INVALID_RESOURCE_NONRETRIABLE` ("azuremanagement is not a valid resource"). Fabric only
> issues `pbi`, `storage`, and `keyvault` audience tokens. **In Fabric, leave
> `CAPACITY_AUTO_PAUSE_CONFIGURED=false`** — the collector then skips cleanly. Run the local CLI flow if
> you need the Pause/Resume (COST-002) verification.

**On a local machine the framework uses an Azure identity you sign in with** (e.g. `az login` /
`DefaultAzureCredential`) — there is no service principal and no Key Vault secret. Grant that identity
Azure **Reader** on the capacity's subscription, set `CAPACITY_AUTO_PAUSE_CONFIGURED=true`, and the local
collect stage acquires the ARM token automatically.

> If you do **not** run the Pause/Resume scan (`CAPACITY_AUTO_PAUSE_CONFIGURED` stays `false`), no Azure
> ARM access is needed at all.

**Grant Reader to the signing-in identity:**

```bash
az role assignment create \
  --assignee <identity-object-id> \
  --role "Reader" \
  --scope /subscriptions/<subscription-id>
```

Scope it to the capacity's resource group instead of the whole subscription if the capacity, Automation
accounts, and Logic Apps all live in one RG. **Reader** is enough because the collector issues only `GET`
ARM calls (including `runbooks/.../content` and `workflows`) — no write/contributor role and no
data-plane (storage/DB) access is involved.

> **Cross-subscription / cross-tenant note:** your local Azure sign-in (`az login`) determines which
> tenant/subscriptions the ARM token can read. If the capacity lives in a subscription that identity
> cannot read, sign in to the tenant that owns it before running the local collect. The framework does
> not ship a separate ARM service principal.

---

## 🎨 Workspace logo (optional)

A ready-made logo ships with the accelerator so you can brand the Fabric workspace you run the
review in — it makes the workspace easy to spot in the Fabric portal.

<p align="center">
  <img src="assets/FAR_logo.png" alt="Fabric Architecture Review logo" width="160">
</p>

The asset lives at [`fabric/assets/FAR_logo.png`](assets/FAR_logo.png) — a square, transparent‑background
PNG (Fabric crops it to a rounded tile automatically).

**Set it as the workspace image:**

1. **Download** the logo: open [`fabric/assets/FAR_logo.png`](assets/FAR_logo.png) on GitHub → **Download raw file** (or right‑click the preview above → *Save image as…*).
2. In Fabric, open your workspace → **Workspace settings** (the gear, or *⋯ → Workspace settings*).
3. Under **General → Workspace image** (also labelled *About* in some tenants), choose **Upload**, pick `FAR_logo.png`, then **Apply / Save**.

The logo is purely cosmetic — it has no effect on the review, the data collected, or the report.

---

## 🔖 Versioning & updates

Every release is stamped with a date-based version in the repo-root [`VERSION`](../VERSION) file
(e.g. `2026.06.0`). When you deploy with `setup.ipynb`, that version is recorded into a small
`meta_deployment` Delta table in your Lakehouse, so the solution always knows which release it was
deployed from.

**Where you see the version**

- **Power BI report — Home page**: a slim banner along the foot shows `FAR v<version> — up to date`,
  or **`Update available: v<newer>`** when a newer release has been published. It refreshes every time
  the pipeline runs the gold stage (`gold_release` table).
- **Markdown / PDF report**: the cover page lists the **FAR version** used to generate it.

**How update detection works**

The gold stage reads your deployed version from `meta_deployment` and best-effort fetches the latest
`VERSION` from GitHub (`main`). If GitHub is unreachable the banner simply shows your deployed version
with no update notice — nothing breaks offline.

**Pinned releases — every run uses the version you deployed**

`setup.ipynb` first clones the branch tip only to read its `VERSION`, then resolves an immutable
**release ref** (`v<version>`, e.g. `v2026.06.0`) and pins the deployment to it: it checks out that tag,
records `git_ref` + `git_sha` into `meta_deployment`, and passes `GITHUB_REF` to every pipeline stage.
Each stage notebook clones **that exact ref** at runtime rather than `main`'s tip — so the code that runs
is frozen at the deployed release and can never drift onto a newer version's schema until you deliberately
update. If the matching tag does not exist, deploy falls back to the branch tip with a printed warning.

- To deploy an **older** release, set `RELEASE_TAG` in `setup.ipynb` (e.g. `RELEASE_TAG = "v2026.05.0"`)
  before running — leave it blank to track the tag matching the current `VERSION`.

**How to update (FUAM-style — your data is safe)**

1. Re-run [`setup.ipynb`](setup.ipynb). It re-clones the latest release and redeploys the notebooks,
   pipeline, semantic model and report, then re-stamps `meta_deployment` with the new version and ref.
2. Your Lakehouse data is **preserved** — every `gold_*` history table and all prior runs stay intact
   (the deploy only ever *bootstraps* missing tables, it never clobbers data).
3. **Plan for customizations:** if you hand-edited the deployed notebooks, pipeline or report, those
   artifacts are overwritten on update — re-apply your changes afterwards (or keep them in a fork).

> Maintainers: when cutting a release, bump the `VERSION` file **and** create + push the matching git
> tag (`git tag v<VERSION> && git push origin v<VERSION>`) in the same release. The tag is what deployed
> instances pin to; without it, deploys fall back to the unpinned branch tip.

---

## 🩺 Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `git clone` exit 128 when running a stage notebook standalone | Stage notebooks are meant to run **inside the pipeline** (it injects `GITHUB_REF`). Run the pipeline, or set `GITHUB_BRANCH` / `GITHUB_REF` to a valid ref. |
| pip prints a dependency-resolver warning, then continues | Harmless — Fabric's `trident_env` ships pinned packages. pip still exits 0; the next line confirms "Repo + deps ready". |
| *Semantic Models* / *Model detail* / *Model internals* pages are empty | The `vertipaq_stats` collector did not run, or no models were resident in memory at scan time. |
| *Overview* maturity radar shows a "can't display this visual" placeholder | Your tenant blocks custom visuals. The radar is the only custom visual; every other visual still renders. Allow the Microsoft-certified *Radar Chart* in **Admin portal → Tenant settings → custom visuals**, or ignore it. |
| Model + report show no data after deploy | Expected on first deploy — they light up after the pipeline runs **once** (the `04_Gold` stage fills the Delta tables). |
| Pause/Resume (COST-002) shows "skipped" | Expected in Fabric — the ARM scan is local-only. Run the local CLI flow for that check. |

---

For the local workflow, configuration reference, rule catalog, and contribution guide, see the
[main README](../README.md). Microsoft, Fabric, and Power BI are trademarks of the Microsoft group of
companies.

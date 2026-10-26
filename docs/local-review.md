<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Run a local review

**Goal:** collect review evidence and produce findings and a written report on
your workstation. This does not deploy Fabric Gold, Power BI, owner reporting or
the Data Agent. For those, use [Fabric deployment](../fabric/DEPLOYMENT.md).

## 1. Prepare

- Install Python 3.11+ and Git.
- Have an account in the tenant being reviewed with the
  [permissions for your selected collectors](auth-setup.md).
- Azure CLI is recommended for sign-in; interactive browser authentication is
  also supported.
- For the Windows PDF route below, install Node.js and Puppeteer.
  The Linux/macOS route produces Markdown without a PDF dependency.

## 2. Install

Clone the repository, then choose your shell. To use a published release, replace
the branch with its tag, such as `v2026.10.1`.

```text
git clone --branch main https://github.com/microsoft/fabric-architecture-review.git
cd fabric-architecture-review
```

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env.far)) { Copy-Item .env.example .env.far }
```

**Linux/macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env.far || cp .env.example .env.far
```

For the Windows PDF route, install Puppeteer once:

```text
npm install -g puppeteer
```

## 3. Set scope and sign in

Edit `.env.far` (see the commented [.env.example](../.env.example)):

| Setting | First-run value |
| --- | --- |
| `TENANT_ID` | The tenant you are authorized to review |
| `WORKSPACE_IDS` | One known test-workspace GUID |
| `CLIENT_NAME`, `ENGAGEMENT_NAME`, `REVIEWER_NAME` | Labels for the report |
| `OUTPUT_DIR` | A dedicated output folder for this engagement |

Declare workspace requirements and thresholds in [config/policy.yaml](../config/policy.yaml)
and [config/thresholds.yaml](../config/thresholds.yaml); see [Configure the review](far-policy-configuration.md).
Never commit `.env.far` or collected outputs. **Blank `WORKSPACE_IDS` means no workspace
filter**, not an empty review.

Sign in to the same tenant:

```text
az login --tenant <tenant-id>
```

For an unattended identity or Conditional Access restrictions, use the
[authentication reference](auth-setup.md), not credentials embedded in commands.

## 4. Run in order

Run Collect, Analyze and Report in order. FAR collects the architectural metadata
needed for the enabled rules. It does not save raw code, DAX/M expressions, parameter
values or notebook outputs. Optional FUAM/FCA connections add evidence;
missing evidence is never a passing result.

**Windows PowerShell** — stop if a stage reports failure:

```powershell
.\scripts\powershell\01_collect.ps1
.\scripts\powershell\02_analyze.ps1
.\scripts\powershell\03_report.ps1
```

**Linux/macOS** — the chain stops on the first failure. Replace `output/far` in the
last command if you changed `OUTPUT_DIR`:

```bash
bash scripts/bash/01_collect.sh &&
bash scripts/bash/02_analyze.sh &&
python -m reports.render_report --findings output/far/findings.json --out output/far/report.md --raw-dir output/far/raw
```

The Linux/macOS route renders Markdown; the bundled PDF generator currently
requires the Windows npm-global Puppeteer installation.

## 5. Check the result

In your configured output folder, verify:

| Output | What to check |
| --- | --- |
| `raw/` | Evidence belongs to the intended scope; inspect collection errors |
| `raw/collection_plan.json` | Which sources were collected and any coverage gaps |
| `review_config.json`, `run_manifest.json` | Settings and review version used by the run |
| `findings.json` | Distinguish failed checks from missing evidence |
| `report.md` | Expected engagement, findings and recommendations |
| `far-review.pdf` (Windows PDF route) | Open the rendered report |

If PDF rendering fails but `fabric_arch_review.html` exists, open it in a browser
and **Print > Save as PDF**. The `03_report` scripts still report failure without
a nonempty generated PDF; repair the renderer and rerun Report for a clean result.

**Next:** [interpret the findings](methodology.md#use-the-results), agree changes,
then rerun the same scope after testing improvements.

Need individual stages, thresholds, logos or deeper collection?
Use the [local/reference guide](../REFERENCE.md).
If a stage fails, stop and follow [support guidance](../SUPPORT.md); do not publish
a stale report from a previous run.

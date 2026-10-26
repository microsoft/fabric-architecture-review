<!--
Copyright (c) Microsoft Corporation. All rights reserved.
Licensed under the MIT License. See LICENSE.TXT in the project root for full license information.
-->

# Fabric Architecture Review app

The Fabric Architecture Review app is a Fabric-hosted workbench for exploring
the latest architectural review. Its pages are: an overview (score, the five
dimensions, priority findings and workspace hotspots), the findings queue, a 3D
estate map of workspaces and their items, the items each finding points at,
capacities, tenant settings, source coverage, and Data Agent chat.

The app consumes review metadata and aggregate engineering statistics. It does not query customer business rows, notebook source, or notebook output.

**Central reviewers only:** owner RLS does not secure this app, its governance
model or its central Data Agent. App filters are navigation, not authorization.
Do not rebind `reviewModel` to the owner model or include this app in an
owner-only audience. For owner access, use the separate
[Workspace Owner report](../../docs/workspace-owner-report.md) and
[Workspace Owner Agent](../../docs/workspace-owner-agent.md).

## Choose your path

| Goal | Start here | Success |
| --- | --- | --- |
| Try the UI without a tenant | [Run locally](#run-locally) | Browser opens an empty preview with `?preview=1` |
| Connect a live review | [Prerequisites](#prerequisites), then [semantic model](#configure-the-semantic-model) and [chat configuration](#configure-rayfin-and-data-agent-chat) | The intended identity can query the model and published agent |
| Publish the app in Fabric | [Build and test](#build-and-test), then [Deploy](#deploy-to-fabric) | Fabric-hosted app shows the intended review and authorized chat |

Do not deploy or grant permissions just to try the preview. Keep existing live
configuration files when switching between tasks.

## Understand the results

The app reads your governance semantic model. Chat uses your published Data Agent
with the signed-in user's permissions. Configure each item with its actual workspace ID,
even when the model, Agent and app are in different workspaces.

- Only assessed controls affect the score. Missing evidence is not a pass.
- Compare reviews only when their version and configuration match.
- Queries have a 5,000-row limit; use the governance model for larger results.
- **Chat does not inherit page filters.** Include workspace or model IDs in your question.
- Live errors never substitute preview data. The anonymous preview is intentionally empty.

## Prerequisites

For local preview, use **Node.js 22.13 or later in the 22.x series**, npm 10 and
the two example configuration files in [Run locally](#run-locally).
`npm ci` installs both Rayfin and Fabric App Data CLIs; no global install is needed.
The development and build commands generate environment settings and model bindings.
The test commands also generate model bindings from `fabric.yaml` before Vitest starts.

For a live app, also have:

- A completed [in-Fabric deployment and review](../DEPLOYMENT.md), with populated
  Gold tables and the governance semantic model. Local collection alone is insufficient.
- A published central Data Agent, deployed by **05_Agent** after Gold, on a
  [supported capacity with the required tenant settings](../REFERENCE.md#data-agent).
- Azure CLI signed in to the intended tenant for deployment and command-line checks.
- Contributor or Admin access to the app workspace for the **deployer**, and
  Fabric Apps enabled for that identity.
- Access to the governance model, published Data Agent and its attached sources
  for each intended reviewer.

## Microsoft Entra configuration

Create a single-tenant **Single-page application** registration for Data Agent chat:

1. Add delegated Microsoft Fabric permission `DataAgent.Execute.All`.
2. Grant tenant admin consent.
3. Add the local callback URI `http://localhost:5173/auth-callback.html` when local chat is required.
4. After deployment, add `https://<generated-host>.webapp.fabricapps.net/auth-callback.html` as a SPA redirect URI.
5. Do not create or expose a client secret. The browser uses authorization code with PKCE.

For local chat, use `localhost:5173` to match the registered callback. The
`127.0.0.1` address below is for anonymous preview.

## Configure the semantic model

```powershell
cd fabric\app
npm ci
if (-not (Test-Path fabric.yaml)) { Copy-Item fabric.example.yaml fabric.yaml }
az login --tenant <tenant-id>
npx fabric-app-data add semanticModel reviewModel --from-url "<semantic-model-url>"
npx fabric-app-data generate -o src/fabric.generated.ts
npx fabric-app-data query reviewModel --query "EVALUATE ROW(`"connected`", 1)"
```

`fabric.yaml` and `src/fabric.generated.ts` contain tenant-specific bindings and are ignored by Git. The alias must remain `reviewModel` because the live queries use that connection name.

After changing the model binding, rebuild and redeploy the app. The build regenerates
the binding from the active profile in `fabric.yaml`; a CLI query alone does not
verify the model used by an already deployed app.

## Configure Rayfin and Data Agent chat

Create the configuration if missing, then replace the example identifiers:

```powershell
if (-not (Test-Path rayfin\.env)) { Copy-Item rayfin\.env.example rayfin\.env }
```

```dotenv
RAYFIN_PUBLIC_ENTRA_CLIENT_ID=11111111-2222-4333-8444-555555555555
RAYFIN_PUBLIC_DATA_AGENT_ID=66666666-7777-4888-8999-aaaaaaaaaaaa
RAYFIN_PUBLIC_DATA_AGENT_WORKSPACE_ID=bbbbbbbb-cccc-4ddd-8eee-ffffffffffff
RAYFIN_PUBLIC_FRONTEND_PORT=5173
```

Use the workspace that actually contains the published Data Agent. A valid Data Agent ID paired with the wrong workspace returns `InvalidRequestUri`.

These are public identifiers. Never place client secrets, bearer tokens, connection strings, or private keys in `RAYFIN_PUBLIC_*` or `VITE_*` variables. Rayfin adds the app tenant, workspace, item, API URL, and publishable key after app selection or deployment. Generate Vite settings with:

```powershell
npx rayfin env --framework vite
```

Deployment commands (`rayfin login`, `rayfin up`) require an authenticated Azure CLI session (`az login`).

## Run locally

From `fabric/app`, install dependencies and create **both** placeholder
configuration files if missing. Keep existing live configuration unchanged:

```powershell
npm ci
if (-not (Test-Path fabric.yaml)) { Copy-Item fabric.example.yaml fabric.yaml }
if (-not (Test-Path rayfin\.env)) { Copy-Item rayfin\.env.example rayfin\.env }
npm run dev -- --host 127.0.0.1
```

- Open `http://127.0.0.1:5173/?preview=1` for an anonymous, empty preview.
- `fabric.yaml` is needed by the predev generator even for preview; the example
  supplies placeholder bindings, not live data access.
- Live semantic-model acceptance must be performed through the Fabric AppBackend item.
- Production builds never fall back to preview fixtures.

## Validate Data Agent grounding

Test the published endpoint with the same workspace and item IDs used by the browser:

```powershell
npm run agent:ask -- `
    --workspace-id <data-agent-workspace-id> `
    --agent-id <published-data-agent-id> `
    --question "What should we fix first?"
```

The probe tests the published endpoint using the Azure CLI identity; it does
not validate browser sign-in or the Fabric-hosted app. The browser adds
review-grounding instructions, while the probe sends your question directly,
so identical answers are not expected. If their data appears to differ, verify:

1. The endpoint built from the configured IDs matches the published agent's
   **Settings > Model Context Protocol** URL.
2. The configured workspace is the parent workspace of that Data Agent item.
3. The latest review pipeline run populated `gold_run_summary`, `gold_findings`, and `gold_tenant_settings`.
4. The agent was republished after source or instruction changes.
5. The signed-in user can read the agent and all attached sources.

The app displays the returned text without a separate summarization step.

## Build and test

```powershell
npm ci
if (-not (Test-Path fabric.yaml)) { Copy-Item fabric.example.yaml fabric.yaml }
if (-not (Test-Path rayfin/.env)) { Copy-Item rayfin/.env.example rayfin/.env }
npm test
npm run lint
npm run build:fabric
npm audit --audit-level=high
```

Use `npm ci` with the committed lockfile to install the reviewed dependency
versions, including the local deployment CLIs. Most packages use your configured
registry. The Body Parser 2.4.0 and Fast URI 3.1.9 security patches use official
GitHub release archives pinned to immutable commits and lockfile integrity hashes;
installation therefore also requires access to `codeload.github.com`.
This avoids unavailable versions in the Microsoft npm mirror without disabling TLS
verification or downgrading Rayfin. Targeted overrides also pin patched MSAL Node,
Zod and Undici versions. Keep the lockfile and run the checks above after updates.

The tests cover Rayfin's `form_post` request compatibility with MSAL Node 7.
Before deployment, also verify interactive sign-in using `npx rayfin login --select`
and browser sign-in in the app; automated tests do not perform live authentication.

The build generates model bindings, checks TypeScript and bundles the app,
sign-in pages and third-party notices. These local checks do **not** establish
live access or deployment acceptance. For dependency audits and contributor
validation, follow the [contributor checks](../../CONTRIBUTING.md#required-validation).

## Deploy to Fabric

For initial app selection or provisioning:

```powershell
npx rayfin login --select
npx rayfin up --workspace-uri "<fabric-app-workspace-url>" --dry-run --yes
npx rayfin up --workspace-uri "<fabric-app-workspace-url>" --yes
```

For subsequent releases to the configured existing AppBackend, deploy static content only:

```powershell
npx rayfin up staticapp deploy --verbose
```

Open the AppBackend item from the Fabric portal as an intended reviewer. Verify
that it shows the expected review and that chat sign-in and source access work.
Local preview and unit tests do not replace this live acceptance.

Static hosting needs `services.staticHosting.assetAccess` in `rayfin/rayfin.yml`
(this project uses `protected`, so the site is served only to signed-in users) and
the Rayfin packages at `1.36` or later, updated together. When the service reports
that asset access is not applied, run `npx rayfin up` once; it applies the setting and
deploys. It also rewrites `rayfin/rayfin.yml` with your own hosting URL and ports, so
do not commit those changes.
Do not enable `services.functions`; the supported runtime is static hosting
plus direct delegated Data Agent MCP access.

### Chat troubleshooting

- Allow sign-in pop-ups for the Fabric portal and the hosted `fabricapps.net` site.
  A popup error occurs before the question is sent; retry in Edge or Chrome.
- Chat shows sign-in, connection and query progress with elapsed time. Fabric Data Agent
  can take several minutes; the app does not automatically repeat a slow failed request.
- Suggestions query one table: the latest review score, the latest Security dimension
  summary, or up to five SEC-024 results. Free-text questions remain available; prefer
  a specific workspace, rule or small set of findings rather than estate-wide analysis.
- For a Fabric internal error, retain the request ID, retry a narrower question,
  and check the same agent in Fabric. Rerunning the review pipeline does not repair
  an authentication or Data Agent service error.

See the repository [data-safety contract](../../docs/data-safety.md), [contribution guide](../../CONTRIBUTING.md), and [security policy](../../SECURITY.md).

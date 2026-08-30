# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Collect metadata, configuration, and (REST-accessible) metrics from the Fabric tenant.
# DATA SAFETY: Every collector below reads metadata only. Customer data is never queried.
$ErrorActionPreference = "Stop"

Push-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
try {
    $envFile = Join-Path $PWD ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
            $k, $v = $_ -split '=', 2
            [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), 'Process')
        }
    }

    $outDir = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { "output" }
    $rawDir = Join-Path $outDir "raw"
    New-Item -ItemType Directory -Force -Path $rawDir | Out-Null
    Write-Host "Raw output directory: $rawDir" -ForegroundColor Gray
    $collectorFailures = [System.Collections.Generic.List[string]]::new()

    function Invoke-Collector([string]$module, [string]$label) {
        Write-Host ""
        Write-Host "==> $label" -ForegroundColor Cyan
        & python -m $module --output-dir $rawDir
        if ($LASTEXITCODE -ne 0) {
            $collectorFailures.Add("$module (exit $LASTEXITCODE)")
            Write-Host "    $module exited with code $LASTEXITCODE." -ForegroundColor Red
        }
    }

    Invoke-Collector "collectors.tenant_settings"      "Tenant settings (Fabric Admin)"
    Invoke-Collector "collectors.scanner_api"          "Workspace + item metadata (Scanner API)"
    Invoke-Collector "collectors.workspace_inventory"  "Workspace inventory (REST fallback / complement)"
    Invoke-Collector "collectors.git_integration"      "Git integration per workspace"
    Invoke-Collector "collectors.capacity_metrics"     "Capacity inventory + refreshables + workloads"
    Invoke-Collector "collectors.capacity_metrics_app" "Capacity Metrics App semantic model (DAX via executeQueries — opt-in via CAPACITY_METRICS_APP_INSTALLED)"
    Invoke-Collector "collectors.azure_capacity_automation" "Azure-side Pause/Resume automation (opt-in via CAPACITY_AUTO_PAUSE_CONFIGURED)"
    Invoke-Collector "collectors.semantic_models"      "Semantic models + refresh history"
    Invoke-Collector "collectors.semantic_model_definitions" "Semantic model TMDL definitions (Import-mode only; for DirectLake feasibility audit)"
    Invoke-Collector "collectors.vertipaq_stats"       "VertiPaq Analyzer stats per semantic model (Fabric-only; size, cardinality, encoding via semantic-link-labs)"
    Invoke-Collector "collectors.best_practices"       "Best Practice Analyzer + Direct Lake fallback + Delta + capacity readiness (Fabric-only; semantic-link-labs)"
    Invoke-Collector "collectors.lakehouse_warehouse"  "Lakehouses + Warehouses + table metadata"
    Invoke-Collector "collectors.pipelines_notebooks"  "Pipelines + Notebooks + recent job runs"
    Invoke-Collector "collectors.pipeline_definitions" "Pipeline + Notebook definitions (getDefinition)"
    Invoke-Collector "collectors.deployment_pipelines" "Fabric / Power BI deployment pipelines"
    Invoke-Collector "collectors.gateways"             "Data gateways (on-prem / VNet / personal)"
    Invoke-Collector "collectors.realtime_intelligence" "Real-Time Intelligence + Mirrored Databases"
    Invoke-Collector "collectors.activity_logs"        "Admin activity log (last 7 days)"

    if ($collectorFailures.Count -gt 0) {
        throw "Collection incomplete. Failed collectors: $($collectorFailures -join ', ')"
    }

    Write-Host ""
    Write-Host "Collection complete. Raw outputs in $rawDir." -ForegroundColor Green
}
finally {
    Pop-Location
}

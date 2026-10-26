# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Collect metadata, configuration, and (REST-accessible) metrics from the Fabric tenant.
# DATA SAFETY: Every collector below reads metadata only. Customer data is never queried.
$ErrorActionPreference = "Stop"

Push-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
try {
    $envFile = Join-Path $PWD ".env.far"
    if (Test-Path $envFile) {
        Get-Content $envFile | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
            $k, $v = $_ -split '=', 2
            [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), 'Process')
        }
    }

    $outDir = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { "output\far" }
    & python -m orchestration.review_config --out-dir $outDir --replace
    if ($LASTEXITCODE -ne 0) { throw "Invalid review configuration; collection did not start." }
    $env:FAR_CONFIG_SNAPSHOT = (Resolve-Path (Join-Path $outDir "review_config.json")).Path
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

    Invoke-Collector "collectors.far_collection" "Demand-driven architectural metadata (shared local/Fabric plan)"

    if ($collectorFailures.Count -gt 0) {
        throw "Collection incomplete. Failed collectors: $($collectorFailures -join ', ')"
    }

    Write-Host ""
    Write-Host "Collection finished. Review collector warnings for unavailable evidence. Raw outputs in $rawDir."
}
finally {
    Pop-Location
}

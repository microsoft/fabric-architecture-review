# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Evaluate raw outputs against the review checklist and merge findings.
$ErrorActionPreference = "Continue"

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
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Write-Host "Output directory: $outDir" -ForegroundColor Gray

    function Invoke-Analyzer([string]$module, [string]$label, [string]$findingsName) {
        Write-Host ""
        Write-Host "==> $label" -ForegroundColor Cyan
        $outPath = Join-Path $outDir $findingsName
        & python -m $module --raw-dir $rawDir --out $outPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    $module exited with code $LASTEXITCODE — continuing." -ForegroundColor Yellow
        }
    }

    Invoke-Analyzer "analyzers.tenant_settings_review" "Tenant settings baseline" "findings_tenant_settings.json"
    Invoke-Analyzer "analyzers.architecture_review"    "Architecture review"      "findings_architecture.json"
    Invoke-Analyzer "analyzers.performance_review"     "Performance review"       "findings_performance.json"
    Invoke-Analyzer "analyzers.semantic_model_storage_review" "Semantic model storage-mode / DirectLake feasibility" "findings_storage_mode.json"
    Invoke-Analyzer "analyzers.governance_review"      "Governance review"        "findings_governance.json"
    Invoke-Analyzer "analyzers.operational_excellence_review" "Operational Excellence review" "findings_operational_excellence.json"
    Invoke-Analyzer "analyzers.security_review"        "Security review"          "findings_security.json"
    Invoke-Analyzer "analyzers.cost_review"            "Cost review"              "findings_cost.json"
    Invoke-Analyzer "analyzers.notebook_code_review"   "Notebook code smells (heuristic)" "findings_notebook_code.json"
    Invoke-Analyzer "analyzers.best_practices_review"  "Best practices (BPA / Direct Lake / Delta / capacity)" "findings_best_practices.json"

    Write-Host ""
    Write-Host "==> Merging dimension findings..." -ForegroundColor Cyan
    python -m analyzers.merge_findings --out-dir $outDir

    Write-Host ""
    Write-Host "Analysis complete. See $outDir/findings.json." -ForegroundColor Green
}
finally {
    Pop-Location
}

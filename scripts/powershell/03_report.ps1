# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Render the findings to a merged Markdown report and then to PDF.
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

    $outDir       = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { "output" }
    $rawDir       = Join-Path $outDir "raw"
    $findingsPath = Join-Path $outDir "findings.json"
    $reportMd     = Join-Path $outDir "report.md"
    $reportPdf    = Join-Path $outDir "fabric-arch-review.pdf"

    if (-not (Test-Path $findingsPath)) {
        throw "$findingsPath not found. Run scripts/powershell/02_analyze.ps1 first."
    }

    Write-Host "==> Rendering markdown report..." -ForegroundColor Cyan
    python -m reports.render_report `
        --findings $findingsPath `
        --out $reportMd `
        --raw-dir $rawDir

    Write-Host "==> Generating PDF..." -ForegroundColor Cyan
    # Title and footer default to $ENGAGEMENT_NAME and "$CLIENT_NAME — $ENGAGEMENT_NAME" from .env.
    # Override per-run with --title and --footer-label if needed.
    python reports/_generate_pdf.py `
        --input $reportMd `
        --output $reportPdf

    Write-Host ""
    Write-Host "Report: $reportPdf" -ForegroundColor Green
}
finally {
    Pop-Location
}

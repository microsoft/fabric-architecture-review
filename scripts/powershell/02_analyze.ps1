# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Evaluate raw outputs against the review checklist and merge findings.
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
    $configPath = Join-Path $outDir "review_config.json"
    if (-not (Test-Path $configPath -PathType Leaf)) {
        throw "Review configuration not found. Run FAR collection first."
    }
    $env:FAR_CONFIG_SNAPSHOT = (Resolve-Path $configPath).Path
    $rawDir = Join-Path $outDir "raw"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Write-Host "Output directory: $outDir" -ForegroundColor Gray
    Get-ChildItem -Path $outDir -Filter "findings_*.json" -File -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item -Path (Join-Path $outDir "findings.json") -Force -ErrorAction SilentlyContinue
    Remove-Item -Path (Join-Path $outDir "run_manifest.json") -Force -ErrorAction SilentlyContinue
    $analyzerFailures = [System.Collections.Generic.List[string]]::new()
    $expectedOutputs = [System.Collections.Generic.List[string]]::new()

    function Invoke-Analyzer([string]$module, [string]$label, [string]$findingsName) {
        Write-Host ""
        Write-Host "==> $label" -ForegroundColor Cyan
        $outPath = Join-Path $outDir $findingsName
        $expectedOutputs.Add($outPath)
        & python -m $module --raw-dir $rawDir --out-dir $outDir
        if ($LASTEXITCODE -ne 0) {
            $analyzerFailures.Add("$module (exit $LASTEXITCODE)")
            Write-Host "    $module exited with code $LASTEXITCODE." -ForegroundColor Red
        }
    }

    Invoke-Analyzer "analyzers.far_review" "FAR architectural profile" "findings_architecture.json"

    $invalidOutputs = [System.Collections.Generic.List[string]]::new()
    foreach ($path in $expectedOutputs) {
        if (-not (Test-Path $path -PathType Leaf)) {
            $invalidOutputs.Add("$path (missing)")
            continue
        }
        try {
            $rawJson = Get-Content $path -Raw
            $payload = $rawJson | ConvertFrom-Json
            if (-not $rawJson.TrimStart().StartsWith("[") -or @($payload).Count -eq 0) {
                $invalidOutputs.Add("$path (not a nonempty JSON list)")
            }
        }
        catch {
            $invalidOutputs.Add("$path ($($_.Exception.Message))")
        }
    }
    if ($analyzerFailures.Count -gt 0 -or $invalidOutputs.Count -gt 0) {
        throw "Analysis incomplete. Failed analyzers: $($analyzerFailures -join ', '); invalid outputs: $($invalidOutputs -join ', ')"
    }

    Write-Host ""
    Write-Host "==> Merging dimension findings..." -ForegroundColor Cyan
    & python -m analyzers.merge_findings --out-dir $outDir
    $mergedPath = Join-Path $outDir "findings.json"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $mergedPath -PathType Leaf)) {
        throw "Finding merge failed with exit code $LASTEXITCODE."
    }
    $mergedJson = Get-Content $mergedPath -Raw
    $mergedPayload = $mergedJson | ConvertFrom-Json
    if (-not $mergedJson.TrimStart().StartsWith("[") -or @($mergedPayload).Count -eq 0) {
        throw "Finding merge did not produce a nonempty JSON list."
    }

    Write-Host ""
    Write-Host "Analysis complete. See $outDir/findings.json." -ForegroundColor Green
}
finally {
    Pop-Location
}

#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Collect metadata, configuration, and (REST-accessible) metrics from the Fabric tenant.
# DATA SAFETY: Every collector below reads metadata only. Customer data is never queried.
# bash equivalent of scripts/01_collect.ps1
set -uo pipefail

# cd to repo root (two levels up: scripts/bash -> scripts -> repo root)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# Load .env (KEY=VALUE lines, skipping comments/blank lines) into the environment.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        export "${key// /}=${value}"
    done < <(grep -E '^[[:space:]]*[^#].*=' .env)
    set +a
fi

OUT_DIR="${OUTPUT_DIR:-output}"
RAW_DIR="$OUT_DIR/raw"
mkdir -p "$RAW_DIR"
echo "Raw output directory: $RAW_DIR"
collector_failures=()

invoke_collector() {
    local module="$1" label="$2"
    echo ""
    echo "==> $label"
    if python -m "$module" --output-dir "$RAW_DIR"; then
        return 0
    else
        local rc=$?
        collector_failures+=("$module (exit $rc)")
        echo "    $module exited with code $rc." >&2
    fi
}

invoke_collector "collectors.tenant_settings"           "Tenant settings (Fabric Admin)"
invoke_collector "collectors.scanner_api"               "Workspace + item metadata (Scanner API)"
invoke_collector "collectors.workspace_inventory"       "Workspace inventory (REST fallback / complement)"
invoke_collector "collectors.git_integration"           "Git integration per workspace"
invoke_collector "collectors.capacity_metrics"          "Capacity inventory + refreshables + workloads"
invoke_collector "collectors.capacity_metrics_app"      "Capacity Metrics App semantic model (DAX via executeQueries — opt-in via CAPACITY_METRICS_APP_INSTALLED)"
invoke_collector "collectors.azure_capacity_automation" "Azure-side Pause/Resume automation (opt-in via CAPACITY_AUTO_PAUSE_CONFIGURED)"
invoke_collector "collectors.semantic_models"           "Semantic models + refresh history"
invoke_collector "collectors.semantic_model_definitions" "Semantic model TMDL definitions (Import-mode only; for DirectLake feasibility audit)"
invoke_collector "collectors.vertipaq_stats"            "VertiPaq Analyzer stats per semantic model (Fabric-only; size, cardinality, encoding via semantic-link-labs)"
invoke_collector "collectors.best_practices"            "Best Practice Analyzer + Direct Lake fallback + Delta + capacity readiness (Fabric-only; semantic-link-labs)"
invoke_collector "collectors.lakehouse_warehouse"       "Lakehouses + Warehouses + table metadata"
invoke_collector "collectors.pipelines_notebooks"       "Pipelines + Notebooks + recent job runs"
invoke_collector "collectors.pipeline_definitions"      "Pipeline + Notebook definitions (getDefinition)"
invoke_collector "collectors.deployment_pipelines"      "Fabric / Power BI deployment pipelines"
invoke_collector "collectors.gateways"                  "Data gateways (on-prem / VNet / personal)"
invoke_collector "collectors.realtime_intelligence"     "Real-Time Intelligence + Mirrored Databases"
invoke_collector "collectors.activity_logs"             "Admin activity log (last 7 days)"

if (( ${#collector_failures[@]} > 0 )); then
    printf 'Collection incomplete. Failed collectors: %s\n' "$(IFS=', '; echo "${collector_failures[*]}")" >&2
    exit 1
fi

echo ""
echo "Collection complete. Raw outputs in $RAW_DIR."

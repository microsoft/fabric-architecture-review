#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Collect demand-driven architectural metadata and configuration from the Fabric tenant.
# DATA SAFETY: Every collector below reads metadata only. Customer data is never queried.
# bash equivalent of scripts/01_collect.ps1
set -uo pipefail

# cd to repo root (two levels up: scripts/bash -> scripts -> repo root)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# Load .env.far (KEY=VALUE lines, skipping comments/blank lines) into the environment.
if [[ -f .env.far ]]; then
    set -a
    # shellcheck disable=SC1091
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        export "${key// /}=${value}"
    done < <(grep -E '^[[:space:]]*[^#].*=' .env.far)
    set +a
fi

OUT_DIR="${OUTPUT_DIR:-output/far}"
python -m orchestration.review_config --out-dir "$OUT_DIR" --replace || exit 1
export FAR_CONFIG_SNAPSHOT="$PWD/$OUT_DIR/review_config.json"
if [[ "$OUT_DIR" = /* ]]; then export FAR_CONFIG_SNAPSHOT="$OUT_DIR/review_config.json"; fi
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

invoke_collector "collectors.far_collection" "Demand-driven architectural metadata (shared local/Fabric plan)"

if (( ${#collector_failures[@]} > 0 )); then
    printf 'Collection incomplete. Failed collectors: %s\n' "$(IFS=', '; echo "${collector_failures[*]}")" >&2
    exit 1
fi

echo ""
echo "Collection finished. Review collector warnings for unavailable evidence. Raw outputs in $RAW_DIR."

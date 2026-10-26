#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Evaluate raw outputs against the review checklist and merge findings.
# bash equivalent of scripts/02_analyze.ps1
set -uo pipefail

# cd to repo root (two levels up: scripts/bash -> scripts -> repo root)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# Load .env.far (KEY=VALUE lines, skipping comments/blank lines) into the environment.
if [[ -f .env.far ]]; then
    set -a
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        export "${key// /}=${value}"
    done < <(grep -E '^[[:space:]]*[^#].*=' .env.far)
    set +a
fi

OUT_DIR="${OUTPUT_DIR:-output/far}"
if [[ ! -f "$OUT_DIR/review_config.json" ]]; then
    echo "Review configuration not found. Run FAR collection first." >&2
    exit 1
fi
export FAR_CONFIG_SNAPSHOT="$PWD/$OUT_DIR/review_config.json"
if [[ "$OUT_DIR" = /* ]]; then export FAR_CONFIG_SNAPSHOT="$OUT_DIR/review_config.json"; fi
RAW_DIR="$OUT_DIR/raw"
mkdir -p "$OUT_DIR"
echo "Output directory: $OUT_DIR"
rm -f "$OUT_DIR"/findings_*.json "$OUT_DIR/findings.json" "$OUT_DIR/run_manifest.json"
analyzer_failures=()
expected_outputs=()

invoke_analyzer() {
    local module="$1" label="$2" findings_name="$3"
    echo ""
    echo "==> $label"
    expected_outputs+=("$OUT_DIR/$findings_name")
    if python -m "$module" --raw-dir "$RAW_DIR" --out-dir "$OUT_DIR"; then
        return 0
    else
        local rc=$?
        analyzer_failures+=("$module (exit $rc)")
        echo "    $module exited with code $rc." >&2
    fi
}

invoke_analyzer "analyzers.far_review" "FAR architectural profile" "findings_architecture.json"

invalid_outputs=()
for path in "${expected_outputs[@]}"; do
    if [[ ! -f "$path" ]]; then
        invalid_outputs+=("$path (missing)")
    elif ! python -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8-sig")); raise SystemExit(0 if isinstance(value, list) and value else 1)' "$path"; then
        invalid_outputs+=("$path (not a nonempty JSON list)")
    fi
done
if (( ${#analyzer_failures[@]} > 0 || ${#invalid_outputs[@]} > 0 )); then
    printf 'Analysis incomplete. Failed analyzers: %s; invalid outputs: %s\n' \
        "$(IFS=', '; echo "${analyzer_failures[*]}")" \
        "$(IFS=', '; echo "${invalid_outputs[*]}")" >&2
    exit 1
fi

echo ""
echo "==> Merging dimension findings..."
python -m analyzers.merge_findings --out-dir "$OUT_DIR"
python -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8-sig")); raise SystemExit(0 if isinstance(value, list) and value else 1)' \
    "$OUT_DIR/findings.json" || { echo "Finding merge did not produce a nonempty JSON list." >&2; exit 1; }

echo ""
echo "Analysis complete. See $OUT_DIR/findings.json."

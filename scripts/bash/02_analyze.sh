#!/usr/bin/env bash
# Evaluate raw outputs against the review checklist and merge findings.
# bash equivalent of scripts/02_analyze.ps1
set -uo pipefail

# cd to repo root (two levels up: scripts/bash -> scripts -> repo root)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# Load .env (KEY=VALUE lines, skipping comments/blank lines) into the environment.
if [[ -f .env ]]; then
    set -a
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        export "${key// /}=${value}"
    done < <(grep -E '^[[:space:]]*[^#].*=' .env)
    set +a
fi

OUT_DIR="${OUTPUT_DIR:-output}"
RAW_DIR="$OUT_DIR/raw"
mkdir -p "$OUT_DIR"
echo "Output directory: $OUT_DIR"

invoke_analyzer() {
    local module="$1" label="$2" findings_name="$3"
    echo ""
    echo "==> $label"
    if ! python -m "$module" --raw-dir "$RAW_DIR" --out "$OUT_DIR/$findings_name"; then
        echo "    $module exited with code $? — continuing."
    fi
}

invoke_analyzer "analyzers.tenant_settings_review"        "Tenant settings baseline"        "findings_tenant_settings.json"
invoke_analyzer "analyzers.architecture_review"           "Architecture review"             "findings_architecture.json"
invoke_analyzer "analyzers.performance_review"            "Performance review"              "findings_performance.json"
invoke_analyzer "analyzers.semantic_model_storage_review" "Semantic model storage-mode / DirectLake feasibility" "findings_storage_mode.json"
invoke_analyzer "analyzers.governance_review"             "Governance review"               "findings_governance.json"
invoke_analyzer "analyzers.operational_excellence_review" "Operational Excellence review"   "findings_operational_excellence.json"
invoke_analyzer "analyzers.security_review"               "Security review"                 "findings_security.json"
invoke_analyzer "analyzers.cost_review"                   "Cost review"                     "findings_cost.json"
invoke_analyzer "analyzers.notebook_code_review"          "Notebook code smells (heuristic)" "findings_notebook_code.json"
invoke_analyzer "analyzers.best_practices_review"         "Best practices (BPA / Direct Lake / Delta / capacity)" "findings_best_practices.json"

echo ""
echo "==> Merging dimension findings..."
python -m analyzers.merge_findings --out-dir "$OUT_DIR"

echo ""
echo "Analysis complete. See $OUT_DIR/findings.json."

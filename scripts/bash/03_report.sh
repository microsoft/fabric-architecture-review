#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Render the findings to a merged Markdown report and then to PDF.
# bash equivalent of scripts/03_report.ps1
set -euo pipefail

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
FINDINGS_PATH="$OUT_DIR/findings.json"
REPORT_MD="$OUT_DIR/report.md"
REPORT_PDF="$OUT_DIR/fabric-arch-review.pdf"

if [[ ! -f "$FINDINGS_PATH" ]]; then
    echo "$FINDINGS_PATH not found. Run scripts/bash/02_analyze.sh first." >&2
    exit 1
fi
rm -f "$REPORT_MD" "$REPORT_PDF"

echo "==> Rendering markdown report..."
python -m reports.render_report \
    --findings "$FINDINGS_PATH" \
    --out "$REPORT_MD" \
    --raw-dir "$RAW_DIR"
[[ -s "$REPORT_MD" ]] || { echo "Markdown report generation did not produce a nonempty report." >&2; exit 1; }

echo "==> Generating PDF..."
# Title and footer default to $ENGAGEMENT_NAME and "$CLIENT_NAME — $ENGAGEMENT_NAME" from .env.
# Override per-run with --title and --footer-label if needed.
python reports/_generate_pdf.py \
    --input "$REPORT_MD" \
    --output "$REPORT_PDF"
[[ -s "$REPORT_PDF" ]] || { echo "PDF generation did not produce a nonempty report." >&2; exit 1; }

echo ""
echo "Report: $REPORT_PDF"

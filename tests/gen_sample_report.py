# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate the committed sample report from the golden findings.

Produces a brand-free Markdown + PDF report under ``samples/`` using the
fully synthetic sample fixture (``tests/fixtures/sample``) and the frozen golden
findings (``tests/fixtures/sample/golden``). No real tenant, customer, or
reviewer details are used.

    python -m tests.gen_sample_report

If Node.js/Puppeteer are unavailable, the PDF step degrades to a self-contained
HTML next to the target path (see reports/_generate_pdf.py).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Anonymized, brand-free report metadata. Set BEFORE importing the renderer so
# its internal load_dotenv() (override=False) cannot pull real .env values.
_SAMPLE_ENV = {
    "ENGAGEMENT_NAME": "Fabric Architecture Review - Sample",
    "CLIENT_NAME": "Contoso",
    "REVIEWER_NAME": "Fabric Review Team",
    "REVIEW_DATE": "2026-01-15",
    "TENANT_ID": "00000000-0000-0000-0000-000000000000",
    "REPORT_BRAND": "",
    "REPORT_LOGO": "",
    "FOOTER_LABEL": "Contoso - Fabric Architecture Review (Sample)",
    "ACTIVITY_LOG_DAYS": "7",
}
os.environ.update(_SAMPLE_ENV)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reports.render_report import render  # noqa: E402
from reports._generate_pdf import generate_pdf  # noqa: E402
from tests._analyzers import FIXTURE_RAW, GOLDEN_DIR, REPO_ROOT  # noqa: E402

SAMPLES_DIR = REPO_ROOT / "samples"
TEMPLATES_DIR = REPO_ROOT / "reports" / "templates"

# Any GUID-shaped token (incl. the all-zero placeholder) -> masked, so the
# committed sample never shows even a synthetic-but-real-looking identifier.
GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
MASK = "***"

# One-line disclaimer injected into the committed sample so readers know the
# masking is sample-only — real engagement reports retain per-resource IDs.
SAMPLE_NOTE = (
    "> **Sample report.** All workspace, dataset, and tenant identifiers have been "
    "masked (`***`) for public distribution. A real engagement run retains the actual "
    "per-resource IDs so every finding is directly actionable.\n"
)


def _mask_guids(obj):
    """Recursively replace GUIDs with *** in any string within a JSON value."""
    if isinstance(obj, str):
        return GUID_RE.sub(MASK, obj)
    if isinstance(obj, list):
        return [_mask_guids(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _mask_guids(v) for k, v in obj.items()}
    return obj


def _trim(findings: list) -> list:
    """Keep a compact but representative subset: every ``fail`` (the value of a
    review) plus at most one ``pass`` and one ``info`` per dimension."""
    kept: list = [f for f in findings if f.get("status") == "fail"]
    seen: dict = {}
    for f in findings:
        status = f.get("status")
        if status in ("pass", "info"):
            key = (f.get("dimension"), status)
            if seen.get(key, 0) < 1:
                kept.append(f)
                seen[key] = seen.get(key, 0) + 1
    return kept


def main() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Load golden findings, trim to a compact subset, and mask every GUID.
    findings: list = []
    for src in sorted(GOLDEN_DIR.glob("findings_*.json")):
        findings.extend(json.loads(src.read_text(encoding="utf-8")))
    findings = _mask_guids(_trim(findings))

    work = Path(tempfile.mkdtemp(prefix="fabric_sample_"))
    findings_json = work / "findings.json"
    findings_json.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Sample findings: {len(findings)} (trimmed from golden, GUIDs masked)")

    # 2) Render the markdown report (diagrams + scope come from the fixture raw).
    report_md = SAMPLES_DIR / "report.md"
    render(findings_json, report_md, TEMPLATES_DIR, raw_dir=FIXTURE_RAW)

    # 3) Mask any GUIDs that came from raw-derived sections (scope, diagrams,
    #    VertiPaq). Diagrams carry no GUIDs, so this never touches mermaid syntax.
    text = GUID_RE.sub(MASK, report_md.read_text(encoding="utf-8"))

    # 3b) Insert the sample disclaimer right after the first H1 heading.
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("# "):
            lines.insert(i + 1, "\n" + SAMPLE_NOTE)
            break
    report_md.write_text("".join(lines), encoding="utf-8")
    print(f"Markdown: {report_md}")

    # 4) Render the PDF (brand-free; HTML fallback if Node is missing).
    report_pdf = SAMPLES_DIR / "fabric-arch-review-sample.pdf"
    out = generate_pdf(
        input_md=report_md,
        output_pdf=report_pdf,
        title="Fabric Architecture Review - Sample",
        logo_path=None,
        footer_label="Contoso - Fabric Architecture Review (Sample)",
        brand="",
    )
    print(f"PDF/HTML: {out}")


if __name__ == "__main__":
    main()

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generic Markdown -> HTML -> Puppeteer -> PDF generator with Mermaid + configurable branding.

Any markdown report (executive summary, findings, recommendations, or a merged
document) can be rendered to PDF. The cover logo and brand label are fully
configurable via the REPORT_LOGO / REPORT_BRAND environment variables (or the
--logo / --brand flags); no branding is hard-coded.

Usage:
    python reports/_generate_pdf.py \
        --input output/report.md \
        --output output/fabric-arch-review.pdf \
        --title "Fabric Architecture Review"
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
DEFAULT_LOGO = HERE / "images" / "logo.png"


def _build_html(md_text: str, title: str, logo_b64: str | None, brand: str = "") -> str:
    # Extract mermaid blocks and replace with div placeholders so Mermaid.js can render them.
    mermaid_blocks: list[str] = []

    def replace_mermaid(match: re.Match) -> str:
        idx = len(mermaid_blocks)
        mermaid_blocks.append(match.group(1).strip())
        return f'<div class="mermaid mermaid-diagram mermaid-{idx}" id="mermaid-{idx}">\n{mermaid_blocks[idx]}\n</div>'

    md_text = re.sub(r"```mermaid\s*\n(.*?)```", replace_mermaid, md_text, flags=re.DOTALL)

    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists", "md_in_html"],
        output_format="html5",
    )

    if logo_b64:
        logo_html = (
            f'<div class="cover-logo"><img src="data:image/png;base64,{logo_b64}" alt="{brand}" /></div>'
        )
        html_body = re.sub(r"(<h1[\s>])", logo_html + r"\1", html_body, count=1)

    def build_cover(match: re.Match) -> str:
      opening = match.group(1)
      metadata = match.group(2)
      metadata_rows = "".join(
        f'<div class="cover-meta-row">{line.strip()}</div>'
        for line in metadata.splitlines()
        if line.strip()
      )
      return (
        '<div class="cover-page">'
        f'{opening}'
        f'<div class="cover-meta">{metadata_rows}</div>'
        '</div>'
      )

    html_body = re.sub(
      r'((?:<div class="cover-logo">.*?</div>\s*)?<h1[^>]*>.*?</h1>)\s*<p>(<strong>Engagement:</strong>.*?<strong>Document status:</strong>[^<]*)</p>\s*<hr>',
      build_cover,
      html_body,
      count=1,
      flags=re.DOTALL,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  @page {{ size: letter; margin: 34mm 19mm 22mm 19mm; }}
  body {{
    font-family: 'Segoe UI', 'Segoe UI Web', -apple-system, sans-serif;
    font-size: 10pt; line-height: 1.52; color: #201F1E; margin: 0; padding: 0;
    widows: 3; orphans: 3;
  }}
  .cover-logo {{ margin-bottom: 16px; }}
  .cover-logo img {{ height: 28px; width: auto; }}
  img {{ max-width: 100%; height: auto; page-break-inside: avoid; break-inside: avoid; }}
      h1 {{ font-family: 'Segoe UI Semibold', 'Segoe UI', sans-serif; font-weight: 600;
        font-size: 25pt; color: #201F1E; line-height: 1.16;
        margin-top: 24px; margin-bottom: 10px;
        page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; }}
  h1:first-of-type {{ font-size: 26pt; margin-top: 0; }}
  h2 {{ font-family: 'Segoe UI Semibold', 'Segoe UI', sans-serif; font-weight: 600;
      font-size: 15.5pt; color: #201F1E; line-height: 1.2;
      margin-top: 24px; margin-bottom: 8px;
      padding-bottom: 5px; border-bottom: 1px solid #D2D2D2;
        page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; }}
  h3 {{ font-family: 'Segoe UI Semibold', 'Segoe UI', sans-serif; font-weight: 600;
      font-size: 12pt; color: #201F1E; margin-top: 16px; margin-bottom: 6px;
        page-break-after: avoid; break-after: avoid-page; page-break-inside: avoid; }}
  h4 {{ font-family: 'Segoe UI', sans-serif; font-weight: 700;
      font-size: 10pt; color: #201F1E; margin-top: 12px; margin-bottom: 4px;
        page-break-after: avoid; break-after: avoid-page; }}
  /* Keep first paragraph / list / table after a heading bonded to it */
  h2 + p, h3 + p, h4 + p,
  h2 + ul, h3 + ul, h4 + ul,
  h2 + ol, h3 + ol, h4 + ol,
  h2 + table, h3 + table, h4 + table {{ page-break-before: avoid; break-before: avoid-page; }}
  p {{ margin-top: 0; margin-bottom: 6pt; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0 12px 0; font-size: 9pt;
           page-break-inside: auto; table-layout: auto; }}
  thead {{ display: table-header-group; }}
  tfoot {{ display: table-footer-group; }}
  tr {{ page-break-inside: avoid; break-inside: avoid; }}
    th {{ background-color: #505050; color: #FFFFFF; padding: 6px 8px; text-align: left;
        font-family: 'Segoe UI Semibold', 'Segoe UI', sans-serif; font-weight: 600;
      font-size: 8.9pt; border: 1px solid #505050;
        word-wrap: break-word; overflow-wrap: break-word; }}
    td {{ padding: 6px 8px; border: 1px solid #D2D2D2; font-size: 9pt; vertical-align: top;
        word-wrap: normal; overflow-wrap: normal; }}
  /* Keep code-like tokens (paths, identifiers, API routes) on a single line.
     Allows the column to widen rather than breaking values mid-token. */
  td code, th code {{ white-space: nowrap; word-break: keep-all; overflow-wrap: normal; }}
  tr:nth-child(even) {{ background-color: #F8F8F8; }}
  .exec-summary-table, .backlog, .compact-table {{ page-break-inside: avoid; break-inside: avoid; }}
  .exec-summary-table table, .backlog table, .compact-table table {{ page-break-inside: avoid; break-inside: avoid; }}
  /* Compact backlog / priority tables */
  .backlog table, .compact-table table {{ font-size: 8.75pt; margin: 6px 0 10px 0; }}
  .backlog th, .compact-table th {{ padding: 5px 7px; }}
  .backlog td, .compact-table td {{ padding: 5px 7px; line-height: 1.4; }}
  /* In compact tables, allow code to wrap at natural boundaries if a single
     value is wider than the column, but never mid-token. */
  .compact-table td code {{ white-space: normal; word-break: keep-all; overflow-wrap: normal; }}
    code {{ background-color: #F3F2F1; padding: 1px 4px; border-radius: 2px; font-size: 8.9pt;
          font-family: 'Consolas', 'Courier New', monospace;
          white-space: nowrap; word-break: keep-all; overflow-wrap: normal;
          font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0, "dlig" 0; }}
      pre {{ background-color: #F8F8F8; padding: 11px 13px; border-left: 3px solid #0078D4;
        font-size: 8.5pt; line-height: 1.42; page-break-inside: avoid; margin: 10px 0 12px 0;
         white-space: pre-wrap; word-wrap: break-word; word-break: break-word; overflow-wrap: anywhere;
         font-family: 'Consolas', 'Courier New', monospace;
         font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0, "dlig" 0; }}
  pre code {{ background: none; padding: 0; font-family: inherit;
              white-space: pre-wrap; word-break: normal; overflow-wrap: anywhere;
              font-variant-ligatures: none; font-feature-settings: "liga" 0, "calt" 0, "dlig" 0; }}
  blockquote {{ border-left: 3px solid #0078D4; margin: 8px 0; padding: 7px 14px;
                background-color: #F8FCFF; color: #201F1E; font-size: 10pt; page-break-inside: avoid; }}
  strong {{ font-family: 'Segoe UI Semibold', 'Segoe UI', sans-serif; font-weight: 600; }}
  a {{ color: #00BCF2; text-decoration: none; }}
  hr {{ border: none; border-top: 1px solid #D2D2D2; margin: 20px 0; }}
  /* Diagrams/images: centered, readable, and constrained to the printable page box */
  figure {{ margin: 10px auto 14px auto; text-align: center;
            page-break-inside: avoid; break-inside: avoid; }}
  figcaption {{ font-size: 8.75pt; color: #595959; font-style: italic;
                text-align: center; margin-top: 4px; line-height: 1.35; }}
  .mermaid {{ box-sizing: border-box; text-align: center; margin: 10px auto 14px auto;
              page-break-inside: avoid; break-inside: avoid; max-width: 100%; overflow: visible; }}
  .mermaid svg {{ box-sizing: border-box; max-width: 100% !important; width: auto !important; height: auto !important;
                  max-height: 170mm !important; display: block; margin: 0 auto; }}
  .mermaid-1 svg {{ width: 100% !important; max-height: 160mm !important; }}
  .mermaid-5 svg, .mermaid-6 svg, .mermaid-7 svg, .mermaid-8 svg, .mermaid-9 svg {{
              max-height: 190mm !important; }}
  .mermaid svg text {{ font-family: 'Segoe UI', 'Segoe UI Web', sans-serif !important; }}
  .mermaid svg .nodeLabel, .mermaid svg .edgeLabel, .mermaid svg .cluster-label {{ font-weight: 500; }}
  .page-break {{ page-break-after: always; break-after: page; height: 0; }}
  .page-break + h2 {{ margin-top: 0; }}
  ul, ol {{ padding-left: 24px; margin-top: 4px; margin-bottom: 6pt; }}
  ul {{ list-style-type: disc; }}
  ul ul {{ list-style-type: circle; }}
  ol {{ list-style-type: decimal; }}
  li {{ margin-bottom: 3px; padding-left: 3px; }}
  li > p {{ margin-bottom: 4pt; }}

  /* Cover page */
  .cover-page {{ box-sizing: border-box; height: 205mm; padding-top: 24mm; page-break-after: always; position: relative; }}
  .cover-page::after {{ content: ""; position: absolute; left: 0; right: 0; bottom: 20mm;
                         border-bottom: 4px solid #0078D4; }}
  .cover-page .cover-logo {{ margin-bottom: 24px; }}
  .cover-page .cover-eyebrow {{ color: #00BCF2; font-size: 9.5pt;
                                 font-family: 'Segoe UI Semibold', sans-serif; font-weight: 600;
                                 letter-spacing: 1.5px; text-transform: uppercase; margin: 0 0 8px 0; }}
  .cover-page h1, .cover-page .cover-title {{ font-size: 30pt; line-height: 1.16;
                                                margin: 0 0 26px 0; max-width: 92%; border: 0; padding: 0; }}
  .cover-page .cover-meta {{ font-size: 10.5pt; color: #323130; line-height: 1.65; margin: 0;
                              border-top: 1px solid #D2D2D2; padding-top: 18px; max-width: 78%; }}
  .cover-page .cover-meta-row {{ margin-bottom: 3px; }}
  .cover-page .cover-meta code {{ font-size: 9.5pt; }}

  /* Severity / status badges */
  .sev, .status {{ display: inline-block; padding: 1px 8px; border-radius: 10px;
                    font-family: 'Segoe UI Semibold', sans-serif; font-weight: 600;
                    font-size: 8.5pt; letter-spacing: 0.5px; vertical-align: middle; }}
  .sev-critical {{ background: #A4262C; color: #FFFFFF; }}
  .sev-high     {{ background: #D83B01; color: #FFFFFF; }}
  .sev-medium   {{ background: #F2C811; color: #1B1B1B; }}
  .sev-low      {{ background: #C8C6C4; color: #1B1B1B; }}
  .sev-info     {{ background: #0078D4; color: #FFFFFF; }}
  .status-fail  {{ background: #FDE7E9; color: #A4262C; border: 1px solid #A4262C; }}
  .status-pass  {{ background: #DFF6DD; color: #0B6A0B; border: 1px solid #107C10; }}
  .status-info  {{ background: #FFF4CE; color: #8A6914; border: 1px solid #8A6914; }}

  /* Environment Overview - FUAM-style metric cards */
  .env-overview {{ margin: 10px 0 14px 0; }}
  .env-group {{ margin: 0 0 14px 0; page-break-inside: avoid; break-inside: avoid; }}
  .env-group-title {{ font-family: 'Segoe UI Semibold', sans-serif; font-weight: 600;
                       font-size: 9pt; letter-spacing: 0.8px; text-transform: uppercase;
                       color: #605E5C; margin: 0 0 6px 0; }}
  .env-cards {{ display: flex; flex-wrap: wrap; gap: 7px; }}
  .env-card {{ box-sizing: border-box; flex: 1 1 0; min-width: 36mm;
                background: #F8FCFF; border: 1px solid #E1DFDD; border-left: 3px solid #8A8886;
                border-radius: 4px; padding: 9px 11px 8px 11px;
                page-break-inside: avoid; break-inside: avoid; }}
  .env-num {{ font-family: 'Segoe UI Semibold', 'Segoe UI', sans-serif; font-weight: 600;
               font-size: 19pt; line-height: 1.05; color: #201F1E; }}
  .env-label {{ font-family: 'Segoe UI Semibold', sans-serif; font-weight: 600;
                 font-size: 9pt; color: #323130; margin-top: 3px; }}
  .env-sub {{ font-size: 7.75pt; color: #797775; margin-top: 1px; line-height: 1.25; }}
  .env-info {{ border-left-color: #0078D4; }}
  .env-good {{ border-left-color: #107C10; }}
  .env-warn {{ border-left-color: #F2C811; }}
  .env-bad  {{ border-left-color: #A4262C; }}
  .env-info .env-num {{ color: #0B5394; }}
  .env-good .env-num {{ color: #0B6A0B; }}
  .env-bad  .env-num {{ color: #A4262C; }}
</style>
</head>
<body>
{html_body}
<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: 'base',
    themeVariables: {{
      primaryColor: '#e8f4fd',
      primaryBorderColor: '#00BCF2',
      primaryTextColor: '#000000',
      lineColor: '#636466',
      secondaryColor: '#F5F5F5',
      tertiaryColor: '#F8F8F8',
      fontSize: '15px',
      fontFamily: 'Segoe UI, Segoe UI Web, sans-serif'
    }},
    flowchart: {{ curve: 'basis', padding: 10, nodeSpacing: 38, rankSpacing: 46,
                  htmlLabels: false, useMaxWidth: true, diagramPadding: 8 }},
    sequence: {{ actorMargin: 50, messageFontSize: 12, noteFontSize: 12, actorFontSize: 14, useMaxWidth: true }}
  }});
</script>
</body>
</html>"""


def _build_node_script(html_path: Path, pdf_path: Path, title: str, footer_label: str, brand: str = "") -> str:
    html_uri = "file:///" + str(html_path).replace(os.sep, "/")
    header_label = brand or "Fabric Architecture Review"
    return f"""
const puppeteer = require('puppeteer');
(async () => {{
  const browser = await puppeteer.launch({{
    headless: true,
    protocolTimeout: 240000,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--font-render-hinting=none']
  }});
  const page = await browser.newPage();
  await page.setViewport({{ width: 2000, height: 2600, deviceScaleFactor: 3 }});
  await page.goto('{html_uri}', {{ waitUntil: 'networkidle0', timeout: 60000 }});
  await page.waitForFunction(() => {{
    const els = document.querySelectorAll('.mermaid');
    return els.length === 0 || Array.from(els).every(el => el.querySelector('svg'));
  }}, {{ timeout: 60000 }});
  await page.evaluate(() => {{
    document.querySelectorAll('.mermaid svg').forEach(svg => {{
      const container = svg.closest('.mermaid');
      const isPerLayerDiagram = container && container.classList.contains('mermaid-1');
      const isLargeDiagram = container && (
        container.classList.contains('mermaid-5') ||
        container.classList.contains('mermaid-6') ||
        container.classList.contains('mermaid-7') ||
        container.classList.contains('mermaid-8') ||
        container.classList.contains('mermaid-9')
      );
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.removeAttribute('style');
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.style.maxWidth = '100%';
      svg.style.maxHeight = isPerLayerDiagram ? '160mm' : (isLargeDiagram ? '190mm' : '170mm');
      svg.style.width = isPerLayerDiagram ? '100%' : 'auto';
      svg.style.height = 'auto';
      svg.style.display = 'block';
      svg.style.margin = '0 auto';
    }});
  }});
  await new Promise(r => setTimeout(r, 2500));
  await page.pdf({{
    path: String.raw`{pdf_path}`,
    format: 'Letter',
    timeout: 240000,
    margin: {{ top: '34mm', bottom: '22mm', left: '19mm', right: '19mm' }},
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<div style="font-family:Segoe UI,sans-serif;font-size:8pt;color:#605E5C;width:100%;padding:0 19mm;margin-top:10mm;"><div style="border-bottom:1px solid #E1DFDD;padding-bottom:4px;display:flex;justify-content:space-between;"><span>{title}</span><span>{header_label}</span></div></div>',
    footerTemplate: '<div style="font-family:Segoe UI,sans-serif;font-size:8pt;color:#605E5C;width:100%;padding:0 19mm;margin-bottom:4mm;"><div style="border-top:1px solid #E1DFDD;padding-top:4px;display:flex;justify-content:space-between;"><span>{footer_label}</span><span>Page <span class="pageNumber"></span></span></div></div>'
  }});
  console.log('PDF generated successfully');
  await browser.close();
}})();
"""


def _write_html_fallback(html_full: str, output_pdf: Path, reason: str) -> Path:
    """Write a self-contained HTML next to the target PDF when Puppeteer/Node
    is unavailable, and tell the user how to turn it into a PDF by hand.

    Returns the path to the HTML file. Does not raise — the report stage still
    produces a usable, shareable artifact on non-Node machines.
    """
    html_path = output_pdf.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_full, encoding="utf-8")
    print(
        "\n".join(
            [
                f"NOTE: {reason}",
                f"      Wrote a self-contained HTML report instead: {html_path}",
                "      To produce a PDF: open this file in a browser (Chrome/Edge),",
                "      then Print -> 'Save as PDF' (Letter, margins: Default, Background graphics: on).",
                "      Mermaid diagrams render via CDN, so open it while online the first time.",
                "      To get automatic PDF output, install Node.js 18+ and Puppeteer:",
                "          npm install -g puppeteer",
            ]
        ),
        file=sys.stderr,
    )
    return html_path


def generate_pdf(
    input_md: Path,
    output_pdf: Path,
    title: str,
    logo_path: Path | None = None,
    footer_label: str = "Fabric Architecture Review",
    brand: str | None = None,
) -> Path:
    md_text = input_md.read_text(encoding="utf-8")

    brand = brand if brand is not None else os.environ.get("REPORT_BRAND", "")

    if logo_path is None:
        env_logo = os.environ.get("REPORT_LOGO")
        logo_path = Path(env_logo) if env_logo else DEFAULT_LOGO
    logo_b64: str | None = None
    if logo_path and logo_path.exists():
        logo_b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    elif os.environ.get("REPORT_LOGO") or logo_path != DEFAULT_LOGO:
        print(f"WARNING: logo not found at {logo_path} — PDF will render without a cover logo.")

    html_full = _build_html(md_text, title=title, logo_b64=logo_b64, brand=brand)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    # Non-Node environments: degrade gracefully to a self-contained HTML the
    # user can print to PDF from any browser.
    if shutil.which("node") is None:
        return _write_html_fallback(
            html_full, output_pdf, "Node.js (`node`) was not found on PATH."
        )

    tmp_dir = Path(tempfile.gettempdir())
    html_path = tmp_dir / "fabric_arch_review.html"
    node_path = tmp_dir / "gen_pdf_fabric_arch_review.js"

    html_path.write_text(html_full, encoding="utf-8")
    node_path.write_text(
        _build_node_script(html_path, output_pdf, title, footer_label, brand=brand), encoding="utf-8"
    )

    print(f"HTML written to: {html_path}")
    print("Running Puppeteer to generate PDF...")

    npm_modules = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules")
    try:
        result = subprocess.run(
            ["node", str(node_path)],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "NODE_PATH": npm_modules},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return _write_html_fallback(
            html_full, output_pdf, f"Could not run Node/Puppeteer ({exc})."
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # Most common cause on a Node machine without the dependency installed.
        hint = ""
        if "Cannot find module 'puppeteer'" in stderr:
            hint = " (install it with: npm install -g puppeteer)"
        print(f"ERROR: {stderr}", file=sys.stderr)
        print(f"STDOUT: {result.stdout}", file=sys.stderr)
        return _write_html_fallback(
            html_full, output_pdf, f"Puppeteer failed to generate the PDF{hint}."
        )

    print(f"PDF saved to: {output_pdf}")

    try:
        html_path.unlink()
        node_path.unlink()
    except OSError:
        pass

    return output_pdf


def main() -> None:
    load_dotenv()
    default_title = os.environ.get("ENGAGEMENT_NAME") or "Fabric Architecture Review"
    client = os.environ.get("CLIENT_NAME") or "Contoso"
    default_footer = os.environ.get("FOOTER_LABEL") or f"{client} \u2014 {default_title}"
    default_brand = os.environ.get("REPORT_BRAND", "")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to the input markdown file.")
    parser.add_argument("--output", required=True, help="Path to the output PDF file.")
    parser.add_argument(
        "--title",
        default=default_title,
        help="Document title shown in the page header. Defaults to $ENGAGEMENT_NAME or 'Fabric Architecture Review'.",
    )
    parser.add_argument("--logo", default=None, help="Optional path to a PNG cover logo. Defaults to $REPORT_LOGO, else reports/images/logo.png (no logo if absent).")
    parser.add_argument(
        "--brand",
        default=default_brand,
        help="Optional brand / organization label on the cover and page header. Defaults to $REPORT_BRAND (empty = no brand).",
    )
    parser.add_argument(
        "--footer-label",
        default=default_footer,
        help="Text on the left of every page footer. Defaults to $FOOTER_LABEL, then '<CLIENT_NAME> \u2014 <title>'.",
    )
    args = parser.parse_args()

    generate_pdf(
        input_md=Path(args.input),
        output_pdf=Path(args.output),
        title=args.title,
        logo_path=Path(args.logo) if args.logo else None,
        footer_label=args.footer_label,
        brand=args.brand,
    )


if __name__ == "__main__":
    main()

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Write architectural review Markdown from manifest-verified active findings."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256
from reports.architecture_contract import architectural_findings, assessment
from reports.gold_layer import build_gold


def _cell(value) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _environment_overview(raw_dir: Path, findings: list[dict]) -> str:
    tables = build_gold(findings, raw_dir, run_id="local-inventory",
                        run_timestamp="", check_remote=False)
    lines = ["## Architectural inventory", "",
             "| Workspace ID | Workspace | Capacity ID | Items |",
             "|---|---|---|---|"]
    for row in tables["gold_workspaces"]:
        lines.append("| " + " | ".join(_cell("Unknown" if key == "item_count" and row.get(key) is None else row.get(key)) for key in
                     ("workspace_id", "workspace_name", "capacity_id", "item_count")) + " |")
    lines += ["", "### Storage design", "", "| Workspace ID | Model ID | Model | Observed storage mode |",
              "|---|---|---|---|"]
    for row in tables["gold_semantic_models"]:
        lines.append("| " + " | ".join(_cell(row.get(key) or ("Unknown" if key == "storage_mode" else "")) for key in
                     ("workspace_id", "model_id", "model_name", "storage_mode")) + " |")
    lines += ["", "Static metadata describes topology; it does not certify compatibility, performance, or independent verification."]
    return "\n".join(lines)


def render(findings_path: Path, out_path: Path, raw_dir: Path | None = None) -> Path:
    payload = json.loads(Path(findings_path).read_text(encoding="utf-8"))
    findings = payload if isinstance(payload, list) else payload.get("findings") or []
    raw = Path(raw_dir) if raw_dir else Path(findings_path).parent / "raw"
    manifests = [raw / "run_manifest.json", raw.parent / "run_manifest.json"]
    manifest = next((json.loads(p.read_text(encoding="utf-8")) for p in manifests if p.exists()), {})
    configuration_hash = manifest.get("configuration_sha256") or manifest.get("review_configuration_sha256")
    compatible = (manifest.get("profile_id") == PROFILE_ID
                  and manifest.get("profile_sha256") == PROFILE_SHA256 and bool(configuration_hash))
    findings = architectural_findings(findings) if compatible else []
    summary = assessment(findings)
    score = f"{summary['score']}%" if summary["score"] is not None else "No assessable controls"
    lines = ["<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->",
             "# FAR Architecture Review", "", f"Profile: `{PROFILE_ID}`",
             f"Profile hash: `{manifest.get('profile_sha256') or 'unverified'}`",
             f"Configuration hash: `{configuration_hash or 'unavailable'}`", "",
             f"Architectural score: **{score}**",
             f"Assessed controls: {summary['assessed_count']}. "
             "Signals and coverage observations are not scored. Unknown evidence is not pass.", ""]
    if not compatible:
        lines += ["This evidence is not comparable with the current architecture profile. "
                  "Collect a new manifest-verified review before drawing conclusions.", ""]
    lines += ["## Architectural rules", "",
              "| Rule | Kind | Status | Severity | Workspace | Finding | Recommendation |",
              "|---|---|---|---|---|---|---|"]
    for finding in sorted(findings, key=lambda f: (f["rule_id"], str((f.get("evidence") or {}).get("workspace_name") or ""))):
        workspace = (finding.get("evidence") or {}).get("workspace_name") or (finding.get("evidence") or {}).get("workspace_id") or "Tenant-wide"
        cells = [finding.get(key) for key in ("rule_id", "rule_kind", "status", "severity")]
        cells += [workspace, finding.get("title"), finding.get("recommendation")]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    if compatible:
        lines += ["", _environment_overview(raw, findings)]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return Path(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", default="output/far/findings.json")
    parser.add_argument("--out", default="output/far/report.md")
    parser.add_argument("--raw-dir", default="output/far/raw")
    args = parser.parse_args()
    print(render(Path(args.findings), Path(args.out), Path(args.raw_dir)))


if __name__ == "__main__":
    main()

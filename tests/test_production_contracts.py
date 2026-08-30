# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Static contracts for production orchestration and packaging."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent


def test_merge_writes_traceable_run_manifest(tmp_path: Path) -> None:
    from analyzers.merge_findings import merge

    artifact = tmp_path / "findings_architecture.json"
    artifact.write_text(json.dumps([{
        "rule_id": "ARCH-001",
        "dimension": "architecture",
        "severity": "medium",
        "status": "pass",
        "title": "Synthetic finding",
    }]), encoding="utf-8")

    findings_path = merge(tmp_path, run_id="test-run")
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))

    assert findings_path.is_file()
    assert manifest["run_id"] == "test-run"
    assert manifest["status"] == "completed"
    assert manifest["finding_count"] == 1
    assert manifest["status_counts"]["pass"] == 1
    assert len(manifest["rule_catalog_sha256"]) == 64
    assert len(manifest["analyzer_registry_sha256"]) == 64
    assert len(manifest["thresholds_sha256"]) == 64
    assert manifest["artifacts"][0]["name"] == artifact.name


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def _notebook_code(relative_path: str) -> str:
    notebook = json.loads(_text(relative_path))
    return "\n".join(
        "\n".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell.get("cell_type") == "code"
    )


def test_fabric_setup_deploys_complete_ordered_pipeline() -> None:
    code = _notebook_code("fabric/setup.ipynb")

    for notebook in (
        "01_collect.ipynb",
        "02_analyze.ipynb",
        "03_report.ipynb",
        "04_gold.ipynb",
        "05_agent.ipynb",
    ):
        assert notebook in code
    assert 'common["RUN_ID"] = "@pipeline().RunId"' in code
    assert 'activity("02 Analyze", analyze_id, analyze_params, [{"activity": "01 Collect"' in code
    assert 'activity("03 Report", report_id, common, [{"activity": "02 Analyze"' in code
    assert 'activity("04 Gold", gold_id, common, [{"activity": "03 Report"' in code


def test_fabric_setup_resolves_immutable_release_ref() -> None:
    code = _notebook_code("fabric/setup.ipynb")

    assert 'with open(os.path.join(REPO_DIR, "VERSION")' in code
    assert 'GITHUB_REF = RELEASE_TAG or (("v" + _branch_version)' in code
    assert 'checkout", "--force", "FETCH_HEAD"' in code
    assert 'GITHUB_REF = GITHUB_BRANCH' in code


@pytest.mark.parametrize(
    ("relative_path", "failure_marker"),
    [
        ("scripts/powershell/01_collect.ps1", "Collection incomplete"),
        ("scripts/bash/01_collect.sh", "Collection incomplete"),
    ],
)
def test_local_collectors_fail_when_any_probe_fails(
    relative_path: str, failure_marker: str
) -> None:
    script = _text(relative_path)
    assert failure_marker in script
    assert "collectorFailures" in script or "collector_failures" in script


@pytest.mark.parametrize(
    "relative_path",
    ["scripts/powershell/02_analyze.ps1", "scripts/bash/02_analyze.sh"],
)
def test_local_analysis_clears_stale_outputs_and_gates_merge(relative_path: str) -> None:
    script = _text(relative_path)
    assert "findings_*.json" in script
    assert "run_manifest.json" in script
    assert "invalidOutputs" in script or "invalid_outputs" in script
    assert script.index("Analysis incomplete") < script.index("analyzers.merge_findings")


@pytest.mark.parametrize(
    "relative_path",
    ["scripts/powershell/03_report.ps1", "scripts/bash/03_report.sh"],
)
def test_local_report_rejects_stale_or_empty_artifacts(relative_path: str) -> None:
    script = _text(relative_path)
    assert "reportMd" in script or "REPORT_MD" in script
    assert "Remove-Item" in script or "rm -f" in script
    assert "Length" in script or "-s" in script


def test_fabric_report_notebook_fails_closed() -> None:
    code = _notebook_code("fabric/notebooks/03_report.ipynb")
    assert "os.remove(report_md)" in code
    assert "if r.returncode != 0:" in code
    assert "os.path.getsize(report_md) == 0" in code


def test_fabric_gold_notebook_replaces_and_verifies_run_rows() -> None:
    code = _notebook_code("fabric/notebooks/04_gold.ipynb")
    assert 'DeltaTable.forName(spark, name).delete(F.col("run_id") == F.lit(RUN_ID))' in code
    assert 'where(F.col("run_id") == RUN_ID).count()' in code
    assert "if actual_count != expected_count:" in code
    assert code.index("frames = {}") < code.index("DeltaTable.forName(spark, name).delete")


def test_app_builds_always_typecheck() -> None:
    package = json.loads(_text("fabric/app/package.json"))
    for command in (package["scripts"]["build"], package["scripts"]["build:fabric"]):
        assert "tsc -b" in command
        assert "--noCheck" not in command


def test_obsolete_architecture_thresholds_are_removed() -> None:
    thresholds = yaml.safe_load(_text("config/thresholds.yaml"))["architecture"]
    assert "git_coverage_min_ratio" not in thresholds
    assert "import_dominance_ratio" not in thresholds


@pytest.mark.parametrize(
    "relative_path",
    ["fabric/app/auth-callback.html", "fabric/app/popup-relay.html"],
)
def test_standalone_html_has_mit_header(relative_path: str) -> None:
    header = "\n".join(_text(relative_path).splitlines()[:3])
    assert "Copyright (c) Microsoft Corporation" in header
    assert "MIT License" in header
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Static contracts for production orchestration and packaging."""
from __future__ import annotations

import ast
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


def test_release_version_is_consistent_across_tracked_surfaces() -> None:
    version = _text("VERSION").strip()
    npm_version = ".".join(str(int(part)) for part in version.split("."))
    package = json.loads(_text("fabric/app/package.json"))
    package_lock = json.loads(_text("fabric/app/package-lock.json"))

    assert package["version"] == npm_version
    assert package_lock["version"] == npm_version
    assert package_lock["packages"][""]["version"] == npm_version
    assert f'version: "{npm_version}"' in _text("fabric/app/src/lib/data-agent-factory.ts")
    assert _text("CHANGELOG.md").splitlines().count(f"## {version}") == 1
    assert f"<code>{version}</code>" in _text("samples/report.md")


def test_report_describes_evidence_gaps_and_source_collection(tmp_path: Path) -> None:
    from reports.render_report import render

    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps([{
        "rule_id": "PERF-003",
        "dimension": "performance",
        "severity": "info",
        "status": "missing_evidence",
        "title": "Model size evidence unavailable",
        "evidence": {"reason": "Collection unavailable."},
        "recommendation": "Restore collection and rerun the review.",
    }]), encoding="utf-8")
    report = tmp_path / "report.md"

    render(findings, report, ROOT / "reports" / "templates", raw_dir=tmp_path / "raw")
    text = report.read_text(encoding="utf-8")

    assert "| Missing evidence | 1 |" in text
    assert "Review assessment coverage and unresolved outcomes" in text
    assert "the tenant is aligned" not in text
    assert "not measured runtime cost" in text
    assert "notebook source and pipeline definitions" in text
    assert "notebook outputs are not retained" in text
    assert "configured local directory or FAR Lakehouse" in text


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
    assert 'def load_agent_nb():' in code
    assert '"GITHUB_REPO_URL": GITHUB_REPO_URL' in code
    assert '"GITHUB_BRANCH": GITHUB_BRANCH' in code
    assert '"GITHUB_REF": GITHUB_REF' in code
    assert 'load_agent_nb())' in code


def test_data_agent_notebook_pins_tested_preview_sdk() -> None:
    code = _notebook_code("fabric/notebooks/05_agent.ipynb")

    assert "%pip install -q fabric-data-agent-sdk==0.1.30a0" in code


def test_data_agent_notebook_stops_when_dependency_install_fails() -> None:
    import subprocess
    from types import SimpleNamespace
    from unittest.mock import Mock

    notebook = json.loads(_text("fabric/notebooks/05_agent.ipynb"))
    source = next(
        "".join(cell["source"]) for cell in notebook["cells"]
        if "from reports.agent.sdk_deploy import deploy_agent" in "".join(cell["source"])
    )
    install = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and "requirements.txt" in ast.unparse(node.value)
    )

    def fail_install(*args, **kwargs):
        assert kwargs.get("check") is True
        raise subprocess.CalledProcessError(1, args[0])

    runner = Mock(side_effect=fail_install)
    namespace = {
        "subprocess": SimpleNamespace(run=runner),
        "sys": SimpleNamespace(executable="python"),
        "os": SimpleNamespace(path=SimpleNamespace(join=lambda *parts: "/".join(parts))),
        "REPO_DIR": "synthetic-repo",
    }
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(ast.Module(body=[install], type_ignores=[]), "agent-install", "exec"), namespace)
    runner.assert_called_once()


def test_setup_stamps_compilable_data_agent_source() -> None:
    setup_code = _notebook_code("fabric/setup.ipynb")
    function_start = setup_code.index("def load_agent_nb():")
    function_end = setup_code.index("\ncollect_id =", function_start)
    function_code = setup_code[function_start:function_end]
    agent_notebook = json.loads(_text("fabric/notebooks/05_agent.ipynb"))
    namespace = {
        "json": json,
        "GITHUB_REPO_URL": "https://example.invalid/repository.git",
        "GITHUB_BRANCH": "test-branch",
        "GITHUB_REF": "test-ref",
        "DATA_AGENT_NAME": "Custom FAR Agent",
        "SEMANTIC_MODEL_NAME": "Custom FAR model",
        "LAKEHOUSE_NAME": "custom_far_lakehouse",
        "load_nb": lambda _relative_path: agent_notebook,
    }

    exec(function_code, namespace)
    stamped_notebook = namespace["load_agent_nb"]()
    deploy_cell = next(
        cell for cell in stamped_notebook["cells"]
        if "# Deploy + publish the data agent" in "".join(cell.get("source", []))
    )

    ast.parse("".join(deploy_cell["source"]))
    for parameter in ("GITHUB_REPO_URL", "GITHUB_BRANCH", "GITHUB_REF",
                      "DATA_AGENT_NAME", "SEMANTIC_MODEL_NAME", "LAKEHOUSE_NAME"):
        stamped_line = next(
            line for line in deploy_cell["source"] if line.lstrip().startswith(parameter + " ")
        )
        assert stamped_line.endswith("\n")
        assert json.dumps(namespace[parameter]) in stamped_line


def test_fabric_stage_notebooks_invoke_dax_pipeline() -> None:
    collect_code = _notebook_code("fabric/notebooks/01_collect.ipynb")
    analyze_code = _notebook_code("fabric/notebooks/02_analyze.ipynb")

    assert '"collectors.dax_analysis"' in collect_code
    assert '("analyzers.dax_review", "findings_dax.json")' in analyze_code


@pytest.mark.parametrize("stage", [
    "01_collect", "02_analyze", "03_report", "04_gold", "05_agent", "08_owner_agent",
])
@pytest.mark.parametrize("source_kind", ["git", "snapshot"])
def test_stage_checkout_reloads_framework_modules_in_a_warm_session(
    tmp_path: Path, stage: str, source_kind: str,
) -> None:
    import os
    import subprocess
    import sys

    # Windows patch.dict restoration can drop empty values from the native environment.
    subprocess_env = dict(os.environ)
    code = _notebook_code(f"fabric/notebooks/{stage}.ipynb")
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("%"))
    start = code.index("sys.path.insert(0, REPO_DIR)")
    bootstrap = code[start:code.index("os.chdir(REPO_DIR)", start)]
    git_helper = next((
        ast.get_source_segment(code, node) for node in ast.parse(code).body
        if isinstance(node, ast.FunctionDef) and node.name == "_git"
    ), "")
    if source_kind == "git":
        initialized = subprocess.run(
            ["git", "init", "--quiet", str(tmp_path)], capture_output=True, text=True,
            env=subprocess_env,
        )
        assert initialized.returncode == 0, initialized.stdout + initialized.stderr
        subprocess.run([
            "git", "-C", str(tmp_path), "-c", "user.name=FAR Test",
            "-c", "user.email=far-test@example.invalid", "-c", "commit.gpgsign=false",
            "-c", f"core.hooksPath={tmp_path / 'disabled-hooks'}",
            "commit", "--quiet", "--allow-empty", "-m", "Synthetic runtime fixture",
            "-m", "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        ], check=True, capture_output=True, env=subprocess_env)
        revision = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
            env=subprocess_env,
        ).stdout.strip()
    packages = ("collectors", "analyzers", "reports", "orchestration")
    for name in packages:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "__init__.py").write_text('VERSION = "current"\n', encoding="utf-8")
        (directory / "probe.py").write_text('VERSION = "current"\n', encoding="utf-8")
    script = f"""
import importlib, os, subprocess, sys
from types import ModuleType
REPO_DIR = sys.argv[1]
exec({git_helper!r})
sys.path.insert(0, REPO_DIR)
for name in {packages!r}:
    importlib.import_module(name).VERSION = "stale"
    importlib.import_module(name + ".probe").VERSION = "stale"
external = ModuleType("sempy_labs")
sys.modules["sempy_labs"] = external
exec({bootstrap!r})
for name in {packages!r}:
    assert importlib.import_module(name).VERSION == "current", name
    assert importlib.import_module(name + ".probe").VERSION == "current", name + ".probe"
assert sys.modules["sempy_labs"] is external
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, cwd=tmp_path,
        env=subprocess_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if not git_helper:
        return
    if source_kind == "git":
        assert f"Framework revision: {revision}" in result.stdout
    else:
        assert "Framework revision: unavailable (source snapshot has no Git metadata)" in result.stdout


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


def test_every_gold_table_has_run_replacement_key() -> None:
    from reports.powerbi.schema import GOLD_TABLES

    missing = [
        table.name for table in GOLD_TABLES
        if not any(column.name == "run_id" for column in table.columns)
    ]
    assert missing == []


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
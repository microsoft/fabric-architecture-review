# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local collection must not install or invoke Fabric-only analysis libraries."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from collectors import best_practices, vertipaq_stats


ROOT = Path(__file__).resolve().parents[1]


def test_example_env_documents_local_skip_defaults():
    config = dotenv_values(ROOT / ".env.example")
    assert config["VERTIPAQ_STATS_SKIP"] == "true"
    assert config["BEST_PRACTICES_SKIP"] == "true"


@pytest.fixture
def no_labs(monkeypatch):
    import_labs = Mock(side_effect=AssertionError("Semantic Link must not be imported locally"))
    install = Mock(side_effect=AssertionError("Packages must not be installed locally"))
    monkeypatch.setattr(vertipaq_stats, "_import_sempy_labs_with_shim", import_labs)
    monkeypatch.setattr(subprocess, "run", install)
    return import_labs, install


@pytest.mark.parametrize("collector,flag", [
    (vertipaq_stats, "VERTIPAQ_STATS_SKIP"),
    (best_practices, "BEST_PRACTICES_SKIP"),
])
def test_explicit_skip_replaces_stale_evidence_without_labs(
    tmp_path, monkeypatch, no_labs, collector, flag,
):
    monkeypatch.setenv(flag, "true")
    monkeypatch.setitem(sys.modules, "notebookutils", None)
    target = tmp_path / (collector.__name__.split(".")[-1] + ".json")
    target.write_text(json.dumps({"available": True, "models": [{"id": "stale"}]}))
    payload = json.loads(collector.collect(tmp_path).read_text())
    assert payload["available"] is False
    assert payload["skipped"] is True
    assert payload["models"] == []
    for operation in no_labs:
        operation.assert_not_called()


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.parametrize("configured_skip", [None, "false", "true"])
def test_local_launchers_force_skip_after_dotenv(tmp_path, shell, configured_skip):
    executable = shutil.which("pwsh" if shell == "powershell" else "bash")
    if shell == "bash" and sys.platform == "win32":
        executable = shutil.which("bash", path=r"C:\Program Files\Git\bin")
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    suffix = "ps1" if shell == "powershell" else "sh"
    script = tmp_path / "scripts" / shell / f"01_collect.{suffix}"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / shell / script.name, script)
    config = "OUTPUT_DIR=output\n"
    if configured_skip is not None:
        config += f"VERTIPAQ_STATS_SKIP={configured_skip}\nBEST_PRACTICES_SKIP={configured_skip}\n"
    (tmp_path / ".env").write_text(config)
    if shell == "powershell":
        command = [
            executable, "-NoProfile", "-Command",
            "function global:python { "
            "Write-Output ('PROBE|' + $args[1] + '|' + $env:VERTIPAQ_STATS_SKIP + "
            "'|' + $env:BEST_PRACTICES_SKIP); $global:LASTEXITCODE = 0 }; "
            "& ./scripts/powershell/01_collect.ps1",
        ]
    else:
        command = [
            executable, "-c",
            'python() { printf "PROBE|%s|%s|%s\\n" "$2" "$VERTIPAQ_STATS_SKIP" '
            '"$BEST_PRACTICES_SKIP"; }; source scripts/bash/01_collect.sh',
        ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    for collector in ("vertipaq_stats", "best_practices"):
        assert f"PROBE|collectors.{collector}|true|true" in result.stdout
    assert "PROBE|collectors.semantic_models|" in result.stdout


def test_fabric_notebook_still_runs_both_collectors_without_forced_opt_out():
    notebook = json.loads((ROOT / "fabric" / "notebooks" / "01_collect.ipynb").read_text())
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    assert '"collectors.vertipaq_stats"' in source
    assert '"collectors.best_practices"' in source
    assert "VERTIPAQ_STATS_SKIP" not in source
    assert "BEST_PRACTICES_SKIP" not in source

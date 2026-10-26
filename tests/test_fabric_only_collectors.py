# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local launchers and the Fabric notebook run only the shared architectural planner."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETIRED = ("vertipaq_stats", "best_practices", "dax_analysis", "tenant_settings", "dataflows")


@pytest.mark.parametrize("shell", ["powershell", "bash"])
def test_local_launchers_only_invoke_the_shared_planner(tmp_path, shell):
    executable = shutil.which("pwsh" if shell == "powershell" else "bash")
    if shell == "bash" and sys.platform == "win32":
        executable = shutil.which("bash", path=r"C:\Program Files\Git\bin")
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    suffix = "ps1" if shell == "powershell" else "sh"
    script = tmp_path / "scripts" / shell / f"01_collect.{suffix}"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / shell / script.name, script)
    (tmp_path / ".env.far").write_text("OUTPUT_DIR=output\n")
    if shell == "powershell":
        command = [
            executable, "-NoProfile", "-Command",
            "function global:python { "
            "Write-Output ('PROBE|' + $args[1]); "
            "if ($args[1] -eq 'orchestration.review_config') { "
            "New-Item -ItemType Directory -Force $args[3] | Out-Null; "
            "Set-Content (Join-Path $args[3] 'review_config.json') '{}' }; "
            "$global:LASTEXITCODE = 0 }; & ./scripts/powershell/01_collect.ps1",
        ]
    else:
        command = [
            executable, "-c",
            'python() { printf "PROBE|%s\\n" "$2"; }; source scripts/bash/01_collect.sh',
        ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    probes = [line for line in result.stdout.splitlines() if line.startswith("PROBE|")]
    assert "PROBE|collectors.far_collection" in probes
    assert {line.split("|", 1)[1] for line in probes} <= {"orchestration.review_config", "collectors.far_collection"}


def test_fabric_notebook_uses_shared_architectural_planner():
    notebook = json.loads((ROOT / "fabric" / "notebooks" / "01_collect.ipynb").read_text())
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    assert '"collectors.far_collection"' in source
    for name in RETIRED:
        assert f'"collectors.{name}"' not in source
    assert "optimizer" not in source.lower()
    assert "VERTIPAQ" not in source.upper()
    assert "BEST_PRACTICES" not in source.upper()

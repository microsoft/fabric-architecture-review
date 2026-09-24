# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Execute the CI Bash validation command against later broken scripts."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("broken_index", [1, 2])
def test_ci_bash_validation_rejects_broken_later_script(tmp_path, broken_index):
    if os.name == "nt":
        bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        executable = str(bash) if bash.is_file() else None
    else:
        executable = shutil.which("bash")
    if not executable:
        pytest.skip("Bash is not installed")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    command = next(
        step["run"] for step in workflow["jobs"]["python"]["steps"]
        if step.get("name") == "Validate Bash scripts"
    )
    scripts = tmp_path / "scripts/bash"
    scripts.mkdir(parents=True)
    for index in range(3):
        (scripts / f"0{index}.sh").write_text(
            "if true; then\n" if index == broken_index else "echo synthetic\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [executable, "-c", command], cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert f"0{broken_index}.sh" in result.stderr

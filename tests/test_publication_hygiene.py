# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline checks for publishable source headers and clean notebook artifacts."""
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {
    ".py", ".ps1", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".md",
    ".ipynb", ".sh", ".yaml", ".yml", ".j2", ".dax",
}


def tracked_sources() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    )
    return [
        Path(name) for name in result.stdout.decode("utf-8").split("\0")
        if name and Path(name).suffix in SOURCE_SUFFIXES and (ROOT / name).is_file()
    ]


@pytest.mark.parametrize("relative", tracked_sources(), ids=str)
def test_authored_source_has_copyright_and_mit_notice(relative: Path):
    text = (ROOT / relative).read_text(encoding="utf-8")
    if relative.suffix == ".ipynb":
        header = "".join(json.loads(text)["cells"][0]["source"])
    else:
        header = "\n".join(text.splitlines()[:8])
    assert "copyright (c) microsoft corporation" in header.casefold()
    assert "licensed under the mit license" in header.casefold()


@pytest.mark.parametrize(
    "relative", [path for path in tracked_sources() if path.suffix == ".ipynb"],
    ids=str,
)
def test_public_notebooks_have_clean_outputs_and_valid_python(relative: Path):
    notebook = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            assert cell.get("execution_count") is None, f"Executed cell {index}"
            assert not cell.get("outputs"), f"Saved outputs in cell {index}"
            source = "".join(cell["source"])
            if not any(line.lstrip().startswith(("%", "!")) for line in source.splitlines()):
                compile(source, f"{relative}:cell{index}", "exec")

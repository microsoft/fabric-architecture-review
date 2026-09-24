# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Execute notebook parameter export without Fabric, authentication or collectors."""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

from orchestration.notebook_parameters import optional_string


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = ("01_collect.ipynb", "02_analyze.ipynb", "03_report.ipynb")


@pytest.mark.parametrize(
    "path", [ROOT / "fabric" / "setup.ipynb", *sorted((ROOT / "fabric" / "notebooks").glob("*.ipynb"))],
    ids=lambda path: path.name,
)
def test_public_notebook_cell_ids_are_valid_and_unique(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    ids = [cell.get("id") for cell in notebook["cells"]]
    assert all(isinstance(cell_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cell_id)
               for cell_id in ids)
    assert len(ids) == len(set(ids))


def export_parameters(filename, overrides):
    notebook = json.loads((ROOT / "fabric" / "notebooks" / filename).read_text(encoding="utf-8"))
    defaults = next(
        "".join(cell["source"]) for cell in notebook["cells"]
        if "parameters" in cell.get("metadata", {}).get("tags", [])
    )
    namespace = {}
    exec(compile(defaults, filename, "exec"), namespace)
    namespace.update(overrides)
    source = next(
        "".join(cell["source"]) for cell in notebook["cells"]
        if 'os.environ["CLIENT_NAME"]' in "".join(cell.get("source", []))
    )
    # Stop before the first Lakehouse access; execute the actual environment-export code.
    exec(compile(source.split('LH = "/lakehouse/default/Files"')[0], filename, "exec"), namespace)


@pytest.mark.parametrize("filename", NOTEBOOKS)
@pytest.mark.parametrize("value", [None, "", "synthetic label"])
def test_labels_accept_nullable_text_without_retaining_prior_values(monkeypatch, filename, value):
    labels = ("CLIENT_NAME", "ENGAGEMENT_NAME", "REVIEWER_NAME")
    with monkeypatch.context() as environment:
        for name in labels:
            environment.setenv(name, "previous")
        environment.setattr(os, "environ", dict(os.environ))
        export_parameters(filename, dict.fromkeys(labels, value))
        assert all(os.environ[name] == ("" if value is None else value) for name in labels)


@pytest.mark.parametrize("scope", [None, "", "aaaaaaaa-0000-4000-8000-000000000001"])
def test_collection_scope_and_optional_defaults(monkeypatch, capsys, scope):
    monkeypatch.setattr(os, "environ", {"WORKSPACE_IDS": "previous"})
    export_parameters("01_collect.ipynb", {
        "WORKSPACE_IDS": scope, "ACTIVITY_DAYS_LOG": None,
        "CAPACITY_METRICS_APP_INSTALLED": None, "VERTIPAQ_STATS_READ_DATA": None,
    })
    assert os.environ["WORKSPACE_IDS"] == (scope or "")
    assert os.environ["ACTIVITY_DAYS_LOG"] == "7"
    assert os.environ["CAPACITY_METRICS_APP_INSTALLED"] == "false"
    assert os.environ["VERTIPAQ_STATS_READ_DATA"] == "false"
    output = capsys.readouterr().out
    assert ("Workspace scope: no filter" in output) == (not scope)


@pytest.mark.parametrize("value", [0, False, [], {}, ["workspace"]])
def test_invalid_scope_is_rejected_without_broadening(monkeypatch, value):
    monkeypatch.setattr(os, "environ", {"WORKSPACE_IDS": "previous"})
    with pytest.raises(ValueError, match="WORKSPACE_IDS must be a string or null"):
        export_parameters("01_collect.ipynb", {"WORKSPACE_IDS": value})
    assert os.environ["WORKSPACE_IDS"] == "previous"


@pytest.mark.parametrize("value", [None, "", "   ", "0.75", 0])
def test_threshold_null_or_blank_clears_stale_override(monkeypatch, value):
    monkeypatch.setattr(os, "environ", {"GOV_ENDORSEMENT_MIN_RATIO": "0.9"})
    export_parameters("02_analyze.ipynb", {"GOV_ENDORSEMENT_MIN_RATIO": value})
    expected = str(value).strip() if value is not None else ""
    assert os.environ.get("GOV_ENDORSEMENT_MIN_RATIO") == (expected or None)
    assert "None" not in os.environ.values()


def test_optional_string_logs_null_without_echoing_values(capsys):
    assert optional_string("REVIEWER_NAME", None) == ""
    assert "REVIEWER_NAME is null" in capsys.readouterr().out


@pytest.mark.parametrize("filename", (*NOTEBOOKS, "04_gold.ipynb", "06_targeted_review_setup.ipynb"))
def test_changed_notebooks_are_valid_python_without_saved_outputs(filename):
    notebook = json.loads((ROOT / "fabric" / "notebooks" / filename).read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
            assert not cell.get("outputs")
            assert cell.get("execution_count") is None


def test_setup_uses_separate_unversioned_owner_artifact_names():
    notebook = json.loads((ROOT / "fabric" / "setup.ipynb").read_text(encoding="utf-8"))
    parameters = next(
        "".join(cell["source"]) for cell in notebook["cells"]
        if cell["cell_type"] == "code" and 'OWNER_SEMANTIC_MODEL_NAME = "' in "".join(cell["source"])
    )
    names = {"OWNER_SEMANTIC_MODEL_NAME", "OWNER_REPORT_NAME"}
    defaults = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(parameters).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names
    }
    assert defaults == {
        "OWNER_SEMANTIC_MODEL_NAME": "Fabric Arch Review - Workspace Owner Model",
        "OWNER_REPORT_NAME": "Fabric Arch Review - Workspace Owner",
    }

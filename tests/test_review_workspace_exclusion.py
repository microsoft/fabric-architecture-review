# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The workspace the review runs in is always left out of the review."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from collectors import workspace_scope
from collectors._common import filter_workspaces_by_scope
from orchestration.review_workspace import export_review_workspace

ROOT = Path(__file__).resolve().parents[1]

OWN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ROWS = [{"id": OWN, "name": "FAR"}, {"id": OTHER, "name": "Sales"}]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    monkeypatch.delenv("FAR_REVIEW_WORKSPACE_ID", raising=False)


def test_the_review_workspace_is_left_out_of_an_unscoped_review(monkeypatch):
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", OWN.upper())
    assert [w["id"] for w in filter_workspaces_by_scope(ROWS)] == [OTHER]
    assert workspace_scope.is_excluded_workspace({"id": OWN})


def notebook_runtime(context):
    return SimpleNamespace(runtime=SimpleNamespace(context=context))


def test_a_stage_notebook_hands_its_workspace_to_the_library_and_its_child_processes(monkeypatch):
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", "restored-after-the-test")
    assert export_review_workspace(notebook_runtime({"currentWorkspaceId": OWN.upper()})) == OWN
    assert os.environ["FAR_REVIEW_WORKSPACE_ID"] == OWN  # what a subprocess inherits
    assert [w["id"] for w in filter_workspaces_by_scope(ROWS)] == [OTHER]


@pytest.mark.parametrize("context", [{}, {"currentWorkspaceId": None}, {"currentWorkspaceId": "  "}])
def test_an_unreadable_workspace_changes_nothing(monkeypatch, context):
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", OWN)
    assert export_review_workspace(notebook_runtime(context)) == ""
    assert os.environ["FAR_REVIEW_WORKSPACE_ID"] == OWN


def test_a_failing_notebook_runtime_never_breaks_the_stage(monkeypatch):
    class Broken:
        @property
        def runtime(self):
            raise RuntimeError("no session")
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", OWN)
    assert export_review_workspace(Broken()) == ""
    assert os.environ["FAR_REVIEW_WORKSPACE_ID"] == OWN


def test_library_code_never_asks_the_notebook_runtime_for_the_workspace():
    """A child process such as the report renderer must not call notebookutils: Livy failed the whole stage on it."""
    assert "notebookutils" not in Path(workspace_scope.__file__).read_text(encoding="utf-8")
    assert not hasattr(workspace_scope, "_runtime_workspace_id")


@pytest.mark.parametrize("stage", ["01_collect", "02_analyze", "03_report", "04_gold"])
def test_every_pipeline_stage_hands_over_its_workspace_after_notebookutils_is_available(stage):
    notebook = json.loads((ROOT / "fabric" / "notebooks" / f"{stage}.ipynb").read_text(encoding="utf-8"))
    code = [(index, "".join(cell["source"])) for index, cell in enumerate(notebook["cells"]) if cell["cell_type"] == "code"]
    calls = [index for index, source in code if "export_review_workspace(notebookutils)" in source]
    assert len(calls) == 1
    imported = [index for index, source in code if "import notebookutils" in source]
    # Imported in an earlier cell (Collect) or in the same cell, before the call.
    assert imported and min(imported) <= calls[0]
    if imported == [calls[0]]:
        cell = dict(code)[calls[0]]
        assert cell.index("import notebookutils") < cell.index("export_review_workspace(notebookutils)")


def test_explicit_scope_cannot_include_the_review_workspace(monkeypatch):
    monkeypatch.setenv("FAR_REVIEW_WORKSPACE_ID", OWN)
    monkeypatch.setenv("WORKSPACE_IDS", f"{OWN}, {OTHER}")
    assert [w["id"] for w in filter_workspaces_by_scope(ROWS)] == [OTHER]
    monkeypatch.setenv("WORKSPACE_IDS", OWN)
    assert filter_workspaces_by_scope(ROWS) == []
    monkeypatch.setenv("WORKSPACE_IDS", OTHER)
    assert [w["id"] for w in filter_workspaces_by_scope(ROWS)] == [OTHER]


def test_nothing_is_left_out_when_the_review_workspace_is_unknown():
    assert [w["id"] for w in filter_workspaces_by_scope(ROWS)] == [OWN, OTHER]
    assert not workspace_scope.is_review_workspace({"id": None})

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import json
from pathlib import Path

import pytest

from orchestration import project
from orchestration.config_deployment import stamp_notebook_bindings
from orchestration.review_config import capture, local_configuration, read_snapshot

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = [ROOT / "fabric" / "setup.ipynb", *sorted((ROOT / "fabric" / "notebooks").glob("*.ipynb"))]


def test_project_identity_is_local(tmp_path):
    project.validate_checkout(ROOT)
    with pytest.raises(ValueError, match="FAR architecture-review"):
        project.validate_checkout(tmp_path)
    (tmp_path / "config").mkdir()
    marker = tmp_path / "config" / "project.yaml"
    marker.write_text("project_id: another-project\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incompatible FAR"):
        project.validate_checkout(tmp_path)


def test_local_environment_reads_only_the_far_file(tmp_path, monkeypatch):
    name = "FAR_TEST_ENVIRONMENT_MARKER"
    (tmp_path / ".env").write_text(f"{name}=other\nOTHER_ONLY_SETTING=yes\n", encoding="utf-8")
    (tmp_path / ".env.far").write_text(f"{name}=far\n", encoding="utf-8")
    monkeypatch.setattr(project, "ROOT", tmp_path)
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OTHER_ONLY_SETTING", raising=False)
    import os
    with monkeypatch.context() as environment:
        environment.setattr(os, "environ", dict(os.environ))
        project.load_environment()
        assert os.environ[name] == "far"
        assert "OTHER_ONLY_SETTING" not in os.environ


def test_far_names_cover_runtime_configuration_and_customer_entry_points():
    from analyzers.far_profile import PROFILE_ID
    from collectors._common import RUN_SCOPE_ENV

    assert project.PROJECT_ID == "fabric-architecture-review"
    assert project.DEFAULT_OUTPUT_DIR == "output/far"
    assert PROFILE_ID == "far-architecture-v1"
    assert RUN_SCOPE_ENV == "FAR_RUN_WORKSPACE_IDS"
    assert (ROOT / "docs" / "far-policy-configuration.md").is_file()
    for shell in ("bash", "powershell"):
        extension = "sh" if shell == "bash" else "ps1"
        for stage in ("01_collect", "02_analyze", "03_report"):
            source = (ROOT / "scripts" / shell / f"{stage}.{extension}").read_text(encoding="utf-8")
            assert ".env.far" in source
            assert "FAR_CONFIG_SNAPSHOT" in source
    for name in ("README.md", "docs/local-review.md", "docs/far-policy-configuration.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert ".env.far" in text
        assert ".env." + "f" + "os" not in text


def test_valid_artifact_names_are_accepted():
    project.validate_artifact_names({"LAKEHOUSE_NAME": "FAR_Lakehouse", "FAR_CONFIG_PREFIX": "FAR"})


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_artifact_names_are_rejected(name):
    with pytest.raises(ValueError, match="nonempty"):
        project.validate_artifact_names({"LAKEHOUSE_NAME": name})


@pytest.mark.parametrize("prefix", ["", " ", "FAR Review", "../FAR", "FAR\nReview"])
def test_invalid_configuration_prefix_is_rejected(prefix):
    with pytest.raises(ValueError):
        project.validate_artifact_names({"FAR_CONFIG_PREFIX": prefix})


def test_run_snapshot_cannot_be_reused_from_another_project(tmp_path):
    path = capture(tmp_path, local_configuration())
    value = read_snapshot(path)
    assert value["project_id"] == project.PROJECT_ID
    value["project_id"] = "another-project"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_snapshot(path)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.name)
def test_public_notebooks_validate_the_checkout_and_pin_a_ref(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    source = "".join("".join(cell["source"]) for cell in notebook["cells"])
    assert "ENABLE_FAR" not in source and "FAR_ENABLED" not in source
    assert "validate_checkout(Path(REPO_DIR))" in source
    assert "fabric-arch-review" not in source
    assert "/blob/feature/" not in source
    assert "https://github.com/microsoft/fabric-architecture-review.git" in source
    assert "github.com/biro98/" not in source


def test_setup_uses_the_release_tag_or_branch_as_the_ref():
    notebook = json.loads((ROOT / "fabric" / "setup.ipynb").read_text(encoding="utf-8"))
    code = "".join(notebook["cells"][4]["source"])
    assert "GITHUB_REF = RELEASE_TAG or GITHUB_BRANCH" in code
    assert "_branch_version" not in code
    assert "WARNING: tag" not in code


def test_public_deployment_defaults_use_far_and_main():
    expected = {
        "GITHUB_REPO_URL": "https://github.com/microsoft/fabric-architecture-review.git",
        "GITHUB_BRANCH": "main",
        "RELEASE_TAG": "",
        "LAKEHOUSE_NAME": "fabric_arch_review_lh",
        "PIPELINE_NAME": "Fabric Arch Review Pipeline",
        "NOTEBOOK_PREFIX": "FabricArchReview",
        "SEMANTIC_MODEL_NAME": "Fabric Arch Review - Governance",
        "REPORT_NAME": "Fabric Arch Review - Governance",
        "DATA_AGENT_NAME": "Fabric Arch Review - Data Agent",
        "OWNER_SEMANTIC_MODEL_NAME": "Fabric Arch Review - Workspace Owner Model",
        "OWNER_REPORT_NAME": "Fabric Arch Review - Workspace Owner",
        "OWNER_AGENT_NAME": "Fabric Arch Review - Workspace Owner Agent",
        "ONTOLOGY_NAME": "Fabric_Arch_Review_Estate_Ontology",
        "PARENT_PIPELINE_NAME": "Fabric Arch Review - Targeted Review",
        "FAR_CONFIG_PREFIX": "FAR",
        "FAR_THRESHOLDS_LIBRARY": "FAR_Thresholds",
        "FAR_CONNECTIONS_LIBRARY": "FAR_Connections",
        "FAR_ENVIRONMENT_LIBRARY": "FAR_Environment",
        "DEPLOY_GOLD_REPORT": "true",
        "DEPLOY_WORKSPACE_OWNER_REPORT": "false",
        "DEPLOY_ONTOLOGY": "true",
    }
    observed = set()
    for path in NOTEBOOKS:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code" or "parameters" not in cell["metadata"].get("tags", []):
                continue
            for node in ast.parse("".join(cell["source"])).body:
                if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                    continue
                name = node.targets[0].id
                if name in expected:
                    assert ast.literal_eval(node.value) == expected[name], (path.name, name)
                    observed.add(name)
    assert observed == set(expected)


def test_report_and_agent_public_branding_is_far():
    from reports.agent.data_agent import DEFAULT_AI_INSTRUCTIONS
    from reports.agent.owner_agent import DEFAULT_OWNER_AGENT_NAME
    from reports.owner.report import BRAND as owner_brand
    from reports.powerbi.report import BRAND as central_brand

    assert central_brand == "FAR"
    assert owner_brand == "FAR Owner"
    assert DEFAULT_OWNER_AGENT_NAME == "Fabric Arch Review - Workspace Owner Agent"
    assert "FAR Architecture Review Agent" in DEFAULT_AI_INSTRUCTIONS


def test_app_public_identity_and_package_metadata_agree():
    app = ROOT / "fabric" / "app"
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((app / "package-lock.json").read_text(encoding="utf-8"))
    assert package["name"] == lock["name"] == lock["packages"][""]["name"] == "fabric-architecture-review-app"
    assert package["version"] == lock["version"] == lock["packages"][""]["version"]
    assert "name: FAR\n" in (app / "rayfin" / "rayfin.yml").read_text(encoding="utf-8")
    assert f"version: {package['version']}\n" in (app / "rayfin" / "rayfin.yml").read_text(encoding="utf-8")
    for name in ("workbench-parts.tsx", "review-workbench.tsx", "estate-map.tsx"):
        source = (app / "src" / "components" / name).read_text(encoding="utf-8")
        assert "F" + "OS" not in source and "Fabric " + "Observability Suite" not in source


@pytest.mark.parametrize("name", ["setup_private_far_DISPOSABLE.ipynb", "setup_private_test_DISPOSABLE.ipynb"])
def test_private_setup_bootstrap_supports_the_current_stage_notebooks(name, tmp_path):
    import os
    import shutil

    path = ROOT / "fabric" / name
    if not path.is_file():
        pytest.skip("Private setup notebook is intentionally not published.")
    notebook = json.loads(path.read_text(encoding="utf-8"))
    stages = tmp_path / "fabric" / "notebooks"
    stages.mkdir(parents=True)
    for source in (ROOT / "fabric" / "notebooks").glob("*.ipynb"):
        shutil.copyfile(source, stages / source.name)
    namespace = {
        "os": os,
        "GITHUB_TOKEN": "ghp_local_validation_only",
        "REPO_DIR": str(tmp_path),
        "DEPLOY_WORKSPACE_OWNER_REPORT": "true",
    }
    exec(compile("".join(notebook["cells"][5]["source"]), name, "exec"), namespace)
    for stage in stages.glob("*.ipynb"):
        generated = json.loads(stage.read_text(encoding="utf-8"))
        if stage.name != "07_owner_access_sync.ipynb":
            source = "".join("".join(cell["source"]) for cell in generated["cells"])
            assert 'GITHUB_TOKEN = "ghp_local_validation_only"' in source, stage.name
            assert "def _download_private_snapshot():" in source, stage.name
        for cell in generated["cells"]:
            lines = cell["source"]
            if cell["cell_type"] == "code" and not any(line.lstrip().startswith(("%", "!")) for line in lines):
                compile("".join(lines), stage.name, "exec")


def test_notebook_binding_helper_stamps_only_declared_configuration_parameters():
    path = ROOT / "fabric" / "notebooks" / "01_collect.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    stamped = stamp_notebook_bindings(notebook, {
        "FAR_CONFIG_WORKSPACE_ID": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "FAR_CONFIG_WAREHOUSE_ID": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "FAR_THRESHOLDS_LIBRARY": "FAR_Thresholds",
        "FAR_CONNECTIONS_LIBRARY": "FAR_Connections",
        "connections_id": "not-a-notebook-parameter",
    })
    source = next("".join(cell["source"]) for cell in stamped["cells"]
                  if "parameters" in cell.get("metadata", {}).get("tags", []))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert "FAR_CONFIG_WAREHOUSE_ID" not in assignments and "FAR_CONFIG_WORKSPACE_ID" not in assignments
    assert assignments["FAR_THRESHOLDS_LIBRARY"] == "FAR_Thresholds"
    assert "connections_id" not in assignments

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Architecture schema, PBIR bindings, theme and versioned-history contracts."""
import json
import re
from pathlib import Path
from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256
from reports.powerbi.semantic_model import build_bim
from reports.powerbi.report import PAGE_W, PAGE_H, build_parts
from reports.owner.model import build_bim as owner_bim
from reports.owner.report import build_parts as owner_parts
from reports.ontology.ontology import ENTITIES, RELATIONSHIPS
from tests._far_surfaces import FORBIDDEN_TABLES


def test_queryable_models_and_ontology_have_no_optimizer_subjects():
    prohibited = re.compile(r"expression_preview|duration_ms|cardinality|vertipaq|refresh_count|execution_key|cost_estimate", re.I)
    for model in [build_bim("test", "server", "db"), owner_bim("owner", "server", "db")]:
        tables = model["model"]["tables"]
        assert not FORBIDDEN_TABLES & {t["name"] for t in tables}
        assert all(not prohibited.search(c["name"]) for t in tables for c in t["columns"])
    assert not FORBIDDEN_TABLES & {e.table for e in ENTITIES}
    by_name = {e.name: e.table for e in ENTITIES}
    assert not FORBIDDEN_TABLES & ({by_name[r.source] for r in RELATIONSHIPS} | {by_name[r.target] for r in RELATIONSHIPS})
    assert all(e.name != "Finding" for e in ENTITIES)


def test_both_reports_bind_only_existing_architecture_columns():
    for builder, model_builder in [(build_parts, build_bim), (owner_parts, owner_bim)]:
        columns = {t["name"]: {c["name"] for c in t["columns"]}
                   for t in model_builder("test", "server", "db")["model"]["tables"]}
        def check(node):
            if isinstance(node, dict):
                field = node.get("Column")
                if isinstance(field, dict):
                    entity = field.get("Expression", {}).get("SourceRef", {}).get("Entity")
                    if entity:
                        assert field["Property"] in columns[entity]
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)
        for part in builder("model"):
            if "text" in part:
                check(json.loads(part["text"]))


def test_fuam_theme_and_profile_hash_are_explicit_report_filters():
    assert (PAGE_W, PAGE_H) == (1920, 1080)
    documents = {p["path"]: json.loads(p["text"]) for p in build_parts("model") if "text" in p}
    theme = next(v for k, v in documents.items() if k.startswith("StaticResources"))
    assert theme["tableAccent"] == "#248A85" and theme["foreground"] == "#121212"
    pages = [v for k, v in documents.items() if k.endswith("/page.json")]
    assert pages and all(PROFILE_SHA256 in json.dumps(p["filterConfig"]) for p in pages)
    owner_pages = [json.loads(part["text"]) for part in owner_parts("model")
                   if part["path"].endswith("/page.json")]
    assert owner_pages and all(PROFILE_SHA256 in json.dumps(page["filterConfig"]) for page in owner_pages)
    assert "configuration_sha256" in json.dumps(documents)
    measure = next(m for t in build_bim("test", "server", "db")["model"]["tables"]
                   for m in t.get("measures", []) if m["name"] == "Architecture Score")
    assert "HASONEVALUE" in measure["expression"] and "configuration_sha256" in measure["expression"]


def test_app_contract_matches_authoritative_profile():
    text = (Path(__file__).resolve().parents[1] / "fabric/app/src/lib/review-data.ts").read_text()
    assert f'architectureProfile = "{PROFILE_ID}"' in text
    assert f'architectureProfileHash = "{PROFILE_SHA256}"' in text
    rules = re.search(r"activeArchitectureRules = new Set\(\[(.*?)\]\)", text, re.S)[1]
    assert set(re.findall(r'"([A-Z]+-\d+)"', rules)) == ACTIVE_RULE_IDS


def test_local_written_outputs_exclude_stale_subjects_and_show_no_score(tmp_path, monkeypatch):
    from reports.gold_layer import build_gold
    from reports.render_report import render
    from tests._far_surfaces import raw, finding
    raw(tmp_path, monkeypatch)
    findings = [
        finding("ARCH-009", status="fail"),
    ]
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(findings), encoding="utf-8")
    tables = build_gold(findings, tmp_path, run_id="local", run_timestamp="2026-09-01T00:00:00Z", check_remote=False)
    gold_file = tmp_path / "gold.json"
    gold_file.write_text(json.dumps(tables), encoding="utf-8")
    report = render(path, tmp_path / "report.md", raw_dir=tmp_path)
    assert tables["gold_run_summary"][0]["score"] is None
    assert tables["gold_run_summary"][0]["assessed_count"] == 0
    assert "No assessable controls" in report.read_text()
    for output in (gold_file, report):
        text = output.read_text()
        assert "DO-NOT-EXPOSE-EXPRESSION" not in text
        assert not any(name in text for name in FORBIDDEN_TABLES)
    assert (tmp_path / "dax_analysis.json").exists()


def test_local_unverified_history_is_not_a_current_architectural_decision(tmp_path, monkeypatch):
    from reports.render_report import render
    from tests._far_surfaces import finding
    monkeypatch.delenv("FAR_CONFIG_SNAPSHOT", raising=False)
    path = tmp_path / "findings.json"
    path.write_text(json.dumps([finding(title="Historical conclusion", recommendation="Historical optimization")]))
    report = render(path, tmp_path / "report.md", raw_dir=tmp_path / "raw")
    text = report.read_text()
    assert "not comparable" in text
    assert "Historical conclusion" not in text and "Historical optimization" not in text
    assert "No assessable controls" in text


def test_missing_configuration_binding_cannot_publish_current_decisions(tmp_path, monkeypatch):
    from reports.gold_layer import build_gold
    from reports.owner.gold import build_owner_gold
    from tests._far_surfaces import raw, finding
    raw(tmp_path, monkeypatch)
    (tmp_path / "run_manifest.json").write_text(json.dumps({
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
    }))
    tables = build_gold([finding()], tmp_path, run_id="unbound",
                        run_timestamp="2026-09-01T00:00:00Z", check_remote=False)
    assert tables["gold_findings"] == []
    assert tables["gold_run_summary"][0]["score"] is None
    assert not any(build_owner_gold(tables).values())
    model = build_bim("test", "server", "db")
    assert all("ISBLANK" in permission["filterExpression"] for permission in
               model["model"]["roles"][0]["tablePermissions"])

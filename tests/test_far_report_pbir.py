# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""PBIR structure, field-resolution and safety contracts for both FUAM-style reports."""
from __future__ import annotations

import base64
import json
import re
from collections import Counter

import pytest

from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256
from reports.owner import report as owner_report
from reports.owner.gold import PARTIAL_NOTICE, STATIC_NOTICE
from reports.owner.model import ACCESS_TABLE, build_bim as owner_bim
from reports.powerbi import report as central_report
from reports.powerbi.pbir import PAGE_H, PAGE_W
from reports.powerbi.semantic_model import build_bim as central_bim
from tests._far_surfaces import FORBIDDEN_TABLES

MODEL_ID = "00000000-0000-0000-0000-000000000001"
CENTRAL_PAGES = ["Home", "Overview", "Findings", "Action plan", "Items to review", "Workspaces", "Capacities & storage", "Model security", "Tenant settings",
                 "Dependencies", "Rule catalogue", "Evidence coverage", "Trends", "Model roles"]
# Reached only by drilling through from another page: hidden, bound to a field, with a Back button and no slicers.
DRILL_PAGES = {"Model roles"}
OWNER_PAGES = ["Home", "Overview", "My findings", "My workspaces", "Architectural inventory", "Evidence coverage"]
REPORTS = {
    "central": (central_report, central_bim, CENTRAL_PAGES),
    "owner": (owner_report, owner_bim, OWNER_PAGES),
}


class Definition:
    def __init__(self, parts: list[dict]) -> None:
        self.parts = parts
        self.docs = {p["path"]: json.loads(p["text"]) for p in parts if "text" in p}
        self.binary = {p["path"]: base64.b64decode(p["b64"]) for p in parts if "b64" in p}
        self.pages_meta = self.docs["definition/pages/pages.json"]
        self.pages = {n: self.docs[f"definition/pages/{n}/page.json"] for n in self.pages_meta["pageOrder"]}

    def visuals(self, page: str) -> list[dict]:
        prefix = f"definition/pages/{page}/visuals/"
        return [d for p, d in self.docs.items() if p.startswith(prefix)]

    def all_visuals(self):
        for page in self.pages:
            yield from ((page, v) for v in self.visuals(page))


@pytest.fixture(params=sorted(REPORTS))
def built(request):
    module, model_builder, names = REPORTS[request.param]
    return request.param, module, Definition(module.build_parts(MODEL_ID)), model_builder("m", "s", "d"), names


def _fields(node):
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            body = node.get(kind)
            if isinstance(body, dict) and "Property" in body:
                source = body.get("Expression", {}).get("SourceRef", {})
                if "Entity" in source:
                    yield kind, source["Entity"], body["Property"]
        for entry in node.get("From", []) if isinstance(node.get("From"), list) else []:
            yield "Entity", entry["Entity"], None
        for value in node.values():
            yield from _fields(value)
    elif isinstance(node, list):
        for value in node:
            yield from _fields(value)


def _model_index(bim):
    tables = bim["model"]["tables"]
    columns = {t["name"]: {c["name"] for c in t["columns"]} for t in tables}
    measures = {t["name"]: {m["name"] for m in t.get("measures", [])} for t in tables}
    return columns, measures


def test_pbir_structure_and_versions(built):
    _, _, definition, _, names = built
    assert [p["displayName"] for p in definition.pages.values()] == names
    assert list(definition.pages) == definition.pages_meta["pageOrder"]
    assert definition.pages_meta["activePageName"] == definition.pages_meta["pageOrder"][0]
    folders = {p.split("/")[2] for p in definition.docs if p.startswith("definition/pages/") and p.count("/") > 3}
    assert folders == set(definition.pages)
    assert definition.docs["definition/version.json"]["version"] == "2.0.0"
    assert definition.docs["definition/report.json"]["$schema"].endswith("/report/3.3.0/schema.json")
    assert definition.pages_meta["$schema"].endswith("/pagesMetadata/1.0.0/schema.json")
    seen = Counter()
    for name, page in definition.pages.items():
        assert re.fullmatch(r"[0-9a-f]{20}", name) and page["name"] == name
        assert page["$schema"].endswith("/page/2.0.0/schema.json")
        assert (page["width"], page["height"], page["displayOption"]) == (PAGE_W, PAGE_H, "FitToPage")
        assert page["objects"]["displayArea"][0]["properties"]["verticalAlignment"]["expr"]["Literal"]["Value"] == "'Middle'"
        assert "image" in page["objects"]["background"][0]["properties"]
        visuals = definition.visuals(name)
        assert visuals
        ids = [v["name"] for v in visuals]
        assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[0-9a-f]{20}", i) for i in ids)
        seen.update(ids)
        assert len({v["position"]["z"] for v in visuals}) == len(visuals)
        assert len({v["position"]["tabOrder"] for v in visuals}) == len(visuals)
        for visual in visuals:
            assert visual["$schema"].endswith("/visualContainer/2.5.0/schema.json")
            pos = visual["position"]
            assert pos["x"] >= 0 and pos["y"] >= 0 and pos["width"] > 0 and pos["height"] > 0
            assert pos["x"] + pos["width"] <= PAGE_W and pos["y"] + pos["height"] <= PAGE_H
    assert max(seen.values()) == 1


def test_visuals_do_not_overlap(built):
    _, _, definition, _, _ = built
    for name in definition.pages:
        rects = [(v["name"], v["position"]) for v in definition.visuals(name)]
        for i, (a, pa) in enumerate(rects):
            for b, pb in rects[i + 1:]:
                apart = (pa["x"] + pa["width"] <= pb["x"] or pb["x"] + pb["width"] <= pa["x"]
                         or pa["y"] + pa["height"] <= pb["y"] or pb["y"] + pb["height"] <= pa["y"])
                assert apart, f"{definition.pages[name]['displayName']}: {a} overlaps {b}"


def test_navigation_targets_and_home_buttons(built):
    _, _, definition, _, _ = built
    order = definition.pages_meta["pageOrder"]
    home = order[0]
    drill = {n for n, p in definition.pages.items() if p["displayName"] in DRILL_PAGES}
    for name in order:
        links = []
        for visual in definition.visuals(name):
            link = visual["visual"].get("visualContainerObjects", {}).get("visualLink")
            if link:
                props = link[0]["properties"]
                kind = props["type"]["expr"]["Literal"]["Value"]
                if kind == "'Back'":
                    assert name in drill
                    continue
                assert kind == "'PageNavigation'"
                links.append(props["navigationSection"]["expr"]["Literal"]["Value"].strip("'"))
        assert set(links) <= set(order)
        if name == home:
            # A drill-through page is not offered on Home: it means nothing without the model that was selected.
            assert sorted(links) == sorted(n for n in order[1:] if n not in drill)
        else:
            assert links == [home]


def test_drill_through_pages_are_hidden_bound_and_filtered_by_their_field(built):
    _, _, definition, _, _ = built
    for name, page in definition.pages.items():
        if page["displayName"] not in DRILL_PAGES:
            assert "pageBinding" not in page and "visibility" not in page
            continue
        binding = page["pageBinding"]
        assert page["type"] == "Drillthrough" and page["visibility"] == "HiddenInViewMode"
        assert binding["type"] == "Drillthrough" and len(binding["parameters"]) == 1
        parameter = binding["parameters"][0]
        bound = [f for f in page["filterConfig"]["filters"] if f["name"] == parameter["boundFilter"]]
        assert len(bound) == 1 and "filter" not in bound[0] and bound[0]["field"] == parameter["fieldExpr"]
        # How Power BI marks the filter a drill-through page is bound to; without it the page is never offered.
        assert bound[0]["howCreated"] == "Drillthrough" and bound[0]["type"] == "Categorical"
        assert [f["howCreated"] for f in page["filterConfig"]["filters"] if f.get("howCreated")] == ["Drillthrough"]
        assert sum(v["visual"]["visualType"] == "actionButton" for v in definition.visuals(name)) == 2


def test_every_content_page_has_title_kpis_table_and_description(built):
    _, _, definition, _, _ = built
    for name in definition.pages_meta["pageOrder"][1:]:
        visuals = definition.visuals(name)
        kinds = Counter(v["visual"]["visualType"] for v in visuals)
        text = json.dumps(visuals)
        is_drill = definition.pages[name]["displayName"] in DRILL_PAGES
        assert kinds["card"] >= 2 and kinds["tableEx"] >= 1 and kinds["actionButton"] == (2 if is_drill else 1)
        assert kinds["slicer"] == 0 if is_drill else kinds["slicer"] >= 1
        assert kinds["textbox"] == 2 and "Description:" in text
        assert '"fontSize": "14pt"' in text and '"fontWeight": "bold"' in text
        cards = [v for v in visuals if v["visual"]["visualType"] == "card"]
        assert all(v["visual"]["objects"]["labels"][0]["properties"]["fontSize"]["expr"]["Literal"]["Value"] == "18D"
                   for v in cards)
        for chart in (v for v in visuals if v["visual"]["visualType"] in {"barChart", "columnChart", "lineChart"}):
            container = chart["visual"]["visualContainerObjects"]
            assert container["title"][0]["properties"]["show"]["expr"]["Literal"]["Value"] == "true"
            assert container["border"][0]["properties"]["radius"]["expr"]["Literal"]["Value"] == "5D"
            assert container["dropShadow"][0]["properties"]["show"]["expr"]["Literal"]["Value"] == "true"


def test_resources_theme_and_artwork(built):
    _, _, definition, _, _ = built
    report = definition.docs["definition/report.json"]
    assert report["themeCollection"]["baseTheme"]["name"] == "CY24SU10"
    items = {i["name"]: i for i in report["resourcePackages"][0]["items"]}
    assert report["themeCollection"]["customTheme"]["name"] in items
    assert items[report["themeCollection"]["customTheme"]["name"]]["type"] == "CustomTheme"
    for name in items:
        path = f"StaticResources/RegisteredResources/{name}"
        assert path in definition.docs or path in definition.binary
    for path, data in definition.binary.items():
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
    theme = definition.docs[f"StaticResources/RegisteredResources/{report['themeCollection']['customTheme']['name']}"]
    assert theme["name"] == "FAR Tidal" and theme["dataColors"][:3] == ["#248A85", "#3DCBFF", "#3D7DEB"]
    assert theme["foreground"] == "#121212" and theme["tableAccent"] == "#248A85"
    assert theme["visualStyles"]["*"]["*"]["border"][0]["radius"] == 8
    referenced = {n["expr"]["ResourcePackageItem"]["ItemName"] for n in _resource_nodes(definition.docs)}
    assert referenced and referenced <= set(items)


def _resource_nodes(node):
    if isinstance(node, dict):
        if "ResourcePackageItem" in node.get("expr", {}):
            yield node
        for value in node.values():
            yield from _resource_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _resource_nodes(value)


def test_output_is_deterministic(built):
    _, module, definition, _, _ = built
    assert module.build_parts(MODEL_ID) == definition.parts == module.build_parts(MODEL_ID)
    assert all(p.get("text") or p.get("b64") for p in definition.parts)


def test_every_visual_and_filter_field_resolves_to_the_model(built):
    _, _, definition, bim, _ = built
    columns, measures = _model_index(bim)
    resolved = 0
    for name in definition.pages:
        documents = [*definition.visuals(name), definition.pages[name]["filterConfig"]]
        for tag, entity, prop in _fields(documents):
            assert entity in columns, f"{entity} is not a model table"
            resolved += 1
            if tag == "Column":
                assert prop in columns[entity], f"{entity}[{prop}] is not a column"
            elif tag == "Measure":
                assert prop in measures[entity], f"{entity}[{prop}] is not a measure"
    assert resolved > 100


def test_reports_expose_no_raw_evidence_or_forbidden_surfaces(built):
    kind, _, definition, _, _ = built
    text = json.dumps(definition.docs)
    assert not any(name in text for name in FORBIDDEN_TABLES)
    for banned in ("evidence_json", "item_type_counts_json", "expression_preview", "principal_object_id", "owner_access"):
        assert banned not in text
    assert ACCESS_TABLE not in text


def test_every_page_is_locked_to_profile_hash_and_latest_run(built):
    kind, _, definition, _, _ = built
    for name, page in definition.pages.items():
        filters = page["filterConfig"]["filters"]
        # The one filter a drill-through page leaves open is the field it is bound to; it carries no value of its own.
        bound = {p["boundFilter"] for p in page.get("pageBinding", {}).get("parameters", [])}
        assert filters and all(f["isLockedInViewMode"] and f["isHiddenInViewMode"] for f in filters if f["name"] not in bound)
        filters = [f for f in filters if f["name"] not in bound]
        by_column = Counter(f["field"]["Column"]["Property"] for f in filters)
        assert by_column["profile_id"] and by_column["profile_sha256"]
        rendered = json.dumps(filters)
        assert PROFILE_ID in rendered and PROFILE_SHA256 in rendered
        trends = page["displayName"] == "Trends"
        assert (by_column["is_latest"] == 0) == trends
        rules = [f for f in filters if f["field"]["Column"]["Property"] == "rule_id"]
        for rule_filter in rules:
            values = {v[0]["Literal"]["Value"].strip("'")
                      for v in rule_filter["filter"]["Where"][0]["Condition"]["In"]["Values"]}
            assert values == set(ACTIVE_RULE_IDS)
        entities = {entity for _, entity, _ in _fields(definition.visuals(name))}
        has_findings = any(e in {"gold_findings", "owner_findings"} for e in entities)
        assert bool(rules) == has_findings or kind == "owner"


def test_central_findings_trends_and_coverage_pages_keep_their_contract(built):
    kind, _, definition, _, _ = built
    if kind != "central":
        pytest.skip("central only")
    by_name = {p["displayName"]: n for n, p in definition.pages.items()}
    trends = definition.visuals(by_name["Trends"])
    slicers = [v for v in trends if v["visual"]["visualType"] == "slicer"]
    assert any("profile_sha256" in json.dumps(v) for v in slicers)
    assert "configuration_sha256" in json.dumps(trends)
    assert any(v["visual"]["visualType"] in {"lineChart", "lineClusteredColumnComboChart"} for v in trends)
    coverage = json.dumps(definition.visuals(by_name["Evidence coverage"]))
    assert "source_module" in coverage and "FUAM and FCA" in coverage
    findings = json.dumps(definition.visuals(by_name["Findings"]))
    for column in ("rule_id", "title", "status", "severity", "rule_kind", "next_action", "status_meaning",
                   "why_it_matters", "how_to_fix", "workspace_name"):
        assert column in findings
    actions = json.dumps(definition.visuals(by_name["Action plan"]))
    for column in ("next_action", "status_meaning", "reason_code", "fail", "unknown", "missing_evidence"):
        assert column in actions
    rules = json.dumps(definition.visuals(by_name["Rule catalogue"]))
    for column in ("what_it_checks", "why_it_matters", "how_to_fix", "requires", "configure"):
        assert column in rules
    home = json.dumps(definition.visuals(definition.pages_meta["pageOrder"][0]))
    for measure in ("Architecture Score", "Scored Count", "Pass Count", "Fail Count", "Assessment Coverage", "Workspaces"):
        assert measure in home


def test_owner_pages_show_required_notices_and_no_global_score():
    definition = Definition(owner_report.build_parts(MODEL_ID))
    pages = {p["displayName"]: json.dumps(definition.visuals(n)) for n, p in definition.pages.items()}
    for name in ("Home", "Overview", "My findings", "My workspaces", "Evidence coverage"):
        assert PARTIAL_NOTICE in pages[name]
    assert STATIC_NOTICE in pages["Architectural inventory"]
    assert "Architecture Score" not in json.dumps(definition.docs)
    assert all("Scoped " in text for name, text in pages.items() if name != "Architectural inventory")


def test_central_measures_keep_profile_scope_and_are_well_formed():
    bim = central_bim("m", "s", "d")
    columns, _ = _model_index(bim)
    measures = [m for t in bim["model"]["tables"] for m in t.get("measures", [])]
    names = [m["name"] for m in measures]
    assert len(names) == len(set(names))
    assert {"Architecture Score", "Scored Count", "Pass Count", "Fail Count", "Findings", "Score Status"} <= set(names)
    for measure in measures:
        expression = measure["expression"]
        assert expression.count("(") == expression.count(")") and expression.count("{") == expression.count("}")
        for table, column in re.findall(r"\b(gold_\w+)\[(\w+)\]", expression):
            assert column in columns[table], f"{table}[{column}]"
        for reference in re.findall(r"(?<![\w\]])\[([A-Za-z ]+)\]", expression):
            assert reference in names
        if measure["name"] not in {"Score Status", "Architecture Score"}:
            assert PROFILE_ID in expression and PROFILE_SHA256 in expression
    score = next(m for m in measures if m["name"] == "Architecture Score")["expression"]
    assert "HASONEVALUE(gold_findings[run_id])" in score and "HASONEVALUE(gold_findings[configuration_sha256])" in score
    coverage = next(m for m in measures if m["name"] == "Assessment Coverage")["expression"]
    assert "HASONEVALUE(gold_run_summary[run_id])" in coverage and "HASONEVALUE(gold_run_summary[configuration_sha256])" in coverage
    for name in ("Findings", "Fail Count", "Pass Count", "Scored Count", "Attributed Findings"):
        expression = next(m for m in measures if m["name"] == name)["expression"]
        assert all(rule in expression for rule in ACTIVE_RULE_IDS)


def test_owner_measures_are_scoped_and_have_no_score():
    bim = owner_bim("m", "s", "d")
    columns, _ = _model_index(bim)
    measures = [m for t in bim["model"]["tables"] for m in t.get("measures", [])]
    assert {"Scoped failures", "Scoped signals", "Scoped items", "Scoped failing workspaces"} <= {m["name"] for m in measures}
    for measure in measures:
        expression = measure["expression"]
        assert expression.count("(") == expression.count(")")
        for table, column in re.findall(r"\b((?:owner|gold)_\w+)\[(\w+)\]", expression):
            assert column in columns[table]
        if measure["name"] != "Evidence notice":
            assert PROFILE_SHA256 in expression and "is_latest" in expression
        assert "REMOVEFILTERS" not in expression and "score" not in measure["name"].lower()


def test_tenant_settings_page_uses_the_recorded_settings_table():
    from reports.powerbi import report as central
    page = next(page for page in central._pages() if page["display"] == "Tenant settings")
    text = json.dumps(page["visuals"])
    assert "gold_tenant_settings" in text
    assert "Settings Open to Everyone" in text

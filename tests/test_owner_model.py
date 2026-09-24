# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Owner artifact contract tests; live Fabric RLS testing remains a release gate."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from reports.owner import model, report
from reports.owner.schema import OWNER_TABLES


@pytest.fixture
def bim():
    return model.build_bim("Workspace owner", "example.fabric.microsoft.com", "database-id")


def _tables(bim):
    return {table["name"]: table for table in bim["model"]["tables"]}


def _permissions(bim):
    return {
        permission["name"]: permission["filterExpression"]
        for permission in bim["model"]["roles"][0]["tablePermissions"]
    }


def test_only_owner_sources_and_notification_compatible_workspace_alias(bim):
    expected = {
        "owner_workspaces", "owner_reviews", "owner_findings",
        "owner_details", "owner_access",
        "owner_executions", "owner_coverage",
    }
    tables = _tables(bim)
    assert len(tables) == 7
    assert {
        table["partitions"][0]["source"]["entityName"] for table in tables.values()
    } == expected == {table.name for table in OWNER_TABLES}
    assert set(tables) == (expected - {"owner_workspaces"}) | {"gold_workspace_risk"}
    workspace = tables["gold_workspace_risk"]
    assert {"workspace_id", "workspace_name"} <= {col["name"] for col in workspace["columns"]}
    assert workspace["partitions"][0]["source"]["entityName"] == "owner_workspaces"
    schema = {table.name: table for table in OWNER_TABLES}
    for table in tables.values():
        partition = table["partitions"][0]
        assert partition["mode"] == "directLake"
        assert partition["source"]["expressionSource"] == "DatabaseQuery"
        assert partition["source"]["schemaName"] == "dbo"
        physical = schema[partition["source"]["entityName"]]
        assert [(c["name"], c["sourceColumn"], c["dataType"]) for c in table["columns"]] == [
            (c.name, c.name, c.kind) for c in physical.columns
        ]


def test_no_sensitive_governance_columns_or_metadata_imports(bim):
    forbidden = {
        "evidence_json", "expression", "dax_expression", "source_code", "code",
        "client_name", "engagement_name", "reviewer_name", "tenant_id",
    }
    for table in _tables(bim).values():
        assert forbidden.isdisjoint(column["name"] for column in table["columns"])
    tree = ast.parse(Path(model.__file__).read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "reports.powerbi.semantic_model" not in imports
    assert "reports.powerbi.schema" in imports


def test_single_read_role_has_no_members_or_permission_grants(bim):
    roles = bim["model"]["roles"]
    assert len(roles) == 1
    assert roles[0]["name"] == "WorkspaceOwner"
    assert roles[0]["modelPermission"] == "read"
    assert "members" not in roles[0]
    assert not any(key in bim["model"] for key in ("members", "permissions", "dataSources"))
    assert bim["model"]["directLakeBehavior"] == "directLakeOnly"
    assert "SSO disabled" in json.dumps(bim["model"]["annotations"])


def test_every_table_including_entitlements_has_independent_fail_closed_rls(bim):
    permissions = _permissions(bim)
    assert set(permissions) == set(_tables(bim))
    guards = [
        "LOWER(USEROBJECTID())", "UTCNOW()", "NOT ISBLANK(_principal)",
        '_principal <> ""',
        "owner_access[principal_object_id] == _principal",
        "NOT ISBLANK(owner_access[workspace_id])",
        'owner_access[workspace_id] <> ""',
        "NOT ISBLANK(owner_access[refreshed_at])",
        "NOT ISBLANK(owner_access[expires_at])",
        "owner_access[refreshed_at] <= _now",
        "owner_access[refreshed_at] > _now - 1",
        "owner_access[expires_at] > _now",
        "owner_access[expires_at] <= owner_access[refreshed_at] + 1",
    ]
    for table, expression in permissions.items():
        for guard in guards:
            assert guard in expression, (table, guard)
        assert "ALL(" not in expression
        assert "RELATED(" not in expression
        assert "USERNAME(" not in expression
        if table == "owner_access":
            assert "FILTER(" not in expression
            assert "COUNTROWS(" not in expression
        else:
            assert f"VAR _workspace = {table}[workspace_id]" in expression
            assert "NOT ISBLANK(_workspace)" in expression
            assert '_workspace <> ""' in expression
            assert "COUNTROWS(FILTER(owner_access," in expression
            assert "owner_access[workspace_id] == _workspace" in expression
            assert expression.endswith(")) > 0")


def _grant_allows(expression, grant, principal="user-a", now=100.0):
    """Evaluate only the emitted scalar conjunction, not the full DAX language."""
    scalar = expression.split("RETURN", 1)[1].strip()
    scalar = re.sub(r"NOT ISBLANK\(([^)]+)\)", r"not isblank(\1)", scalar)
    scalar = re.sub(r"owner_access\[([a-z_]+)\]", r'grant["\1"]', scalar)
    scalar = " ".join(scalar.replace("&&", "and").replace("<>", "!=").split())
    return eval(  # noqa: S307 - trusted generated predicate, no external input/code
        compile(ast.parse(scalar, mode="eval"), "<owner-rls-predicate>", "eval"),
        {"__builtins__": {}},
        {
            "grant": grant,
            "_principal": principal.lower() if principal else principal,
            "_now": now,
            "isblank": lambda value: value is None,
        },
    )


def _grant(**changes):
    return {
        "workspace_id": "workspace-a",
        "principal_object_id": "user-a",
        "refreshed_at": 99.5,
        "expires_at": 100.5,
        **changes,
    }


@pytest.mark.parametrize("changes,principal,allowed", [
    ({}, "user-a", True),
    ({}, "USER-A", True),
    ({}, "user-b", False),
    ({}, "", False),
    ({}, None, False),
    ({"principal_object_id": "user-b"}, "user-a", False),
    ({"principal_object_id": ""}, "user-a", False),
    ({"principal_object_id": None}, "user-a", False),
    ({"workspace_id": ""}, "user-a", False),
    ({"workspace_id": None}, "user-a", False),
    ({"refreshed_at": None}, "user-a", False),
    ({"expires_at": None}, "user-a", False),
    ({"refreshed_at": 100.1}, "user-a", False),
    ({"refreshed_at": 100.0, "expires_at": 101.0}, "user-a", True),
    ({"expires_at": 100.0}, "user-a", False),
    ({"expires_at": 99.9}, "user-a", False),
    ({"refreshed_at": 99.0, "expires_at": 100.0}, "user-a", False),
    ({"refreshed_at": 98.0, "expires_at": 105.0}, "user-a", False),
    ({"expires_at": 100.50001}, "user-a", False),
])
def test_emitted_entitlement_predicate_boundaries(bim, changes, principal, allowed):
    assert _grant_allows(_permissions(bim)["owner_access"], _grant(**changes), principal) is allowed


def test_empty_stale_or_other_user_entitlements_reveal_no_workspace(bim):
    predicate = _permissions(bim)["owner_access"]
    for grants in (
        [],
        [_grant(expires_at=100.0)],
        [_grant(principal_object_id="user-b")],
        [_grant(refreshed_at=98.0, expires_at=105.0)],
    ):
        assert {g["workspace_id"] for g in grants if _grant_allows(predicate, g)} == set()
    grants = [
        _grant(),
        _grant(workspace_id="workspace-b", principal_object_id="user-b"),
        _grant(workspace_id="workspace-c", expires_at=100.0),
    ]
    assert {g["workspace_id"] for g in grants if _grant_allows(predicate, g)} == {"workspace-a"}


def test_workspace_review_detail_graph_and_disconnected_access(bim):
    expected = {
        ("owner_reviews", "gold_workspace_risk", "workspace_id"),
        ("owner_findings", "owner_reviews", "review_key"),
        ("owner_details", "owner_reviews", "review_key"),
        ("owner_executions", "owner_reviews", "review_key"),
        ("owner_coverage", "owner_reviews", "review_key"),
    }
    relationships = bim["model"]["relationships"]
    assert len(relationships) == len(expected)
    assert {(r["fromTable"], r["toTable"], r["fromColumn"]) for r in relationships} == expected
    tables = _tables(bim)
    for relationship in relationships:
        assert relationship["fromColumn"] == relationship["toColumn"]
        assert relationship["fromCardinality"] == "many"
        assert relationship["toCardinality"] == "one"
        assert relationship["crossFilteringBehavior"] == "oneDirection"
        assert relationship["securityFilteringBehavior"] == "oneDirection"
        assert relationship["isActive"] is True
        for end in ("from", "to"):
            assert relationship[f"{end}Column"] in {
                c["name"] for c in tables[relationship[f"{end}Table"]]["columns"]
            }
    assert tables["owner_access"]["isHidden"] is True


def test_measures_only_aggregate_rls_protected_rows_in_current_context(bim):
    tables = _tables(bim)
    measures = {m["name"]: m["expression"] for m in tables[model.WORKSPACE_TABLE]["measures"]}
    workspace_measures = {
        "Latest review": "MAX(owner_reviews[run_timestamp])",
        "Findings": "COUNTROWS(owner_findings)",
        "Failed findings": 'CALCULATE(COUNTROWS(owner_findings), KEEPFILTERS(owner_findings[status] = "fail"))',
        "Technical details": "COUNTROWS(owner_details)",
    }
    assert {name: measures[name] for name in workspace_measures} == {
        name: f"IF(HASONEVALUE(gold_workspace_risk[workspace_id]), {expression}, BLANK())"
        for name, expression in workspace_measures.items()
    }
    for expression in measures.values():
        assert all(token not in expression for token in ("ALL(", "REMOVEFILTERS", "CROSSFILTER", "TREATAS"))
        referenced = set(re.findall(r"\b(owner_[a-z_]+)\b", expression))
        assert referenced <= set(_permissions(bim))
        assert "owner_access" not in referenced


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_report_keeps_action_pages_and_adds_focused_evidence_pages():
    parts = {p["path"]: json.loads(p["text"]) for p in report.build_parts("owner-model-id")}
    pages = [value for path, value in parts.items() if path.endswith("/page.json")]
    assert [page["displayName"] for page in pages] == [
        "My workspaces", "Findings", "Technical details", "Executions", "Coverage",
    ]
    assert parts["definition/pages/pages.json"]["pageOrder"] == [page["name"] for page in pages]
    for page in pages:
        assert " " not in page["name"]
        filters = page["filterConfig"]["filters"]
        assert len(filters) == 1
        condition = filters[0]
        assert condition["field"]["Column"] == {
            "Expression": {"SourceRef": {"Entity": "owner_reviews"}},
            "Property": "is_latest",
        }
        assert condition["filter"]["From"] == [{"Name": "r", "Entity": "owner_reviews", "Type": 0}]
        assert condition["filter"]["Where"][0]["Condition"]["In"]["Values"] == [[{"Literal": {"Value": "true"}}]]
        assert condition["isLockedInViewMode"] is True
    assert parts["definition.pbir"]["datasetReference"]["byConnection"]["connectionString"] == "semanticmodelid=owner-model-id"
    assert "publicCustomVisuals" not in parts["definition/report.json"]
    assert not any("gold_run_summary" in json.dumps(part) for part in parts.values())


def test_all_report_field_references_exist_and_no_entitlements_are_visualized(bim):
    tables = _tables(bim)
    bindings = 0
    for part in report.build_parts("owner-model-id"):
        for value in _walk(json.loads(part["text"])):
            for kind, collection in (("Column", "columns"), ("Measure", "measures")):
                if kind not in value:
                    continue
                reference = value[kind]
                entity = reference.get("Expression", {}).get("SourceRef", {}).get("Entity")
                if entity is None:
                    continue
                assert entity in tables
                assert entity != "owner_access"
                assert reference["Property"] in {field["name"] for field in tables[entity].get(collection, [])}
                bindings += 1
    assert bindings > 20


def test_report_reuses_helpers_not_governance_pages_or_release_agent_content():
    tree = ast.parse(Path(report.__file__).read_text(encoding="utf-8"))
    imports = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "reports.powerbi.report"
    ]
    assert len(imports) == 1
    assert {alias.name for alias in imports[0].names} == {
        "BAD", "BRAND", "CARD", "PAGE_W", "SEV_HEX", "_action_button",
        "_banner", "_bar", "_card", "_column", "_donut", "_field_slicer",
        "_id", "_info", "_lit", "_measure", "_page_background", "_solid", "_theme", "_visual",
    }
    encoded = json.dumps(report.build_parts("owner-model-id"))
    for text in ("gold_release", "gold_agent_eval", "evidence_json", "dax_expression", "notebook_code"):
        assert text not in encoded


def test_builders_are_deterministic_and_json_serializable(bim):
    assert json.loads(model.build_model_bim_json(
        "Workspace owner", "example.fabric.microsoft.com", "database-id",
    )) == bim
    assert report.build_parts("owner-model-id") == report.build_parts("owner-model-id")
    paths = [part["path"] for part in report.build_parts("owner-model-id")]
    assert len(paths) == len(set(paths))
    assert all("\\" not in path for path in paths)


def test_sql_endpoint_identifiers_are_m_quoted_without_injection():
    document = model.build_bim("Owner", 'host"quoted', "db#(lf)\nnext")
    expression = document["model"]["expressions"][0]["expression"]
    assert 'Sql.Database("host""quoted", "db#(#)(lf)#(lf)next")' in expression


def test_model_rejects_accidental_governance_table_exposure(monkeypatch):
    from reports.powerbi.schema import Column, Table

    monkeypatch.setattr(
        model, "OWNER_TABLES",
        [*OWNER_TABLES, Table("gold_findings", [Column("workspace_id", "string")], "Not sanitized")],
    )
    with pytest.raises(ValueError, match="seven approved owner tables"):
        model.build_bim("Owner", "host", "database")


def _report_pages():
    parts = {part["path"]: json.loads(part["text"]) for part in report.build_parts("owner-model-id")}
    return {
        page["name"]: (
            page,
            [visual for path, visual in parts.items()
             if path.startswith(f"definition/pages/{page['name']}/visuals/")],
        )
        for path, page in parts.items() if path.endswith("/page.json")
    }


def test_dashboard_geometry_is_readable_in_bounds_and_without_overlaps():
    for page, visuals in _report_pages().values():
        assert page["width"] == 1280
        assert page["height"] == 920
        for index, visual in enumerate(visuals):
            box = visual["position"]
            assert box["tabOrder"] == index
            assert box["width"] > 0 and box["height"] > 0
            assert 0 <= box["x"] <= page["width"] - box["width"]
            assert 0 <= box["y"] <= page["height"] - box["height"]
            for other in visuals[index + 1:]:
                candidate = other["position"]
                overlap_x = min(box["x"] + box["width"], candidate["x"] + candidate["width"]) - max(box["x"], candidate["x"])
                overlap_y = min(box["y"] + box["height"], candidate["y"] + candidate["height"]) - max(box["y"], candidate["y"])
                assert overlap_x <= 0 or overlap_y <= 0, (page["name"], visual["name"], other["name"])
        table = next(v for v in visuals if v["visual"]["visualType"] == "tableEx")
        assert table["position"]["width"] >= 1200
        assert table["position"]["height"] >= 180


def test_every_page_has_branded_navigation_synced_scope_and_evidence_context():
    pages = _report_pages()
    for page, visuals in pages.values():
        serialized = json.dumps(visuals)
        assert "WORKSPACE OWNER" in serialized
        assert "Partial assessment - never a full FAR score" in serialized
        assert "daily sync" in serialized
        assert "within 24h" in serialized
        footer = next(v for v in visuals if v["name"] == report._id(page["name"], "scope-note"))
        assert footer["position"]["height"] >= 70
        for measure in ("Evidence notice", "Review window"):
            assert any(
                value.get("Measure", {}).get("Property") == measure
                for value in _walk(visuals)
            )
        navigation = [v for v in visuals if v["visual"]["visualType"] == "actionButton"]
        targets = {
            v["visual"]["visualContainerObjects"]["visualLink"][0]["properties"]["navigationSection"]["expr"]["Literal"]["Value"].strip("'")
            for v in navigation
        }
        assert targets == set(pages)
        scope = [
            v for v in visuals if v["visual"].get("syncGroup", {}).get("groupName", "").startswith("OwnerScope-")
        ]
        assert len(scope) == 2
        for slicer in scope:
            group = slicer["visual"]["syncGroup"]
            assert group["filterChanges"] is True and group["fieldChanges"] is True
            assert slicer["visual"]["visualType"] == "slicer"
            assert slicer["visual"]["objects"]["data"][0]["properties"]["mode"]["expr"]["Literal"]["Value"] == "'Dropdown'"
        assert {v["visual"]["syncGroup"]["groupName"] for v in scope} == {
            "OwnerScope-workspace_id", "OwnerScope-workspace_name",
        }


def test_meaningful_builtin_charts_bind_to_scoped_measures_not_raw_metric_sums(bim):
    expected_counts = {
        "OwnerWorkspaces": 2, "OwnerFindings": 0, "OwnerTechnicalDetails": 0,
        "OwnerExecutions": 0, "OwnerCoverage": 0,
    }
    expressions = {m["name"]: m["expression"] for m in _tables(bim)[model.WORKSPACE_TABLE]["measures"]}
    for name, (_, visuals) in _report_pages().items():
        charts = [v for v in visuals if v["visual"]["visualType"] in {"donutChart", "clusteredBarChart"}]
        assert len(charts) == expected_counts[name]
        for visual in charts:
            assert visual["position"]["width"] >= 300
            assert visual["position"]["height"] >= 170
            state = visual["visual"]["query"]["queryState"]
            category = state["Category"]["projections"][0]["field"]["Column"]
            assert category["Expression"]["SourceRef"]["Entity"] in {
                "owner_reviews", "owner_findings", "owner_details",
            }
            measure = state["Y"]["projections"][0]["field"]["Measure"]
            assert measure["Expression"]["SourceRef"]["Entity"] == model.WORKSPACE_TABLE
            assert measure["Property"].startswith("Scoped ")
            assert "KEEPFILTERS(owner_reviews[is_latest] = TRUE())" in expressions[measure["Property"]]
        assert not any("Aggregation" in value for value in _walk(visuals))
        assert {v["visual"]["visualType"] for v in visuals} <= {
            "card", "textbox", "slicer", "actionButton", "tableEx", "donutChart", "clusteredBarChart",
        }


def test_technical_explorer_has_exact_item_metric_filters_and_no_source_or_scores():
    _, visuals = _report_pages()["OwnerTechnicalDetails"]
    slicer_columns = {
        projection["field"]["Column"]["Property"]
        for v in visuals if v["visual"]["visualType"] == "slicer"
        for projection in v["visual"]["query"]["queryState"]["Values"]["projections"]
    }
    assert slicer_columns == {
        "workspace_name", "workspace_id", "detail_type", "item_type", "item_name", "item_id", "metric_name",
    }
    text = json.dumps(visuals)
    assert "values have different units, never a FAR score" in text
    for field in ("detail", "notice", "run_timestamp", "signal_codes", "metric_value"):
        assert any(value.get("Column", {}).get("Property") == field for value in _walk(visuals))


def test_owner_theme_is_registered_with_consistent_severity_and_evidence_colors():
    assert report.THEME_NAME == "FabricArchReviewOwner"
    parts = {part["path"]: json.loads(part["text"]) for part in report.build_parts("owner-model-id")}
    theme = parts[f"StaticResources/RegisteredResources/{report.THEME_NAME}.json"]
    root = parts["definition/report.json"]
    assert theme["name"] == root["themeCollection"]["customTheme"]["name"]
    assert root["resourcePackages"][0]["items"][0]["type"] == "CustomTheme"
    assert theme["textClasses"]["title"]["fontFace"] == "Segoe UI Semibold"
    assert theme["visualStyles"]["*"]["*"]["visualHeader"] == [{"show": True}]
    assert theme["visualStyles"]["tableEx"]["*"]["columnHeaders"][0]["alignment"] == "Left"
    assert theme["visualStyles"]["donutChart"]["*"]["labels"][0]["labelStyle"] == "Data"
    for page_name, key, expected in (
        ("OwnerWorkspaces", "evidence-by-status", report._EVIDENCE_COLORS),
        ("OwnerWorkspaces", "findings-by-severity", report.SEV_HEX),
    ):
        visual = next(v for v in _report_pages()[page_name][1] if v["name"] == report._id(page_name, key))
        colors = visual["visual"]["objects"]["dataPoint"]
        assert len(colors) == len(expected)
        for entry, color in zip(colors, expected.values()):
            assert entry["properties"]["fill"]["solid"]["color"]["expr"]["Literal"]["Value"] == f"'{color}'"


def test_owner_action_guidance_preserves_pages_and_prioritizes_remediation():
    pages = _report_pages()
    assert set(pages) == {
        "OwnerWorkspaces", "OwnerFindings", "OwnerTechnicalDetails", "OwnerExecutions", "OwnerCoverage",
    }
    for page_name, (_, visuals) in pages.items():
        assert report._GUIDANCE[page_name] in json.dumps(visuals)
        assert "Static signals are not measured runtime impact." in json.dumps(visuals)
    table = next(
        v for v in pages["OwnerFindings"][1] if v["visual"]["visualType"] == "tableEx"
    )
    columns = table["visual"]["query"]["queryState"]["Values"]["projections"]
    names = [projection["nativeQueryRef"] for projection in columns]
    assert names.index("title") < names.index("item_id")
    assert {"severity", "status", "rule_id", "notice", "signal_codes", "run_timestamp",
            "item_id", "finding_key"} <= set(names)
    guidance = next(v for v in pages["OwnerFindings"][1] if v["name"] == report._id("OwnerFindings", "finding-guidance"))
    projections = guidance["visual"]["query"]["queryState"]["Values"]["projections"]
    assert [p["nativeQueryRef"] for p in projections] == ["title", "recommendation"]
    recommendation = next(p for p in projections if p["nativeQueryRef"] == "recommendation")
    assert recommendation["displayName"] == "Recommended next step"


def test_action_selection_filters_guidance_without_hiding_the_queue_or_crossing_fact_tables():
    page, visuals = _report_pages()["OwnerFindings"]
    queue_id = report._id("OwnerFindings", "workspace-scoped-data")
    guidance_id = report._id("OwnerFindings", "finding-guidance")
    assert [i for i in page["visualInteractions"] if i["type"] == "DataFilter"] == [
        {"source": queue_id, "target": guidance_id, "type": "DataFilter"},
    ]
    by_id = {visual["name"]: visual for visual in visuals}
    query_ids = {
        v["name"] for v in visuals if v["visual"].get("query", {}).get("queryState")
    }
    expected = {(source, target) for source in (queue_id, guidance_id)
                for target in query_ids if target != source}
    assert len(page["visualInteractions"]) == len(expected)
    assert {(i["source"], i["target"]) for i in page["visualInteractions"]} == expected
    for interaction in page["visualInteractions"]:
        if (interaction["source"], interaction["target"]) != (queue_id, guidance_id):
            assert interaction["type"] == "NoFilter"
    for visual_id in (queue_id, guidance_id):
        columns = by_id[visual_id]["visual"]["query"]["queryState"]["Values"]["projections"]
        entities = {p["field"]["Column"]["Expression"]["SourceRef"]["Entity"] for p in columns}
        assert "owner_findings" in entities
        assert entities <= {"owner_findings", model.WORKSPACE_TABLE}
        assert "filterConfig" not in by_id[visual_id]  # no saved finding selection
    assert "clear selection to show all" in json.dumps(by_id[guidance_id])
    for page_name, (other_page, _) in _report_pages().items():
        if page_name not in {"OwnerFindings", "OwnerCoverage"}:
            assert not other_page.get("visualInteractions")


def test_coverage_notes_are_readable_and_selectable_without_filtering_cards():
    page, visuals = _report_pages()["OwnerCoverage"]
    queue = report._id("OwnerCoverage", "workspace-scoped-data")
    notes = report._id("OwnerCoverage", "coverage-notes")
    assert [i for i in page["visualInteractions"] if i["type"] == "DataFilter"] == [
        {"source": queue, "target": notes, "type": "DataFilter"},
    ]
    detail = next(v for v in visuals if v["name"] == notes)
    projections = detail["visual"]["query"]["queryState"]["Values"]["projections"]
    assert [p["nativeQueryRef"] for p in projections] == ["item_name", "evidence_type", "notice"]
    assert detail["position"]["height"] >= 130


@pytest.mark.parametrize("page_name", ["OwnerExecutions", "OwnerCoverage"])
def test_native_registers_preserve_item_workspace_and_review_identity(page_name):
    _, visuals = _report_pages()[page_name]
    register = next(v for v in visuals if v["name"] == report._id(page_name, "workspace-scoped-data"))
    columns = register["visual"]["query"]["queryState"]["Values"]["projections"]
    names = {p["nativeQueryRef"] for p in columns}
    assert {"workspace_name", "workspace_id", "item_id", "run_id", "run_timestamp"} <= names
    # Equal labels and execution IDs are possible in distinct authorized items.
    # These identity columns must participate in the table's grouping query.
    assert all("Column" in p["field"] for p in columns)
    if page_name == "OwnerExecutions":
        assert {"execution_key", "item_type"} <= names


def test_action_and_evidence_space_is_prioritized_over_charts():
    pages = _report_pages()
    tables = {
        name: [v for v in visuals if v["visual"]["visualType"] == "tableEx"]
        for name, (_, visuals) in pages.items()
    }
    assert len(tables["OwnerWorkspaces"]) == 1
    register = tables["OwnerWorkspaces"][0]["position"]
    assert register["height"] >= 280
    for visual in pages["OwnerWorkspaces"][1]:
        if visual["visual"]["visualType"] in {"donutChart", "clusteredBarChart"}:
            assert register["y"] + register["height"] < visual["position"]["y"]
    assert len(tables["OwnerFindings"]) == 2
    queue, guidance = tables["OwnerFindings"]
    assert queue["position"]["height"] >= 260
    assert guidance["position"]["height"] >= 198
    assert sum(v["position"]["height"] for v in tables["OwnerFindings"]) >= 450
    assert len(tables["OwnerTechnicalDetails"]) == 1
    assert tables["OwnerTechnicalDetails"][0]["position"]["height"] >= 350


def test_table_widths_wrapping_and_reading_order_keep_actions_visible():
    for page_name, (_, visuals) in _report_pages().items():
        for visual in visuals:
            if visual["visual"]["visualType"] != "tableEx":
                continue
            objects = visual["visual"]["objects"]
            for key in ("columnHeaders", "values"):
                assert objects[key][0]["properties"]["wordWrap"] == report._lit("true")
            assert objects["columnHeaders"][0]["properties"]["autoSizeColumnWidth"] == report._lit("false")
            assert objects["total"][0]["properties"]["totals"] == report._lit("false")
            projections = visual["visual"]["query"]["queryState"]["Values"]["projections"]
            widths = {
                entry["selector"]["metadata"]: int(entry["properties"]["value"]["expr"]["Literal"]["Value"].removesuffix("D"))
                for entry in objects["columnWidth"]
            }
            assert set(widths) == {p["queryRef"] for p in projections}
            assert all(width > 0 for width in widths.values())
            if page_name in {"OwnerExecutions", "OwnerCoverage"}:
                assert sum(widths[p["queryRef"]] for p in projections[:5]) <= visual["position"]["width"] - 40
            if visual["name"] == report._id("OwnerFindings", "finding-guidance"):
                assert sum(widths.values()) <= visual["position"]["width"] - 40
                recommendation = next(p for p in projections if p["nativeQueryRef"] == "recommendation")
                assert widths[recommendation["queryRef"]] >= 800
            if visual["name"] == report._id("OwnerFindings", "workspace-scoped-data"):
                primary = ["workspace_name", "severity", "item_name", "title", "affected_count"]
                assert [p["nativeQueryRef"] for p in projections[:5]] == primary
                assert sum(widths[p["queryRef"]] for p in projections[:5]) <= visual["position"]["width"] - 40


def _evaluate_scoped_count(expression, data, predicate, grants, selections=None):
    """Small evaluator for the emitted count grammar; not a live DAX engine."""
    latest = "KEEPFILTERS(owner_reviews[is_latest] = TRUE())"
    assert latest in expression
    aggregate = re.search(r"(COUNTROWS|DISTINCTCOUNT|COUNT|MAX)\((owner_[a-z_]+)(?:\[([a-z_]+)\])?\)", expression)
    assert aggregate
    kind, source, column = aggregate.groups()
    clauses = re.findall(
        r'KEEPFILTERS\((owner_[a-z_]+)\[([a-z_]+)\] (=|<>|IN) ("[^"]*"|\{[^}]+\})\)',
        expression,
    )
    negative_clauses = re.findall(
        r'KEEPFILTERS\(NOT \((owner_[a-z_]+)\[([a-z_]+)\] IN (\{[^}]+\})\)\)',
        expression,
    )
    reconstructed = model._current_count(
        aggregate.group(0),
        *(f"{table}[{field}] {operation} {value}" for table, field, operation, value in clauses),
        *(f"NOT ({table}[{field}] IN {value})" for table, field, value in negative_clauses),
    )
    if kind == "MAX":
        reconstructed = f"CALCULATE({aggregate.group(0)}, {latest})"
    assert expression == reconstructed
    allowed = {grant["workspace_id"] for grant in grants if _grant_allows(predicate, grant)}
    rows = {
        table: [row for row in values if row["workspace_id"] in allowed]
        for table, values in data.items()
    }
    # Apply the same filters independently of whether they come from slicers,
    # chart categories, or CALCULATE/KEEPFILTERS. None may replace another.
    for table, field, values in selections or []:
        if table == model.WORKSPACE_TABLE:
            rows = {name: [row for row in values_ if row[field] in values] for name, values_ in rows.items()}
        else:
            rows[table] = [row for row in rows[table] if row.get(field) in values]
    for table, field, operation, value in clauses:
        allowed_values = json.loads(value.replace("{", "[").replace("}", "]")) if operation == "IN" else [json.loads(value)]
        rows[table] = [
            row for row in rows[table]
            if ((row.get(field) not in allowed_values) if operation == "<>" else (row.get(field) in allowed_values))
        ]
    for table, field, value in negative_clauses:
        denied_values = json.loads(value.replace("{", "[").replace("}", "]"))
        rows[table] = [row for row in rows[table] if row.get(field) not in denied_values]
    rows["owner_reviews"] = [row for row in rows["owner_reviews"] if row["is_latest"]]
    review_keys = {row["review_key"] for row in rows["owner_reviews"]}
    selected = [row for row in rows[source] if row["review_key"] in review_keys]
    if kind == "MAX":
        return max((row[column] for row in selected if row[column] is not None), default=None)
    if kind == "COUNT":
        return sum(row[column] is not None for row in selected)
    return len({row[column] for row in selected}) if kind == "DISTINCTCOUNT" else len(selected)


@pytest.fixture
def scoped_dashboard_data():
    reviews = [
        {"workspace_id": "workspace-a", "review_key": "a-old", "is_latest": False, "technical_evidence_status": "missing", "run_timestamp": 90},
        {"workspace_id": "workspace-a", "review_key": "a-now", "is_latest": True, "technical_evidence_status": "partial", "run_timestamp": 100},
        {"workspace_id": "workspace-b", "review_key": "b-now", "is_latest": True, "technical_evidence_status": "missing", "run_timestamp": 95},
        {"workspace_id": "workspace-c", "review_key": "c-now", "is_latest": True, "technical_evidence_status": "partial", "run_timestamp": 105},
        {"workspace_id": "workspace-d", "review_key": "d-now", "is_latest": True, "technical_evidence_status": "partial", "run_timestamp": 110},
    ]
    findings = []
    for workspace, review, severity, item_type in [
        ("a", "a-now", "high", "SemanticModel"),
        ("a", "a-now", "medium", "Notebook"),
        ("b", "b-now", "low", "Notebook"),
        ("a", "a-old", "critical", "SemanticModel"),
        ("a", "orphan-review", "critical", "SemanticModel"),
        ("c", "c-now", "critical", "SemanticModel"),
        ("d", "d-now", "critical", "SemanticModel"),
    ]:
        findings.append({
            "workspace_id": f"workspace-{workspace}", "review_key": review,
            "severity": severity, "item_type": item_type, "status": "fail",
            "rule_id": "DAX-001" if item_type == "SemanticModel" else "NBCODE-001",
        })
    details = [
        {"workspace_id": f"workspace-{workspace}", "review_key": review,
         "detail_type": kind, "signal_codes": codes, "metric_value": 9999999}
        for workspace, review, kind, codes in [
            ("a", "a-now", "model_inventory", ""),
            ("a", "a-now", "dax_measure", "DAX-A"),
            ("a", "a-now", "notebook_signal", "NB-A"),
            ("b", "b-now", "model_table_stat", ""),
            ("b", "b-now", "dax_measure", ""),
            ("a", "a-old", "notebook_signal", "NB-A"),
            ("c", "c-now", "dax_measure", "DAX-A"),
            ("d", "d-now", "notebook_signal", "NB-A"),
        ]
    ]
    grants = [
        _grant(), _grant(workspace_id="workspace-b"),
        _grant(workspace_id="workspace-c", principal_object_id="foreign-user"),
        _grant(workspace_id="workspace-d", expires_at=100.0),
    ]
    return {"owner_reviews": reviews, "owner_findings": findings, "owner_details": details}, grants


def test_scoped_kpis_count_only_authorized_per_workspace_latest_rows(bim, scoped_dashboard_data):
    data, grants = scoped_dashboard_data
    expressions = {m["name"]: m["expression"] for m in _tables(bim)[model.WORKSPACE_TABLE]["measures"]}
    expected = {
        "Scoped workspaces": 2, "Scoped findings": 3, "Scoped priority findings": 1,
        "Scoped missing evidence": 1, "Scoped DAX findings": 1, "Scoped notebook findings": 2,
        "Scoped detail rows": 5, "Scoped DAX rows": 2, "Scoped notebook rows": 1,
        "Scoped table metric rows": 1, "Scoped signal rows": 2,
    }
    assert set(expected) <= set(expressions)
    for name, count in expected.items():
        assert _evaluate_scoped_count(expressions[name], data, _permissions(bim)["owner_access"], grants) == count
        assert _evaluate_scoped_count(expressions[name], data, _permissions(bim)["owner_access"], []) == 0
        assert all(token not in expressions[name] for token in ("ALL(", "ALLSELECTED", "REMOVEFILTERS", "TREATAS", "SUM("))


@pytest.mark.parametrize("measure,selections,expected", [
    ("Scoped workspaces", [(model.WORKSPACE_TABLE, "workspace_id", {"workspace-a"})], 1),
    ("Scoped findings", [(model.WORKSPACE_TABLE, "workspace_id", {"workspace-a"})], 2),
    ("Scoped findings", [("owner_findings", "severity", {"medium"})], 1),
    ("Scoped priority findings", [("owner_findings", "severity", {"medium"})], 0),
    ("Scoped notebook findings", [("owner_findings", "item_type", {"SemanticModel"})], 0),
    ("Scoped findings", [("owner_reviews", "technical_evidence_status", {"missing"})], 1),
    ("Scoped detail rows", [("owner_details", "detail_type", {"dax_measure"})], 2),
    ("Scoped signal rows", [("owner_details", "detail_type", {"dax_measure"})], 1),
    ("Scoped notebook rows", [("owner_details", "detail_type", {"dax_measure"})], 0),
])
def test_scoped_kpis_intersect_slicer_and_chart_filters(bim, scoped_dashboard_data, measure, selections, expected):
    data, grants = scoped_dashboard_data
    expression = next(m["expression"] for m in _tables(bim)[model.WORKSPACE_TABLE]["measures"] if m["name"] == measure)
    assert _evaluate_scoped_count(expression, data, _permissions(bim)["owner_access"], grants, selections) == expected


def test_dynamic_review_and_evidence_context_never_claims_complete_or_pass(bim):
    expressions = {m["name"]: m["expression"] for m in _tables(bim)[model.WORKSPACE_TABLE]["measures"]}
    assert "MIN(owner_reviews[run_timestamp])" in expressions["Review window"]
    assert "MAX(owner_reviews[run_timestamp])" in expressions["Review window"]
    assert expressions["Review window"].count("KEEPFILTERS(owner_reviews[is_latest] = TRUE())") == 2
    assert "No current entitled review" in expressions["Review window"]
    assert "PARTIAL ASSESSMENT" in expressions["Evidence notice"]
    assert "Zero findings does not mean pass." in expressions["Evidence notice"]
    assert "[Scoped missing evidence]" in expressions["Evidence notice"]


def test_evidence_kpis_preserve_latest_rls_slicers_and_nullable_milliseconds(bim, scoped_dashboard_data):
    data, grants = scoped_dashboard_data
    data["owner_executions"] = [
        {"workspace_id": f"workspace-{workspace}", "review_key": review, "execution_key": key,
         "status": status, "duration_ms": duration}
        for workspace, review, key, status, duration in [
            ("a", "a-now", "key-1", "Failed", 125),
            ("a", "a-now", "key-2", "Completed", None),
            ("b", "b-now", "key-3", "Failed", 0),
            ("a", "a-old", "key-1", "Failed", 9999),
            ("a", "orphan", "key-4", "Failed", 9999),
            ("c", "c-now", "key-5", "Failed", 9999),
            ("d", "d-now", "key-6", "Failed", 9999),
        ]
    ]
    data["owner_coverage"] = [
        {"workspace_id": f"workspace-{workspace}", "review_key": review, "collection_status": status}
        for workspace, review, status in [
            ("a", "a-now", "collected"), ("a", "a-now", "partial"),
            ("b", "b-now", "empty"), ("c", "c-now", "partial"),
            ("d", "d-now", "partial"), ("a", "a-old", "partial"),
        ]
    ]
    for workspace, review in [("a", "a-now"), ("c", "c-now"), ("d", "d-now"), ("a", "a-old")]:
        data["owner_details"].extend(
            {"workspace_id": f"workspace-{workspace}", "review_key": review,
             "detail_type": kind, "signal_codes": "approved", "metric_value": 99999}
            for kind in ("dax_calculated_column", "dax_calculated_table", "dax_calculation_item", "dataflow_query")
        )
        data["owner_findings"].append({
            "workspace_id": f"workspace-{workspace}", "review_key": review,
            "rule_id": "EXEC-001", "item_type": "SemanticModel", "status": "fail", "severity": "high",
        })
    expressions = {m["name"]: m["expression"] for m in _tables(bim)[model.WORKSPACE_TABLE]["measures"]}
    expected = {
        "Scoped executions": 3, "Scoped failed executions": 2, "Scoped timed executions": 2,
        "Scoped maximum duration ms": 125, "Scoped coverage rows": 3, "Scoped incomplete coverage": 1,
        "Scoped DAX object rows": 3, "Scoped Dataflow rows": 1, "Scoped static objects": 4,
        "Scoped DAX rows": 2, "Scoped DAX findings": 1,
    }
    predicate = _permissions(bim)["owner_access"]
    for name, count in expected.items():
        assert _evaluate_scoped_count(expressions[name], data, predicate, grants) == count
        assert _evaluate_scoped_count(expressions[name], data, predicate, []) == (
            None if name == "Scoped maximum duration ms" else 0
        )
    for name, count in {"Scoped executions": 1, "Scoped maximum duration ms": 0, "Scoped static objects": 0}.items():
        assert _evaluate_scoped_count(expressions[name], data, predicate, grants, [
            (model.WORKSPACE_TABLE, "workspace_id", {"workspace-b"}),
        ]) == count
    assert _evaluate_scoped_count(expressions["Scoped failed executions"], data, predicate, grants, [
        ("owner_executions", "status", {"Completed"}),
    ]) == 0
    assert _evaluate_scoped_count(expressions["Scoped incomplete coverage"], data, predicate, grants, [
        ("owner_coverage", "collection_status", {"collected"}),
    ]) == 0
    assert _evaluate_scoped_count(expressions["Scoped incomplete coverage"], data, predicate, grants, [
        ("owner_coverage", "collection_status", {"empty"}),
    ]) == 0

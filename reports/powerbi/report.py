# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Central "Fabric Arch Review - Governance" PBIR report in the FUAM visual language.

Every page is locked to the active profile, rule-logic hash and latest run; all
visual fields are existing Gold columns or semantic-model measures.
"""
from __future__ import annotations

from analyzers.far_profile import ACTIVE_RULE_IDS, CAPACITY_RULES, PROFILE_ID, PROFILE_SHA256
from reports.powerbi.pbir import (BAD_FILL, PAGE_H, PAGE_W, SEVERITY_COLORS, STATUS_COLORS,  # noqa: F401
                                  THEME_ID, build_content_page, build_home_page, entities_of, make_id,
                                  page_filter, report_parts)
from reports.powerbi.schema import GOLD_TABLES_BY_NAME
from reports.powerbi.semantic_model import FACT_TABLE, MODEL_TABLE, RISK_TABLE, RUN_TABLE

REPORT_KEY = "central"
BRAND = "FAR"
NOTICE = "Signals and coverage observations are not scored. No assessable controls means no valid score."
F = FACT_TABLE
_HOME = "home"
_COVERAGE = "gold_source_coverage"
_TENANT = "gold_tenant_settings"
_RULES = "gold_rules"
_SECURITY = "gold_model_security"
_ROLES = "gold_model_security_roles"
_ROLES_PAGE = "modelroles"
_ITEMS = "gold_finding_items"
_ORANGE = "#FFA342"
SECURITY_COLORS = {"present": "#248A85", "absent": _ORANGE, "unknown": "#97B7F3"}
FILTER_COLORS = {"static": "#248A85", "dynamic": "#3D7DEB", "mixed": "#97B7F3", "constant": _ORANGE,
                 "none": "#C6C2BE", "not_applicable": "#C6C2BE", "unknown": "#D195EA"}
POSTURE_COLORS = {"Judged by a rule": "#248A85", "Open to everyone (can be limited)": _ORANGE,
                  "Limited to groups": "#56C17B", "Disabled": "#C6C2BE", "Enabled (cannot be limited)": "#97B7F3"}

# (key, display name, home-page blurb)
_PAGE_INDEX = [
    ("overview", "Overview", "Score, results by status and dimension, and which rules fail the most."),
    ("findings", "Findings", "Every finding with what it means and what to do next. Select a row to read why."),
    ("actions", "Action plan", "What to fix first, and what the review still needs from you to decide."),
    ("items", "Items to review", "The pipelines, lakehouses, models and other items each finding points at, by name and type."),
    ("workspaces", "Workspaces", "Workspace size, environment, capacity assignment and risk."),
    ("capacities", "Capacities & storage", "Capacity SKU, state and region plus semantic-model storage modes."),
    ("modelsecurity", "Model security", "Which semantic models define row-level and object-level security, by capacity and workspace, and which declared ones do not."),
    ("tenant", "Tenant settings", "Every tenant setting recorded, the controls that judge them and their changes."),
    ("dependencies", "Dependencies", "Model-to-report lineage and dependency edges between estate items."),
    ("rules", "Rule catalogue", "Every rule: what it checks, why it matters, how to fix it and what it needs."),
    ("evidence", "Evidence coverage", "What was collected, from which source (FUAM, FCA, native) and how fresh it is."),
    ("trends", "Trends", "Run history for one rule-logic version; compare only runs of the same version."),
]


def page_id(key: str) -> str:
    return make_id(REPORT_KEY, "page", key)


def _bar(title: str, entity: str, prop: str, value: str, *, fill: str | None = None,
         colors: dict[str, str] | None = None, weight: int = 1) -> dict:
    spec = {"kind": "barChart", "title": title, "category": (entity, prop), "values": [(F, value)], "weight": weight}
    if fill:
        spec["fill"] = fill
    if colors:
        spec["colors"] = colors
    return spec


def _donut(title: str, entity: str, prop: str, value: str, *, colors: dict[str, str] | None = None,
           weight: int = 1) -> dict:
    spec = {"kind": "donutChart", "title": title, "category": (entity, prop), "values": [(F, value)],
            "weight": weight}
    if colors:
        spec["colors"] = colors
    return spec


def _tree(title: str, entity: str, prop: str, value: str, *, weight: int = 1) -> dict:
    return {"kind": "treemap", "title": title, "category": (entity, prop), "values": [(F, value)], "weight": weight}


def _mix(title: str, category: tuple[str, str], series: tuple[str, str], value: str, *,
         colors: dict[str, str] | None = None, weight: int = 1) -> dict:
    spec = {"kind": "hundredPercentStackedBarChart", "title": title, "category": category, "series": series,
            "values": [(F, value)], "weight": weight}
    if colors:
        spec["colors"] = colors
    return spec


def _run_chart(title: str, values: list[str], kind: str = "columnChart", weight: int = 1) -> dict:
    return {"kind": kind, "title": title, "category": (RUN_TABLE, "run_timestamp"),
            "values": [(F, v) for v in values], "ascending": True, "weight": weight}


def _row(height: int, *charts: dict) -> dict:
    return {"h": height, "charts": list(charts)}


def _content_specs() -> dict[str, dict]:
    return {
        "overview": dict(
            description="Latest run. The score is passed scored controls over assessed controls. Signals, unknown results and missing evidence never change it; the Action plan explains each one.",
            cards=[(F, "Architecture Score"), (F, "Scored Count"), (F, "Pass Count"), (F, "Fail Count"),
                   (F, "Needs Attention"), (F, "Signals To Review")],
            slicers=[(F, "dimension", "Dimension"), (F, "status", "Status")], slicer_row=2,
            chart_rows=[
                _row(300,
                     _donut("Findings by Status", F, "status", "Findings", colors=STATUS_COLORS),
                     _mix("Result Mix by Dimension", (F, "dimension"), (F, "status"), "Findings",
                          colors=STATUS_COLORS, weight=2),
                     _tree("Failures by Rule", F, "title", "Fail Count", weight=2)),
                _row(230,
                     _bar("Failures by Severity", F, "severity", "Fail Count", colors=SEVERITY_COLORS),
                     _bar("Failures by Dimension", F, "dimension", "Fail Count", fill=BAD_FILL),
                     _bar("Needs Attention by Dimension", F, "dimension", "Needs Attention", fill=_ORANGE),
                     _bar("Signals to Review by Dimension", F, "dimension", "Signals To Review", fill=_ORANGE))],
            tables=[dict(title="Assessment by dimension", entity="gold_dimension_summary", sort="dimension",
                         columns=["dimension", "assessed_count", "pass_count", "fail_count", "unknown_count",
                                  "missing_evidence_count", "info_count", "not_applicable_count", "worst_severity",
                                  "score_status"])]),
        "findings": dict(
            description="Every finding with what it means and what to do next. Select a row to read why it matters and how to fix it on the right. Controls are scored; signals and coverage are context.",
            cards=[(F, "Findings"), (F, "Fail Count"), (F, "Pass Count"), (F, "Needs Attention"), (F, "Signals To Review")],
            slicers=[(F, "dimension", "Dimension"), (F, "severity", "Severity"), (F, "status", "Status"),
                     (F, "rule_kind", "Rule kind"), (F, "review_suggested", "Review suggested")], slicer_row=2,
            chart_rows=[_row(190,
                             _donut("Findings by Status", F, "status", "Findings", colors=STATUS_COLORS),
                             _bar("Failures by Severity", F, "severity", "Fail Count", colors=SEVERITY_COLORS),
                             _bar("Findings by Dimension", F, "dimension", "Findings"))],
            tables=[dict(title="Architectural findings", entity=F, sort="status", weight=7,
                         columns=["workspace_name", "rule_id", "title", "status", "severity", "next_action"]),
                    dict(kind="multiRowCard", title="Selected finding: what it means", entity=F, weight=3,
                         columns=["rule_id", "title", "status", "status_meaning", "next_action",
                                  "why_it_matters", "how_to_fix", "configure"])]),
        "actions": dict(
            description="Fix the failures first. Then supply what the review is missing: each unknown or missing-evidence result says why and which setting to change. Neither ever counts as a pass. Signals that suggest a review are worth a look but never failures.",
            cards=[(F, "Fail Count"), (F, "Failing Rules"), (F, "Needs Attention"), (F, "Signals To Review")],
            slicers=[(F, "dimension", "Dimension"), (F, "severity", "Severity")], slicer_row=2,
            chart_rows=[_row(250,
                             _tree("Failures by Rule", F, "title", "Fail Count", weight=2),
                             _donut("Needs Attention by Reason", F, "reason_code", "Needs Attention"),
                             _bar("Needs Attention by Workspace", F, "workspace_name", "Needs Attention",
                                  fill=_ORANGE))],
            tables=[dict(title="Fix first: failing controls", entity=F, sort="severity_rank", descending=True,
                         weight=4, where=[(F, "status", ["fail"])],
                         columns=["workspace_name", "rule_id", "severity", "next_action"]),
                    dict(title="Needs your input: unknown and missing evidence", entity=F, sort="rule_id", weight=7,
                         where=[(F, "status", ["unknown", "missing_evidence"])],
                         columns=["workspace_name", "rule_id", "status_meaning", "next_action"]),
                    dict(title="Worth a look: signals that suggest a review", entity=F, sort="rule_id", weight=4,
                         where=[(F, "review_suggested", ["Yes"])],
                         columns=["workspace_name", "rule_id", "title"])]),
        "items": dict(
            description="The specific items behind a finding, by name and type, and why each is listed. Capacity-wide findings have no items; they appear on the Findings page under their capacity.",
            cards=[(F, "Items To Review"), (F, "Workspaces With Items")],
            slicers=[(_ITEMS, "rule_id", "Rule"), (_ITEMS, "item_type", "Item type"),
                     (_ITEMS, "workspace_name", "Workspace"), (_ITEMS, "status", "Status")], slicer_row=2,
            chart_rows=[_row(260,
                             _bar("Items by Rule", _ITEMS, "rule_id", "Items To Review", fill=_ORANGE),
                             _donut("Items by Type", _ITEMS, "item_type", "Items To Review"),
                             _bar("Items by Workspace", _ITEMS, "workspace_name", "Items To Review"))],
            tables=[dict(title="Items to review", entity=_ITEMS, sort="rule_id",
                         columns=["workspace_name", "rule_id", "title", "item_type", "item_name", "reason", "item_id"])]),
        "workspaces": dict(
            description="Workspace boundaries from the latest run. Risk counts confirmed failures only; a workspace without failures is not proven healthy.",
            cards=[(F, "Workspaces"), (F, "Workspace Items"), (F, "Workspace Admins"), (F, "Assessed Workspaces"),
                   (F, "Failing Issues")],
            slicers=[("gold_workspaces", "environment", "Environment")],
            chart_rows=[_row(300,
                             _tree("Items by Workspace", "gold_workspaces", "workspace_name", "Workspace Items",
                                   weight=2),
                             _donut("Workspaces by Environment", "gold_workspaces", "environment", "Workspaces"),
                             _bar("Failing Issues by Workspace", RISK_TABLE, "workspace_name", "Failing Issues",
                                  fill=BAD_FILL),
                             _bar("Workspaces by Capacity", RISK_TABLE, "capacity_name", "Assessed Workspaces"))],
            tables=[dict(title="Workspaces", entity="gold_workspaces", sort="workspace_name",
                         columns=["workspace_name", "workspace_id", "capacity_id", "environment",
                                  "item_count", "admin_count", "description"])]),
        "capacities": dict(
            description="Hosting metadata from the last scan, not utilization or cost, and the findings about each capacity as a whole (throttling, state, administrators). With FCA connected, each capacity also shows its Azure region and the required tags it lacks.",
            cards=[(F, "Capacities"), (F, "Assigned Workspaces"), (F, "Semantic Models"), (F, "Reports")],
            slicers=[("gold_capacities", "sku", "Capacity SKU"), (MODEL_TABLE, "storage_mode", "Storage mode")],
            chart_rows=[_row(300,
                             _donut("Semantic Models by Storage Mode", MODEL_TABLE, "storage_mode", "Semantic Models"),
                             _tree("Assigned Workspaces by Capacity", "gold_capacities", "capacity_name",
                                   "Assigned Workspaces", weight=2),
                             _bar("Capacities by Region", "gold_capacities", "region", "Capacities"),
                             _bar("Capacities by SKU", "gold_capacities", "sku", "Capacities"))],
            tables=[dict(title="Capacities based on last scan", entity="gold_capacities", sort="capacity_name",
                         weight=6, columns=["capacity_name", "sku", "state", "admin_count", "region", "fca_region",
                                            "missing_tags", "assigned_workspace_count"]),
                    dict(title="Capacity findings: throttling, state and administrators", entity=F, sort="rule_id", weight=4,
                         where=[(F, "rule_id", sorted(CAPACITY_RULES))],
                         columns=["workspace_name", "rule_id", "title", "status", "next_action"]),
                    dict(title="Semantic models", entity=MODEL_TABLE, sort="model_name", weight=4,
                         columns=["workspace_name", "model_name", "storage_mode"])]),
        "modelsecurity": dict(
            description="Whether each semantic model defines row-level (RLS) or object-level (OLS) security, from its definition. Only declared requirements (SEC-024, SEC-025) fail; unknown is not \"no security\". Right-click a model, then Drill through > Model roles, for its roles (needs role detail on). Filters are never shown.",
            cards=[(F, "Models Inspected"), (F, "Models With RLS"), (F, "Models With OLS"),
                   (F, "Models Without RLS or OLS"), (F, "Models Not Inspected"), (F, "Required Security Gaps")],
            slicers=[(_SECURITY, "capacity_name", "Capacity"), (_SECURITY, "workspace_name", "Workspace"),
                     (_SECURITY, "rls_status", "RLS"), (_SECURITY, "ols_status", "OLS"),
                     (_SECURITY, "security_inspection", "Inspection")], slicer_row=2,
            chart_rows=[_row(270,
                             _donut("Models by RLS", _SECURITY, "rls_status", "Models Inspected", colors=SECURITY_COLORS),
                             _donut("Models by OLS", _SECURITY, "ols_status", "Models Inspected", colors=SECURITY_COLORS),
                             _bar("Models Without RLS or OLS by Capacity", _SECURITY, "capacity_name",
                                  "Models Without RLS or OLS", fill=_ORANGE, weight=2),
                             _bar("Required Security Gaps by Workspace", _SECURITY, "workspace_name",
                                  "Required Security Gaps", fill=BAD_FILL, weight=2))],
            tables=[dict(title="Semantic model security", entity=_SECURITY, sort="model_name",
                         columns=["capacity_name", "workspace_name", "model_name", "model_id", "rls_status",
                                  "ols_status", "rls_required", "ols_required", "rls_outcome", "ols_outcome",
                                  "security_inspection", "inspection_reason", "role_count", "role_detail",
                                  "rls_filter_count", "ols_table_count", "ols_column_count"])]),
        "tenant": dict(
            description="Every tenant setting is recorded on each run, so run history is the audit trail. Settings with a control are judged pass or fail; the rest show their posture, so you can review any that are open to everyone but could be limited to groups.",
            cards=[(F, "Tenant Settings Recorded"), (F, "Judged Settings"), (F, "Settings Open to Everyone"),
                   (F, "Settings Limited to Groups"), (F, "Settings Changed")],
            slicers=[(_TENANT, "setting_group", "Setting group"), (_TENANT, "posture", "Posture"),
                     (_TENANT, "status", "Control result")], slicer_row=2,
            chart_rows=[_row(290,
                             _donut("Settings by Posture", _TENANT, "posture", "Tenant Settings Recorded",
                                    colors=POSTURE_COLORS),
                             _tree("Settings by Group", _TENANT, "setting_group", "Tenant Settings Recorded",
                                   weight=2),
                             _bar("Open to Everyone by Group", _TENANT, "setting_group", "Settings Open to Everyone",
                                  fill=_ORANGE),
                             _bar("Judged Settings by Result", _TENANT, "status", "Judged Settings",
                                  colors=STATUS_COLORS))],
            tables=[dict(title="Recorded tenant settings", entity=_TENANT, sort="setting_name",
                         columns=["setting_name", "title", "setting_group", "posture", "rule_id", "status",
                                  "enabled", "enabled_group_count", "last_changed_at", "change_count"])]),
        "dependencies": dict(
            description="Static, verified model-to-report lineage and explicit hosting and access edges within the latest run. Absence of an edge does not prove independence.",
            cards=[(F, "Lineage Links"), (F, "Graph Edges"), (F, "Semantic Models"), (F, "Reports")],
            slicers=[("gold_graph_edges", "relationship", "Relationship"),
                     ("gold_graph_edges", "source_type", "Source type")],
            chart_rows=[_row(270,
                             _donut("Edges by Relationship", "gold_graph_edges", "relationship", "Graph Edges"),
                             _tree("Edges by Source Type", "gold_graph_edges", "source_type", "Graph Edges"),
                             _bar("Reports per Model", "gold_lineage_edges", "model_name", "Lineage Links"),
                             _bar("Models per Report", "gold_lineage_edges", "report_name", "Lineage Links"))],
            tables=[dict(title="Model to report lineage", entity="gold_lineage_edges", sort="model_name", weight=5,
                         columns=["model_name", "report_name", "model_id", "report_id"]),
                    dict(title="Dependency graph edges", entity="gold_graph_edges", sort="source_name", weight=5,
                         columns=["source_name", "source_type", "relationship", "target_name", "target_type"])]),
        "rules": dict(
            description="What each rule checks, why it matters, how to fix it and what it needs. Select a rule to read it in full; Configure names the variables that control it.",
            cards=[(F, "Rules"), (F, "Failing Rules")],
            slicers=[(_RULES, "dimension", "Dimension"), (_RULES, "rule_kind", "Rule kind"),
                     (_RULES, "requires", "Needs")],
            chart_rows=[_row(230,
                             _donut("Rules by Kind", _RULES, "rule_kind", "Rules"),
                             _bar("Rules by Dimension", _RULES, "dimension", "Rules"),
                             _bar("Rules by What They Need", _RULES, "requires", "Rules"))],
            tables=[dict(title="Rules", entity=_RULES, sort="rule_id", weight=6,
                         columns=["rule_id", "title", "rule_kind", "severity", "requires", "configure"]),
                    dict(kind="multiRowCard", title="Selected rule", entity=_RULES, weight=4,
                         columns=["rule_id", "title", "what_it_checks", "why_it_matters", "how_to_fix", "configure",
                                  "requires", "microsoft_learn_url"])]),
        "evidence": dict(
            description="Optional FUAM and FCA enrichment appears here as a source module. See what was collected, how fresh it is and what to do when something could not be read. Rules that could not decide are in the Action plan.",
            cards=[(F, "Coverage Records"), (F, "Evidence Families"), (F, "Covered Workspaces")],
            slicers=[(_COVERAGE, "collection_status", "Collection status"),
                     (_COVERAGE, "source_module", "Source module")],
            chart_rows=[_row(270,
                             _donut("Records by Collection Status", _COVERAGE, "collection_status", "Coverage Records"),
                             _mix("Freshness by Source Module", (_COVERAGE, "source_module"),
                                  (_COVERAGE, "freshness_status"), "Coverage Records", weight=2),
                             _tree("Records by Evidence Family", _COVERAGE, "evidence_family", "Coverage Records",
                                   weight=2))],
            tables=[dict(title="Evidence coverage and provenance", entity=_COVERAGE, sort="evidence_family",
                         columns=["evidence_family", "collection_status", "source_module", "source_table",
                                  "freshness_status", "notice", "next_action"])]),
        "trends": dict(
            description="One point per run, so a trend needs two or more runs. Scores compare only within one rule-logic version (profile hash). Unknown means a rule could not decide; the Action plan says why.",
            cards=[(F, "Run Count"), (F, "Configuration Versions")],
            slicers=[(RUN_TABLE, "profile_sha256", "Rule-logic version"),
                     (RUN_TABLE, "configuration_sha256", "Configuration")],
            chart_rows=[_row(300,
                             {"kind": "lineClusteredColumnComboChart", "title": "Controls and Score by Run",
                              "category": (RUN_TABLE, "run_timestamp"),
                              "values": [(F, "Pass Count"), (F, "Fail Count")],
                              "values2": [(F, "Architecture Score")], "ascending": True, "weight": 2},
                             _run_chart("Assessment Coverage by Run", ["Assessment Coverage"]),
                             _run_chart("Needs Attention by Run", ["Needs Attention"]))],
            tables=[dict(title="Run history", entity=RUN_TABLE, sort="run_timestamp", descending=True,
                         columns=["run_timestamp", "profile_id", "profile_sha256", "configuration_sha256",
                                  "assessed_count", "pass_count", "fail_count", "unknown_count",
                                  "missing_evidence_count", "score", "score_status", "assessment_coverage"])]),
    }


def _roles_spec() -> dict:
    return dict(
        description="Roles of the model you drilled into: permission, tables with a row filter, hidden tables and columns, and whether a filter uses the user's identity (dynamic). Empty unless role detail is on. Names and counts only; filters and members are never stored.",
        cards=[(F, "Roles Listed"), (F, "Static Roles"), (F, "Dynamic Roles"), (F, "Roles Without Row Filter"),
               (F, "Administrator Roles")],
        chart_rows=[_row(230,
                         _donut("Roles by Filter Kind", _ROLES, "filter_kind", "Roles Listed", colors=FILTER_COLORS),
                         _bar("Roles by Permission", _ROLES, "role_permission", "Roles Listed"))],
        tables=[dict(kind="multiRowCard", title="Model", entity=_SECURITY, weight=3,
                     columns=["model_name", "workspace_name", "model_id", "rls_status", "ols_status",
                              "security_inspection", "role_detail"]),
                dict(title="Roles", entity=_ROLES, sort="role_name", weight=7,
                     columns=["role_name", "role_permission", "filter_kind", "row_filter_table_count",
                              "dynamic_filter_table_count", "constant_filter_table_count", "ols_table_count",
                              "ols_column_count"])])


def _roles_page(home_id: str) -> dict:
    """Hidden drill-through page: the roles of the one model selected on the Model security page."""
    canvas, meta = build_content_page(REPORT_KEY, _ROLES_PAGE, "Model roles", brand=BRAND, home_page=home_id,
                                      back_button=True, **_roles_spec())
    field = {"Column": {"Expression": {"SourceRef": {"Entity": _SECURITY}}, "Property": "model_id"}}
    drill_filter = make_id(REPORT_KEY, _ROLES_PAGE, "drill-filter")
    # Power BI offers Drill through only for a page whose bound filter is marked howCreated "Drillthrough".
    return {**meta, "name": page_id(_ROLES_PAGE), "display": "Model roles", "visuals": canvas.visuals,
            "filters": [*_locks(_ROLES_PAGE, canvas.visuals),
                        {"name": drill_filter, "type": "Categorical", "field": field, "howCreated": "Drillthrough"}],
            "drill": {"name": make_id(REPORT_KEY, _ROLES_PAGE, "binding"),
                      "parameter": make_id(REPORT_KEY, _ROLES_PAGE, "parameter"),
                      "filter": drill_filter, "field": field}}


def _locks(key: str, visuals: list[dict]) -> list[dict]:
    """Locked profile/version filters on every entity a page touches, plus latest-run outside history."""
    filters = []
    if key != "trends":
        filters.append(page_filter(key, RUN_TABLE, "is_latest", ["true"], boolean=True))
    for entity in sorted(entities_of(visuals) | {F}):
        columns = {c.name for c in GOLD_TABLES_BY_NAME[entity].columns}
        filters += [page_filter(key, entity, "profile_id", [PROFILE_ID]),
                    page_filter(key, entity, "profile_sha256", [PROFILE_SHA256])]
        if "rule_id" in columns:
            filters.append(page_filter(key, entity, "rule_id", sorted(ACTIVE_RULE_IDS)))
    return filters


def _pages() -> list[dict]:
    home_id = page_id(_HOME)
    home, meta = build_home_page(
        REPORT_KEY, "Home", brand=BRAND, headline="Fabric Architecture Review",
        summary="Architecture-only assessment of your Fabric estate: scored controls, non-scored signals and "
                "evidence coverage for one active rule-logic version. Optional FUAM and FCA evidence enriches coverage.",
        notice=NOTICE,
        cards=[(F, "Architecture Score"), (F, "Scored Count"), (F, "Pass Count"), (F, "Fail Count"),
               (F, "Assessment Coverage"), (F, "Workspaces")],
        status_card=(F, "Score Status"),
        navigation=[(page_id(key), label, blurb, key) for key, label, blurb in _PAGE_INDEX],
        overview="Use the buttons to open a page; each page has a Home button. Scores and counts use the latest run "
                 "of the active profile only and never mix rule-logic versions or configurations.")
    pages = [{**meta, "name": home_id, "display": "Home", "visuals": home.visuals,
              "filters": _locks(_HOME, home.visuals)}]
    specs = _content_specs()
    for key, label, _ in _PAGE_INDEX:
        canvas, meta = build_content_page(REPORT_KEY, key, label, brand=BRAND, home_page=home_id, **specs[key])
        pages.append({**meta, "name": page_id(key), "display": label, "visuals": canvas.visuals,
                      "filters": _locks(key, canvas.visuals)})
    pages.append(_roles_page(home_id))
    return pages


def build_parts(semantic_model_id: str) -> list[dict[str, str]]:
    return report_parts(semantic_model_id, _pages())

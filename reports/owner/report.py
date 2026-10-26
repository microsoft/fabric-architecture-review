# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Owner PBIR projection in the FUAM visual language.

Filters never substitute for independent table RLS: the model's WorkspaceOwner
role still limits every table to the viewer's current workspace grants.
"""
from __future__ import annotations

from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256
from reports.owner.gold import PARTIAL_NOTICE, STATIC_NOTICE
from reports.owner.model import COVERAGE_TABLE, DETAIL_TABLE, FINDING_TABLE, REVIEW_TABLE, WORKSPACE_TABLE
from reports.powerbi.pbir import (BAD_FILL, SEVERITY_COLORS, STATUS_COLORS, THEME_ID,  # noqa: F401
                                  build_content_page, build_home_page, entities_of, make_id, page_filter,
                                  report_parts)

REPORT_KEY = "owner"
BRAND = "FAR Owner"
W, R, F, D, C = WORKSPACE_TABLE, REVIEW_TABLE, FINDING_TABLE, DETAIL_TABLE, COVERAGE_TABLE
_HOME = "home"
_SCOPED_ENTITIES = (R, F, D, C)
_SCOPE = (W, "workspace_name", "Authorized workspace")

_PAGE_INDEX = [
    ("overview", "Overview", "Findings, failures and signals for the workspaces you administer, by status, severity and dimension."),
    ("findings", "My findings", "Every architectural finding attributed to your workspaces, filtered by severity, status and rule kind."),
    ("workspaces", "My workspaces", "Per-workspace assessment and technical-evidence status for your authorized workspaces."),
    ("details", "Architectural inventory", "Approved per-item counters and pattern codes; no source code or free-form evidence."),
    ("coverage", "Evidence coverage", "What evidence was collected for your workspaces and what is missing or stale."),
]


def page_id(key: str) -> str:
    return make_id(REPORT_KEY, "page", key)


def _bar(title: str, entity: str, prop: str, value: str, *, fill: str | None = None,
         colors: dict[str, str] | None = None, weight: int = 1) -> dict:
    spec = {"kind": "barChart", "title": title, "category": (entity, prop), "values": [(W, value)], "weight": weight}
    if fill:
        spec["fill"] = fill
    if colors:
        spec["colors"] = colors
    return spec


def _donut(title: str, entity: str, prop: str, value: str, *, colors: dict[str, str] | None = None,
           weight: int = 1) -> dict:
    spec = {"kind": "donutChart", "title": title, "category": (entity, prop), "values": [(W, value)],
            "weight": weight}
    if colors:
        spec["colors"] = colors
    return spec


def _tree(title: str, entity: str, prop: str, value: str, *, weight: int = 1) -> dict:
    return {"kind": "treemap", "title": title, "category": (entity, prop), "values": [(W, value)], "weight": weight}


def _mix(title: str, category: tuple[str, str], series: tuple[str, str], value: str, *,
         colors: dict[str, str] | None = None, weight: int = 1) -> dict:
    spec = {"kind": "hundredPercentStackedBarChart", "title": title, "category": category, "series": series,
            "values": [(W, value)], "weight": weight}
    if colors:
        spec["colors"] = colors
    return spec


def _content_specs() -> dict[str, dict]:
    return {
        "overview": dict(
            description="Latest run for your authorized workspaces only. There is no global score.",
            notice=PARTIAL_NOTICE,
            cards=[(W, "Scoped workspaces"), (W, "Scoped findings"), (W, "Scoped failures"),
                   (W, "Scoped priority findings"), (W, "Scoped signals")],
            slicers=[_SCOPE],
            chart_rows=[
                {"h": 300, "charts": [
                    _donut("Findings by Status", F, "status", "Scoped findings", colors=STATUS_COLORS),
                    _mix("Result Mix by Dimension", (F, "dimension"), (F, "status"), "Scoped findings",
                         colors=STATUS_COLORS, weight=2),
                    _tree("Failures by Rule", F, "title", "Scoped failures", weight=2)]},
                {"h": 230, "charts": [
                    _bar("Failures by Severity", F, "severity", "Scoped failures", colors=SEVERITY_COLORS),
                    _bar("Findings by Rule Kind", F, "rule_kind", "Scoped findings"),
                    _bar("Failures by Workspace", R, "workspace_name", "Scoped failures", fill=BAD_FILL)]}],
            tables=[dict(title="Assessment by workspace", entity=R, sort="workspace_name",
                         columns=["workspace_name", "run_timestamp", "owner_fail_count", "owner_finding_count",
                                  "assessment_status", "technical_evidence_status", "notice"])]),
        "findings": dict(
            description="What each result means and what to do next. Select a row to read why it matters and how to fix it. Controls are scored; signals are context.",
            notice=PARTIAL_NOTICE,
            cards=[(W, "Scoped findings"), (W, "Scoped failures"), (W, "Scoped priority findings"),
                   (W, "Scoped signals")],
            slicers=[_SCOPE, (F, "severity", "Severity"), (F, "status", "Status"), (F, "rule_kind", "Rule kind")],
            slicer_row=2,
            chart_rows=[{"h": 190, "charts": [
                _donut("Findings by Status", F, "status", "Scoped findings", colors=STATUS_COLORS),
                _bar("Failures by Severity", F, "severity", "Scoped failures", colors=SEVERITY_COLORS),
                _bar("Findings by Dimension", F, "dimension", "Scoped findings")]}],
            tables=[dict(title="Architecture findings", entity=F, sort="status", weight=7,
                         columns=[(W, "workspace_name"), "rule_id", "title", "status", "severity", "next_action"]),
                    dict(kind="multiRowCard", title="Selected finding: what it means", entity=F, weight=3,
                         columns=["rule_id", "title", "status", "status_meaning", "next_action", "why_it_matters",
                                  "how_to_fix", "configure"])]),
        "workspaces": dict(
            description="One row per authorized workspace in the latest run.",
            notice=PARTIAL_NOTICE,
            cards=[(W, "Scoped workspaces"), (W, "Scoped failing workspaces"), (W, "Scoped missing evidence"),
                   (W, "Scoped detail rows")],
            slicers=[_SCOPE],
            charts=[_bar("Workspaces by Assessment Status", R, "assessment_status", "Scoped workspaces"),
                    _bar("Workspaces by Technical Evidence", R, "technical_evidence_status", "Scoped workspaces"),
                    _bar("Failures by Workspace", R, "workspace_name", "Scoped failures", fill=BAD_FILL),
                    _bar("Findings by Workspace", R, "workspace_name", "Scoped findings")],
            tables=[dict(title="My workspaces", entity=R, sort="workspace_name",
                         columns=["workspace_name", "run_timestamp", "owner_fail_count", "owner_finding_count",
                                  "owner_detail_count", "technical_detail_count", "assessment_status",
                                  "technical_evidence_status", "notice"])]),
        "details": dict(
            description="Counters and pattern codes. When role detail is enabled, also the names of each model's roles; never filters or members.",
            notice=STATIC_NOTICE,
            cards=[(W, "Scoped detail rows"), (W, "Scoped items")],
            slicers=[_SCOPE, (D, "item_type", "Item type")],
            charts=[_bar("Details by Item Type", D, "item_type", "Scoped detail rows"),
                    _bar("Details by Detail Type", D, "detail_type", "Scoped detail rows"),
                    _bar("Details by Workspace", R, "workspace_name", "Scoped detail rows")],
            tables=[dict(title="Architectural inventory", entity=D, sort="item_name",
                         columns=[(W, "workspace_name"), "item_id", "item_name", "item_type", "detail_type",
                                  "detail", "notice"])]),
        "coverage": dict(
            description="Zero rows never implies successful collection.",
            notice=PARTIAL_NOTICE,
            cards=[(W, "Scoped coverage rows"), (W, "Scoped missing evidence")],
            slicers=[_SCOPE, (C, "collection_status", "Collection status")],
            charts=[_bar("Records by Collection Status", C, "collection_status", "Scoped coverage rows"),
                    _bar("Records by Evidence Type", C, "evidence_type", "Scoped coverage rows"),
                    _bar("Records by Freshness", C, "freshness_status", "Scoped coverage rows"),
                    _bar("Records by Source", C, "source", "Scoped coverage rows")],
            tables=[dict(title="Evidence coverage", entity=C, sort="evidence_type",
                         columns=[(W, "workspace_name"), "evidence_type", "collection_status", "source",
                                  "source_contract", "item_name", "observed_at", "freshness_status",
                                  "notice"])]),
    }


def _locks(key: str) -> list[dict]:
    """Latest-run, profile and rule-logic locks; RLS remains the real entitlement boundary."""
    filters = [page_filter(key, R, "is_latest", ["true"], boolean=True)]
    for entity in _SCOPED_ENTITIES:
        filters += [page_filter(key, entity, "profile_id", [PROFILE_ID]),
                    page_filter(key, entity, "profile_sha256", [PROFILE_SHA256])]
    filters.append(page_filter(key, F, "rule_id", sorted(ACTIVE_RULE_IDS)))
    return filters


def _pages() -> list[dict]:
    home_id = page_id(_HOME)
    home, meta = build_home_page(
        REPORT_KEY, "Home", brand=BRAND, headline="Workspace Owner Review",
        summary="Architecture findings and evidence coverage for the workspaces you administer. "
                "Access is limited to your current, expiring workspace-admin grant.",
        notice=PARTIAL_NOTICE,
        cards=[(W, "Scoped workspaces"), (W, "Scoped findings"), (W, "Scoped priority findings"),
               (W, "Scoped failing workspaces"), (W, "Scoped missing evidence"), (W, "Scoped coverage rows")],
        navigation=[(page_id(key), label, blurb, key) for key, label, blurb in _PAGE_INDEX],
        overview="Use the buttons to open a page; each page has a Home button. Counts cover the latest run of "
                 "the active profile for your authorized workspaces only; there is no tenant score.")
    pages = [{**meta, "name": home_id, "display": "Home", "visuals": home.visuals, "filters": _locks(_HOME)}]
    specs = _content_specs()
    for key, label, _ in _PAGE_INDEX:
        canvas, meta = build_content_page(REPORT_KEY, key, label, brand=BRAND, home_page=home_id, **specs[key])
        pages.append({**meta, "name": page_id(key), "display": label, "visuals": canvas.visuals,
                      "filters": _locks(key)})
    return pages


def build_parts(semantic_model_id: str) -> list[dict[str, str]]:
    return report_parts(semantic_model_id, _pages())

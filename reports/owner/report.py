# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate the owner action pages and dedicated execution/coverage pages.

The URL-filter target ``gold_workspace_risk/workspace_id`` is retained by the
owner model's workspace alias. Page filters select the latest review *for each
workspace*. Neither these filters nor slicers grant access: every source table
is independently protected by WorkspaceOwner RLS.
Native execution and coverage registers project workspace/item IDs and review
context so equal display labels cannot collapse separately scoped facts.

Operators approve access requests separately and configure the model's fixed
identity before sharing. This builder performs no API calls or permission writes.
"""
from __future__ import annotations

import json
from typing import Any

from reports.owner.model import (
    COVERAGE_TABLE, DETAIL_TABLE, EXECUTION_TABLE, FINDING_TABLE, REVIEW_TABLE, WORKSPACE_TABLE,
)
from reports.powerbi.report import (
    BAD,
    BRAND,
    CARD,
    PAGE_W,
    SEV_HEX,
    _action_button,
    _banner,
    _bar,
    _card,
    _column,
    _donut,
    _field_slicer,
    _id,
    _info,
    _lit,
    _measure,
    _page_background,
    _solid,
    _theme,
    _visual,
)

_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
PAGE_H = 920
THEME_NAME = "FabricArchReviewOwner"
_PURPLE = "#8764B8"
_AMBER = "#8A6914"
_TEAL = "#038387"
_EVIDENCE_COLORS = {"partial": "#E8702A", "missing": _AMBER}
_FIELD_LABELS = {
    "workspace_name": "Workspace", "workspace_id": "Workspace ID",
    "assessment_status": "Assessment", "technical_evidence_status": "Technical evidence",
    "run_timestamp": "Review time (UTC)", "run_id": "Review run ID",
    "item_name": "Item", "item_id": "Item ID",
    "item_type": "Item type", "detail_type": "Detail category", "rule_id": "Rule",
    "table_name": "Model table", "measure_name": "Measure",
    "metric_name": "Metric / unit", "metric_value": "Value (not a FAR score)",
    "notice": "Evidence limitation", "detail": "Sanitized detail",
    "title": "What needs attention", "recommendation": "Recommended next step",
    "affected_count": "Affected objects / runs / cells", "signal_codes": "Pattern codes",
    "finding_key": "Finding ID",
    "execution_id": "Execution ID", "execution_key": "Scoped execution key",
    "execution_type": "Execution type",
    "duration_ms": "Duration (ms)", "start_time": "Start (UTC)", "end_time": "End (UTC)",
    "collection_status": "Collection status", "evidence_type": "Evidence category",
    "observed_count": "Observed count", "flagged_count": "Flagged count",
    "history_scope": "Scope limitation",
}
_GUIDANCE = {
    "OwnerWorkspaces": "Next: choose a workspace, then open Findings. Missing evidence means ask the review operator to check collection and rerun.",
    "OwnerFindings": "Select one action row to read its next step below. Use Severity to focus on high/critical findings. Technical details keeps workspace scope; select the item there.",
    "OwnerTechnicalDetails": "Validate: locate the measure, table or notebook cells in the source item. Compare results and runtime before/after a change, then rerun FAR.",
    "OwnerExecutions": "Inspect native execution IDs in the source item. Milliseconds describe observed runs, not DAX query time or CU. Latest review only; no complete-history claim.",
    "OwnerCoverage": "Check coverage before drawing conclusions. Available means collection returned evidence, not that the item passed. Missing inventory cannot establish that no items exist.",
}
_PAGES = [
    ("OwnerWorkspaces", "My workspaces"),
    ("OwnerFindings", "Findings"),
    ("OwnerTechnicalDetails", "Technical details"),
    ("OwnerExecutions", "Executions"),
    ("OwnerCoverage", "Coverage"),
]


def _latest_review_filter(page: str) -> dict[str, Any]:
    return {
        "name": _id(page, "filter", "latest-workspace-review"),
        "field": {
            "Column": {
                "Expression": {"SourceRef": {"Entity": REVIEW_TABLE}},
                "Property": "is_latest",
            },
        },
        "type": "Categorical",
        "isLockedInViewMode": True,
        "isHiddenInViewMode": True,
        "filter": {
            "Version": 2,
            "From": [{"Name": "r", "Entity": REVIEW_TABLE, "Type": 0}],
            "Where": [{
                "Condition": {
                    "In": {
                        "Expressions": [{
                            "Column": {
                                "Expression": {"SourceRef": {"Source": "r"}},
                                "Property": "is_latest",
                            },
                        }],
                        "Values": [[{"Literal": {"Value": "true"}}]],
                    },
                },
            }],
        },
    }


def _data_table(page: str, table: str, columns: list[str], title: str,
                *, y: int = 616, height: int = 210,
                measures: list[str] | None = None, key: str = "workspace-scoped-data",
                show_workspace: bool = True,
                widths: dict[str, int] | None = None) -> dict[str, Any]:
    projections = [_column(WORKSPACE_TABLE, "workspace_name")] if show_workspace else []
    projections.extend(_column(table, column) for column in columns)
    projections.extend(_measure(WORKSPACE_TABLE, name) for name in measures or [])
    if show_workspace:
        projections.append(_column(WORKSPACE_TABLE, "workspace_id"))
    visual = _visual(
        page, key, "tableEx", 16, y, PAGE_W - 32, height,
        {"Values": {"projections": projections}},
        title=title, tab=3,
    )
    objects = visual["visual"]["objects"]
    objects["columnHeaders"] = [{"properties": {
        "autoSizeColumnWidth": _lit("false"), "wordWrap": _lit("true"),
    }}]
    objects["values"] = [{"properties": {"wordWrap": _lit("true")}}]
    objects["total"] = [{"properties": {"totals": _lit("false")}}]
    objects["columnWidth"] = [
        {"properties": {"value": _lit(f"{widths[p['nativeQueryRef']]}D")},
         "selector": {"metadata": p["queryRef"]}}
        for p in projections if widths and p["nativeQueryRef"] in widths
    ]
    return visual


def _selector(page: str, key: str, entity: str, column: str,
              x: int, width: int, title: str, *, y: int = 140) -> dict[str, Any]:
    visual = _field_slicer(page, key, entity, column, x, y, width, 64, title, 0)
    visual["visual"]["objects"]["data"] = [{"properties": {"mode": _lit("'Dropdown'")}}]
    if entity == WORKSPACE_TABLE:
        visual["visual"]["syncGroup"] = {
            "groupName": f"OwnerScope-{column}", "fieldChanges": True, "filterChanges": True,
        }
    return visual


def _metric(page: str, key: str, measure: str, title: str,
            x: int, y: int, width: int, height: int, color: str,
            *, font_size: int = 28, background: str = CARD) -> dict[str, Any]:
    visual = _card(page, key, WORKSPACE_TABLE, measure, x, y, title, 0)
    visual["position"].update(width=width, height=height)
    objects = visual["visual"].setdefault("objects", {})
    objects["labels"] = [{"properties": {
        "fontSize": _lit(f"{font_size}D"), "color": _solid(color),
        "fontFamily": _lit("'Segoe UI Semibold'"),
    }}]
    objects["categoryLabels"] = [{"properties": {"show": _lit("false")}}]
    objects["wordWrap"] = [{"properties": {"show": _lit("true")}}]
    if not title:
        objects["title"] = [{"properties": {"show": _lit("false")}}]
    container = visual["visual"].setdefault("visualContainerObjects", {})
    container["background"] = [{"properties": {
        "show": _lit("true"), "color": _solid(background), "transparency": _lit("0D"),
    }}]
    container["general"] = [{"properties": {"altText": _lit(f"'{title or measure}'")}}]
    return visual


def _chart(page: str, key: str, entity: str, category: str, measure: str,
           x: int, width: int, title: str, *, y: int = 376, height: int = 228,
           donut: bool = False, colors: dict[str, str] | None = None) -> dict[str, Any]:
    builder = _donut if donut else _bar
    visual = builder(
        page, key, entity, category, measure, x, y, width, height, title, 0,
        measure=True, color=(entity, category, colors) if colors else None,
    )
    role = "Y"
    # The shared chart helpers assume measure and category share a table.
    # Owner measures live on the secured workspace dimension, not the fact.
    visual["visual"]["query"]["queryState"][role]["projections"] = [
        _measure(WORKSPACE_TABLE, measure),
    ]
    if not donut:
        visual["visual"]["query"]["sortDefinition"] = {
            "sort": [{"field": _measure(WORKSPACE_TABLE, measure)["field"], "direction": "Descending"}],
            "isDefaultSort": False,
        }
    return visual


def _chrome(page: str, display_name: str) -> list[dict[str, Any]]:
    visuals = [_banner(
        page, display_name,
        "WORKSPACE OWNER  /  Latest review per workspace  /  Sanitized static and runtime evidence",
    )]
    for index, (target, label) in enumerate(_PAGES):
        x, width = 16 + index * 154, 142
        visual = _action_button(
            page, f"nav-{target}", target, x, 94, width, 34, 0,
            label=label, fill=BRAND if target == page else CARD,
            fg="#FFFFFF" if target == page else BRAND,
        )
        visual["visual"]["visualContainerObjects"]["general"] = [{
            "properties": {"altText": _lit(f"'Navigate to {label}'")},
        }]
        visuals.append(visual)
    visuals.extend([
        _metric(page, "review-context", "Review window", "", 794, 94, 470, 34,
                BRAND, font_size=11, background="#DEECF9"),
        _selector(page, "workspace-selector", WORKSPACE_TABLE, "workspace_name",
                  16, 328, "Workspace"),
        _selector(page, "workspace-id-selector", WORKSPACE_TABLE, "workspace_id",
                  356, 356, "Workspace ID (disambiguates names)"),
        _metric(page, "evidence-notice", "Evidence notice", "", 16, 216, 1248, 46,
                _AMBER, font_size=11, background="#FFF4CE"),
        _info(
            page, "scope-note", "Partial assessment - never a full FAR score",
            _GUIDANCE[page] + " Authorized latest reviews only; no raw source. "
            "Access requires approval and daily sync, expiry within 24h. Static signals are not measured runtime impact.",
            16, 832, 1248, 76, 0,
        ),
    ])
    return visuals


def _visuals(page: str, display_name: str) -> list[dict[str, Any]]:
    visuals = _chrome(page, display_name)
    if page == "OwnerWorkspaces":
        visuals.extend([
            _selector(page, "evidence-selector", REVIEW_TABLE, "technical_evidence_status",
                      724, 260, "Technical evidence"),
            _info(page, "scope-help", "Start here",
                  "1 Choose a workspace. 2 Open Findings. 3 Validate the suggested change in the source item.",
                  996, 140, 268, 64, 0),
        ])
        metrics = [
            ("Scoped workspaces", "Reviewed workspaces", BRAND),
            ("Scoped findings", "Findings in view", _PURPLE),
            ("Scoped priority findings", "High + critical findings", BAD),
            ("Scoped missing evidence", "Workspaces missing evidence", _AMBER),
        ]
        visuals.extend([
            _chart(page, "findings-by-severity", FINDING_TABLE, "severity", "Scoped findings",
                   16, 612, "Priority context | findings by severity",
                   y=642, height=178, colors=SEV_HEX),
            _chart(page, "evidence-by-status", REVIEW_TABLE, "technical_evidence_status",
                   "Scoped workspaces", 644, 620,
                   "Evidence availability | partial is not complete",
                   y=642, height=178, donut=True, colors=_EVIDENCE_COLORS),
        ])
        visuals.append(_data_table(
            page, REVIEW_TABLE,
            ["assessment_status", "technical_evidence_status", "notice"],
            "Workspace review register | select a workspace ID above to focus",
            y=350, height=280,
            measures=["Latest review", "Findings", "Failed findings", "Technical details"],
            widths={"workspace_name": 160, "assessment_status": 120,
                    "technical_evidence_status": 130, "notice": 320,
                    "Latest review": 150, "Findings": 90, "Failed findings": 100,
                    "Technical details": 100, "workspace_id": 280},
        ))
    elif page == "OwnerFindings":
        visuals.extend([
            _selector(page, "severity-selector", FINDING_TABLE, "severity", 724, 260, "Severity"),
            _selector(page, "dimension-selector", FINDING_TABLE, "dimension", 996, 268, "Assessment area"),
        ])
        metrics = [
            ("Scoped findings", "Findings in view", BRAND),
            ("Scoped priority findings", "High + critical findings", BAD),
            ("Scoped DAX findings", "DAX findings", BRAND),
            ("Scoped notebook findings", "Notebook findings", _PURPLE),
        ]
        visuals.append(_data_table(
            page, FINDING_TABLE,
            ["severity", "item_name", "title", "affected_count", "item_id",
             "status", "rule_id", "signal_codes", "run_timestamp", "notice", "finding_key"],
            "1. Choose an action | select one row; scroll right for diagnostic IDs",
            y=350, height=260,
            widths={"workspace_name": 140, "severity": 85, "item_name": 185,
                    "title": 470, "affected_count": 120, "item_id": 280,
                    "status": 90, "rule_id": 100, "signal_codes": 220,
                    "run_timestamp": 160, "notice": 360, "finding_key": 300,
                    "workspace_id": 280},
        ))
        visuals.append(_data_table(
            page, FINDING_TABLE, ["title", "recommendation"],
            "2. Recommended next step | filtered by the action selected above; clear selection to show all",
            y=622, height=198, key="finding-guidance", show_workspace=False,
            widths={"title": 330, "recommendation": 860},
        ))
    elif page == "OwnerTechnicalDetails":
        visuals.extend([
            _selector(page, "detail-type-selector", DETAIL_TABLE, "detail_type", 724, 260, "Detail category"),
            _selector(page, "item-type-selector", DETAIL_TABLE, "item_type", 996, 268, "Item type"),
            _selector(page, "item-selector", DETAIL_TABLE, "item_name", 16, 416, "Item", y=376),
            _selector(page, "item-id-selector", DETAIL_TABLE, "item_id", 444, 416, "Item ID (exact selection)", y=376),
            _selector(page, "metric-selector", DETAIL_TABLE, "metric_name", 872, 392, "Metric / unit", y=376),
        ])
        metrics = [
            ("Scoped detail rows", "Detail records in view", BRAND),
            ("Scoped DAX rows", "DAX measure records", BRAND),
            ("Scoped static objects", "Non-measure DAX + queries", _PURPLE),
            ("Scoped table metric rows", "Model-table metric records", _TEAL),
        ]
        visuals.append(_data_table(
            page, DETAIL_TABLE,
            ["item_name", "table_name", "measure_name", "detail", "signal_codes",
             "metric_name", "metric_value", "detail_type", "item_id", "run_timestamp", "notice"],
            "3. Inspect evidence | select the item and category; values have different units, never a FAR score",
            y=452, height=368,
            widths={"workspace_name": 135, "item_name": 165, "table_name": 130,
                    "measure_name": 190, "detail": 530, "signal_codes": 220,
                    "metric_name": 180, "metric_value": 100, "detail_type": 140,
                    "item_id": 280, "run_timestamp": 160, "notice": 360,
                    "workspace_id": 280},
        ))
    elif page == "OwnerExecutions":
        visuals.extend([
            _selector(page, "status-selector", EXECUTION_TABLE, "status", 724, 260, "Run status"),
            _selector(page, "item-type-selector", EXECUTION_TABLE, "item_type", 996, 268, "Item type"),
            _selector(page, "item-selector", EXECUTION_TABLE, "item_name", 16, 612, "Item", y=350),
            _selector(page, "item-id-selector", EXECUTION_TABLE, "item_id", 644, 620, "Item ID (exact selection)", y=350),
        ])
        metrics = [
            ("Scoped executions", "Observed native executions", BRAND),
            ("Scoped failed executions", "Failed native executions", BAD),
            ("Scoped timed executions", "Executions with duration", _TEAL),
            ("Scoped maximum duration ms", "Maximum duration (ms)", _PURPLE),
        ]
        visuals.append(_data_table(
            page, EXECUTION_TABLE,
            ["item_name", "status", "start_time", "duration_ms", "end_time", "execution_id",
             "item_type", "item_id", "run_timestamp", "run_id", "execution_key"],
            "Native execution observations | scroll right for scoped IDs and review context; source errors withheld",
            y=430, height=390,
            widths={"workspace_name": 150, "item_name": 180, "status": 110, "start_time": 180,
                    "end_time": 180, "duration_ms": 140, "execution_id": 320,
                    "item_type": 140, "item_id": 280, "run_timestamp": 180, "run_id": 280,
                    "execution_key": 320, "workspace_id": 280},
        ))
    else:
        visuals.extend([
            _selector(page, "evidence-selector", COVERAGE_TABLE, "evidence_type", 724, 260, "Evidence category"),
            _selector(page, "status-selector", COVERAGE_TABLE, "collection_status", 996, 268, "Collection status"),
            _selector(page, "item-selector", COVERAGE_TABLE, "item_name", 16, 612, "Item", y=350),
            _selector(page, "item-id-selector", COVERAGE_TABLE, "item_id", 644, 620, "Item / workspace ID", y=350),
        ])
        metrics = [
            ("Scoped coverage rows", "Coverage records", BRAND),
            ("Scoped incomplete coverage", "Incomplete collection records", _AMBER),
            ("Scoped DAX object rows", "Non-measure DAX objects", _PURPLE),
            ("Scoped Dataflow rows", "Dataflow query records", _TEAL),
        ]
        visuals.append(_data_table(
            page, COVERAGE_TABLE,
            ["item_name", "evidence_type", "collection_status", "observed_count", "flagged_count",
             "history_scope", "item_id", "run_timestamp", "run_id"],
            "Coverage register | scroll right for scoped IDs and review context; missing evidence is not a pass",
            y=430, height=240,
            widths={"workspace_name": 150, "item_name": 200, "evidence_type": 200,
                    "collection_status": 160, "observed_count": 140, "flagged_count": 120,
                    "history_scope": 350, "item_id": 280, "run_timestamp": 180,
                    "run_id": 280, "workspace_id": 280},
        ))
        visuals.append(_data_table(
            page, COVERAGE_TABLE, ["item_name", "evidence_type", "notice"],
            "Collection notes | select a coverage row; clear selection to show all",
            y=682, height=138, key="coverage-notes", show_workspace=False,
            widths={"item_name": 180, "evidence_type": 180, "notice": 830},
        ))
    for index, (measure, title, color) in enumerate(metrics):
        visuals.append(_metric(
            page, f"kpi-{index}", measure, title, 16 + index * 316, 274, 300, 64, color,
            font_size=24,
        ))
    visuals.sort(key=lambda visual: (visual["position"]["y"], visual["position"]["x"]))
    for index, visual in enumerate(visuals):
        visual["position"].update(z=index, tabOrder=index)
        state = visual["visual"].get("query", {}).get("queryState", {})
        for role in state.values():
            for projection in role.get("projections", []):
                name = projection["nativeQueryRef"]
                projection["displayName"] = _FIELD_LABELS.get(
                    name, name.removeprefix("Scoped ").replace("_", " ").capitalize(),
                )
    return visuals


def _owner_theme() -> dict[str, Any]:
    theme = _theme()
    theme.update(name=THEME_NAME, dataColors=[BRAND, _PURPLE, _TEAL, "#E8702A", _AMBER, BAD])
    styles = theme["visualStyles"]
    styles["*"]["*"]["visualHeader"] = [{"show": True}]
    styles["*"]["*"]["dropShadow"] = [{"show": False}]
    styles["tableEx"]["*"]["grid"][0]["rowPadding"] = 7
    styles["tableEx"]["*"]["values"][0]["fontSize"] = 11
    styles["tableEx"]["*"]["columnHeaders"][0].update(fontSize=11, alignment="Left")
    styles["donutChart"]["*"]["legend"][0].update(position="Bottom", fontSize=10)
    styles["donutChart"]["*"]["labels"][0]["labelStyle"] = "Data"
    return theme


def build_parts(semantic_model_id: str) -> list[dict[str, str]]:
    """Return deterministic ``path``/``text`` PBIR parts for the owner model."""
    parts: list[dict[str, str]] = []

    def add(path: str, value: Any) -> None:
        parts.append({"path": path, "text": json.dumps(value, indent=2)})

    add("definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {
            "byConnection": {"connectionString": f"semanticmodelid={semantic_model_id}"},
        },
    })
    add("definition/version.json", {
        "$schema": f"{_SCHEMA}/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })
    add(f"StaticResources/RegisteredResources/{THEME_NAME}.json", _owner_theme())
    add("definition/report.json", {
        "$schema": f"{_SCHEMA}/report/1.0.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU10",
                "reportVersionAtImport": "5.55",
                "type": "SharedResources",
            },
            "customTheme": {
                "name": THEME_NAME,
                "reportVersionAtImport": "5.55",
                "type": "RegisteredResources",
            },
        },
        "resourcePackages": [{
            "name": "RegisteredResources",
            "type": "RegisteredResources",
            "items": [{"name": THEME_NAME, "path": f"{THEME_NAME}.json", "type": "CustomTheme"}],
        }],
        "layoutOptimization": "None",
    })
    add("definition/pages/pages.json", {
        "$schema": f"{_SCHEMA}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [page for page, _ in _PAGES],
        "activePageName": _PAGES[0][0],
    })
    for page, display_name in _PAGES:
        visuals = _visuals(page, display_name)
        page_definition = {
            "$schema": f"{_SCHEMA}/page/1.0.0/schema.json",
            "name": page,
            "displayName": display_name,
            "displayOption": "FitToPage",
            "height": PAGE_H,
            "width": PAGE_W,
            "objects": _page_background(),
            "filterConfig": {"filters": [_latest_review_filter(page)]},
        }
        if page in {"OwnerFindings", "OwnerCoverage"}:
            queue = _id(page, "workspace-scoped-data")
            guidance = _id(page, "finding-guidance" if page == "OwnerFindings" else "coverage-notes")
            page_definition["visualInteractions"] = [
                {"source": source, "target": target["name"],
                 "type": "DataFilter" if source == queue and target["name"] == guidance else "NoFilter"}
                for source in (queue, guidance)
                for target in visuals
                if target["name"] != source and target["visual"].get("query", {}).get("queryState")
            ]
        add(f"definition/pages/{page}/page.json", page_definition)
        for visual in visuals:
            add(f"definition/pages/{page}/visuals/{visual['name']}/visual.json", visual)
    if len({part["path"] for part in parts}) != len(parts):
        raise ValueError("Duplicate owner PBIR part paths.")
    return parts

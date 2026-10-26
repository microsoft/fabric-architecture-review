# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""PBIR building blocks that follow the FUAM report conventions.

Mirrors the FUAM Core report: visualContainer 2.5.0, page 2.0.0, report 3.3.0,
a 1920x1080 FitToPage canvas, a themed header band, KPI cards, titled bar and
line charts with drop shadows and 5px radii, a Home navigation button and a
"Description:" text box on every content page. Identifiers are deterministic.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Iterable

from reports.powerbi import artwork

PAGE_W, PAGE_H = 1920, 1080
BRAND, INK = "#248A85", "#121212"
BAR_FILL, BAD_FILL = "#84DCAA", "#F68B8B"
THEME_ID = "FAR_Tidal"
THEME_FILE = f"{THEME_ID}.json"
FONT_SEMIBOLD = "'Segoe UI Semibold', wf_segoe-ui_semibold, helvetica, arial, sans-serif"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/2.5.0/schema.json"
_NS = uuid.UUID("2a7d9e10-3b4c-4d5e-8f60-1a2b3c4d5e6f")

# Content grid, taken from the FUAM Capacities page (1920x1080 canvas).
MARGIN, GAP = 24, 8
ROW1_Y, ROW1_H = 92, 72
CARD_W, CARD_STEP = 165, 174
DESC_X, DESC_W = 1136, 760
CHART_H, ROW_GAP = 276, 24
BOTTOM = 1052
_LABELS = {"id": "ID", "sha256": "SHA256", "url": "URL", "json": "JSON", "sku": "SKU"}
_WIDE = {"recommendation": 420, "title": 320, "description": 300, "notice": 320, "detail": 300,
         "workspace_name": 200, "profile_reason": 240, "next_action": 420, "status_meaning": 380,
         "why_it_matters": 380, "how_to_fix": 380, "configure": 300, "what_it_checks": 420, "requires": 200}
# Short header text for long column names so headers are not clipped.
_SHORT = {
    "assigned_workspace_count": "Workspaces", "observed_item_count": "Items",
    "workspace_scope_limited": "Scope limited", "configuration_sha256": "Config hash",
    "profile_sha256": "Rule logic hash", "missing_evidence_count": "Missing evidence",
    "assessment_coverage": "Coverage", "assessed_count": "Assessed",
    "enabled_group_count": "Enabled groups", "excluded_group_count": "Excluded groups",
    "can_specify_security_groups": "Can scope", "last_changed_at": "Last changed",
    "not_applicable_count": "Not applicable", "workspace_scope": "Scope",
}
HOME_TILE_ROW, HOME_TILE_TOP = 148, 500


def make_id(*parts: str) -> str:
    """Deterministic 20-character hexadecimal PBIR name."""
    return uuid.uuid5(_NS, "|".join(parts)).hex[:20]


def lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def text_lit(value: str) -> dict:
    return lit("'" + value.replace("'", "''") + "'")


def solid(color: dict) -> dict:
    return {"solid": {"color": color}}


def theme_color(color_id: int, percent: float = 0) -> dict:
    return {"expr": {"ThemeDataColor": {"ColorId": color_id, "Percent": percent}}}


def hex_color(value: str) -> dict:
    return text_lit(value)


def label_for(prop: str) -> str:
    if prop in _SHORT:
        return _SHORT[prop]
    return " ".join(_LABELS.get(word, word.capitalize()) for word in prop.split("_"))


def projection(kind: str, entity: str, prop: str, *, active: bool | None = None, friendly: bool = False) -> dict:
    result: dict[str, Any] = {
        "field": {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
        "queryRef": f"{entity}.{prop}", "nativeQueryRef": label_for(prop) if friendly else prop}
    if active is not None:
        result["active"] = active
    if friendly:
        result["displayName"] = label_for(prop)
    return result


def column(entity: str, prop: str, **kwargs: Any) -> dict:
    return projection("Column", entity, prop, **kwargs)


def measure(entity: str, prop: str, **kwargs: Any) -> dict:
    return projection("Measure", entity, prop, **kwargs)


# Fixed colours for result states, so a status looks the same on every page.
STATUS_COLORS = {"pass": "#56C17B", "fail": "#F68B8B", "info": "#97B7F3", "unknown": "#FFA342",
                 "missing_evidence": "#D195EA", "not_applicable": "#C6C2BE"}
SEVERITY_COLORS = {"critical": "#F6465A", "high": "#F68B8B", "medium": "#FFA342", "low": "#FFD27F",
                   "info": "#97B7F3", "none": "#C6C2BE"}


def category_colors(entity: str, prop: str, colors: dict[str, str]) -> list[dict]:
    """One data-point colour per category value, in the structure Power BI itself writes."""
    return [{"properties": {"fill": solid(hex_color(color))},
             "selector": {"data": [{"scopeId": {"Comparison": {
                 "ComparisonKind": 0,
                 "Left": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
                 "Right": {"Literal": {"Value": "'" + value + "'"}}}}}]}}
            for value, color in colors.items()]


def page_filter(page: str, entity: str, prop: str, values: Iterable[str], *, boolean: bool = False) -> dict:
    """Hidden, locked categorical filter that viewers cannot clear or widen."""
    field = {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": prop}}
    return {"name": make_id(page, entity, prop), "type": "Categorical",
            "field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
            "isLockedInViewMode": True, "isHiddenInViewMode": True,
            "filter": {"Version": 2, "From": [{"Name": "s", "Entity": entity, "Type": 0}],
                       "Where": [{"Condition": {"In": {"Expressions": [field],
                                  "Values": [[{"Literal": {"Value": v if boolean else "'" + v + "'"}}]
                                             for v in values]}}}]}}


def _shadow(percent: float = -0.1) -> list[dict]:
    return [{"properties": {"show": lit("true"), "color": solid(theme_color(0, percent))}}]


def _frame(title: str | None, *, radius: str = "5D", title_size: str = "12D", tooltip: bool = False) -> dict:
    objects: dict[str, Any] = {}
    if title is not None:
        objects["title"] = [{"properties": {"show": lit("true"), "text": text_lit(title), "fontSize": lit(title_size)}}]
    objects["border"] = [{"properties": {"show": lit("true"), "radius": lit(radius)}}]
    objects["dropShadow"] = _shadow()
    if tooltip:
        objects["visualTooltip"] = [{"properties": {"type": lit("'Default'")}}]
    return objects


def _bare() -> dict:
    return {"background": [{"properties": {"show": lit("false")}}],
            "border": [{"properties": {"show": lit("false")}}]}


def _paragraph(text: str, **style: str) -> dict:
    return {"textRuns": [{"value": text, **({"textStyle": style} if style else {})}]}


class Canvas:
    """Collects the visuals of one page and allocates z-order and tab order."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.visuals: list[dict] = []

    def _add(self, key: str, rect: tuple[float, float, float, float], visual: dict,
             how_created: str | None = None) -> dict:
        x, y, w, h = rect
        if min(x, y) < 0 or w <= 0 or h <= 0 or x + w > PAGE_W or y + h > PAGE_H:
            raise ValueError(f"Visual {key} is outside the {PAGE_W}x{PAGE_H} canvas.")
        order = (len(self.visuals) + 1) * 1000
        item: dict[str, Any] = {
            "$schema": VISUAL_SCHEMA, "name": make_id(self.scope, key),
            "position": {"x": x, "y": y, "z": order, "height": h, "width": w, "tabOrder": order},
            "visual": {**visual, "drillFilterOtherVisuals": True}}
        if how_created:
            item["howCreated"] = how_created
        self.visuals.append(item)
        return item

    def text(self, key: str, rect: tuple, paragraphs: list[dict], *, card: bool = False) -> dict:
        container = _bare()
        if card:
            container = {"background": [{"properties": {"show": lit("true")}}], **_frame(None),
                         "padding": [{"properties": {"top": lit("6D"), "left": lit("8D"), "right": lit("8D")}}]}
        return self._add(key, rect, {"visualType": "textbox",
                                     "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
                                     "visualContainerObjects": container})

    def title(self, page_title: str, brand: str) -> dict:
        style = {"fontWeight": "bold", "fontSize": "14pt"}
        return self.text("title", (72, 16, 900, 40), [{"textRuns": [
            {"value": f"{brand} | ", "textStyle": {**style, "color": BRAND}},
            {"value": page_title, "textStyle": style}]}])

    def description(self, text: str, rect: tuple = (DESC_X, ROW1_Y, DESC_W, ROW1_H + 6), *,
                    key: str = "description", color: str | None = None, heading: str = "Description:") -> dict:
        extra = {"color": color} if color else {}
        return self._add(key, rect, {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [
            _paragraph(heading, fontWeight="bold", fontSize="10pt", **extra),
            _paragraph(text, fontSize="10pt", **extra)]}}]}, "visualContainerObjects": _bare()})

    def home_button(self, home_page: str) -> dict:
        icon = {"properties": {
            "shapeType": lit("'custom'"), "lineColor": solid(theme_color(2)),
            "image": {"image": {"name": text_lit("icon_home.png"), "url": {"expr": {"ResourcePackageItem": {
                "PackageName": "RegisteredResources", "PackageType": 1, "ItemName": icon_resource_name()}}},
                "scaling": text_lit("Fit")}}}, "selector": {"id": "default"}}
        return self._add("home-button", (1844, 12, 48, 50), {
            "visualType": "actionButton", "objects": {"icon": [icon]}, "visualContainerObjects": {
                "visualLink": [{"properties": {"show": lit("true"), "type": text_lit("PageNavigation"),
                                               "navigationSection": text_lit(home_page),
                                               "tooltip": text_lit("Go to home page")}}],
                "border": [{"properties": {"show": lit("true"), "radius": lit("12D")}}],
                "background": [{"properties": {"show": lit("true")}}]}}, "InsertVisualButton")

    def back_button(self) -> dict:
        icon = {"properties": {"shapeType": lit("'back'"), "lineColor": solid(theme_color(2))},
                "selector": {"id": "default"}}
        return self._add("back-button", (1788, 12, 48, 50), {
            "visualType": "actionButton", "objects": {"icon": [icon]}, "visualContainerObjects": {
                "visualLink": [{"properties": {"show": lit("true"), "type": text_lit("Back"),
                                               "tooltip": text_lit("Back to the previous page")}}],
                "border": [{"properties": {"show": lit("true"), "radius": lit("12D")}}],
                "background": [{"properties": {"show": lit("true")}}]}}, "InsertVisualButton")

    def nav_button(self, key: str, rect: tuple, label: str, target_page: str) -> dict:
        text = [{"properties": {"show": lit("true")}},
                {"properties": {"text": text_lit(label), "fontColor": solid(theme_color(0)),
                                "fontSize": lit("14D"), "fontFamily": text_lit(FONT_SEMIBOLD)},
                 "selector": {"id": "default"}}]
        return self._add(key, rect, {"visualType": "actionButton", "objects": {
            "icon": [{"properties": {"shapeType": lit("'blank'")}, "selector": {"id": "default"}},
                     {"properties": {"show": lit("false")}}],
            "text": text,
            "outline": [{"properties": {"show": lit("false")}}],
            "shape": [{"properties": {"roundEdge": lit("8L")}, "selector": {"id": "default"}}],
            "fill": [{"properties": {"show": lit("true")}},
                     {"properties": {"fillColor": solid(theme_color(2, -0.25)), "transparency": lit("0D")},
                      "selector": {"id": "default"}}]},
            "visualContainerObjects": {
                "visualLink": [{"properties": {"show": lit("true"), "type": text_lit("PageNavigation"),
                                               "navigationSection": text_lit(target_page),
                                               "tooltip": text_lit(f"Go to {label}")}}],
                "border": [{"properties": {"show": lit("false")}}]}}, "InsertVisualButton")

    def card(self, key: str, rect: tuple, entity: str, name: str, *, label_size: str = "18D",
             category_size: str = "9D", light: bool = False) -> dict:
        objects: dict[str, Any] = {"general": [{"properties": {}}], "labels": [{"properties": {
            "fontSize": lit(label_size), "labelDisplayUnits": lit("1D")}}],
            "categoryLabels": [{"properties": {"fontSize": lit(category_size)}}]}
        container = {"dropShadow": [{"properties": {"show": lit("true"), "color": solid(theme_color(0, -0.2))}}],
                     "border": [{"properties": {"radius": lit("5D")}}]}
        if light:
            objects["labels"][0]["properties"]["color"] = solid(theme_color(0))
            objects["categoryLabels"][0]["properties"]["color"] = solid(theme_color(0))
            container = {**_bare(), "dropShadow": [{"properties": {"show": lit("false")}}]}
        return self._add(key, rect, {"visualType": "card", "query": {"queryState": {"Values": {
            "projections": [measure(entity, name)]}}}, "objects": objects,
            "visualContainerObjects": container})

    def chart(self, key: str, rect: tuple, kind: str, title: str, category: tuple[str, str],
              values: list[tuple[str, str]], *, fill: str = BAR_FILL, ascending: bool = False,
              colors: dict[str, str] | None = None) -> dict:
        """bar/column/line chart with one category column and one or more measures."""
        sort_field = ({"Column": {"Expression": {"SourceRef": {"Entity": category[0]}}, "Property": category[1]}}
                      if ascending else
                      {"Measure": {"Expression": {"SourceRef": {"Entity": values[0][0]}}, "Property": values[0][1]}})
        query = {"queryState": {
            "Category": {"projections": [column(*category, active=True)]},
            "Y": {"projections": [measure(*v) for v in values]}},
            "sortDefinition": {"sort": [{"field": sort_field,
                                         "direction": "Ascending" if ascending else "Descending"}],
                               "isDefaultSort": True}}
        objects: dict[str, Any] = {"labels": [{"properties": {"show": lit("true")}}]}
        if kind == "lineChart":
            objects["categoryAxis"] = [{"properties": {"show": lit("true"), "axisType": lit("'Categorical'"),
                                                       "gridlineShow": lit("true")}}]
            objects["valueAxis"] = [{"properties": {"showAxisTitle": lit("false")}}]
            objects["lineStyles"] = [{"properties": {"showMarker": lit("true"), "markerSize": lit("5D")}}]
        else:
            objects["categoryAxis"] = [{"properties": {"maxMarginFactor": lit("50L")}}]
        if len(values) > 1:
            objects["legend"] = [{"properties": {"show": lit("true"), "position": lit("'Top'")}}]
        else:
            color = solid(hex_color(fill)) if kind != "lineChart" else solid(theme_color(2))
            objects["dataPoint"] = (category_colors(*category, colors) if colors
                                    else [{"properties": {"fill": color}}])
        return self._add(key, rect, {"visualType": kind, "query": query, "objects": objects,
                                     "visualContainerObjects": _frame(title, tooltip=True)})

    def table(self, key: str, rect: tuple, title: str, entity: str, columns: list[str | tuple[str, str]],
              *, sort: str | tuple[str, str] | None = None, descending: bool = False) -> dict:
        """Table of columns from `entity`; (entity, column) pairs pull related-table columns."""
        width = rect[2]
        refs = [c if isinstance(c, tuple) else (entity, c) for c in columns]
        query: dict[str, Any] = {"queryState": {"Values": {"projections": [
            column(e, name, friendly=True) for e, name in refs]}}}
        if sort:
            sort_entity, sort_name = sort if isinstance(sort, tuple) else (entity, sort)
            query["sortDefinition"] = {"sort": [{"field": {"Column": {"Expression": {
                "SourceRef": {"Entity": sort_entity}}, "Property": sort_name}},
                "direction": "Descending" if descending else "Ascending"}]}
        natural = [float(_WIDE.get(name, max(96, len(label_for(name)) * 8 + 32))) for _, name in refs]
        scale = min(1.0, max(0.8, (width - 24) / sum(natural)))
        widths = [{"properties": {"value": lit(f"{min(size * scale, width * 0.45):.1f}D")},
                   "selector": {"metadata": f"{e}.{name}"}} for (e, name), size in zip(refs, natural)]
        objects: dict[str, Any] = {
            "columnHeaders": [{"properties": {"fontFamily": text_lit(FONT_SEMIBOLD),
                                              "backColor": solid(theme_color(2)),
                                              "fontColor": solid(theme_color(0))}}],
            "grid": [{"properties": {"gridVertical": lit("true"), "rowPadding": lit("3D")}}],
            "values": [{"properties": {"backColorSecondary": solid(hex_color("#F5F6F5"))}}]}
        if widths:
            objects["columnWidth"] = widths
        return self._add(key, rect, {"visualType": "tableEx", "query": query, "objects": objects,
                                     "visualContainerObjects": _frame(title)})

    def rich(self, key: str, rect: tuple, spec: dict) -> dict:
        """Any supported chart: the simple bar/line family or the richer FUAM-style visuals.

        spec: kind, title, category (entity, column), values [(entity, measure)], and optionally
        series (entity, column), values2 for the line of a combo chart, colors (value -> hex) and
        ascending. Query roles follow the visuals in the FUAM reports.
        """
        kind = spec["kind"]
        if kind in {"barChart", "columnChart", "clusteredBarChart", "clusteredColumnChart", "lineChart"}:
            return self.chart(key, rect, kind, spec["title"], spec["category"], spec["values"],
                              fill=spec.get("fill", BAR_FILL), ascending=spec.get("ascending", False),
                              colors=spec.get("colors"))
        values = [measure(*v) for v in spec["values"]]
        category = {"projections": [column(*spec["category"], active=True)]}
        if kind == "donutChart":
            state = {"Category": category, "Y": {"projections": values}}
        elif kind == "treemap":
            state = {"Group": category, "Values": {"projections": values}}
        elif kind in {"ribbonChart", "hundredPercentStackedBarChart", "stackedAreaChart", "stackedBarChart",
                      "stackedColumnChart", "hundredPercentStackedColumnChart"}:
            state = {"Category": category, "Series": {"projections": [column(*spec["series"], active=True)]},
                     "Y": {"projections": values}}
        elif kind == "lineClusteredColumnComboChart":
            state = {"Category": category, "Y": {"projections": values},
                     "Y2": {"projections": [measure(*v) for v in spec["values2"]]}}
        else:
            raise ValueError(f"Unsupported chart kind: {kind}")
        if spec.get("ascending"):
            order = {"Column": {"Expression": {"SourceRef": {"Entity": spec["category"][0]}},
                                "Property": spec["category"][1]}}
        else:
            order = {"Measure": {"Expression": {"SourceRef": {"Entity": spec["values"][0][0]}},
                                 "Property": spec["values"][0][1]}}
        query = {"queryState": state, "sortDefinition": {"sort": [{
            "field": order, "direction": "Ascending" if spec.get("ascending") else "Descending"}],
            "isDefaultSort": True}}
        objects: dict[str, Any] = {"labels": [{"properties": {"show": lit("true")}}]}
        if kind in {"donutChart", "treemap"}:
            objects["legend"] = [{"properties": {"show": lit("true" if kind == "donutChart" else "false"),
                                                 "position": lit("'Right'")}}]
        elif "series" in spec or kind == "lineClusteredColumnComboChart":
            objects["legend"] = [{"properties": {"show": lit("true"), "position": lit("'Top'")}}]
        if spec.get("colors"):
            target = spec.get("series") or spec["category"]
            objects["dataPoint"] = category_colors(*target, spec["colors"])
        return self._add(key, rect, {"visualType": kind, "query": query, "objects": objects,
                                     "visualContainerObjects": _frame(spec["title"], tooltip=True)})

    def detail(self, key: str, rect: tuple, title: str, entity: str, columns: list[str]) -> dict:
        """Multi-row card: one block per row of the selected or filtered records."""
        query = {"queryState": {"Values": {"projections": [column(entity, name, friendly=True) for name in columns]}}}
        objects = {"categoryLabels": [{"properties": {"show": lit("true"), "fontSize": lit("9D")}}],
                   "dataLabels": [{"properties": {"fontSize": lit("10D")}}]}
        return self._add(key, rect, {"visualType": "multiRowCard", "query": query, "objects": objects,
                                     "visualContainerObjects": _frame(title)})

    def matrix(self, key: str, rect: tuple, title: str, rows: list[tuple[str, str]],
               values: list[tuple[str, str]], columns: list[tuple[str, str]] | None = None) -> dict:
        state: dict[str, Any] = {"Rows": {"projections": [column(*r, active=True) for r in rows]},
                                 "Values": {"projections": [measure(*v) for v in values]}}
        if columns:
            state["Columns"] = {"projections": [column(*c, active=True) for c in columns]}
        objects = {
            "columnHeaders": [{"properties": {"fontFamily": text_lit(FONT_SEMIBOLD), "backColor": solid(theme_color(2)),
                                              "fontColor": solid(theme_color(0))}}],
            "grid": [{"properties": {"gridVertical": lit("true"), "rowPadding": lit("3D")}}],
            "values": [{"properties": {"backColorSecondary": solid(hex_color("#F5F6F5"))}}]}
        return self._add(key, rect, {"visualType": "pivotTable", "query": {"queryState": state}, "objects": objects,
                                     "visualContainerObjects": _frame(title)})

    def slicer(self, key: str, rect: tuple, entity: str, prop: str, header: str) -> dict:
        return self._add(key, rect, {"visualType": "slicer", "query": {"queryState": {"Values": {
            "projections": [column(entity, prop, active=True)]}}, "sortDefinition": {"isDefaultSort": True}},
            "objects": {
                "data": [{"properties": {"mode": lit("'Dropdown'")}}],
                "items": [{"properties": {"textSize": lit("8D")}}],
                "header": [{"properties": {"textSize": lit("9D"), "text": text_lit(header)}}],
                "general": [{"properties": {"selfFilterEnabled": lit("true")}}],
                "selection": [{"properties": {"singleSelect": lit("false"),
                                              "selectAllCheckboxEnabled": lit("true")}}]},
            "visualContainerObjects": {**_frame(None), "title": [{"properties": {
                "show": lit("false"), "text": text_lit(""), "fontSize": lit("12D")}}]}})


def entities_of(visuals: list[dict]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            entity = node.get("SourceRef", {}).get("Entity") if isinstance(node.get("SourceRef"), dict) else None
            if entity:
                found.add(entity)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(visuals)
    return found


def row_widths(total: int, count: int, gap: int = GAP) -> list[int]:
    """Split a row into equal integer widths; the last one absorbs the remainder."""
    base = (total - gap * (count - 1)) // count
    widths = [base] * count
    widths[-1] += total - gap * (count - 1) - base * count
    return widths


def build_content_page(
    report: str, key: str, display: str, *, brand: str, home_page: str, description: str,
    cards: list[tuple[str, str]], slicers: list[tuple[str, str, str]] | None = None,
    charts: list[dict] | None = None, tables: list[dict] | None = None,
    slicer_row: int = 1, notice: str | None = None, chart_rows: list[dict] | None = None,
    back_button: bool = False,
) -> tuple[Canvas, dict]:
    """Lay out a FUAM-style content page: header, KPI row, charts, then tables."""
    if len(description) + len(notice or "") > 330:
        raise ValueError("Page description and notice must stay under 330 characters to fit the text box.")
    canvas = Canvas(f"{report}|{key}")
    canvas.title(display, brand)
    canvas.home_button(home_page)
    if back_button:
        canvas.back_button()
    for index, (entity, name) in enumerate(cards):
        canvas.card(f"card-{index}", (MARGIN + index * CARD_STEP, ROW1_Y, CARD_W, ROW1_H), entity, name)
    if slicer_row == 1:
        canvas.description(description + (" " + notice if notice else ""))
    else:
        # The KPI row is short here, so the description fills the space to its right.
        left = MARGIN + len(cards) * CARD_STEP + 10
        canvas.description(description + (" " + notice if notice else ""),
                           (left, ROW1_Y, PAGE_W - MARGIN - left, ROW1_H + 6))
    y = 180
    slicers = slicers or []
    if slicers and slicer_row == 1:
        left = MARGIN + len(cards) * CARD_STEP + 10
        widths = row_widths(1120 - left, len(slicers))
        if min(widths) < 150:
            raise ValueError("Too many slicers for the KPI row.")
        for index, (entity, prop, header) in enumerate(slicers):
            canvas.slicer(f"slicer-{index}", (left + sum(widths[:index]) + GAP * index, ROW1_Y, widths[index], ROW1_H),
                          entity, prop, header)
    elif slicers:
        widths = row_widths(PAGE_W - 2 * MARGIN, len(slicers))
        for index, (entity, prop, header) in enumerate(slicers):
            canvas.slicer(f"slicer-{index}", (MARGIN + sum(widths[:index]) + GAP * index, 172, widths[index], 64),
                          entity, prop, header)
        y = 248
    charts = charts or []
    rows = [{"h": CHART_H, "charts": charts}] if charts else []
    rows += chart_rows or []
    for row_index, row in enumerate(rows):
        specs = row["charts"]
        weights = [spec.get("weight", 1) for spec in specs]
        usable = PAGE_W - 2 * MARGIN - GAP * (len(specs) - 1)
        widths = [usable * weight // sum(weights) for weight in weights]
        widths[-1] += usable - sum(widths)
        x = MARGIN
        for index, spec in enumerate(specs):
            canvas.rich(f"chart-{row_index}-{index}", (x, y, widths[index], row["h"]), spec)
            x += widths[index] + GAP
        y += row["h"] + ROW_GAP
    tables = tables or []
    if tables:
        if BOTTOM - y < 160:
            raise ValueError(f"Page {key} leaves no room for its tables.")
        weights = [spec.get("weight", len(spec.get("columns", ())) or 1) for spec in tables]
        usable = PAGE_W - 2 * MARGIN - GAP * (len(tables) - 1)
        widths = [usable * weight // sum(weights) for weight in weights]
        widths[-1] += usable - sum(widths)
        x = MARGIN
        for index, spec in enumerate(tables):
            rect = (x, y, widths[index], BOTTOM - y)
            kind = spec.get("kind", "tableEx")
            if kind == "pivotTable":
                item = canvas.matrix(f"table-{index}", rect, spec["title"], spec["rows"], spec["values"],
                                     spec.get("columns"))
            elif kind == "multiRowCard":
                item = canvas.detail(f"table-{index}", rect, spec["title"], spec["entity"], spec["columns"])
            else:
                item = canvas.table(f"table-{index}", rect, spec["title"], spec["entity"], spec["columns"],
                                    sort=spec.get("sort"), descending=spec.get("descending", False))
            if spec.get("where"):
                # A locked filter on this visual only, for example failures only.
                item["filterConfig"] = {"filters": [
                    page_filter(f"{report}|{key}|{index}", entity, prop, values)
                    for entity, prop, values in spec["where"]]}
            x += widths[index] + GAP
    return canvas, {"key": key, "background": "content"}


def build_home_page(
    report: str, display: str, *, brand: str, headline: str, summary: str, notice: str,
    cards: list[tuple[str, str]], navigation: list[tuple[str, str, str, str]], overview: str,
    status_card: tuple[str, str] | None = None,
) -> tuple[Canvas, dict]:
    """Home page: hero band, KPI cards and one navigation tile per report page.

    navigation items are (page id, button label, page description, tooltip key).
    """
    canvas = Canvas(f"{report}|home")
    canvas.title(display, brand)
    white = {"color": "#FFFFFF"}
    canvas.text("headline", (48, 104, 1100, 56), [_paragraph(headline, fontWeight="bold", fontSize="28pt", **white)])
    canvas.text("summary", (48, 164, 1100, 140), [_paragraph(summary, fontSize="14pt", **white)])
    canvas.description(notice, (1180, 104, 692, 120), key="notice", color="#FFFFFF", heading="Scope:")
    if status_card:
        canvas.card("status", (1180, 236, 692, 64), *status_card, label_size="16D", category_size="9D", light=True)
    widths = row_widths(PAGE_W - 2 * MARGIN, len(cards), 19)
    for index, (entity, name) in enumerate(cards):
        canvas.card(f"kpi-{index}", (MARGIN + sum(widths[:index]) + 19 * index, 336, widths[index], 110),
                    entity, name, label_size="28D", category_size="11D")
    canvas.text("explore", (MARGIN, 462, 600, 32), [_paragraph("Explore the report", fontWeight="bold",
                                                              fontSize="14pt", color=BRAND)])
    tile_w = (PAGE_W - 2 * MARGIN - 3 * 16) // 4
    rows = -(-len(navigation) // 4)
    for index, (page_id, label, blurb, tile_key) in enumerate(navigation):
        x = MARGIN + (index % 4) * (tile_w + 16)
        y = HOME_TILE_TOP + (index // 4) * HOME_TILE_ROW
        canvas.nav_button(f"nav-{tile_key}", (x, y, tile_w, 56), label, page_id)
        canvas.text(f"blurb-{tile_key}", (x, y + 64, tile_w, 76), [_paragraph(blurb, fontSize="11pt")], card=True)
    canvas.description(overview, (MARGIN, HOME_TILE_TOP + rows * HOME_TILE_ROW + 8, PAGE_W - 2 * MARGIN, 84),
                       key="overview-description")
    return canvas, {"key": "home", "background": "home"}


def icon_resource_name() -> str:
    return f"icon_home{_digest(artwork.home_icon())}.png"


def background_resource_name(kind: str) -> str:
    data = artwork.home_background() if kind == "home" else artwork.content_background()
    return f"far_{kind}_bg{_digest(data)}.png"


def _digest(data: bytes) -> str:
    return str(int(hashlib.sha256(data).hexdigest()[:13], 16))


def theme() -> dict:
    """Report theme "FAR Tidal": the FUAM Accessible Tidal structure with a trimmed palette."""
    return {
        "name": "FAR Tidal",
        "dataColors": [BRAND, "#3DCBFF", "#3D7DEB", "#D195EA", "#56C17B", "#97B7F3", "#3EE5C9", "#FFA342",
                       "#84DCAA", "#F69CCC", "#F68B8B", "#8E8CD8"],
        "foreground": INK, "background": "#FFFFFF", "foregroundNeutralSecondary": "#6C6966",
        "backgroundLight": "#E5E0DA", "foregroundNeutralTertiary": "#ABA7A2", "backgroundNeutral": "#C6C2BE",
        "tableAccent": BRAND, "maximum": "#F69CCC", "center": "#FFA342", "minimum": "#3D7DEB",
        "bad": "#F6465A", "neutral": "#3DD9EB", "good": "#37CCB7",
        "textClasses": {"callout": {"fontSize": 25}, "title": {"fontSize": 10}, "header": {"fontSize": 10}},
        "visualStyles": {
            "*": {"*": {"border": [{"color": {"solid": {"color": "#FFFFFF"}}, "show": True, "radius": 8}],
                        "outspacePane": [{"titleSize": 10}]}},
            "page": {"*": {"background": [{"color": {"solid": {"color": "#EAEAEA"}}, "transparency": 50}]}}}}


def _page_json(page: dict) -> dict:
    objects: dict[str, Any] = {
        "displayArea": [{"properties": {"verticalAlignment": text_lit("Middle")}}],
        "background": [{"properties": {"image": {"image": {
            "name": text_lit(f"far_{page['background']}_bg.png"),
            "url": {"expr": {"ResourcePackageItem": {"PackageName": "RegisteredResources", "PackageType": 1,
                                                      "ItemName": background_resource_name(page["background"])}}},
            "scaling": text_lit("Fill")}}, "transparency": lit("0D")}}]}
    if page["background"] == "home":
        objects["outspacePane"] = [{"properties": {"width": lit("232L")}}]
    result = {"$schema": f"{SCHEMA}/page/2.0.0/schema.json", "name": page["name"], "displayName": page["display"],
              "displayOption": "FitToPage", "height": PAGE_H, "width": PAGE_W,
              "filterConfig": {"filters": page["filters"]}, "objects": objects}
    drill = page.get("drill")
    if drill:
        # A drill-through target: hidden from the page list, reached from the field it is bound to.
        result.update({"type": "Drillthrough", "visibility": "HiddenInViewMode", "pageBinding": {
            "name": drill["name"], "type": "Drillthrough", "referenceScope": "Default",
            "acceptsFilterContext": "None",
            "parameters": [{"name": drill["parameter"], "boundFilter": drill["filter"], "fieldExpr": drill["field"]}]}})
    return result


def report_parts(semantic_model_id: str, pages: list[dict]) -> list[dict[str, str]]:
    """Assemble definition.pbir, report/page/visual JSON, theme and generated PNGs."""
    parts: list[dict[str, str]] = []

    def add(path: str, obj: Any) -> None:
        parts.append({"path": path, "text": json.dumps(obj, indent=2)})

    kinds = sorted({page["background"] for page in pages})
    images = [(background_resource_name(kind), artwork.home_background() if kind == "home"
               else artwork.content_background()) for kind in kinds]
    images.append((icon_resource_name(), artwork.home_icon()))
    version = {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"}
    add("definition.pbir", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
                            "version": "4.0", "datasetReference": {"byConnection": {
                                "connectionString": f"semanticmodelid={semantic_model_id}"}}})
    add("definition/version.json", {"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    add("definition/report.json", {
        "$schema": f"{SCHEMA}/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": version, "type": "SharedResources"},
            "customTheme": {"name": THEME_FILE, "reportVersionAtImport": version, "type": "RegisteredResources"}},
        "objects": {"section": [{"properties": {"verticalAlignment": text_lit("Top")}}],
                    "outspacePane": [{"properties": {"expanded": lit("false")}}]},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources", "items": [
            {"name": THEME_FILE, "path": THEME_FILE, "type": "CustomTheme"},
            *[{"name": name, "path": name, "type": "Image"} for name, _ in images]]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                     "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True},
        "slowDataSourceSettings": {"isCrossHighlightingDisabled": False, "isSlicerSelectionsButtonEnabled": False,
                                   "isFilterSelectionsButtonEnabled": False, "isFieldWellButtonEnabled": False,
                                   "isApplyAllButtonEnabled": False}})
    add("definition/pages/pages.json", {"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                                        "pageOrder": [p["name"] for p in pages], "activePageName": pages[0]["name"]})
    for page in pages:
        add(f"definition/pages/{page['name']}/page.json", _page_json(page))
        for visual in page["visuals"]:
            add(f"definition/pages/{page['name']}/visuals/{visual['name']}/visual.json", visual)
    add(f"StaticResources/RegisteredResources/{THEME_FILE}", theme())
    for name, data in images:
        parts.append({"path": f"StaticResources/RegisteredResources/{name}", "b64": base64.b64encode(data).decode("ascii")})
    return parts

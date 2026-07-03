# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate the *Fabric Arch Review - Governance* report in PBIR format.

PBIR (the enhanced report format) stores the report as many small JSON files:

    definition.pbir
    definition/report.json
    definition/pages/pages.json
    definition/pages/<pageId>/page.json
    definition/pages/<pageId>/visuals/<visualId>/visual.json

We generate every part from a compact page spec so the eight governance pages
stay consistent and bind to the Direct Lake model produced by
:mod:`reports.powerbi.semantic_model`. Visuals are limited to well-documented
types (card, slicer, tableEx, clusteredColumnChart, textbox) and a custom
theme (``StaticResources/RegisteredResources``) gives the report a clean,
FUAM-style look: a brand header banner per page, KPI cards, plain-language
explanations of every metric, and clickable links.

The report connects **live** to the semantic model by id (byConnection), so it
needs no embedded data.

DATA SAFETY: builds report metadata only.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

_NS = uuid.UUID("2a7d9e10-3b4c-4d5e-8f60-1a2b3c4d5e6f")

_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
PAGE_W, PAGE_H = 1280, 720

# ---- FUAM-style palette + custom theme ----------------------------------
# NOTE: Fabric/Power BI caches a registered theme *by name* across deploys, so
# editing the theme body without renaming it keeps the stale (base CY24SU10)
# palette. Bump this suffix whenever the theme changes to force a fresh load.
THEME_NAME = "FabricArchReviewGov_v2"
BRAND = "#0F6CBD"   # primary blue (headers, callouts, bars)
BRAND_DK = "#0A4A82"
GOOD = "#107C10"    # pass / green
WARN = "#C19C00"    # caution / amber (kept for the generic theme palette)
BAD = "#D13438"     # fail / red — also High severity
CRIT = "#A4262C"    # Critical severity — deep red, one step past High
ORANGE = "#E8702A"  # Medium severity — orange
LOWBLUE = "#2B88D8" # Low severity — informational blue
INK = "#242424"     # primary text
MUTED = "#605E5C"   # secondary text
LINE = "#E1DFDD"    # borders / gridlines
CANVAS = "#F3F2F1"  # page background
CARD = "#FFFFFF"    # visual background
BANNER_SUB = "#DEECF9"

# Microsoft-certified "Radar Chart" public custom visual (web / spider chart).
# Declared in report.json -> publicCustomVisuals so the radar renders live; if a
# tenant blocks custom visuals it degrades to a placeholder without breaking the
# rest of the report. GUID + data roles verified from the visual's pbiviz.json /
# capabilities.json (roles: "Category" grouping, "Y" measure).
RADAR_VISUAL = "RadarChart1446119667547"

# Microsoft-certified "Sankey" public custom visual (flow / lineage diagram).
# GUID is the CURRENT published id from microsoft/powerbi-visuals-sankey
# pbiviz.json (v3.5) - the visual was rewritten and re-guid'd, so the legacy
# "Sankey1446581057829" id no longer resolves (that was why the estate flow
# rendered empty while the radar, whose GUID was current, rendered fine).
# Roles (capabilities.json): "Source" grouping, "Destination" grouping, "Weight"
# measure. Declared in report.json -> publicCustomVisuals; degrades to a
# placeholder if a tenant blocks custom visuals.
SANKEY_VISUAL = "sankey02300D1BE6F5427989F3DE31CCA9E0F32020"

# Microsoft-certified "Sunburst" public custom visual (multilevel donut for
# hierarchical composition). GUID from microsoft/powerbi-visuals-sunburst
# pbiviz.json (stable legacy id, same era as the working Radar). Roles
# (capabilities.json): "Nodes" grouping (one projection per ring, outermost
# last) + "Values" measure. Used for the tenant-settings severity -> status
# compliance breakdown.
SUNBURST_VISUAL = "Sunburst1445472000808"

# Severity / status palettes — a fixed semantic ramp: red = worst, then orange,
# then blue for Low, with GREEN RESERVED FOR PASS ONLY. These hex values are
# baked straight into each visual's dataPoint fill (see _value_data_colors), so
# slices/bars colour by value regardless of the tenant's theme AND without
# needing any model measure.
SEV_COLORS = {"Critical": CRIT, "High": BAD, "Medium": ORANGE, "Low": LOWBLUE, "Info": MUTED}
# Lower-case severity keys matching how gold_layer stores the data (every
# severity is .lower()'d before it lands in the gold tables). This is the map
# bound into every severity visual's dataPoint via _value_data_colors.
SEV_HEX = {"critical": CRIT, "high": BAD, "medium": ORANGE, "low": LOWBLUE, "info": MUTED}
# Status palette — pass green, fail red, info / not-evaluated grey.
STATUS_COLORS = {"pass": GOOD, "fail": BAD, "info": MUTED, "grey": MUTED}

# Per-page category accent (matches the Home hero orb colours). Painted as a
# thin accent line under each page banner to tie the inside pages to the hero.
ACCENT = {
    "Overview": BRAND,
    "Trends": "#2E86AB",
    "EstateMap": "#5B8DEF",
    "Architecture": "#4F6BED",
    "Performance": "#038387",
    "Cost": "#CA7A25",
    "Governance": "#8764B8",
    "Security": "#C43D57",
    "TenantSettings": "#2D79FF",
    "BestPractices": "#107C41",
    "SemanticModels": "#23A99A",
    "ModelDetail": "#1E8A97",
    "ModelInternals": "#1E8A97",
    "Notebooks": "#B88745",
}


def _id(*parts: str) -> str:
    return uuid.uuid5(_NS, "|".join(parts)).hex


# ---- formatting literal helpers -----------------------------------------

def _lit(value: str) -> Dict[str, Any]:
    """Wrap a raw formatting literal (e.g. ``"'#fff'"``, ``"true"``, ``"0D"``)."""
    return {"expr": {"Literal": {"Value": value}}}


def _solid(hex_color: str) -> Dict[str, Any]:
    return {"solid": {"color": _lit(f"'{hex_color}'")}}


def _value_data_colors(entity: str, column: str, mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """Bake a fixed colour per category value straight into the visual's
    ``dataPoint`` object. Each entry pins ``column == value`` via a scopeId
    selector to a literal hex, so slices/bars colour by meaning (severity red
    -> green) with NO dependency on a model measure or the tenant theme -- this
    is exactly what Power BI emits for manual per-category data colours. So the
    report carries its own palette and a report-only redeploy recolours it.
    ``column`` must be one of the visual's projected fields (category or series)
    and ``value`` keys must match the stored casing (severity/status are
    lower-cased in gold_layer)."""
    points: List[Dict[str, Any]] = []
    for value, hex_color in mapping.items():
        points.append({
            "properties": {"fill": _solid(hex_color)},
            "selector": {"data": [{"scopeId": {"Comparison": {
                "ComparisonKind": 0,
                "Left": {"Column": {
                    "Expression": {"SourceRef": {"Entity": entity}},
                    "Property": column,
                }},
                "Right": {"Literal": {"Value": f"'{value}'"}},
            }}}]},
        })
    return points


def _apply_data_color(visual: Dict[str, Any],
                      color: Optional[Tuple[str, str, Dict[str, str]]]) -> Dict[str, Any]:
    """Bind fixed per-value colours onto a built visual's ``dataPoint`` object."""
    if color:
        visual["visual"].setdefault("objects", {})["dataPoint"] = _value_data_colors(*color)
    return visual



# ---- field reference helpers --------------------------------------------

def _measure(entity: str, name: str) -> Dict[str, Any]:
    return {
        "field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": name}},
        "queryRef": f"{entity}.{name}",
        "nativeQueryRef": name,
    }


def _column(entity: str, name: str) -> Dict[str, Any]:
    return {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": name}},
        "queryRef": f"{entity}.{name}",
        "nativeQueryRef": name,
    }


def _sum(entity: str, name: str) -> Dict[str, Any]:
    """Aggregated (Sum) projection of a numeric column.

    Charts need an explicit aggregation on the value role; a bare column does
    not render bars in Direct Lake, so wrap it in ``Aggregation`` (Function 0 =
    Sum).
    """
    return {
        "field": {"Aggregation": {
            "Expression": {"Column": {
                "Expression": {"SourceRef": {"Entity": entity}}, "Property": name,
            }},
            "Function": 0,
        }},
        "queryRef": f"Sum({entity}.{name})",
        "nativeQueryRef": f"Sum of {name}",
    }


# ---- visual builders -----------------------------------------------------

def _visual(page: str, key: str, vtype: str, x: float, y: float, w: float, h: float,
            query_state: Dict[str, Any], *, title: Optional[str] = None,
            tab: int = 0) -> Dict[str, Any]:
    visual: Dict[str, Any] = {
        "visualType": vtype,
        "query": {"queryState": query_state, "sortDefinition": {"sort": [], "isDefaultSort": True}},
        "drillFilterOtherVisuals": True,
    }
    if title:
        visual["objects"] = {"title": [{"properties": {
            "text": _lit(f"'{title}'"),
            "show": _lit("true"),
            "fontColor": _solid(INK),
            "fontFamily": _lit("'Segoe UI Semibold'"),
            "fontSize": _lit("12D"),
            "alignment": _lit("'left'"),
        }}]}
        # Default tooltips on (data values + thresholds carried in side panels),
        # plus an accessible alt-text description for screen readers.
        visual["objects"]["tooltip"] = [{"properties": {"show": _lit("true")}}]
        alt = title.replace("'", "")
        visual["visualContainerObjects"] = {"general": [{"properties": {
            "altText": _lit(f"'{alt}'"),
        }}]}
    return {
        "$schema": f"{_SCHEMA}/visualContainer/1.0.0/schema.json",
        "name": _id(page, key),
        "position": {"x": x, "y": y, "z": tab, "width": w, "height": h, "tabOrder": tab},
        "visual": visual,
    }


def _card(page: str, key: str, entity: str, measure: str, x: float, y: float,
          title: str, tab: int) -> Dict[str, Any]:
    qs = {"Values": {"projections": [_measure(entity, measure)]}}
    return _visual(page, key, "card", x, y, 200, 120, qs, title=title, tab=tab)


def _slicer(page: str, key: str, entity: str, col: str, x: float, y: float, tab: int) -> Dict[str, Any]:
    qs = {"Values": {"projections": [_column(entity, col)]}}
    return _visual(page, key, "slicer", x, y, 260, 120, qs, title="Run", tab=tab)


def _field_slicer(page: str, key: str, entity: str, col: str, x: float, y: float,
                  w: float, h: float, title: str, tab: int) -> Dict[str, Any]:
    """A slicer over an arbitrary column, used to drive the model-detail page."""
    qs = {"Values": {"projections": [_column(entity, col)]}}
    return _visual(page, key, "slicer", x, y, w, h, qs, title=title, tab=tab)


def _table(page: str, key: str, entity: str, cols: List[str], measures: List[str],
           x: float, y: float, w: float, h: float, title: str, tab: int) -> Dict[str, Any]:
    proj = [_column(entity, c) for c in cols] + [_measure(entity, m) for m in measures]
    qs = {"Values": {"projections": proj}}
    return _visual(page, key, "tableEx", x, y, w, h, qs, title=title, tab=tab)


def _column_chart(page: str, key: str, entity: str, category: str, value_col: str,
                  x: float, y: float, w: float, h: float, title: str, tab: int) -> Dict[str, Any]:
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [_sum(entity, value_col)]},
    }
    return _visual(page, key, "clusteredColumnChart", x, y, w, h, qs, title=title, tab=tab)


def _stacked_bar(page: str, key: str, entity: str, category: str, value_col: str,
                 series: str, x: float, y: float, w: float, h: float, title: str,
                 tab: int, *,
                 color: Optional[Tuple[str, str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """A horizontal stacked bar: ``category`` on the axis, ``series`` stacked and
    coloured, ``value_col`` summed. Used for the dimension x severity breakdown."""
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [_sum(entity, value_col)]},
        "Series": {"projections": [_column(entity, series)]},
    }
    v = _visual(page, key, "clusteredColumnChart", x, y, w, h, qs, title=title, tab=tab)
    return _apply_data_color(v, color)


def _stacked_hbar(page: str, key: str, entity: str, category: str, value: str,
                  series: str, x: float, y: float, w: float, h: float, title: str,
                  tab: int, *, measure: bool = False,
                  color: Optional[Tuple[str, str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """A horizontal stacked bar: one bar per ``category`` (the axis scrolls when
    there are many), each segmented and coloured by ``series`` and sized by
    ``value``. Stays readable with dozens of categories where a treemap would
    fragment into confetti. Core visual, so it honours the data-colour palette."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [val_proj]},
        "Series": {"projections": [_column(entity, series)]},
    }
    v = _visual(page, key, "barChart", x, y, w, h, qs, title=title, tab=tab)
    return _apply_data_color(v, color)


def _treemap(page: str, key: str, entity: str, group: str, value: str,
             x: float, y: float, w: float, h: float, title: str, tab: int,
             *, details: Optional[str] = None, measure: bool = False,
             color: Optional[Tuple[str, str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """A treemap: one rectangle per ``group`` value, area set by the aggregated
    ``value`` and optionally subdivided by ``details``. Far better than a bar when
    there are many categories - it reads as a part-to-whole size map and never
    needs a scrollbar. Used for VertiPaq column size and notebook smell load. Core
    Power BI visual, so it is not affected by a custom-visual tenant block."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs: Dict[str, Any] = {
        "Group": {"projections": [_column(entity, group)]},
        "Values": {"projections": [val_proj]},
    }
    if details:
        qs["Details"] = {"projections": [_column(entity, details)]}
    v = _visual(page, key, "treemap", x, y, w, h, qs, title=title, tab=tab)
    return _apply_data_color(v, color)


def _donut(page: str, key: str, entity: str, category: str, value: str,
           x: float, y: float, w: float, h: float, title: str, tab: int,
           *, measure: bool = False,
           color: Optional[Tuple[str, str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """A donut chart: one slice per ``category`` value sized by ``value``."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [val_proj]},
    }
    v = _visual(page, key, "donutChart", x, y, w, h, qs, title=title, tab=tab)
    return _apply_data_color(v, color)


def _scatter(page: str, key: str, entity: str, details: str, x_col: str, y_col: str,
             size_col: str, series: Optional[str], x: float, y: float, w: float,
             h: float, title: str, tab: int) -> Dict[str, Any]:
    """A scatter / bubble chart: one bubble per ``details`` value, positioned by
    ``x_col`` x ``y_col``, sized by ``size_col`` and coloured by ``series``.

    X / Y / Size use Sum aggregations (one row per ``details`` value, so Sum just
    returns that row's value) - bare columns do not plot in Direct Lake."""
    qs = {
        "Category": {"projections": [_column(entity, details)]},
        "X": {"projections": [_sum(entity, x_col)]},
        "Y": {"projections": [_sum(entity, y_col)]},
        "Size": {"projections": [_sum(entity, size_col)]},
    }
    if series:
        qs["Series"] = {"projections": [_column(entity, series)]}
    return _visual(page, key, "scatterChart", x, y, w, h, qs, title=title, tab=tab)


def _matrix(page: str, key: str, entity: str, rows: List[str], columns: List[str],
            value: str, x: float, y: float, w: float, h: float, title: str, tab: int,
            *, measure: bool = False) -> Dict[str, Any]:
    """A matrix (pivot): ``rows`` down the side, ``columns`` across the top, the
    aggregated ``value`` in each cell. Used for the dimension x severity heatmap."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Rows": {"projections": [_column(entity, r) for r in rows]},
        "Columns": {"projections": [_column(entity, c) for c in columns]},
        "Values": {"projections": [val_proj]},
    }
    return _visual(page, key, "pivotTable", x, y, w, h, qs, title=title, tab=tab)


def _radar(page: str, key: str, entity: str, category: str, value: str,
           x: float, y: float, w: float, h: float, title: str, tab: int,
           *, measure: bool = False) -> Dict[str, Any]:
    """A maturity-by-dimension spider/radar: one spoke per ``category`` value,
    reach set by the aggregated ``value``. Microsoft-certified public custom
    visual (declared in report.json -> publicCustomVisuals); degrades to a
    placeholder if a tenant blocks custom visuals. Used for platform maturity
    across the review dimensions."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [val_proj]},
    }
    return _visual(page, key, RADAR_VISUAL, x, y, w, h, qs, title=title, tab=tab)


def _sankey(page: str, key: str, entity: str, source: str, dest: str, weight: str,
            x: float, y: float, w: float, h: float, title: str, tab: int,
            *, measure: bool = True) -> Dict[str, Any]:
    """Source -> destination flow / lineage ribbons, ribbon width = aggregated
    ``weight``. Microsoft-certified public custom visual (declared in report.json
    -> publicCustomVisuals); degrades to a placeholder if a tenant blocks custom
    visuals. Roles: Source (grouping), Destination (grouping), Weight (measure)."""
    wt = _measure(entity, weight) if measure else _sum(entity, weight)
    qs = {
        "Source": {"projections": [_column(entity, source)]},
        "Destination": {"projections": [_column(entity, dest)]},
        "Weight": {"projections": [wt]},
    }
    return _visual(page, key, SANKEY_VISUAL, x, y, w, h, qs, title=title, tab=tab)


def _sunburst(page: str, key: str, entity: str, levels: List[str], value: str,
              x: float, y: float, w: float, h: float, title: str, tab: int,
              *, measure: bool = True) -> Dict[str, Any]:
    """A multilevel donut (sunburst): one concentric ring per ``levels`` column
    (innermost first), each wedge sized by the aggregated ``value``. Microsoft-
    certified public custom visual (declared in report.json ->
    publicCustomVisuals); degrades to a placeholder if a tenant blocks custom
    visuals. Roles: Nodes (grouping, one projection per ring), Values (measure)."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Nodes": {"projections": [_column(entity, c) for c in levels]},
        "Values": {"projections": [val_proj]},
    }
    return _visual(page, key, SUNBURST_VISUAL, x, y, w, h, qs, title=title, tab=tab)


def _gauge(page: str, key: str, entity: str, value: str, x: float, y: float,
           w: float, h: float, title: str, tab: int,
           *, target: Optional[str] = None, maxv: Optional[str] = None) -> Dict[str, Any]:
    """A radial gauge: ``value`` measure on a 0..``maxv`` scale with an optional
    ``target`` marker. Used for the headline best-practice / maturity score."""
    qs: Dict[str, Any] = {"Y": {"projections": [_measure(entity, value)]}}
    if target:
        qs["TargetValue"] = {"projections": [_measure(entity, target)]}
    if maxv:
        qs["MaxValue"] = {"projections": [_measure(entity, maxv)]}
    return _visual(page, key, "gauge", x, y, w, h, qs, title=title, tab=tab)


def _line(page: str, key: str, entity: str, category: str, value: str,
          x: float, y: float, w: float, h: float, title: str, tab: int,
          *, measure: bool = False) -> Dict[str, Any]:
    """A line / trend chart: ``category`` on the axis, the aggregated ``value`` as
    the line. Used for score / activity trend over runs."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [val_proj]},
    }
    return _visual(page, key, "lineChart", x, y, w, h, qs, title=title, tab=tab)


def _line2(page: str, key: str, entity: str, category: str, values: List[str],
           x: float, y: float, w: float, h: float, title: str, tab: int) -> Dict[str, Any]:
    """A multi-series trend: ``category`` on the axis with several summed measures
    plotted as separate lines on a shared scale (e.g. critical vs high fails)."""
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [_sum(entity, v) for v in values]},
    }
    return _visual(page, key, "lineChart", x, y, w, h, qs, title=title, tab=tab)


def _bar(page: str, key: str, entity: str, category: str, value: str,
         x: float, y: float, w: float, h: float, title: str, tab: int,
         *, measure: bool = False,
         color: Optional[Tuple[str, str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """A horizontal ranked bar (lollipop-style top-N): one bar per ``category``
    value sized by the aggregated ``value``, sorted descending by default so the
    worst offenders sit at the top."""
    val_proj = _measure(entity, value) if measure else _sum(entity, value)
    qs = {
        "Category": {"projections": [_column(entity, category)]},
        "Y": {"projections": [val_proj]},
    }
    v = _visual(page, key, "clusteredBarChart", x, y, w, h, qs, title=title, tab=tab)
    return _apply_data_color(v, color)


# ---- text visuals (banners + plain-language explanations) ----------------

def _run(text: str, *, size: int = 10, bold: bool = False, color: str = INK,
         family: str = "Segoe UI") -> Dict[str, Any]:
    return {"value": text, "textStyle": {
        "fontFamily": family,
        "fontSize": f"{size}pt",
        "fontWeight": "bold" if bold else "normal",
        "color": color,
    }}


def _para(runs: List[Dict[str, Any]], align: str = "left") -> Dict[str, Any]:
    return {"textRuns": runs, "horizontalTextAlignment": align}


def _textbox(page: str, key: str, paragraphs: List[Dict[str, Any]],
             x: float, y: float, w: float, h: float, *,
             bg: Optional[str] = None, alt: Optional[str] = None,
             tab: int = 0) -> Dict[str, Any]:
    visual: Dict[str, Any] = {
        "visualType": "textbox",
        "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
        "drillFilterOtherVisuals": True,
    }
    vco: Dict[str, Any] = {}
    if alt:
        vco["general"] = [{"properties": {"altText": _lit(f"'{alt}'")}}]
    if bg:
        vco["background"] = [{"properties": {
            "show": _lit("true"), "color": _solid(bg), "transparency": _lit("0D"),
        }}]
        vco["border"] = [{"properties": {"show": _lit("false")}}]
        vco["dropShadow"] = [{"properties": {"show": _lit("false")}}]
    if vco:
        visual["visualContainerObjects"] = vco
    return {
        "$schema": f"{_SCHEMA}/visualContainer/1.0.0/schema.json",
        "name": _id(page, key),
        "position": {"x": x, "y": y, "z": tab, "width": w, "height": h, "tabOrder": tab},
        "visual": visual,
    }


def _banner(page: str, title: str, subtitle: str, tab: int = 0) -> Dict[str, Any]:
    paras = [
        _para([_run(title, size=19, bold=True, color="#FFFFFF", family="Segoe UI Semibold")]),
        _para([_run(subtitle, size=10, color=BANNER_SUB)]),
    ]
    return _textbox(page, "banner", paras, 16, 12, PAGE_W - 32, 70, bg=BRAND, alt=title, tab=tab)


def _info(page: str, key: str, heading: str, body: str,
          x: float, y: float, w: float, h: float, tab: int) -> Dict[str, Any]:
    paras = [
        _para([_run(heading, size=11, bold=True, color=BRAND, family="Segoe UI Semibold")]),
        _para([_run(body, size=10, color=MUTED)]),
    ]
    return _textbox(page, key, paras, x, y, w, h, alt=heading, tab=tab)


def _version_strip(page: str, tab: int) -> List[Dict[str, Any]]:
    """A slim, data-bound release banner along the foot of the Home page.

    A single ``card`` bound to ``gold_release[Release Banner]`` (latest row,
    filter-independent). The callout value is styled down to a small caption so
    it reads as a footer line rather than a giant KPI, and the upgrade note is
    folded into the same measure so there is never an empty second card. Purely
    informational FUAM-style release awareness; it never blocks the report.
    """
    h = 44
    y = PAGE_H - h - 8
    v = _visual(
        page, "version_status", "card",
        16, y, PAGE_W - 32, h,
        {"Values": {"projections": [_measure("gold_release", "Release Banner")]}},
        title="Solution version", tab=tab,
    )
    # Shrink the callout value to a caption-sized footer line (cards default to a
    # huge KPI font) and drop the redundant category label.
    obj = v["visual"]["objects"]
    obj["labels"] = [{"properties": {
        "color": _solid(MUTED),
        "fontFamily": _lit("'Segoe UI'"),
        "fontSize": _lit("11D"),
        "labelDisplayUnits": _lit("0D"),
    }}]
    obj["categoryLabels"] = [{"properties": {"show": _lit("false")}}]
    obj["wordWrap"] = [{"properties": {"show": _lit("true")}}]
    return [v]


# ---- navigation buttons + map tiles (FUAM-style) -------------------------
# A FUAM-style landing "map": a grid of large coloured tiles that navigate to
# each page on click. The page-navigation action is a button visual
# (``actionButton`` with a ``visualLink`` of type ``PageNavigation``), matching
# the way FUAM's home page wires its tiles together.

def _action_button(page: str, key: str, target: str, x: float, y: float,
                   w: float, h: float, tab: int, *, label: Optional[str] = None,
                   fill: Optional[str] = None, fg: str = "#FFFFFF",
                   font_size: int = 11) -> Dict[str, Any]:
    """An ``actionButton`` that navigates to ``target`` (a page ``name``).

    With ``label``/``fill`` it renders as a visible coloured button; without
    them it is a transparent click target laid over a tile so the whole tile is
    clickable. ``navigationSection`` must equal the destination page's ``name``.
    """
    objects: Dict[str, Any] = {
        "outline": [{"properties": {"show": _lit("false")}}],
        "icon": [{"properties": {"show": _lit("false")}}],
    }
    if label:
        objects["text"] = [
            {"properties": {"show": _lit("true")}},
            {"properties": {
                "text": _lit(f"'{label}'"),
                "fontSize": _lit(f"{font_size}D"),
                "fontColor": _solid(fg),
                "fontFamily": _lit("'Segoe UI Semibold'"),
            }, "selector": {"id": "default"}},
        ]
    else:
        objects["text"] = [{"properties": {"show": _lit("false")}}]
    vco: Dict[str, Any] = {
        "border": [{"properties": {"show": _lit("false")}}],
        "visualLink": [{"properties": {
            "show": _lit("true"),
            "type": _lit("'PageNavigation'"),
            "navigationSection": _lit(f"'{target}'"),
        }}],
    }
    if fill:
        vco["background"] = [{"properties": {
            "show": _lit("true"), "color": _solid(fill), "transparency": _lit("0D"),
        }}]
    else:
        vco["background"] = [{"properties": {"show": _lit("false")}}]
    return {
        "$schema": f"{_SCHEMA}/visualContainer/2.9.0/schema.json",
        "name": _id(page, key),
        "position": {"x": x, "y": y, "z": tab, "width": w, "height": h, "tabOrder": tab},
        "visual": {
            "visualType": "actionButton",
            "objects": objects,
            "visualContainerObjects": vco,
            "drillFilterOtherVisuals": True,
        },
        "howCreated": "InsertVisualButton",
    }


def _tile(page: str, key: str, target: str, title: str, subtitle: str,
          x: float, y: float, w: float, h: float, color: str,
          card_tab: int, nav_tab: int) -> List[Dict[str, Any]]:
    """A clickable map tile: a coloured card with a title + one-line subtitle,
    plus a transparent navigation button laid on top (``nav_tab`` > ``card_tab``
    so the button sits above the card and captures the click)."""
    paras = [
        _para([_run(" ", size=14)]),
        _para([_run(" ", size=14)]),
        _para([_run(title, size=15, bold=True, color="#FFFFFF",
                    family="Segoe UI Semibold")], align="center"),
        _para([_run(subtitle, size=10, color="#EAF2FB")], align="center"),
    ]
    card = _textbox(page, f"{key}_card", paras, x, y, w, h, bg=color, tab=card_tab)
    nav = _action_button(page, f"{key}_nav", target, x, y, w, h, nav_tab)
    return [card, nav]


# ---- page-level dimension filter ----------------------------------------

def _dimension_filter(page: str, dimension: str) -> Dict[str, Any]:
    return {
        "name": _id(page, "filter", dimension),
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": "gold_findings"}}, "Property": "dimension"}},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "g", "Entity": "gold_findings", "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "g"}}, "Property": "dimension"}}],
                "Values": [[{"Literal": {"Value": f"'{dimension}'"}}]],
            }}}],
        },
    }


def _latest_run_filter(page: str) -> Dict[str, Any]:
    """Page-level filter that keeps only the most recent run.

    Filters ``gold_run_summary.is_latest`` to ``true``. The gold build marks the
    newest run's row ``is_latest = true`` and flips every earlier run to
    ``false``, so this single boolean filter propagates through the run
    relationship to every fact table — each page shows the latest review run
    automatically, no manual slicer and no stale rows from earlier appends.
    """
    return {
        "name": _id(page, "filter", "latestrun"),
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": "gold_run_summary"}}, "Property": "is_latest"}},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "r", "Entity": "gold_run_summary", "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "r"}}, "Property": "is_latest"}}],
                "Values": [[{"Literal": {"Value": "true"}}]],
            }}}],
        },
    }


def _estate_lineage_filter(page: str) -> Dict[str, Any]:
    """Restrict the Estate Map Sankey to the curated data-lineage chain.

    Keeps only ``gold_graph_edges`` rows flagged ``is_lineage = true`` (Capacity
    -> Workspace -> Semantic model -> Report). The structural ``contains`` edges
    to notebooks/pipelines/lakehouses and the owner edges are dropped so the flow
    reads as one clean story instead of a hairball. The Sankey is the only
    ``gold_graph_edges`` visual on the page besides the edge-count card, so this
    page filter does not affect the node inventory or risk visuals.
    """
    return {
        "name": _id(page, "filter", "lineage"),
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": "gold_graph_edges"}}, "Property": "is_lineage"}},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "e", "Entity": "gold_graph_edges", "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "e"}}, "Property": "is_lineage"}}],
                "Values": [[{"Literal": {"Value": "true"}}]],
            }}}],
        },
    }

# Shared grid: a brand banner (y=12..76), a KPI/slicer row (y=88..198) and a
# content area below (y=210..704).
_ROW_KPI_Y = 88
_KPI_H = 110
_CONTENT_Y = 210
_CONTENT_H = PAGE_H - _CONTENT_Y - 16  # 494


def _findings_table(page: str, x: float, y: float, w: float, h: float, tab: int) -> Dict[str, Any]:
    return _table(page, "findings", "gold_findings",
                  ["rule_id", "severity", "status", "title", "affected", "recommendation"], [],
                  x, y, w, h, "Findings — rule, result, where it happens, and the fix", tab)


def _kpi_row(page: str, cards: List[tuple], start_tab: int = 1) -> List[Dict[str, Any]]:
    """Lay out KPI cards left-to-right. ``cards`` = [(key, measure, label), ...]."""
    out = []
    x = 16
    for i, (key, measure, label) in enumerate(cards):
        out.append(_card(page, key, "gold_findings", measure, x, _ROW_KPI_Y, label, start_tab + i))
        x += 216
    return out


# Per-dimension priority guidance shown in a side panel beside the findings.
_DIMENSION_ACTIONS = {
    "architecture": "Fix Critical + High fails first. Consolidate monolithic models, "
                    "adopt a medallion layout, and standardise naming. 80+ = aligned, "
                    "50-79 = needs review, under 50 = action required.",
    "performance": "Tackle red fails first: long refreshes, oversized models and "
                   "throttling. Right-size heavy models and stagger overlapping refreshes "
                   "before adding capacity.",
    "cost": "Target cost concentration: idle capacities, near-empty workspaces and "
            "stale assets. Right-size SKUs and retire unused items — optimisation, not "
            "just spend.",
    "governance": "Close ownership and operating-model gaps: assign workspace admins, "
                  "align to domains, and enforce naming/description coverage. Treat orphaned "
                  "assets as governance risk.",
    "security": "Severity-first. Remediate critical/high exposure now: broad access, "
                "missing gateways and stale credentials. Reduce blast radius before "
                "expanding sharing.",
    "tenant_settings": "Align tenant switches to recommended state. Amber = review, red = "
                       "high-impact gap. Lock down export/sharing controls before enabling "
                       "broad self-service.",
    "best_practices": "Clear high-value items first: model BPA violations, broken reports and "
                      "Direct Lake fallback. Then tackle medium: report BPA, Delta health, unused "
                      "objects and P-SKU->F-SKU capacity migration.",
}


def _dimension_page(dimension: str, display: str, detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    page = display
    visuals = [_banner(
        page, f"{display} review",
        f"Best-practice checks for the {display.lower()} dimension of your Fabric estate.",
    )]
    visuals += _kpi_row(page, [
        ("fails", "Fail Count", "Fails"),
        ("pass", "Pass Count", "Passes"),
        ("score", "Best Practice Score", "Score (%)"),
        ("ch", "Critical & High Fails", "Critical + High"),
    ])
    visuals.append(_info(
        page, "explain", "What these numbers mean",
        f"Every metric here is filtered to the {display.lower()} dimension. "
        f"Fail Count = {display.lower()} checks that did not pass this run. "
        "Critical & High Fails = those failures rated critical/high severity — fix first. "
        "Score = passes ÷ (passes + fails).",
        16 + 4 * 216, _ROW_KPI_Y, PAGE_W - (16 + 4 * 216) - 16, _KPI_H, 5,
    ))
    # Analytical band — consistent on every dimension page: a severity donut, a
    # ranked bar of failing checks and a rule x severity heat grid, plus the
    # dimension-specific detail table (if any). All dimension-filtered.
    by, bh = _CONTENT_Y, 196
    visuals.append(_donut(page, "bysev", "gold_findings", "severity", "Fail Count",
                          16, by, 240, bh, "Fails by severity", 6, measure=True,
                          color=("gold_findings", "severity", SEV_HEX)))
    if dimension in ("performance", "cost"):
        # Estate hotspot hero: workspaces plotted items (x) vs risk (y), sized by
        # issues — the FUAM-style way to spot where to spend effort first.
        visuals.append(_bar(page, "byrule", "gold_findings", "rule_id", "is_fail",
                            264, by, 320, bh, "Failing checks by rule (ranked)", 7))
        visuals.append(_scatter(
            page, "hotspot", "gold_workspace_risk", "workspace_name",
            "item_count", "risk_score", "issue_count", "status",
            592, by, PAGE_W - 592 - 16, bh,
            "Estate hotspots — items vs risk, sized by issues", 8))
    elif dimension == "governance":
        # Maturity radar across dimensions gives governance the estate-wide context.
        visuals.append(_bar(page, "byrule", "gold_findings", "rule_id", "is_fail",
                            264, by, 320, bh, "Failing checks by rule (ranked)", 7))
        visuals.append(_radar(page, "maturity", "gold_dimension_summary", "dimension",
                              "score", 592, by, PAGE_W - 592 - 16, bh,
                              "Platform maturity by dimension (score %)", 8))
    elif detail:
        visuals.append(_bar(page, "byrule", "gold_findings", "rule_id", "is_fail",
                            264, by, 360, bh, "Failing checks by rule (ranked)", 7))
        visuals.append(_matrix(page, "heat", "gold_findings", ["rule_id"], ["severity"],
                               "Fail Count", 632, by, 320, bh, "Rule x severity", 8, measure=True))
        visuals.append(_table(page, "detail", detail["entity"], detail["cols"], [],
                              960, by, PAGE_W - 960 - 16, bh, detail["title"], 9))
    else:
        visuals.append(_bar(page, "byrule", "gold_findings", "rule_id", "is_fail",
                            264, by, 488, bh, "Failing checks by rule (ranked)", 7))
        visuals.append(_matrix(page, "heat", "gold_findings", ["rule_id"], ["severity"],
                               "Fail Count", 760, by, PAGE_W - 760 - 16, bh,
                               "Rule x severity", 8, measure=True))
    fy = by + bh + 12
    fh = PAGE_H - fy - 16
    actions = _DIMENSION_ACTIONS.get(dimension)
    if actions:
        panel_w = 300
        visuals.append(_info(page, "actions", "Priority actions", actions,
                             PAGE_W - 16 - panel_w, fy, panel_w, fh, 10))
        visuals.append(_findings_table(page, 16, fy, PAGE_W - panel_w - 44, fh, 11))
    else:
        visuals.append(_findings_table(page, 16, fy, PAGE_W - 32, fh, 11))
    return {
        "name": page, "display": display, "visuals": visuals,
        "filters": [_dimension_filter(page, dimension), _latest_run_filter(page)],
    }


def _best_practices_page() -> Dict[str, Any]:
    """Dedicated Best Practice Analyzer page: object-level violations from BPA,
    Direct Lake fallback, Delta health, unused objects and capacity migration,
    plus the rolled-up best_practices findings. Richer than the generic
    dimension layout — area donut, top-offenders bar and area x severity heat."""
    page, display = "BestPractices", "Best Practices"
    visuals = [_banner(
        page, "Best Practices review",
        "Object-level Best Practice Analyzer results across models, reports and capacities.",
    )]
    visuals += _kpi_row(page, [
        ("fails", "Fail Count", "Check Fails"),
        ("score", "Best Practice Score", "Score (%)"),
        ("ch", "Critical & High Fails", "Critical + High"),
    ])
    visuals.append(_card(page, "bpacount", "gold_bpa_violations", "BPA Violation Count",
                         16 + 3 * 216, _ROW_KPI_Y, "BPA Violations", 4))
    visuals.append(_info(
        page, "explain", "Priority actions",
        "Clear high severity first: fix model BPA violations, then broken/oversized "
        "reports and Direct Lake fallback. The area donut shows where they cluster, "
        "the bar ranks the worst objects, the heat grid pivots area against severity.",
        16 + 4 * 216, _ROW_KPI_Y, PAGE_W - (16 + 4 * 216) - 16, _KPI_H, 5,
    ))
    by, bh = _CONTENT_Y, 196
    visuals.append(_donut(page, "byarea", "gold_bpa_violations", "area", "BPA Violation Count",
                          16, by, 300, bh, "Violations by area", 6, measure=True))
    visuals.append(_bar(page, "byobject", "gold_bpa_violations", "object_name", "BPA Violation Count",
                        324, by, 480, bh, "Worst offending objects (ranked)", 7, measure=True))
    visuals.append(_matrix(page, "heat", "gold_bpa_violations", ["area"], ["severity"],
                           "BPA Violation Count", 820, by, PAGE_W - 820 - 16, bh,
                           "Area x severity", 8, measure=True))
    fy = by + bh + 12
    fh = PAGE_H - fy - 16
    half = (PAGE_W - 32 - 12) // 2
    visuals.append(_table(page, "violations", "gold_bpa_violations",
                          ["object_type", "object_name", "workspace_name", "area", "rule", "severity"],
                          [], 16, fy, half, fh, "Every BPA violation — what, where and how bad", 9))
    visuals.append(_findings_table(page, 16 + half + 12, fy, half, fh, 10))
    return {
        "name": page, "display": display, "visuals": visuals,
        "filters": [_dimension_filter(page, "best_practices"), _latest_run_filter(page)],
    }


_HOME_TILES = [
    # (target page name, title, one-line subtitle, tile colour)
    ("Overview", "Overview", "The whole estate at a glance", BRAND_DK),
    ("Trends", "Trends", "Score & risk over time", "#2E86AB"),
    ("EstateMap", "Estate map", "Lineage, hotspots & risk", "#5B8DEF"),
    ("Architecture", "Architecture", "Modelling & design checks", BRAND),
    ("Performance", "Performance", "Capacity & query speed", "#038387"),
    ("Cost", "Cost", "Capacity spend & efficiency", "#CA5010"),
    ("Governance", "Governance", "Workspaces & ownership", "#8764B8"),
    ("Security", "Security", "Access & data exposure", "#C50F1F"),
    ("TenantSettings", "Tenant Settings", "Admin tenant switches", "#4F6BED"),
    ("BestPractices", "Best Practices", "BPA, Delta & capacity health", "#107C41"),
    ("SemanticModels", "Semantic models", "VertiPaq memory footprint", GOOD),
    ("ModelDetail", "Model detail", "Tables, columns & encoding", "#00787A"),
    ("Notebooks", "Notebooks", "Spark code anti-patterns", "#8E562E"),
]


# Name of the embedded home-map background image (RegisteredResources item).
HOME_BG_NAME = "archreview_home_map.png"


def _home_page() -> Dict[str, Any]:
    """A FUAM-style navigation *map*: an organic "circuit" illustration (baked
    into the page background) of one coloured region per page, joined by faint
    traces around a central core, with headline KPIs floating on top and a
    transparent navigation button over each region so a click opens its page.

    Falls back to :func:`_home_tiles_page` if Pillow (used to render the map) is
    unavailable.
    """
    try:
        import base64
        from reports.powerbi import home_map
        png = home_map.render(PAGE_W, PAGE_H)
        nodes = home_map.NODES
    except Exception:
        return _home_tiles_page()

    page = "Home"
    banner = _banner(
        page, "Fabric Architecture Review",
        "Your governance review as a map — click any region to explore its "
        "findings. Every page opens filtered to your latest review run.",
    )
    # The rounded brand banner is baked into the hero background (home_map.render),
    # so make the banner textbox transparent and let its title float over it —
    # exactly like every non-Home page.
    banner["visual"].setdefault("visualContainerObjects", {})["background"] = [
        {"properties": {"show": _lit("false")}}
    ]
    visuals = [banner]
    visuals += _kpi_row(page, [
        ("score", "Best Practice Score", "Score (%)"),
        ("total", "Total Findings", "Total Findings"),
        ("fails", "Fail Count", "Fails"),
        ("ch", "Critical & High Fails", "Critical + High"),
    ], start_tab=1)
    # Transparent click target over each region (button on top, captures click).
    for i, nd in enumerate(nodes):
        r = nd["r"]
        visuals.append(_action_button(
            page, f"nav{i}", nd["target"],
            nd["cx"] - r, nd["cy"] - r, 2 * r, 2 * r, 300 + i,
        ))
    # Prominent quick-links to the cross-cutting pages that have no orb on the
    # map — Estate map, Trends and Best Practices — as a right-hand column
    # beside the KPI scorecards, clear of the orb map so it does not crowd them.
    quick = [
        ("EstateMap", "\U0001F5FA  Estate map", ACCENT["EstateMap"]),
        ("Trends", "\U0001F4C8  Trends", ACCENT["Trends"]),
        ("BestPractices", "\u2705  Best practices", ACCENT["BestPractices"]),
    ]
    qw, qh, qgap = 288, 34, 8
    qx = PAGE_W - 16 - qw  # right-aligned, separated from the KPI cards by hero space
    for j, (target, label, fill) in enumerate(quick):
        visuals.append(_action_button(
            page, f"quick{j}", target, qx, _ROW_KPI_Y + j * (qh + qgap), qw, qh,
            60 + j, label=label, fill=fill, font_size=12,
        ))
    visuals += _version_strip(page, 80)
    return {
        "name": page, "display": "Home", "visuals": visuals,
        "filters": [_latest_run_filter(page)],
        "bg_image_name": HOME_BG_NAME,
        "bg_image_b64": base64.b64encode(png).decode("ascii"),
    }


def _home_tiles_page() -> Dict[str, Any]:
    """Fallback navigation map: headline KPIs plus a grid of large coloured
    tiles, one per page, used when Pillow is unavailable to render the
    illustrated circuit map."""
    page = "Home"
    visuals = [_banner(
        page, "Fabric Architecture Review",
        "Your governance review at a glance — pick an area below to explore the "
        "findings. Every page opens filtered to your latest review run.",
    )]
    visuals += _kpi_row(page, [
        ("score", "Best Practice Score", "Score (%)"),
        ("total", "Total Findings", "Total Findings"),
        ("fails", "Fail Count", "Fails"),
        ("ch", "Critical & High Fails", "Critical + High"),
    ], start_tab=1)
    visuals.append(_info(
        page, "how", "How to use this map",
        "Each tile opens a focused page: the six review dimensions plus deep-dive "
        "pages for semantic models and notebooks. Use the 'Home' button on any page "
        "to come straight back here.",
        16 + 4 * 216, _ROW_KPI_Y, PAGE_W - (16 + 4 * 216) - 16, _KPI_H, 5,
    ))
    cols, gap, margin = 5, 16, 16
    tile_w = (PAGE_W - 2 * margin - (cols - 1) * gap) / cols
    tile_h = 210
    top = _CONTENT_Y + 24
    for i, (target, title, subtitle, color) in enumerate(_HOME_TILES):
        row, col = divmod(i, cols)
        x = margin + col * (tile_w + gap)
        y = top + row * (tile_h + gap)
        visuals += _tile(page, f"tile{i}", target, title, subtitle,
                         x, y, tile_w, tile_h, color, 10 + i, 200 + i)
    visuals += _version_strip(page, 80)
    return {"name": page, "display": "Home", "visuals": visuals,
            "filters": [_latest_run_filter(page)]}


def _overview_page() -> Dict[str, Any]:
    page = "Overview"
    visuals = [_banner(
        page, "Fabric Architecture Review — Governance",
        "Best-practice assessment across architecture, performance, cost, "
        "governance, security and tenant settings.",
    )]
    visuals.append(_info(
        page, "run", "Latest run",
        "Every page shows your most recent review run automatically.",
        16 + 5 * 216, _ROW_KPI_Y, PAGE_W - (16 + 5 * 216) - 16, _KPI_H, 1,
    ))
    x = 16
    for i, (key, ent, measure, label) in enumerate([
        ("total", "gold_findings", "Total Findings", "Total Findings"),
        ("fails", "gold_findings", "Fail Count", "Fails"),
        ("ch", "gold_findings", "Critical & High Fails", "Critical + High"),
        ("bpa", "gold_bpa_violations", "BPA Violation Count", "BPA violations"),
        ("score", "gold_findings", "Best Practice Score", "Score (%)"),
    ]):
        visuals.append(_card(page, key, ent, measure, x, _ROW_KPI_Y, label, 2 + i))
        x += 216
    # Band 1 — executive scorecard: maturity radar, score gauge, severity mix, heatmap.
    b1y, b1h = _CONTENT_Y, 196
    visuals.append(_radar(page, "maturity", "gold_dimension_summary", "dimension",
                          "score", 16, b1y, 312, b1h,
                          "Platform maturity by dimension (score %)", 6))
    visuals.append(_gauge(page, "scoregauge", "gold_findings", "Best Practice Score",
                          336, b1y, 180, b1h, "Overall best-practice score", 7,
                          target="Score Target", maxv="Score Max"))
    visuals.append(_donut(page, "bysev", "gold_findings", "severity", "Fail Count",
                          524, b1y, 196, b1h, "Fails by severity", 8, measure=True,
                          color=("gold_findings", "severity", SEV_HEX)))
    visuals.append(_matrix(page, "sevmatrix", "gold_severity_matrix", ["dimension"],
                           ["severity"], "issue_count", 728, b1y, PAGE_W - 728 - 16, b1h,
                           "Fail count by dimension & severity", 9))
    # Band 2 — worst workspaces ranked + priority-actions panel + findings list.
    b2y = b1y + b1h + 12
    b2h = PAGE_H - b2y - 16
    visuals.append(_bar(page, "topws", "gold_workspace_risk", "workspace_name",
                        "issue_count", 16, b2y, 320, b2h,
                        "Top workspaces by open issues", 10))
    visuals.append(_info(
        page, "actions", "Priority actions",
        "Fix Critical + High fails first (red on the heatmap). Tackle the worst dimension, "
        "then the top-risk workspaces. Score 80+ = aligned, 50-79 = needs review, under 50 = action required.",
        344, b2y, 220, b2h, 11))
    visuals.append(_findings_table(page, 572, b2y, PAGE_W - 572 - 16, b2h, 12))
    return {"name": page, "display": "Overview", "visuals": visuals,
            "filters": [_latest_run_filter(page)]}


def _trends_page() -> Dict[str, Any]:
    """Run-over-run trend page (FUAM capacity-style). No latest-run filter, so the
    line charts show the full review history accumulated in gold_run_summary —
    score, fails and Critical+High over time."""
    page = "Trends"
    visuals = [_banner(
        page, "Trends — score & risk over time",
        "How the estate is tracking across reviews. Score should climb and "
        "Critical + High fails should fall as recommendations are actioned.",
    )]
    for i, (key, measure, label) in enumerate([
        ("runscore", "Run Score", "Latest score (%)"),
        ("runfails", "Run Fails", "Latest fails"),
        ("runch", "Run Critical & High", "Latest critical+high"),
    ]):
        visuals.append(_card(page, key, "gold_run_summary", measure,
                             16 + i * 216, _ROW_KPI_Y, label, 1 + i))
    visuals.append(_info(
        page, "history", "Reading the trend",
        "Trends need at least two review runs. After the second run, score should "
        "climb and Critical + High fails should fall. A single run shows one point.",
        16 + 3 * 216, _ROW_KPI_Y, PAGE_W - (16 + 3 * 216) - 16, _KPI_H, 5,
    ))
    b1y, b1h = _CONTENT_Y, 240
    visuals.append(_line(page, "scoretrend", "gold_run_summary", "run_timestamp",
                         "score", 16, b1y, 624, b1h, "Best-practice score over runs", 6))
    visuals.append(_line(page, "failtrend", "gold_run_summary", "run_timestamp",
                         "fail_count", 656, b1y, PAGE_W - 656 - 16, b1h,
                         "Total fails over runs", 7))
    b2y = b1y + b1h + 12
    b2h = PAGE_H - b2y - 16
    visuals.append(_line2(page, "crithigh", "gold_run_summary", "run_timestamp",
                          ["critical_fail", "high_fail"], 16, b2y, 624, b2h,
                          "Critical + high fails over runs (combined)", 8))
    visuals.append(_table(page, "runs", "gold_run_summary",
                          ["run_timestamp", "score", "fail_count", "critical_fail", "high_fail"], [],
                          656, b2y, PAGE_W - 656 - 16, b2h, "Review history", 9))
    return {"name": page, "display": "Trends", "visuals": visuals, "filters": []}


def _estate_map_page() -> Dict[str, Any]:
    """The marquee FUAM-style page: the whole estate as risk-coloured plots.

    Capacities host workspaces, which contain models/reports/notebooks/pipelines
    /lakehouses. A bubble chart surfaces the riskiest workspaces, a severity
    heatmap and a stacked bar break failures down by dimension x severity, and
    two inventories list every node and every relationship - all from the live
    Direct Lake model so they reflect the latest run.
    """
    page = "EstateMap"
    visuals = [_banner(
        page, "Estate map — capacities, workspaces & lineage",
        "How your Fabric estate fits together, with bubbles and bars sized and "
        "coloured by risk so the hotspots stand out at a glance.",
    )]
    kpis = [
        ("wscount", "Workspace Count", "Workspaces", "gold_workspace_risk"),
        ("atrisk", "Workspaces at Risk", "At risk (amber+red)", "gold_workspace_risk"),
        ("avgrisk", "Average Risk Score", "Avg risk score", "gold_workspace_risk"),
        ("nodecount", "Node Count", "Estate items", "gold_graph_nodes"),
        ("edgecount", "Relationship Count", "Lineage links", "gold_graph_edges"),
    ]
    x = 16
    for i, (key, measure, label, entity) in enumerate(kpis):
        visuals.append(_card(page, key, entity, measure, x, _ROW_KPI_Y, label, 1 + i))
        x += 216
    # Band 1: hotspot bubbles | severity-by-dimension stacked bar | severity heatmap.
    band1_y, band1_h = _CONTENT_Y, 236
    visuals.append(_scatter(
        page, "hotspot", "gold_workspace_risk", "workspace_name",
        "item_count", "risk_score", "issue_count", "status",
        16, band1_y, 440, band1_h,
        "Workspace hotspots — items (x) vs risk (y), sized by issues", 6,
    ))
    visuals.append(_stacked_bar(
        page, "sevbars", "gold_severity_matrix", "dimension", "issue_count", "severity",
        464, band1_y, 360, band1_h, "Failures by dimension & severity", 7,
        color=("gold_severity_matrix", "severity", SEV_HEX),
    ))
    visuals.append(_matrix(
        page, "sevmatrix", "gold_severity_matrix", ["dimension"], ["severity"],
        "weighted_risk", 832, band1_y, PAGE_W - 832 - 16, band1_h,
        "Risk heatmap — weighted severity by dimension", 8,
    ))
    # Band 2: estate inventory & lineage — an item-type bar plus the marquee
    # Sankey flow (source -> target relationships, ribbon width = edge count).
    # The Sankey was rendering empty before because it referenced the legacy
    # GUID; it now uses the current published id (SANKEY_VISUAL).
    band2_y = band1_y + band1_h + 12
    band2_h = PAGE_H - band2_y - 16
    visuals.append(_bar(
        page, "inventory", "gold_graph_nodes", "node_type", "Node Count",
        16, band2_y, 320, band2_h, "Estate inventory by item type", 9, measure=True,
    ))
    visuals.append(_sankey(
        page, "lineage", "gold_graph_edges", "source_name", "target_name",
        "Relationship Count", 352, band2_y, PAGE_W - 352 - 16, band2_h,
        "Lineage flow — capacity → workspace → model → report", 10, measure=True,
    ))
    return {"name": page, "display": "Estate Map", "visuals": visuals,
            "filters": [_latest_run_filter(page), _estate_lineage_filter(page)]}


def _notebook_page() -> Dict[str, Any]:
    page = "Notebooks"
    visuals = [_banner(
        page, "Notebook code review",
        "Heuristic scan of notebook source for common Spark / Fabric anti-patterns.",
    )]
    visuals.append(_info(
        page, "explain", "How to read this",
        "Each row is an NBCODE rule that matched. The description explains what the "
        "rule checks; 'Cells' lists the offending cell number(s). Open the notebook "
        "with the link, then jump to those cells. Heuristic — review each match before "
        "acting; false positives are possible.",
        16, _ROW_KPI_Y, PAGE_W - 32, 76, 1,
    ))
    # Band — severity mix + the notebooks carrying the most smells.
    by, bh = _ROW_KPI_Y + 88, 200
    visuals.append(_donut(page, "bysev", "gold_notebook_smells", "severity",
                          "Notebook Smell Count", 16, by, 260, bh,
                          "Smells by severity", 2, measure=True,
                          color=("gold_notebook_smells", "severity", SEV_HEX)))
    visuals.append(_stacked_hbar(page, "bynb", "gold_notebook_smells", "notebook_name",
                                 "Notebook Smell Count", "severity",
                                 288, by, PAGE_W - 288 - 16 - 332, bh,
                                 "Notebooks by smell load (colour = severity)", 3,
                                 measure=True,
                                 color=("gold_notebook_smells", "severity", SEV_HEX)))
    visuals.append(_info(
        page, "actions", "Remediation priorities",
        "Fix high-severity smells first. Common gaps: hardcoded paths/secrets, no "
        "error handling, missing parameterisation, oversized cells and notebooks "
        "without an owner. Open a notebook via the link, then patch the listed cells.",
        PAGE_W - 16 - 320, by, 320, bh, 4))
    ty = by + bh + 12
    visuals.append(_table(
        page, "smells", "gold_notebook_smells",
        ["rule_id", "severity", "rule_description", "notebook_name", "cells", "notebook_url"], [],
        16, ty, PAGE_W - 32, PAGE_H - ty - 16,
        "Notebook code smells", 4,
    ))
    return {"name": page, "display": "Notebooks", "visuals": visuals,
            "filters": [_latest_run_filter(page)]}



def _tenant_settings_page() -> Dict[str, Any]:
    """Compliance control panel: every tenant switch as a status matrix plus the
    critical deviations, rather than a generic findings page."""
    page = "TenantSettings"
    visuals = [_banner(
        page, "Tenant settings — compliance control panel",
        "Each admin switch checked against its recommended state. Aligned = pass, "
        "needs review = info, not aligned = fail. Lock down export/sharing first.",
    )]
    x = 16
    for i, (key, measure, label) in enumerate([
        ("pass", "Pass Count", "Aligned"),
        ("fails", "Fail Count", "Not aligned"),
        ("ch", "Critical & High Fails", "High-impact gaps"),
        ("score", "Best Practice Score", "Compliance (%)"),
    ]):
        visuals.append(_card(page, key, "gold_findings", measure, x, _ROW_KPI_Y, label, 1 + i))
        x += 216
    by, bh = _CONTENT_Y, 300
    visuals.append(_sunburst(
        page, "flow", "gold_findings", ["severity", "status"], "Total Findings",
        16, by, 760, bh, "Compliance composition — severity then status", 5,
        measure=True))
    visuals.append(_bar(page, "bystatus", "gold_findings", "status", "Total Findings",
                        792, by, 220, bh, "Aligned vs review vs gap", 6, measure=True,
                        color=("gold_findings", "status", STATUS_COLORS)))
    visuals.append(_info(
        page, "critical", "Critical deviations",
        "Fix not-aligned, high-impact switches first: Publish to web, export, guest "
        "access and developer/API switches. Amber = review with the data owner. Grey "
        "= not evaluated (verify admin scope).",
        1028, by, PAGE_W - 1028 - 16, bh, 7))
    ty = by + bh + 12
    visuals.append(_table(page, "settings", "gold_findings",
                          ["rule_id", "severity", "status", "title", "recommendation"], [],
                          16, ty, PAGE_W - 32, PAGE_H - ty - 16,
                          "Settings — current state, severity and recommended action", 8))
    return {"name": page, "display": "Tenant Settings", "visuals": visuals,
            "filters": [_dimension_filter(page, "tenant_settings"), _latest_run_filter(page)]}


def _semantic_models_page() -> Dict[str, Any]:
    """Summary page: the VertiPaq memory burden of every semantic model."""
    page = "SemanticModels"
    visuals = [_banner(
        page, "Semantic models — VertiPaq footprint",
        "How much memory each model loads into the engine, its column/calculated-"
        "column count and storage mode. Drill into a model on the next page.",
    )]
    x = 16
    for i, (key, measure, label) in enumerate([
        ("models", "Model Count", "Models"),
        ("size", "Total Model Size (MB)", "Total size (MB)"),
        ("cols", "Total Columns", "Columns"),
        ("calc", "Calculated Columns", "Calc columns"),
    ]):
        visuals.append(_card(page, key, "gold_semantic_models", measure, x, _ROW_KPI_Y, label, 1 + i))
        x += 216
    visuals.append(_card(page, "bpa", "gold_bpa_violations", "BPA Violation Count",
                         x, _ROW_KPI_Y, "BPA violations", 5))
    visuals.append(_column_chart(
        page, "bysize", "gold_semantic_models", "model_name", "total_size",
        16, _CONTENT_Y, 600, 240,
        "In-memory size by model (bytes)", 6,
    ))
    visuals.append(_scatter(
        page, "hotspots", "gold_semantic_models", "model_name",
        "total_size", "max_refresh_seconds", "column_count", "storage_mode",
        16, _CONTENT_Y + 252, 600, _CONTENT_H - 252,
        "Model hotspots — size vs. refresh, sized by columns", 7,
    ))
    visuals.append(_table(
        page, "modelstable", "gold_semantic_models",
        ["model_name", "workspace_name", "storage_mode"],
        ["Total Model Size (MB)", "Total Columns", "Calculated Columns"],
        632, _CONTENT_Y, PAGE_W - 632 - 16, _CONTENT_H - 252,
        "Models — size and column counts", 8,
    ))
    visuals.append(_info(
        page, "explain", "What this measures",
        "Size is the in-memory VertiPaq footprint (Import / Abf models). Direct Lake "
        "and DirectQuery models do not pre-load data, so they show little or no size. "
        "Calculated columns are stored uncompressed and recomputed on refresh.",
        632, _CONTENT_Y + _CONTENT_H - 240, (PAGE_W - 632 - 16 - 12) // 2, 240, 9,
    ))
    visuals.append(_info(
        page, "actions", "Priority actions",
        "Shrink the biggest models first: drop unused calc columns, lower high-"
        "cardinality string precision and split fact tables. Pick a model on Model "
        "detail to see the heaviest columns. Clear BPA violations alongside size.",
        632 + (PAGE_W - 632 - 16 - 12) // 2 + 12, _CONTENT_Y + _CONTENT_H - 240,
        (PAGE_W - 632 - 16 - 12) // 2, 240, 10,
    ))
    return {"name": page, "display": "Semantic Models", "visuals": visuals,
            "filters": [_latest_run_filter(page)]}


def _model_detail_page() -> Dict[str, Any]:
    """Drill page: pick a model + table, see VertiPaq Analyzer-style column stats."""
    page = "ModelDetail"
    visuals = [_banner(
        page, "Model detail — tables & columns",
        "Select a model, then a table, to inspect every column the way DAX Studio's "
        "VertiPaq Analyzer does: data type, encoding, cardinality and size.",
    )]
    visuals.append(_field_slicer(
        page, "model", "gold_model_columns", "model_name",
        16, _ROW_KPI_Y, 320, _KPI_H, "Model", 1,
    ))
    visuals.append(_field_slicer(
        page, "table", "gold_model_columns", "table_name",
        344, _ROW_KPI_Y, 320, _KPI_H, "Table", 2,
    ))
    visuals.append(_info(
        page, "explain", "How to read VertiPaq stats",
        "Cardinality = distinct values (the main driver of dictionary size). Encoding: "
        "HASH stores a dictionary of values; VALUE stores the raw integer. Dictionary "
        "size holds the distinct-value lookup; data size holds the per-row pointers. "
        "Large high-cardinality string columns are the usual cost — split, remove or "
        "lower their precision to shrink the model.",
        672, _ROW_KPI_Y, PAGE_W - 672 - 16 - 216, _KPI_H, 3,
    ))
    visuals.append(_card(page, "bpa", "gold_bpa_violations", "BPA Violation Count",
                         PAGE_W - 16 - 200, _ROW_KPI_Y, "BPA violations", 4))
    visuals.append(_bar(
        page, "topcols", "gold_model_columns", "qualified_column", "total_size",
        16, _CONTENT_Y, 360, _CONTENT_H,
        "Heaviest columns by in-memory size (bytes)", 5,
    ))
    visuals.append(_table(
        page, "columns", "gold_model_columns",
        ["table_name", "column_name", "data_type", "encoding", "cardinality",
         "total_size", "dictionary_size", "data_size", "pct_table", "is_calculated"], [],
        392, _CONTENT_Y, PAGE_W - 392 - 16, _CONTENT_H,
        "Columns — type, encoding, cardinality and size (VertiPaq)", 6,
    ))
    return {"name": page, "display": "Model detail", "visuals": visuals,
            "filters": [_latest_run_filter(page)]}


def _model_internals_page() -> Dict[str, Any]:
    """Stacked VertiPaq frames: partitions, relationships and hierarchies.

    Mirrors the way semantic-link-labs' VertiPaq Analyzer returns separate
    frames — each is shown as its own readable grid, sortable in place, with the
    model name as the first column so you can scan every model at once.
    """
    page = "ModelInternals"
    visuals = [_banner(
        page, "Model internals — partitions, relationships & hierarchies",
        "The other VertiPaq Analyzer frames for every model. Sort or filter any "
        "grid in place; the model name is the first column in each.",
    )]
    for i, (key, ent, meas, label) in enumerate([
        ("pcount", "gold_model_partitions", "Partition Count", "Partitions"),
        ("rcount", "gold_model_relationships", "Model Relationship Count", "Relationships"),
        ("hcount", "gold_model_hierarchies", "Hierarchy Count", "Hierarchies"),
    ]):
        visuals.append(_card(page, key, ent, meas, 16 + i * 216, _ROW_KPI_Y, label, 1 + i))
    visuals.append(_info(
        page, "explain", "How to read these frames",
        "Partitions show how each table is segmented (many small segments or a "
        "DirectLake partition). Relationships list each model relationship with its "
        "used size and any missing-key rows (a data-quality and performance smell). "
        "Hierarchies are user-defined drill paths and the extra memory they cost.",
        16 + 3 * 216, _ROW_KPI_Y, PAGE_W - (16 + 3 * 216) - 16, _KPI_H, 4,
    ))
    iw = (PAGE_W - 32 - 24) // 3
    visuals.append(_table(
        page, "partitions", "gold_model_partitions",
        ["model_name", "table_name", "partition_name", "mode",
         "record_count", "segment_count"], [],
        16, _CONTENT_Y, iw, _CONTENT_H,
        "Partitions — mode, records & segments", 5,
    ))
    visuals.append(_table(
        page, "relationships", "gold_model_relationships",
        ["model_name", "from_object", "to_object", "multiplicity",
         "used_size", "missing_rows"], [],
        16 + iw + 12, _CONTENT_Y, iw, _CONTENT_H,
        "Relationships — used size & missing rows", 6,
    ))
    visuals.append(_table(
        page, "hierarchies", "gold_model_hierarchies",
        ["model_name", "table_name", "hierarchy_name", "used_size"], [],
        16 + 2 * (iw + 12), _CONTENT_Y, PAGE_W - 16 - (16 + 2 * (iw + 12)), _CONTENT_H,
        "Hierarchies — in-memory size", 7,
    ))
    return {"name": page, "display": "Model internals", "visuals": visuals,
            "filters": [_latest_run_filter(page)]}



def _pages() -> List[Dict[str, Any]]:
    pages = [
        _home_page(),
        _overview_page(),
        _trends_page(),
        _estate_map_page(),
        _dimension_page("architecture", "Architecture",
                        {"entity": "gold_semantic_models", "title": "Semantic models",
                         "cols": ["workspace_name", "model_name", "storage_mode"]}),
        _dimension_page("performance", "Performance",
                        {"entity": "gold_capacities", "title": "Capacities",
                         "cols": ["capacity_name", "sku", "kind", "state", "region"]}),
        _dimension_page("cost", "Cost",
                        {"entity": "gold_capacities", "title": "Capacities",
                         "cols": ["capacity_name", "sku", "kind", "state", "region"]}),
        _dimension_page("governance", "Governance",
                        {"entity": "gold_workspaces", "title": "Workspaces",
                         "cols": ["workspace_name", "on_capacity", "item_count"]}),
        _dimension_page("security", "Security", None),
        _tenant_settings_page(),
        _best_practices_page(),
        _semantic_models_page(),
        _model_detail_page(),
        _model_internals_page(),
        _notebook_page(),
    ]
    # Persistent "Home" button (top-right, over the banner) on every page except
    # the map itself, so you can always jump back to the navigation map.
    for p in pages:
        if p["name"] == "Home":
            continue
        p["visuals"].append(_action_button(
            p["name"], "homebtn", "Home", PAGE_W - 128, 20, 96, 30, 9000,
            label="\u2302 Home", fill=BRAND_DK,
        ))

    # Premium banners: a subtle azure→brand gradient baked into each non-Home
    # page background, with a thin per-page category accent line beneath it. The
    # banner textbox is then made transparent so the gradient shows. Falls back
    # silently to the solid-brand banner if Pillow is unavailable.
    import base64
    from reports.powerbi import home_map
    for p in pages:
        if p["name"] == "Home":
            continue
        try:
            png = home_map.banner_background(ACCENT.get(p["name"], BRAND))
        except Exception:
            continue
        slug = p["name"].replace(" ", "")
        p["bg_image_name"] = f"archreview_banner_{slug}.png"
        p["bg_image_b64"] = base64.b64encode(png).decode("ascii")
        ban = next((v for v in p["visuals"] if v["name"] == _id(p["name"], "banner")), None)
        if ban is not None:
            ban["visual"].setdefault("visualContainerObjects", {})["background"] = [
                {"properties": {"show": _lit("false")}}
            ]
    return pages



# ---- custom theme --------------------------------------------------------

def _theme() -> Dict[str, Any]:
    """A FUAM-style custom theme: brand palette, rounded cards with a subtle
    shadow, a coloured table header and clear status colours. Power BI ignores
    unknown theme properties, so this stays forward-compatible."""
    return {
        "name": THEME_NAME,
        "dataColors": [BRAND, GOOD, WARN, BAD, "#8764B8", "#038387", "#CA5010", "#4F6BED"],
        "background": CARD,
        "foreground": INK,
        "tableAccent": BRAND,
        "good": GOOD,
        "neutral": WARN,
        "bad": BAD,
        "maximum": BRAND,
        "center": WARN,
        "minimum": BAD,
        "null": MUTED,
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "color": INK, "fontSize": 13},
            "header": {"fontFace": "Segoe UI Semibold", "color": INK, "fontSize": 12},
            "callout": {"fontFace": "Segoe UI Semibold", "color": BRAND, "fontSize": 28},
            "label": {"fontFace": "Segoe UI", "color": MUTED, "fontSize": 10},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": CARD}}, "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": LINE}}, "radius": 8}],
                    "dropShadow": [{"show": True, "preset": "Bottom",
                                    "color": {"solid": {"color": "#B3B0AD"}},
                                    "shadowSpread": 1, "shadowBlur": 9, "transparency": 70}],
                    # Hide the hover header (focus/filter icons) for a clean,
                    # product-grade reading surface.
                    "visualHeader": [{"show": False}],
                    "title": [{"show": True, "fontColor": {"solid": {"color": INK}},
                               "fontSize": 12, "fontFamily": "Segoe UI Semibold",
                               "alignment": "left", "titleWrap": True}],
                },
            },
            "card": {"*": {
                "labels": [{"color": {"solid": {"color": BRAND}}, "fontSize": 28,
                            "fontFamily": "Segoe UI Semibold"}],
                "categoryLabels": [{"show": True, "color": {"solid": {"color": MUTED}},
                                    "fontSize": 11, "fontFamily": "Segoe UI"}],
                "wordWrap": [{"show": True}],
            }},
            "tableEx": {"*": {
                # Horizontal-only rules read cleaner than a full grid (Fluent).
                "grid": [{"gridVertical": False,
                          "gridHorizontal": True, "gridHorizontalColor": {"solid": {"color": LINE}},
                          "rowPadding": 6, "outlineColor": {"solid": {"color": LINE}},
                          "outlineWeight": 1}],
                "columnHeaders": [{"fontColor": {"solid": {"color": "#FFFFFF"}},
                                   "backColor": {"solid": {"color": BRAND}},
                                   "fontFamily": "Segoe UI Semibold", "fontSize": 10,
                                   "alignment": "left", "wordWrap": True}],
                "values": [{"fontColorPrimary": {"solid": {"color": INK}},
                            "backColorPrimary": {"solid": {"color": CARD}},
                            "backColorSecondary": {"solid": {"color": "#F7F9FC"}},
                            "fontSize": 10, "wordWrap": True}],
            }},
            "slicer": {"*": {
                "header": [{"show": True, "fontColor": {"solid": {"color": INK}},
                            "fontFamily": "Segoe UI Semibold", "fontSize": 11}],
                "items": [{"fontColor": {"solid": {"color": INK}}, "fontSize": 10}],
            }},
            "columnChart": {"*": {
                "dataPoint": [{"fill": {"solid": {"color": BRAND}}}],
                "labels": [{"show": True, "color": {"solid": {"color": INK}},
                            "fontSize": 9, "fontFamily": "Segoe UI Semibold"}],
                "legend": [{"show": True, "position": "Top", "fontSize": 9,
                            "labelColor": {"solid": {"color": MUTED}}}],
            }},
            "barChart": {"*": {
                "dataPoint": [{"fill": {"solid": {"color": BRAND}}}],
                "labels": [{"show": True, "color": {"solid": {"color": INK}},
                            "fontSize": 9, "fontFamily": "Segoe UI Semibold"}],
            }},
            "clusteredColumnChart": {"*": {
                "labels": [{"show": True, "color": {"solid": {"color": INK}}, "fontSize": 9}],
                "legend": [{"show": True, "position": "Top", "labelColor": {"solid": {"color": MUTED}}}],
            }},
            "clusteredBarChart": {"*": {
                "labels": [{"show": True, "color": {"solid": {"color": INK}}, "fontSize": 9}],
            }},
            "stackedBarChart": {"*": {
                "labels": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "fontSize": 9}],
                "legend": [{"show": True, "position": "Top", "labelColor": {"solid": {"color": MUTED}}}],
            }},
            "lineChart": {"*": {
                "lineStyles": [{"strokeWidth": 3, "showMarker": True, "markerShape": "circle"}],
                "labels": [{"show": True, "color": {"solid": {"color": INK}}, "fontSize": 9}],
            }},
            "donutChart": {"*": {
                "labels": [{"show": True, "color": {"solid": {"color": INK}}, "fontSize": 9,
                            "labelStyle": "Data value, percent of total"}],
                "legend": [{"show": True, "position": "Right", "labelColor": {"solid": {"color": MUTED}}}],
            }},
            "scatterChart": {"*": {
                "categoryLabels": [{"show": True, "color": {"solid": {"color": MUTED}}, "fontSize": 8}],
                "legend": [{"show": True, "position": "Top", "labelColor": {"solid": {"color": MUTED}}}],
            }},
        },
    }


def _page_background() -> Dict[str, Any]:
    return {
        "background": [{"properties": {
            "color": _solid(CANVAS), "transparency": _lit("0D"),
        }}],
        "outspace": [{"properties": {
            "color": _solid("#EDEBE9"), "transparency": _lit("0D"),
        }}],
    }


def _image_background(item_name: str) -> Dict[str, Any]:
    """Page background that fills the canvas with a RegisteredResources image —
    the FUAM-style way of shipping an illustrated landing page."""
    return {
        "background": [{"properties": {
            "image": {"image": {
                "name": _lit(f"'{item_name}'"),
                "url": {"expr": {"ResourcePackageItem": {
                    "PackageName": "RegisteredResources",
                    "PackageType": 1,
                    "ItemName": item_name,
                }}},
                "scaling": _lit("'Fill'"),
            }},
            "transparency": _lit("0D"),
        }}],
        "outspace": [{"properties": {
            "color": _solid("#EDEBE9"), "transparency": _lit("0D"),
        }}],
    }


# ---- part emission -------------------------------------------------------

def build_parts(semantic_model_id: str) -> List[Dict[str, str]]:
    """Return PBIR parts as ``[{"path": ..., "text": <json str>}]``."""
    parts: List[Dict[str, str]] = []

    def add(path: str, obj: Any) -> None:
        parts.append({"path": path, "text": json.dumps(obj, indent=2)})

    def add_binary(path: str, b64: str) -> None:
        # Binary RegisteredResources item (already base64-encoded raw bytes).
        parts.append({"path": path, "text": "", "b64": b64})

    add("definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byConnection": {
            "connectionString": f"semanticmodelid={semantic_model_id}",
        }},
    })

    add("definition/version.json", {
        "$schema": f"{_SCHEMA}/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })

    # Custom theme lives under StaticResources and is referenced by report.json.
    add(f"StaticResources/RegisteredResources/{THEME_NAME}.json", _theme())

    pages = _pages()

    # Any page may ship an illustrated background image (the Home map). Collect
    # them as RegisteredResources image items + binary parts.
    resource_items: List[Dict[str, str]] = [{
        "name": THEME_NAME, "path": f"{THEME_NAME}.json", "type": "CustomTheme",
    }]
    for p in pages:
        name = p.get("bg_image_name")
        if name and p.get("bg_image_b64"):
            resource_items.append({"name": name, "path": name, "type": "Image"})
            add_binary(f"StaticResources/RegisteredResources/{name}", p["bg_image_b64"])

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
            "items": resource_items,
        }],
        "publicCustomVisuals": [RADAR_VISUAL, SANKEY_VISUAL, SUNBURST_VISUAL],
        "layoutOptimization": "None",
    })

    add("definition/pages/pages.json", {
        "$schema": f"{_SCHEMA}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p["name"] for p in pages],
        "activePageName": pages[0]["name"],
    })

    for p in pages:
        bg_name = p.get("bg_image_name") if p.get("bg_image_b64") else None
        # A background image needs the page/2.0.0 schema (image url expression).
        page_schema = "page/2.0.0" if bg_name else "page/1.0.0"
        page_obj: Dict[str, Any] = {
            "$schema": f"{_SCHEMA}/{page_schema}/schema.json",
            "name": p["name"],
            "displayName": p["display"],
            "displayOption": "FitToPage",
            "height": PAGE_H,
            "width": PAGE_W,
            "objects": _image_background(bg_name) if bg_name else _page_background(),
        }
        if p["filters"]:
            page_obj["filterConfig"] = {"filters": p["filters"]}
        add(f"definition/pages/{p['name']}/page.json", page_obj)
        for v in p["visuals"]:
            add(f"definition/pages/{p['name']}/visuals/{v['name']}/visual.json", v)

    # Fabric rejects an item definition with duplicate part paths
    # (DuplicateDefinitionParts). Two visuals sharing a (page, key) collide on
    # the same visual.json path, so fail fast here instead of at deploy time.
    seen: Dict[str, int] = {}
    for part in parts:
        seen[part["path"]] = seen.get(part["path"], 0) + 1
    dupes = sorted(path for path, n in seen.items() if n > 1)
    if dupes:
        raise ValueError("Duplicate PBIR part paths (give the visuals unique keys): " + ", ".join(dupes))

    return parts

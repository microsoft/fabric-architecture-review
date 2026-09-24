# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Normalize semantic-model DAX metadata and flag explainable static risks.

The collector reads semantic-model TMDL definitions already collected through
Fabric ``getDefinition``. It never executes DAX and never reads model rows.
Signals describe patterns that may be expensive; they are not runtime timings.
"""
from __future__ import annotations

from collectors.workspace_scope import filter_review_payload

import argparse
import json
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List


_MEASURE = re.compile(
    r"^(?P<indent>\s*)measure\s+(?P<name>'(?:''|[^'])*'|[^=]+?)\s*=\s*(?P<expression>.*)$",
    re.IGNORECASE,
)
_PROPERTY = re.compile(
    r"^\s*(?:description|displayFolder|formatString|lineageTag|annotation)\s*:",
    re.IGNORECASE,
)
_ITERATORS = re.compile(r"\b(?:SUMX|AVERAGEX|MINX|MAXX|COUNTX|RANKX|CONCATENATEX)\s*\(", re.IGNORECASE)

OBJECT_TYPES = ("calculated_column", "calculated_table", "calculation_item")
OBJECT_COVERAGE_NOTICE = (
    "Metadata-only static heuristics, not execution or TOM/DAX semantic validation. "
    "Coverage is limited to calculated columns, calculated-table partition expressions, "
    "and calculation items in collected BIM/TMSL or TMDL definitions. "
    "Visual calculations are not collected. Format-string expressions, calculation-group "
    "selection expressions, functions, detail-row and security expressions are not analyzed. "
    "TMDL parsing supports an indentation-based subset, not a full TOM deserializer."
)


def _dax_code(expression: str) -> tuple[str, bool]:
    """Mask literals, identifiers and comments without creating new token boundaries."""
    result: list[str] = []
    index = 0
    valid = True
    while index < len(expression):
        start = index
        char = expression[index]
        if expression.startswith(("//", "--"), index):
            end = expression.find("\n", index)
            index = len(expression) if end == -1 else end
            result.append(" ")
        elif expression.startswith("/*", index):
            end = expression.find("*/", index + 2)
            valid = valid and end != -1
            index = len(expression) if end == -1 else end + 2
            result.append(" " + "\n" * expression[start:index].count("\n"))
        elif char in "\"'[":
            delimiter = "]" if char == "[" else char
            index += 1
            closed = False
            while index < len(expression):
                if expression[index] == delimiter:
                    index += 1
                    if index < len(expression) and expression[index] == delimiter:
                        index += 1
                        continue
                    closed = True
                    break
                index += 1
            valid = valid and closed
            result.append("'Table'" if char == "'" else "[Column]" if char == "[" else '"Literal"')
        else:
            result.append(char)
            index += 1
    return "".join(result), valid


def _name(value: str) -> str:
    value = value.strip()
    return value[1:-1].replace("''", "'") if value.startswith("'") and value.endswith("'") else value


def extract_measures(text: str) -> List[Dict[str, str]]:
    """Extract measure names and expressions from a TMDL part."""
    lines = text.splitlines()
    measures: List[Dict[str, str]] = []
    index = 0
    while index < len(lines):
        match = _MEASURE.match(lines[index])
        if not match:
            index += 1
            continue
        expression = [match.group("expression").strip()]
        base_indent = len(match.group("indent").expandtabs(4))
        index += 1
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            indent = len(line[: len(line) - len(line.lstrip())].expandtabs(4))
            if stripped and indent <= base_indent:
                break
            if stripped and _PROPERTY.match(line):
                index += 1
                continue
            if stripped:
                expression.append(stripped)
            index += 1
        dax = "\n".join(part for part in expression if part).strip()
        measures.append({"measure_name": _name(match.group("name")), "expression": dax})
    return measures


def extract_bim_measures(text: str) -> List[Dict[str, str]]:
    """Extract table-qualified measures from a model.bim JSON definition."""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, dict):
        return []
    measures: List[Dict[str, str]] = []
    for table in model.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for measure in table.get("measures") or []:
            if not isinstance(measure, dict) or not measure.get("name"):
                continue
            expression = measure.get("expression") or ""
            if isinstance(expression, list):
                expression = "\n".join(str(line) for line in expression)
            measures.append({
                "table_name": str(table.get("name") or ""),
                "measure_name": str(measure["name"]),
                "expression": str(expression).strip(),
            })
    return measures


def analyze_expression(expression: str) -> List[Dict[str, Any]]:
    """Return deterministic, explainable static-risk signals for one expression."""
    signals: List[Dict[str, Any]] = []
    expression_length = len(expression)
    expression, _ = _dax_code(expression)

    def add(code: str, category: str, points: int, message: str) -> None:
        signals.append({"code": code, "category": category, "points": points, "message": message})

    iterator_count = len(_ITERATORS.findall(expression))
    if iterator_count >= 2:
        add("nested_iterators", "iteration", 25, f"Expression contains {iterator_count} iterator calls.")
    elif iterator_count == 1:
        add("iterator", "iteration", 8, "Expression contains an iterator; validate its input cardinality.")
    if re.search(r"\bCROSSJOIN\s*\(", expression, re.IGNORECASE):
        add("crossjoin", "cardinality", 35, "CROSSJOIN can create a large intermediate row set.")
    if re.search(r"\b(?:GENERATE|GENERATEALL)\s*\(", expression, re.IGNORECASE):
        add("generate", "cardinality", 30, "GENERATE can multiply rows in an intermediate table.")
    if re.search(r"\bADDCOLUMNS\s*\(", expression, re.IGNORECASE):
        add("addcolumns", "materialization", 15, "ADDCOLUMNS can materialize an expanded virtual table.")
    if re.search(r"\bFILTER\s*\(\s*(?:'(?:''|[^'])+'|[A-Za-z_][\w ]*)\s*,", expression, re.IGNORECASE):
        add("whole_table_filter", "filtering", 20, "FILTER iterates a whole table instead of a narrowed column set.")
    if re.search(r"\b(?:ALL|REMOVEFILTERS)\s*\(\s*(?:'(?:''|[^'])+'|[A-Za-z_][\w ]*)\s*\)", expression, re.IGNORECASE):
        add("broad_context_removal", "filtering", 12, "Filter context is removed from an entire table.")
    if re.search(r"\bEARLIER\s*\(", expression, re.IGNORECASE):
        add("earlier", "complexity", 20, "EARLIER usually indicates nested row-context evaluation.")
    if expression_length >= 1500:
        add("very_long_expression", "complexity", 20, "Expression exceeds 1,500 characters.")
    elif expression_length >= 750:
        add("long_expression", "complexity", 10, "Expression exceeds 750 characters.")
    return signals


def _expression(value: Any) -> str | None:
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        value = "\n".join(value)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _object(table: str, kind: str, name: str, value: Any, issues: set[str]) -> Dict[str, Any] | None:
    expression = _expression(value)
    if not table or not name or expression is None:
        issues.add("missing_or_invalid_object_expression")
        return None
    code, valid = _dax_code(expression)
    delimiters: list[str] = []
    for char in code:
        if char in "({":
            delimiters.append(char)
        elif char in ")}":
            if not delimiters or delimiters.pop() != ({"}": "{", ")": "("}[char]):
                valid = False
    valid = valid and not delimiters
    if not valid or not code.strip():
        issues.add("incomplete_object_expression")
        return None
    signals = analyze_expression(expression)
    score = min(100, sum(int(signal["points"]) for signal in signals))
    return {
        "table_name": table, "object_type": kind, "object_name": name,
        "expression": expression, "expression_length": len(expression),
        "risk_score": score,
        "risk_level": "high" if score >= 40 else "medium" if score >= 20 else "low" if score else "none",
        "signals": signals,
    }


def _records(value: Any, issues: set[str]) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        issues.add("invalid_object_collection")
        return []
    if any(not isinstance(item, dict) for item in value):
        issues.add("invalid_object_collection")
    return [item for item in value if isinstance(item, dict)]


def extract_bim_objects(text: str) -> tuple[list[dict], set[str]]:
    """Read model/database definitions and full-model TMSL create/replace envelopes.

    CalculatedTableColumn metadata is deliberately not a calculated column.
    Only a partition source explicitly typed ``calculated`` supplies table DAX.
    Partial table/partition commands and arbitrary JSON are not full definitions.
    """
    issues: set[str] = set()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return [], {"invalid_json_definition"}
    if isinstance(payload, dict):
        for command in ("create", "createOrReplace", "alter"):
            if command in payload:
                payload = payload[command]
                break
    if isinstance(payload, dict) and "database" in payload:
        payload = payload["database"]
    model = payload.get("model") if isinstance(payload, dict) else None
    if model is None and isinstance(payload, dict) and "tables" in payload:
        model = payload
    if not isinstance(model, dict):
        return [], {"unsupported_json_definition"}
    if not isinstance(model.get("tables"), list):
        issues.add("missing_or_invalid_tables_collection")
    rows: list[dict] = []

    def append(table: str, kind: str, name: Any, expression: Any) -> None:
        row = _object(table, kind, name if isinstance(name, str) else "", expression, issues)
        if row is not None:
            rows.append(row)

    for table in _records(model.get("tables"), issues):
        name = table.get("name")
        if not isinstance(name, str) or not name:
            issues.add("missing_table_name")
            continue
        for column in _records(table.get("columns"), issues):
            kind = column.get("type")
            if kind == "calculated":
                append(name, "calculated_column", column.get("name"), column.get("expression"))
            elif "expression" in column:
                issues.add("unsupported_column_expression")
            elif kind not in (None, "data", "calculatedTableColumn", "rowNumber"):
                issues.add("unsupported_column_type")
        calculated = []
        partitions = _records(table.get("partitions"), issues)
        for partition in partitions:
            source = partition.get("source")
            if not isinstance(source, dict):
                issues.add("missing_or_invalid_partition_source")
            elif source.get("type") == "calculated":
                calculated.append(source.get("expression"))
            elif source.get("type") not in ("m", "query", "entity", "none", "calculationGroup", "policyRange"):
                issues.add("unsupported_partition_source")
        if len(calculated) > 1:
            issues.add("multiple_calculated_partitions")
        elif calculated and len(partitions) > 1:
            issues.add("mixed_partition_sources")
        elif calculated:
            append(name, "calculated_table", name, calculated[0])
        group = table.get("calculationGroup")
        if group is not None:
            if not isinstance(group, dict):
                issues.add("invalid_calculation_group")
            else:
                for item in _records(group.get("calculationItems"), issues):
                    append(name, "calculation_item", item.get("name"), item.get("expression"))
    return rows, issues


@dataclass
class _TmdlNode:
    kind: str
    name: str
    indent: int
    expression: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    children: list[_TmdlNode] = field(default_factory=list)


_TMDL_HEADER = re.compile(
    r"^(?P<kind>[A-Za-z]\w*)(?:\s+(?P<name>'(?:''|[^'])*'|[^=:']+?))?"
    r"\s*(?:(?P<delimiter>[=:])\s*(?P<value>.*))?$"
)
_TMDL_KINDS = {
    "database", "model", "table", "column", "measure", "partition", "source",
    "calculationgroup", "calculationitem", "expression", "formatstringdefinition",
    "noselectionexpression", "multipleoremptyselectionexpression", "detailrowsdefinition",
    "annotation", "extendedproperty", "lineagetag", "hierarchy", "level",
    "relationship", "role", "tablepermission", "columnpermission", "member",
    "culture", "cultureinfo", "linguisticmetadata", "perspective", "perspectivetable",
    "perspectivecolumn", "perspectivemeasure", "perspectivehierarchy", "translation",
    "datasource", "function", "refreshexpression", "refreshpolicy",
    "dataaccessoptions", "querygroup", "changedproperty", "variation", "kpi",
    "sortbycolumn", "ishidden", "isunique", "iskey", "isnullable", "isdefaultlabel",
    "isdefaultimage", "isavailableinmdx", "discourageimplicitmeasures",
    "excludefrommodelrefresh", "showasvariationsonly",
    "legacyredirects", "returnerrorvaluesasnull", "relyonreferentialintegrity",
}


def _indent(line: str) -> int:
    return len(line[:len(line) - len(line.lstrip())].expandtabs(4))


def _tmdl_expression(lines: list[str], index: int, value: str, issues: set[str]) -> tuple[str, int]:
    base = _indent(lines[index])
    index += 1
    if value.strip() == "```":
        start = index
        while index < len(lines) and lines[index].strip() != "```":
            index += 1
        if index == len(lines):
            issues.add("unterminated_tmdl_expression")
        return "\n".join(lines[start:index]), min(index + 1, len(lines))
    if value.strip():
        return value.strip(), index
    start = index
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index == len(lines) or _indent(lines[index]) <= base:
        return "", index
    expression_indent = _indent(lines[index])
    while index < len(lines):
        line = lines[index]
        if line.strip() and _indent(line) < expression_indent:
            break
        # Metadata is never expression text, including legacy one-level indentation.
        if line.strip() and _indent(line) == expression_indent and re.match(
            r"(?:\w+\s*:|(?:formatStringDefinition|annotation|extendedProperty)\b|"
            r"(?:isHidden|isKey|isUnique|isNullable|isAvailableInMdx)\s*$)",
            line.strip(), re.IGNORECASE,
        ):
            break
        index += 1
    return textwrap.dedent("\n".join(line.expandtabs(4) for line in lines[start:index])).rstrip(), index


def _extract_tmdl_objects(text: str) -> tuple[list[dict], set[str], set[str], set[str]]:
    """Extract typed DAX, respecting table/group/partition parents and expression blocks.

    Default expression properties and ``source =`` use the official TMDL rules:
    https://learn.microsoft.com/analysis-services/tmdl/tmdl-overview
    Unknown constructs are reported, not treated as an empty clean definition.
    """
    issues: set[str] = set()
    root = _TmdlNode("root", "", -1)
    table_names: set[str] = set()
    table_refs: set[str] = set()
    stack = [root]
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            index += 1
            continue
        if stripped.lower().startswith("ref "):
            reference = re.fullmatch(r"ref\s+table\s+('(?:''|[^'])*'|[^']+)", stripped, re.IGNORECASE)
            if reference:
                table_refs.add(_name(reference[1]))
            index += 1
            continue
        indent = _indent(line)
        while len(stack) > 1 and indent <= stack[-1].indent:
            stack.pop()
        match = _TMDL_HEADER.fullmatch(stripped)
        if not match:
            issues.add("unrecognized_tmdl_syntax")
            index += 1
            continue
        kind = match["kind"].lower()
        if kind == "ref":
            index += 1
            continue
        if match["delimiter"] == ":":
            stack[-1].properties[kind] = match["value"]
            index += 1
            continue
        if kind not in _TMDL_KINDS:
            issues.add("unsupported_tmdl_construct")
        node = _TmdlNode(kind, _name(match["name"] or ""), indent)
        stack[-1].children.append(node)
        stack.append(node)
        if match["delimiter"] == "=" and kind != "partition":
            node.expression, index = _tmdl_expression(lines, index, match["value"], issues)
        else:
            if kind == "partition":
                node.properties["sourcetype"] = (match["value"] or "").strip().lower()
            index += 1
    rows: list[dict] = []
    seen_table = False

    def expression(node: _TmdlNode) -> str | None:
        values = ([node.expression] if node.expression is not None else []) + [
            child.expression for child in node.children if child.kind == "expression"
        ]
        if len(values) > 1:
            issues.add("duplicate_expression_definition")
            return None
        return values[0] if values else None

    def append(table: str, kind: str, name: str, value: Any) -> None:
        row = _object(table, kind, name, value, issues)
        if row is not None:
            rows.append(row)

    def visit(node: _TmdlNode, table: str = "", parent: str = "") -> None:
        nonlocal seen_table
        if node.kind == "table":
            seen_table = True
            table = node.name
            table_names.add(table)
            if not table or node.expression is not None:
                issues.add("unsupported_table_declaration")
            calculated = [child for child in node.children if child.kind == "partition"
                          and child.properties.get("sourcetype") == "calculated"]
            if len(calculated) > 1:
                issues.add("multiple_calculated_partitions")
            elif calculated and sum(child.kind == "partition" for child in node.children) > 1:
                issues.add("mixed_partition_sources")
            elif calculated:
                sources = [child for child in calculated[0].children if child.kind == "source"]
                if len(sources) != 1:
                    issues.add("missing_or_duplicate_calculated_source")
                else:
                    append(table, "calculated_table", table, expression(sources[0]))
        elif node.kind == "column":
            value = expression(node)
            if node.properties.get("type", "").lower() not in (
                "", "data", "calculated", "calculatedtablecolumn", "rownumber",
            ):
                issues.add("unsupported_column_type")
            if value is not None or node.properties.get("type") == "calculated":
                if parent != "table":
                    issues.add("unsupported_column_parent")
                else:
                    append(table, "calculated_column", node.name, value)
        elif node.kind == "calculationgroup" and parent != "table":
            issues.add("unsupported_calculation_group_parent")
        elif node.kind == "calculationitem":
            if parent != "calculationgroup" or not table:
                issues.add("unsupported_calculation_item_parent")
            else:
                append(table, "calculation_item", node.name, expression(node))
        elif node.kind == "partition":
            if parent != "table" or node.properties.get("sourcetype") not in (
                "calculated", "m", "query", "entity", "none", "calculationgroup", "policyrange",
            ):
                issues.add("unsupported_partition_source")
        for child in node.children:
            visit(child, table, node.kind)

    visit(root)
    if not seen_table and not any(child.kind in ("model", "database", "culture", "cultureinfo", "role", "perspective",
                                                "relationship", "expression", "function")
                                  for child in root.children):
        issues.add("no_supported_tmdl_root")
    return rows, issues, table_names, table_refs


def extract_tmdl_objects(text: str) -> tuple[list[dict], set[str]]:
    """Extract objects and parsing limitations from one TMDL document.

    Model-level coverage additionally checks references across collected parts.
    """
    rows, issues, _, _ = _extract_tmdl_objects(text)
    return rows, issues


def build_object_analysis(definitions: Dict[str, Any]) -> Dict[str, list[dict]]:
    """Independent non-measure raw contract; status is scoped to supported object types."""
    objects: list[dict] = []
    coverage: list[dict] = []
    for model in _models(definitions):
        context = {
            "model_id": model.get("id"), "model_name": model.get("name") or model.get("displayName"),
            "workspace_id": model.get("workspaceId"), "workspace_name": model.get("workspaceName"),
        }
        issues: set[str] = set()
        found = False
        unique: dict[tuple[str, str, str], dict] = {}
        conflicts: set[tuple[str, str, str]] = set()
        table_names: set[str] = set()
        table_refs: set[str] = set()
        for part in _records(model.get("parts"), issues):
            path = str(part.get("path") or "").lower()
            if not path.endswith((".tmdl", ".bim", ".json", ".tmsl")):
                continue
            if path.endswith(".json") and not path.endswith(("model.json", ".tmsl.json", "database.json")):
                # Definitions may use arbitrary filenames for TMSL, but unrelated
                # JSON metadata (for example diagram layouts) is not a model.
                try:
                    candidate = json.loads(part.get("text") or "")
                except (json.JSONDecodeError, TypeError):
                    candidate = None
                if not isinstance(candidate, dict) or not {
                    "model", "database", "create", "createOrReplace", "alter",
                }.intersection(candidate):
                    continue
            found = True
            text = part.get("text")
            if not isinstance(text, str):
                issues.add("definition_text_unavailable")
                continue
            if path.endswith(".tmdl"):
                rows, parse_issues, names, refs = _extract_tmdl_objects(text)
                table_names.update(names)
                table_refs.update(refs)
            else:
                rows, parse_issues = extract_bim_objects(text)
            issues.update(parse_issues)
            for row in rows:
                key = (row["table_name"], row["object_type"], row["object_name"])
                if key in unique:
                    issues.add("duplicate_object_definition")
                    if unique[key]["expression"] != row["expression"]:
                        conflicts.add(key)
                unique[key] = row
        if table_refs - table_names:
            issues.add("referenced_tables_not_collected")
        if model.get("error"):
            issues.add("definition_collection_error")
        if not found:
            issues.add("definition_not_collected")
        model_rows = [{**context, **row} for key, row in sorted(unique.items()) if key not in conflicts]
        objects.extend(model_rows)
        coverage.append({
            **context,
            "definition_status": "unavailable" if not found or model.get("error") and not model_rows
            else "partial" if issues else "complete",
            "object_count": len(model_rows),
            "flagged_object_count": sum(row["risk_level"] in ("medium", "high") for row in model_rows),
            "notice": OBJECT_COVERAGE_NOTICE + (" Limitations: " + ", ".join(sorted(issues)) + "." if issues else ""),
        })
    key_fields = ("workspace_id", "model_id", "table_name", "object_type", "object_name")
    objects.sort(key=lambda row: tuple(str(row.get(key) or "") for key in key_fields))
    coverage.sort(key=lambda row: (str(row.get("workspace_id") or ""), str(row.get("model_id") or "")))
    return {"objects": objects, "object_coverage": coverage}


def _models(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    value = payload.get("models") or []
    return value if isinstance(value, list) else []


def build_analysis(definitions: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    model_count = 0
    definition_errors = 0
    for model in _models(definitions):
        model_count += 1
        if model.get("error"):
            definition_errors += 1
            continue
        for part in model.get("parts") or []:
            text = part.get("text")
            if not isinstance(text, str):
                continue
            path = str(part.get("path") or "")
            table_match = re.search(r"(?:^|/)tables/([^/]+)\.tmdl$", path, re.IGNORECASE)
            table_name = table_match.group(1) if table_match else ""
            extracted = extract_bim_measures(text) if path.lower().endswith("model.bim") else extract_measures(text)
            for measure in extracted:
                signals = analyze_expression(measure["expression"])
                risk_score = min(100, sum(int(signal["points"]) for signal in signals))
                risk_level = "high" if risk_score >= 40 else "medium" if risk_score >= 20 else "low" if risk_score else "none"
                rows.append({
                    "model_id": model.get("id"),
                    "model_name": model.get("name") or model.get("displayName"),
                    "workspace_id": model.get("workspaceId"),
                    "workspace_name": model.get("workspaceName"),
                    "table_name": measure.pop("table_name", table_name),
                    **measure,
                    "expression_length": len(measure["expression"]),
                    "risk_score": risk_score,
                    "risk_level": risk_level,
                    "signals": signals,
                })
    return {
        "available": model_count > 0,
        "metadata_only": True,
        "models_scanned": model_count,
        "definition_errors": definition_errors,
        "measures": rows,
        **build_object_analysis(definitions),
    }


def collect(output_dir: str | Path = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "dax_analysis.json"
    source = raw_dir / "semantic_model_definitions.json"
    if source.exists():
        definitions = filter_review_payload(json.loads(source.read_text(encoding="utf-8-sig")), raw_dir)
        payload = build_analysis(definitions)
    else:
        payload = {
            "available": False,
            "metadata_only": True,
            "models_scanned": 0,
            "definition_errors": 0,
            "measures": [],
            "objects": [],
            "object_coverage": [],
            "notes": ["semantic_model_definitions.json was not collected"],
        }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(payload['measures'])} measure(s)).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()
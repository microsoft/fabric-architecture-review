# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Normalize semantic-model DAX metadata and flag explainable static risks.

The collector reads semantic-model TMDL definitions already collected through
Fabric ``getDefinition``. It never executes DAX and never reads model rows.
Signals describe patterns that may be expensive; they are not runtime timings.
"""
from __future__ import annotations

import argparse
import json
import re
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
    if len(expression) >= 1500:
        add("very_long_expression", "complexity", 20, "Expression exceeds 1,500 characters.")
    elif len(expression) >= 750:
        add("long_expression", "complexity", 10, "Expression exceeds 750 characters.")
    return signals


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
    }


def collect(output_dir: str | Path = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "dax_analysis.json"
    source = raw_dir / "semantic_model_definitions.json"
    if source.exists():
        definitions = json.loads(source.read_text(encoding="utf-8-sig"))
        payload = build_analysis(definitions)
    else:
        payload = {
            "available": False,
            "metadata_only": True,
            "models_scanned": 0,
            "definition_errors": 0,
            "measures": [],
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
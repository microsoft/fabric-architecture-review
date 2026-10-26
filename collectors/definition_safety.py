# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""In-memory reduction of definitions to architectural evidence, never source."""
from __future__ import annotations

import ast
import base64
import binascii
import json
import re
from typing import Any

_NAME = re.compile(r"^[\w .:/-]{1,256}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
# Notebook source patterns, keyed by the rule that reports their hit count.
_GUID_LITERAL = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
# Storage modes are reported in the canonical names the policy and rules use.
_STORAGE_MODES = {"import": "Import", "directquery": "DirectQuery", "directlake": "DirectLake", "dual": "Dual"}
SIGNAL_PATTERNS = {
    "SEC-011": r"(?i)(?:password|secret|api[_-]?key|access[_-]?token)\s*=\s*['\"][^'\"]+",
    "ARCH-010": r"(?i)(?:dbutils\.|/dbfs/|databricks\.)",
    # A literal storage path, workspace or item identifier, or server name ties code to one environment.
    "ARCH-011": (
        r"(?i)(?:abfss?://[^\s'\"{}]+|jdbc:\w+://"
        r"|(?:server|data source)\s*=\s*[\w.-]+\.(?:database\.windows\.net|fabric\.microsoft\.com)"
        r"|(?:workspace|lakehouse|warehouse|dataset|capacity)[_ ]?id['\"]?\s*[=:]\s*['\"]" + _GUID_LITERAL + r"['\"])"
    ),
    # An install with no pinned version or requirements file changes the runtime between runs.
    # A package token with no "==" on the line, unless the line installs from a requirements file.
    "ARCH-012": (
        r"(?im)^[ \t]*[%!]pip[ \t]+install\b(?![^\n]*[ \t](?:-r|--requirement)\b)"
        r"(?=[^\n]*[ \t](?!-)(?![^ \t\n]*==)[^ \t\n]+)[^\n]*"
    ),
}

_PRIVATE_KEYS = frozenset({
    "expression", "expressions", "query", "querytext", "mashup", "source", "sourcecode",
    "code", "payload", "outputs", "execution_count", "connectionstring",
    "connectiondetails", "password", "secret", "token", "apikey", "credential",
    "credentials", "defaultvalue", "parameterValues".lower(),
    "script", "scripts", "definition", "definitions", "dax", "m", "mexpression",
    "daxexpression", "sql", "sqlscript", "authorization", "access_token", "clientsecret",
})


def metadata_only(value: Any) -> Any:
    """Discard executable/literal payload fields from untrusted inventory responses."""
    if isinstance(value, dict):
        return {key: metadata_only(item) for key, item in value.items()
                if str(key).lower() not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [metadata_only(item) for item in value]
    return value


def safe_name(value: Any) -> str | None:
    return value if isinstance(value, str) and _NAME.fullmatch(value) else None


def safe_guid(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and _GUID.fullmatch(value) else None


def decoded_parts(definition: dict | None) -> list[tuple[str, str]]:
    result = []
    for part in ((definition or {}).get("definition") or {}).get("parts") or []:
        if not isinstance(part, dict) or str(part.get("payloadType", "")).lower() != "inlinebase64":
            continue
        try:
            text = base64.b64decode(part.get("payload", ""), validate=True).decode("utf-8-sig")
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
            continue
        result.append((str(part.get("path") or ""), text))
    return result


def _activities(rows: Any) -> list[dict]:
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        activity = {key: value for key in ("name", "type")
                    if (value := safe_name(row.get(key))) is not None}
        dependencies = []
        for dependency in row.get("dependsOn") or []:
            if isinstance(dependency, dict) and (name := safe_name(dependency.get("activity"))):
                dependencies.append({
                    "activity": name,
                    "dependencyConditions": [value for value in dependency.get("dependencyConditions") or []
                                             if value in {"Succeeded", "Failed", "Skipped", "Completed"}],
                })
        activity["dependsOn"] = dependencies
        policy = row.get("policy")
        if isinstance(policy, dict):
            safe_policy: dict = {}
            retry, timeout = policy.get("retry"), policy.get("timeout")
            if isinstance(retry, int) and not isinstance(retry, bool):
                safe_policy["retry"] = retry
            if isinstance(timeout, str) and re.fullmatch(r"(?:\d+\.)?\d{1,2}:\d{2}:\d{2}", timeout):
                safe_policy["timeout"] = timeout
            if ("retry" in policy and "retry" not in safe_policy) or ("timeout" in policy and "timeout" not in safe_policy):
                safe_policy["dynamic"] = True
            activity["policy"] = safe_policy
        props = row.get("typeProperties") or {}
        safe_props = {}
        for key in ("notebookId", "workspaceId", "pipelineId"):
            if value := safe_guid(props.get(key)):
                safe_props[key] = value
        notebook = props.get("notebook")
        if isinstance(notebook, dict) and (value := safe_guid(notebook.get("referenceName"))):
            safe_props["notebook"] = {"referenceName": value}
        parameters = props.get("parameters")
        if isinstance(parameters, dict):
            safe_props["parameters"] = {name: None for key in parameters if (name := safe_name(key))}
        for key in ("activities", "ifTrueActivities", "ifFalseActivities"):
            if key in props:
                safe_props[key] = _activities(props[key])
        cases = props.get("cases")
        if isinstance(cases, list):
            safe_props["cases"] = [{"activities": _activities(case.get("activities"))}
                                  for case in cases if isinstance(case, dict)]
        defaults = props.get("defaultActivities")
        if defaults is not None:
            safe_props["defaultActivities"] = _activities(defaults)
        activity["typeProperties"] = safe_props
        result.append(activity)
    return result


def pipeline_summary(definition: dict | None) -> dict:
    parts = []
    for path, text in decoded_parts(definition):
        if not path.lower().endswith(".json"):
            continue
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(body, dict):
            continue
        properties = body.get("properties", body)
        if not isinstance(properties, dict) or "activities" not in properties:
            continue
        skeleton = {"properties": {"activities": _activities(properties["activities"])}}
        parameters = properties.get("parameters")
        if isinstance(parameters, dict):
            skeleton["properties"]["parameters"] = {
                name: None for key in parameters if (name := safe_name(key))
            }
        parts.append({"path": "pipeline-content.json", "decoded": skeleton})
    return {"parts": parts, "definitionCoverage": "complete" if parts else "unknown", "metadataOnly": True}


def notebook_summary(definition: dict | None) -> dict:
    names: set[str] = set()
    signals = dict.fromkeys(SIGNAL_PATTERNS, 0)
    coverage = "unknown"
    unreadable = False
    sources: list[str] = []
    for path, text in decoded_parts(definition):
        if path.lower().endswith(".ipynb"):
            try:
                notebook = json.loads(text)
                cells = notebook["cells"]
                if not isinstance(cells, list):
                    continue
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            coverage = "complete"
            for cell in cells:
                if not isinstance(cell, dict):
                    coverage = "partial"
                    unreadable = True
                    continue
                if cell.get("cell_type") != "code":
                    continue
                source = cell.get("source", "")
                source = "".join(source) if isinstance(source, list) else source
                if not isinstance(source, str):
                    coverage = "partial"
                    unreadable = True
                    continue
                sources.append(source)
                if "parameters" not in (cell.get("metadata") or {}).get("tags", []):
                    continue
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    coverage = "partial"
                    continue
                for node in tree.body:
                    targets = node.targets if isinstance(node, ast.Assign) else (
                        [node.target] if isinstance(node, ast.AnnAssign) else []
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
        elif path.lower().endswith((".py", ".scala", ".sql", ".r")):
            cells = re.split(r"(?m)^# (CELL|PARAMETERS CELL|MARKDOWN)[^\n]*$", text) if path.lower().endswith(".py") else []
            if len(cells) > 1:
                # Fabric's source format: a header line per cell; the parameter cell is the
                # "PARAMETERS CELL" one (some exports tag it "parameters" instead).
                coverage = "complete" if coverage in ("unknown", "complete") else coverage
                for kind, body in zip(cells[1::2], cells[2::2]):
                    if kind == "MARKDOWN":
                        continue
                    code, _, metadata = body.partition("# METADATA")
                    try:
                        tags = json.loads("\n".join(re.findall(r"(?m)^# META (.*)$", metadata))).get("tags", [])
                    except (json.JSONDecodeError, AttributeError):
                        tags = []
                    code = re.sub(r"(?m)^# MAGIC ?", "", code)
                    sources.append(code)
                    if kind != "PARAMETERS CELL" and "parameters" not in tags:
                        continue
                    try:
                        tree = ast.parse(code)
                    except SyntaxError:
                        coverage = "partial"
                        continue
                    for node in tree.body:
                        targets = node.targets if isinstance(node, ast.Assign) else (
                            [node.target] if isinstance(node, ast.AnnAssign) else []
                        )
                        names.update(target.id for target in targets if isinstance(target, ast.Name))
            else:
                sources.append(text)
                coverage = "partial"
            for key in re.findall(r"(?:widgets|parameters)\.(?:get|text)\(\s*['\"](\w+)['\"]", text):
                names.add(key)
    for source in sources:
        for rule, pattern in SIGNAL_PATTERNS.items():
            signals[rule] += len(re.findall(pattern, source))
    return {"parts": [], "parameterNames": sorted(names), "parameterCoverage": coverage,
            "signalCoverage": "partial" if unreadable else ("complete" if sources or coverage == "complete" else "unknown"),
            "signals": signals, "metadataOnly": True}


def model_summary(definition: dict | None) -> dict:
    storage_modes: set[str] = set()
    lake_modes: set[str] = set()
    features: set[str] = set()
    bindings: list[dict] = []
    behaviors: set[str] = set()
    decoded = decoded_parts(definition)
    for path, text in decoded:
        if path.lower().endswith((".bim", ".json")):
            try:
                model = json.loads(text)
                model = model.get("model", model)
                if isinstance(model, dict):
                    behavior = str(model.get("directLakeBehavior") or "").lower()
                    if behavior in {"automatic", "directlakeonly", "directqueryonly"}:
                        behaviors.add(behavior)
                    for table in model.get("tables") or []:
                        if not isinstance(table, dict):
                            continue
                        if table.get("calculationGroup"):
                            features.add("calculationGroup")
                        for column in table.get("columns") or []:
                            if isinstance(column, dict) and column.get("type") == "calculated":
                                features.add("calculatedColumn")
                        for partition in table.get("partitions") or []:
                            if not isinstance(partition, dict):
                                continue
                            mode = str(partition.get("mode") or "").lower()
                            if mode in _STORAGE_MODES:
                                storage_modes.add(_STORAGE_MODES[mode])
                            if isinstance(partition.get("source"), dict) and partition["source"].get("type") == "calculated":
                                features.add("calculatedTable")
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass
        for match in re.finditer(r"(?im)^\s*mode:\s*(import|directQuery|directLake|dual)\s*$", text):
            storage_modes.add(_STORAGE_MODES[match[1].lower()])
        for match in re.finditer(r"(?im)^\s*directLakeBehavior:\s*(automatic|directLakeOnly|directQueryOnly)\s*$", text):
            behaviors.add(match[1].lower())
        for token in ("calculationGroup", "calculatedTable", "calculatedColumn", "detailRowsDefinition"):
            if re.search(rf"(?im)^\s*{token}\b", text):
                features.add(token)
        if re.search(r"(?im)^\s*partition\s+.+\s*=\s*calculated\s*$", text):
            features.add("calculatedTable")
        if re.search(r"(?im)^\s*column\s+.+\s*=", text):
            features.add("calculatedColumn")
        if "directlake" in text.lower():
            if re.search(r"(?i)Sql\.Database\s*\(", text):
                lake_modes.add("sql_endpoint")
            if re.search(r"(?i)(?:AzureStorage\.DataLake|OneLake)\b", text):
                lake_modes.add("onelake")
        ids = sorted(set(value.lower() for value in re.findall(
            r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", text
        )))
        # UUIDs alone do not establish their role or a verified dependency.
        for value in ids:
            bindings.append({"id": value, "identityRole": "unknown"})
    return {"parts": [], "storageModes": sorted(storage_modes), "directLakeModes": sorted(lake_modes),
            "directLakeBehavior": next(iter(behaviors)) if len(behaviors) == 1 else None,
            "sourceBindings": bindings, "featureNames": sorted(features),
            "definitionCoverage": "partial" if decoded else "unknown", "metadataOnly": True}

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Reduce a semantic model definition to its row-level (RLS) and object-level (OLS) security configuration.

Roles are read in memory from the TMSL (``model.bim``) or TMDL parts the Fabric ``getDefinition`` API
returns. Only counts and an inspection status leave this module by default: filter expressions, role and
table/column names, and role membership are never stored.

Role detail is opt-in (``keep_roles``). It adds one record per role: the role name, its permission, how many
tables carry a row filter, how many tables and columns are hidden, and whether the filters use the identity of
the user (dynamic) or not (static). Filter text, table names, column names and members are still never kept.

What is established
  RLS  A role that can read the model has at least one table filter that restricts rows. A filter that is
       constant ``TRUE`` restricts nothing and is counted separately.
  OLS  A role that can read the model hides at least one table or column (``metadataPermission: none``).
  Dynamic  A filter calls USERPRINCIPALNAME, USERNAME, USEROBJECTID or CUSTOMDATA outside a string, a quoted
       name, a column reference or a comment. A filter that looks up a mapping table is dynamic only if that
       table's own permission calls one of these, so the label says what the filter reads, not that it is safe.

What is not established: that a filter implements the intended rule, who is a member of a role, or security
enforced outside the model (for example at the data source or through OneLake security).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from collectors.definition_safety import decoded_parts

# Roles that can read model data. ``administrator`` bypasses filters, and ``none``/``refresh`` read nothing.
# A role with no permission stated is treated as a reader role: it exists to carry filters.
READER_PERMISSIONS = frozenset({"read", "readrefresh"})
INSPECTION_STATES = ("complete", "partial", "unavailable")
ROLE_PERMISSIONS = ("read", "readRefresh", "refresh", "administrator", "none", "unspecified", "other")
FILTER_KINDS = ("none", "constant", "static", "dynamic", "mixed", "unknown", "not_applicable")
MAX_ROLE_RECORDS = 200
MAX_ROLE_NAME = 128
_PERMISSION_NAMES = {"read": "read", "readrefresh": "readRefresh", "refresh": "refresh",
                     "administrator": "administrator", "none": "none"}
_TRIVIAL_FILTERS = frozenset({"TRUE()", "TRUE", "(TRUE())", "1=1", "(1=1)"})
_IDENTITY_CALL = re.compile(r"\b(?:USERPRINCIPALNAME|USERNAME|USEROBJECTID|CUSTOMDATA)\s*\(", re.IGNORECASE)
_TMDL_KEYWORDS = ("metadatapermission", "columnpermission", "annotation", "changedproperty", "lineagetag",
                  "extendedproperty", "///")
_TABLE_PERMISSION = re.compile(r"^tablePermission\s+(?:'(?:[^']|'')+'|[^\s='][^\s=]*)\s*(=(.*))?$")
# MetadataPermission is the default property of a column permission, so `columnPermission Salary = none` is valid TMDL.
_COLUMN_PERMISSION = re.compile(r"^columnPermission\s+(?:'(?:[^']|'')+'|[^\s='][^\s=]*)\s*(?:=\s*(\w+))?\s*$")
_REF_ROLE = re.compile(r"(?m)^\s*ref\s+role\b")
_COUNTERS = ("rlsFilterCount", "rlsConstantFilterCount", "olsTableCount", "olsColumnCount", "unparsedCount")


def _empty(inspection: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "inspection": inspection, "reason": reason, "roleCount": 0, "readerRoleCount": 0,
        "rlsFilterCount": 0, "rlsConstantFilterCount": 0, "olsTableCount": 0, "olsColumnCount": 0,
        "unparsedCount": 0,
    }


def _skip_quoted(text: str, start: int, quote: str) -> int:
    """Index after the quoted token that opens at ``start``; a doubled quote is an escaped quote."""
    index = start + 1
    while index < len(text):
        if text[index] == quote:
            if text[index + 1:index + 2] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return len(text)


def _skip_bracket(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "]":
            if text[index + 1:index + 2] == "]":
                index += 2
                continue
            return index + 1
        index += 1
    return len(text)


def _dax_code(text: str) -> str:
    """The expression without comments, string literals, quoted names and column references."""
    out: list[str] = []
    index, size = 0, len(text)
    while index < size:
        char, pair = text[index], text[index:index + 2]
        if pair in ("//", "--"):
            end = text.find("\n", index)
            index = size if end < 0 else end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            index = size if end < 0 else end + 2
            out.append(" ")
        elif char in "\"'":
            index = _skip_quoted(text, index, char)
            out.append(char * 2)
        elif char == "[":
            index = _skip_bracket(text, index)
            out.append("[]")
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _is_trivial(code: str) -> bool:
    return re.sub(r"\s+", "", code).upper() in _TRIVIAL_FILTERS


def _count_filter(expression: Any, counters: dict[str, int]) -> None:
    text = expression if isinstance(expression, str) else ""
    code = _dax_code(text)
    if not code.strip():
        return
    if _is_trivial(code):
        counters["rlsConstantFilterCount"] += 1
        return
    counters["rlsFilterCount"] += 1
    if _IDENTITY_CALL.search(code):
        counters["dynamicFilterCount"] += 1


def _permission(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "unspecified" if not text else _PERMISSION_NAMES.get(text, "other")


def _role_name(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ord(ch) >= 32).strip()
    return text[:MAX_ROLE_NAME] or "(unnamed)"


def _filter_kind(counters: dict[str, int]) -> str:
    if counters["unparsedCount"]:
        return "unknown"
    filters, dynamic = counters["rlsFilterCount"], counters["dynamicFilterCount"]
    if not filters:
        return "constant" if counters["rlsConstantFilterCount"] else "none"
    return "dynamic" if dynamic == filters else "static" if not dynamic else "mixed"


def _new_counters() -> dict[str, int]:
    return {**{key: 0 for key in _COUNTERS}, "dynamicFilterCount": 0}


def _finish_role(name: Any, permission: Any, counters: dict[str, int], totals: dict[str, int],
                 roles: list[dict[str, Any]]) -> None:
    """Add one role to the model totals and, bounded, to the role records."""
    reads = _reads(permission)
    totals["roleCount"] += 1
    if reads:
        totals["readerRoleCount"] += 1
        for key in _COUNTERS:
            totals[key] += counters[key]
    roles.append({
        "name": _role_name(name), "permission": _permission(permission), "readsData": reads,
        "rowFilterTables": counters["rlsFilterCount"], "dynamicFilterTables": counters["dynamicFilterCount"],
        "constantFilterTables": counters["rlsConstantFilterCount"],
        "hiddenTables": counters["olsTableCount"], "hiddenColumns": counters["olsColumnCount"],
        "filterKind": _filter_kind(counters) if reads else "not_applicable"})


def _reads(permission: Any) -> bool:
    value = str(permission or "").strip().lower()
    return not value or value in READER_PERMISSIONS


def _is_none(value: Any) -> bool:
    return str(value or "").strip().lower() == "none"


def _from_tmsl(model: dict[str, Any], totals: dict[str, int], roles: list[dict[str, Any]]) -> None:
    for role in model.get("roles") or []:
        if not isinstance(role, dict):
            continue
        counters = _new_counters()
        if _reads(role.get("modelPermission")):
            for table in role.get("tablePermissions") or []:
                if not isinstance(table, dict):
                    continue
                _count_filter(table.get("filterExpression"), counters)
                if _is_none(table.get("metadataPermission")):
                    counters["olsTableCount"] += 1
                for column in table.get("columnPermissions") or []:
                    if isinstance(column, dict) and _is_none(column.get("metadataPermission")):
                        counters["olsColumnCount"] += 1
        _finish_role(role.get("name"), role.get("modelPermission"), counters, totals, roles)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _role_blocks(text: str) -> list[list[str]]:
    """Top-level ``role`` objects: the line itself and every following indented or blank line."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.strip() and _indent(line) == 0:
            current = [line] if re.match(r"role\s+\S", line) else None
            if current is not None:
                blocks.append(current)
        elif current is not None:
            current.append(line)
    return blocks


def _tmdl_role_name(line: str) -> str:
    """The name on a ``role`` line, unquoting ``'My role'`` and its doubled quotes."""
    rest = line.strip()[len("role"):].strip()
    if rest.startswith("'"):
        end = _skip_quoted(rest, 0, "'")
        closed = end > 1 and rest[end - 1] == "'"
        return rest[1:end - 1].replace("''", "'") if closed else rest[1:]
    return rest.split()[0] if rest else ""


def _from_tmdl_role(lines: list[str], totals: dict[str, int], roles: list[dict[str, Any]]) -> None:
    body = [line for line in lines[1:] if line.strip() and not line.strip().startswith("///")]
    if not body:
        permission = None
    else:
        top = min(_indent(line) for line in body)
        permission = next((m[1] for line in body if _indent(line) == top
                           and (m := re.match(r"\s*modelPermission\s*:\s*(\w+)", line))), None)
    counters = _new_counters()
    if _reads(permission):
        _scan_tmdl_permissions(body, counters)
    _finish_role(_tmdl_role_name(lines[0]), permission, counters, totals, roles)


def _scan_tmdl_permissions(body: list[str], counters: dict[str, int]) -> None:
    table_indent: int | None = None
    column_indent: int | None = None
    pending: list[str] | None = None  # filter text still being read, never kept past this block

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            # Lines stay separate so a line comment cannot swallow the rest of the filter.
            _count_filter("\n".join(pending).replace("```", ""), counters)
            pending = None

    for line in body:
        stripped, depth = line.strip(), _indent(line)
        if table_indent is not None and depth <= table_indent:
            flush()
            table_indent = column_indent = None
        match = _TABLE_PERMISSION.match(stripped) if depth > 0 else None
        if match:
            flush()
            table_indent, column_indent = depth, None
            if match[1] is not None:
                pending = [match[2].strip()]
            continue
        if depth > 0 and stripped.startswith("tablePermission"):
            # A permission this reader does not recognize must not be read as "no filter".
            flush()
            counters["unparsedCount"] += 1
            table_indent = column_indent = None
            continue
        if table_indent is None:
            continue
        lowered = stripped.lower()
        if lowered.startswith("columnpermission"):
            flush()
            column_indent = depth
            column = _COLUMN_PERMISSION.match(stripped)
            if column is None:
                counters["unparsedCount"] += 1
            elif (column[1] or "").lower() == "none":
                counters["olsColumnCount"] += 1
            continue
        if lowered.startswith("metadatapermission"):
            flush()
            if re.match(r"metadataPermission\s*:\s*none\b", stripped, re.IGNORECASE):
                if column_indent is not None and depth > column_indent:
                    counters["olsColumnCount"] += 1
                else:
                    counters["olsTableCount"] += 1
            continue
        if pending is not None and not lowered.startswith(_TMDL_KEYWORDS):
            pending.append(stripped)
    flush()


def model_security(definition: dict | None, error: str | None = None, *, keep_roles: bool = False) -> dict[str, Any]:
    """RLS/OLS counts and how completely the definition could be inspected.

    ``keep_roles`` adds one record per role (name, permission, counts and filter kind); see the module note.
    """
    if error or not isinstance(definition, dict):
        return _with_roles(_empty("unavailable", "definition_unreadable"), [], keep_roles)
    parts = ((definition.get("definition") or {}).get("parts")) or []
    decoded = decoded_parts(definition)
    undecodable = len(parts) - len(decoded)
    totals = {key: 0 for key in _empty("complete") if key not in {"inspection", "reason"}}
    roles: list[dict[str, Any]] = []
    tmsl_found = tmdl_model = False
    tmdl_roles = tmdl_refs = 0
    for path, text in decoded:
        lowered = path.lower()
        if lowered.endswith((".bim", ".json")):
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                continue
            model = body.get("model", body) if isinstance(body, dict) else None
            if isinstance(model, dict) and ("tables" in model or "roles" in model):
                tmsl_found = True
                _from_tmsl(model, totals, roles)
        elif lowered.endswith(".tmdl"):
            if lowered.endswith("model.tmdl"):
                tmdl_model = True
                tmdl_refs += len(_REF_ROLE.findall(text))
            before = totals["roleCount"]
            for block in _role_blocks(text):
                _from_tmdl_role(block, totals, roles)
            tmdl_roles += totals["roleCount"] - before
    if not (tmsl_found or tmdl_model):
        return _with_roles(_empty("unavailable", "definition_format_unrecognized"), [], keep_roles)
    reason = None
    if undecodable:
        reason = "definition_parts_undecodable"
    elif totals["unparsedCount"]:
        reason = "role_syntax_unrecognized"
    elif not tmsl_found and tmdl_refs > tmdl_roles:
        reason = "role_definitions_missing"
    return _with_roles({"inspection": "partial" if reason else "complete", "reason": reason, **totals}, roles, keep_roles)


def _with_roles(result: dict[str, Any], roles: list[dict[str, Any]], keep_roles: bool) -> dict[str, Any]:
    if keep_roles:
        result["roles"] = roles[:MAX_ROLE_RECORDS]
        result["rolesTruncated"] = len(roles) > MAX_ROLE_RECORDS
    return result


def role_detail_enabled(environ: dict[str, str] | None = None) -> bool:
    """Whether role records are kept: the ``model_security__role_detail`` Environment setting, default off."""
    from orchestration.environment import validate_model_security
    source = os.environ if environ is None else environ
    return validate_model_security({"role_detail": source.get("MODEL_SECURITY_ROLE_DETAIL", "false")})["role_detail"]

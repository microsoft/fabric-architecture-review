# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Build the separate, read-only workspace-owner Direct Lake semantic model.

Only the sanitized owner tables are exposed. Every table has its own RLS
predicate; report filters and relationship propagation are not security gates.
The entitlement table is deliberately disconnected to avoid recursive security
dependencies. Membership in WorkspaceOwner and report access require operator
approval; this module neither declares members nor grants permissions.

Before sharing, operators must manually bind a least-privilege fixed-identity
cloud connection with SSO disabled. Consumers must not have source-table access
or workspace Contributor/Member/Admin roles (which bypass model RLS).

USEROBJECTID returns the viewing user's Entra object ID. UTCNOW is evaluated
when the formula is evaluated, not continuously on an already-rendered visual:
https://learn.microsoft.com/dax/userobjectid-function-dax
https://learn.microsoft.com/dax/utcnow-function-dax
https://learn.microsoft.com/fabric/fundamentals/direct-lake-security-integration

DATA SAFETY: pure metadata generation; no credentials, memberships, or writes.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from reports.owner.schema import OWNER_CONTRACT_VERSION, OWNER_TABLES
from reports.powerbi.schema import Table

_NS = uuid.UUID("97543a3d-1ab9-4c6c-a58e-6ce977575734")
WORKSPACE_TABLE = "gold_workspace_risk"
REVIEW_TABLE = "owner_reviews"
FINDING_TABLE = "owner_findings"
DETAIL_TABLE = "owner_details"
ACCESS_TABLE = "owner_access"
EXECUTION_TABLE = "owner_executions"
COVERAGE_TABLE = "owner_coverage"
ROLE_NAME = "WorkspaceOwner"
_SOURCE_TABLES = {
    "owner_workspaces", REVIEW_TABLE, FINDING_TABLE, DETAIL_TABLE, ACCESS_TABLE,
    EXECUTION_TABLE, COVERAGE_TABLE,
}


def _lineage(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _valid_grant() -> str:
    """Scalar predicate shared by the access table and each workspace lookup."""
    return (
        "NOT ISBLANK(_principal)\n"
        '    && _principal <> ""\n'
        f"    && {ACCESS_TABLE}[principal_object_id] == _principal\n"
        f"    && NOT ISBLANK({ACCESS_TABLE}[workspace_id])\n"
        f'    && {ACCESS_TABLE}[workspace_id] <> ""\n'
        f"    && NOT ISBLANK({ACCESS_TABLE}[refreshed_at])\n"
        f"    && NOT ISBLANK({ACCESS_TABLE}[expires_at])\n"
        f"    && {ACCESS_TABLE}[refreshed_at] <= _now\n"
        f"    && {ACCESS_TABLE}[refreshed_at] > _now - 1\n"
        f"    && {ACCESS_TABLE}[expires_at] > _now\n"
        f"    && {ACCESS_TABLE}[expires_at] <= {ACCESS_TABLE}[refreshed_at] + 1"
    )


def _rls(table_name: str) -> str:
    preamble = "VAR _principal = LOWER(USEROBJECTID())\nVAR _now = UTCNOW()\n"
    if table_name == ACCESS_TABLE:
        return preamble + "RETURN\n    " + _valid_grant()
    return (
        preamble
        + f"VAR _workspace = {table_name}[workspace_id]\n"
        + "RETURN\n    NOT ISBLANK(_workspace)\n"
        + '    && _workspace <> ""\n'
        + f"    && COUNTROWS(FILTER({ACCESS_TABLE},\n"
        + f"        {ACCESS_TABLE}[workspace_id] == _workspace\n"
        + "        && " + _valid_grant()
        + "\n    )) > 0"
    )


def _measures() -> list[dict[str, Any]]:
    definitions = [
        ("Latest review", f"MAX({REVIEW_TABLE}[run_timestamp])",
         "yyyy-MM-dd HH:mm", "Review timestamp within the authorized workspace and page filters."),
        ("Findings", f"COUNTROWS({FINDING_TABLE})", "0",
         "Sanitized findings within the authorized workspace and current review filters."),
        ("Failed findings",
         f'CALCULATE(COUNTROWS({FINDING_TABLE}), KEEPFILTERS({FINDING_TABLE}[status] = "fail"))',
         "0", "Failed findings within the authorized workspace and current review filters."),
        ("Technical details", f"COUNTROWS({DETAIL_TABLE})", "0",
         "Sanitized technical details within the authorized workspace and current review filters."),
    ]
    measures = [
        {
            "name": name,
            "expression": (
                f"IF(HASONEVALUE({WORKSPACE_TABLE}[workspace_id]), {expression}, BLANK())"
            ),
            "formatString": formatting,
            "description": description,
            "lineageTag": _lineage("measure", name),
        }
        for name, expression, formatting, description in definitions
    ]
    measures.extend(_scoped_measures())
    return measures


def _current_count(aggregate: str, *predicates: str) -> str:
    """Preserve RLS and slicers while selecting each workspace's latest review."""
    filters = [f"{REVIEW_TABLE}[is_latest] = TRUE()", *predicates]
    return (
        f"COALESCE(CALCULATE({aggregate}, "
        + ", ".join(f"KEEPFILTERS({predicate})" for predicate in filters)
        + "), 0)"
    )


def _scoped_measures() -> list[dict[str, Any]]:
    """Counts, never tenant totals or sums of incompatible technical metrics."""
    definitions = [
        ("Scoped workspaces", _current_count(f"DISTINCTCOUNT({REVIEW_TABLE}[workspace_id])")),
        ("Scoped findings", _current_count(f"COUNTROWS({FINDING_TABLE})")),
        ("Scoped priority findings", _current_count(
            f"COUNTROWS({FINDING_TABLE})",
            f'{FINDING_TABLE}[status] = "fail"',
            f'{FINDING_TABLE}[severity] IN {{"critical", "high"}}',
        )),
        ("Scoped missing evidence", _current_count(
            f"DISTINCTCOUNT({REVIEW_TABLE}[workspace_id])",
            f'{REVIEW_TABLE}[technical_evidence_status] = "missing"',
        )),
        ("Scoped DAX findings", _current_count(
            f"COUNTROWS({FINDING_TABLE})", f'{FINDING_TABLE}[rule_id] IN {{"DAX-001", "DAX-OBJECT-001"}}',
        )),
        ("Scoped notebook findings", _current_count(
            f"COUNTROWS({FINDING_TABLE})",
            f'{FINDING_TABLE}[rule_id] IN {{"NBCODE-001", "NBCODE-002", "NBCODE-003", "NBCODE-004", "NBCODE-005", "NBCODE-006"}}',
        )),
        ("Scoped detail rows", _current_count(f"COUNTROWS({DETAIL_TABLE})")),
        ("Scoped DAX rows", _current_count(
            f"COUNTROWS({DETAIL_TABLE})", f'{DETAIL_TABLE}[detail_type] = "dax_measure"',
        )),
        ("Scoped notebook rows", _current_count(
            f"COUNTROWS({DETAIL_TABLE})", f'{DETAIL_TABLE}[detail_type] = "notebook_signal"',
        )),
        ("Scoped table metric rows", _current_count(
            f"COUNTROWS({DETAIL_TABLE})", f'{DETAIL_TABLE}[detail_type] = "model_table_stat"',
        )),
        ("Scoped signal rows", _current_count(
            f"COUNTROWS({DETAIL_TABLE})",
            f'{DETAIL_TABLE}[detail_type] IN {{"dax_measure", "notebook_signal"}}',
            f'{DETAIL_TABLE}[signal_codes] <> ""',
        )),
        ("Scoped DAX object rows", _current_count(
            f"COUNTROWS({DETAIL_TABLE})",
            f'{DETAIL_TABLE}[detail_type] IN {{"dax_calculated_column", "dax_calculated_table", "dax_calculation_item"}}',
        )),
        ("Scoped Dataflow rows", _current_count(
            f"COUNTROWS({DETAIL_TABLE})", f'{DETAIL_TABLE}[detail_type] = "dataflow_query"',
        )),
        ("Scoped executions", _current_count(f"DISTINCTCOUNT({EXECUTION_TABLE}[execution_key])")),
        ("Scoped failed executions", _current_count(
            f"DISTINCTCOUNT({EXECUTION_TABLE}[execution_key])", f'{EXECUTION_TABLE}[status] = "Failed"',
        )),
        ("Scoped timed executions", _current_count(f"COUNT({EXECUTION_TABLE}[duration_ms])")),
        ("Scoped maximum duration ms",
         f"CALCULATE(MAX({EXECUTION_TABLE}[duration_ms]), KEEPFILTERS({REVIEW_TABLE}[is_latest] = TRUE()))"),
        ("Scoped coverage rows", _current_count(f"COUNTROWS({COVERAGE_TABLE})")),
        ("Scoped incomplete coverage", _current_count(
            f"COUNTROWS({COVERAGE_TABLE})",
            f'NOT ({COVERAGE_TABLE}[collection_status] IN {{"available", "collected", "empty", "inspected", "complete"}})',
        )),
        ("Scoped static objects", _current_count(
            f"COUNTROWS({DETAIL_TABLE})",
            f'{DETAIL_TABLE}[detail_type] IN {{"dax_calculated_column", "dax_calculated_table", "dax_calculation_item", "dataflow_query"}}',
        )),
    ]
    measures = [
        {
            "name": name,
            "expression": expression,
            "formatString": "0",
            "description": (
                ("Maximum observed duration in milliseconds within " if name == "Scoped maximum duration ms"
                 else "Count within ")
                + "currently authorized workspace rows, current slicers, "
                "and the latest review for each workspace. Not an estate total or score."
            ),
            "lineageTag": _lineage("measure", name),
        }
        for name, expression in definitions
    ]
    latest = f"KEEPFILTERS({REVIEW_TABLE}[is_latest] = TRUE())"
    context = [
        ("Review window", (
            f"VAR _first = CALCULATE(MIN({REVIEW_TABLE}[run_timestamp]), {latest})\n"
            f"VAR _last = CALCULATE(MAX({REVIEW_TABLE}[run_timestamp]), {latest})\n"
            'RETURN IF([Scoped workspaces] = 0, "No current entitled review", '
            '"Latest per workspace | " & FORMAT(_first, "yyyy-MM-dd") & '
            'IF(_first = _last, "", " to " & FORMAT(_last, "yyyy-MM-dd")) & " UTC")'
        )),
        ("Evidence notice", (
            'IF([Scoped workspaces] = 0, '
            '"No current entitled reviews. Request access or ask your operator to check the daily entitlement sync.", '
            '"PARTIAL ASSESSMENT | " & FORMAT([Scoped workspaces], "0") & '
            '" reviewed workspace(s); " & FORMAT([Scoped missing evidence], "0") & '
            '" missing technical evidence. Zero findings does not mean pass.")'
        )),
    ]
    measures.extend({
        "name": name,
        "expression": expression,
        "description": "Current authorized review context; never a global-latest filter.",
        "lineageTag": _lineage("measure", name),
    } for name, expression in context)
    return measures


def _table(table: Table) -> dict[str, Any]:
    name = WORKSPACE_TABLE if table.name == "owner_workspaces" else table.name
    columns = []
    for column in table.columns:
        result: dict[str, Any] = {
            "name": column.name,
            "dataType": column.kind,
            "sourceColumn": column.name,
            "summarizeBy": "none",
            "lineageTag": _lineage("column", name, column.name),
        }
        if column.name == "microsoft_learn_url":
            result["dataCategory"] = "WebUrl"
        if column.name in {"review_key", "principal_object_id", "is_latest"}:
            result["isHidden"] = True
        if column.kind == "dateTime":
            result["formatString"] = "yyyy-MM-dd HH:mm"
        columns.append(result)
    result = {
        "name": name,
        "description": table.description,
        "lineageTag": _lineage("table", name),
        "columns": columns,
        "partitions": [{
            "name": name,
            "mode": "directLake",
            "source": {
                "type": "entity",
                "entityName": table.name,
                "schemaName": "dbo",
                "expressionSource": "DatabaseQuery",
            },
        }],
    }
    if name == ACCESS_TABLE:
        result["isHidden"] = True
    if name == WORKSPACE_TABLE:
        result["measures"] = _measures()
    return result


def _relationships() -> list[dict[str, Any]]:
    return [
        {
            "name": _lineage("relationship", child, parent, key),
            "fromTable": child,
            "fromColumn": key,
            "fromCardinality": "many",
            "toTable": parent,
            "toColumn": key,
            "toCardinality": "one",
            "crossFilteringBehavior": "oneDirection",
            "securityFilteringBehavior": "oneDirection",
            "isActive": True,
        }
        for child, parent, key in [
            (REVIEW_TABLE, WORKSPACE_TABLE, "workspace_id"),
            (FINDING_TABLE, REVIEW_TABLE, "review_key"),
            (DETAIL_TABLE, REVIEW_TABLE, "review_key"),
            (EXECUTION_TABLE, REVIEW_TABLE, "review_key"),
            (COVERAGE_TABLE, REVIEW_TABLE, "review_key"),
        ]
    ]


def _m_string(value: str) -> str:
    """Quote an M text literal, including its escape introducer."""
    return '"' + (
        value.replace("#", "#(#)").replace('"', '""')
        .replace("\r", "#(cr)").replace("\n", "#(lf)").replace("\t", "#(tab)")
    ) + '"'


def build_bim(model_name: str, sql_endpoint: str, database_id: str) -> dict[str, Any]:
    """Build model.bim without granting access or configuring a connection."""
    if {table.name for table in OWNER_TABLES} != _SOURCE_TABLES:
        raise ValueError("The owner model must contain only the seven approved owner tables.")
    tables = [_table(table) for table in OWNER_TABLES]
    return {
        "name": model_name,
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "directLakeBehavior": "directLakeOnly",
            "expressions": [{
                "name": "DatabaseQuery",
                "kind": "m",
                "expression": (
                    "let\n"
                    f"    database = Sql.Database({_m_string(sql_endpoint)}, {_m_string(database_id)})\n"
                    "in\n    database"
                ),
                "lineageTag": _lineage("expression", "DatabaseQuery"),
            }],
            "tables": tables,
            "relationships": _relationships(),
            "roles": [{
                "name": ROLE_NAME,
                "modelPermission": "read",
                "description": (
                    "Manually approved report consumers; direct User workspace Admin "
                    "entitlements are synchronized daily and expire within 24 hours."
                ),
                "tablePermissions": [
                    {"name": table["name"], "filterExpression": _rls(table["name"])}
                    for table in tables
                ],
            }],
            "annotations": [
                {"name": "OwnerContractVersion", "value": str(OWNER_CONTRACT_VERSION)},
                {"name": "PBI_QueryOrder", "value": '["DatabaseQuery"]'},
                {
                    "name": "OwnerAccessPrerequisite",
                    "value": (
                        "Before sharing, manually configure a fixed-identity cloud "
                        "connection with SSO disabled and approve WorkspaceOwner "
                        "membership. Do not grant consumers source access or write roles."
                    ),
                },
            ],
        },
    }


def build_model_bim_json(model_name: str, sql_endpoint: str, database_id: str) -> str:
    """Serialize the owner model using the existing deployment part convention."""
    return json.dumps(build_bim(model_name, sql_endpoint, database_id), indent=2)

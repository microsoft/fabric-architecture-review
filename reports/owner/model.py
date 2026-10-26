# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Separate owner architecture model; every table has independent expiring RLS."""
from __future__ import annotations

import json
import uuid
from typing import Any

from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256
from reports.owner.schema import OWNER_CONTRACT_VERSION, OWNER_TABLES

WORKSPACE_TABLE = "gold_workspace_risk"
REVIEW_TABLE = "owner_reviews"
FINDING_TABLE = "owner_findings"
DETAIL_TABLE = "owner_details"
ACCESS_TABLE = "owner_access"
COVERAGE_TABLE = "owner_coverage"
ROLE_NAME = "WorkspaceOwner"
_SOURCE_TABLES = {"owner_workspaces", REVIEW_TABLE, FINDING_TABLE, DETAIL_TABLE, ACCESS_TABLE, COVERAGE_TABLE}
_NS = uuid.UUID("97543a3d-1ab9-4c6c-a58e-6ce977575734")


def _lineage(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _valid_grant() -> str:
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
    predicate = (
        preamble + f"VAR _workspace = {table_name}[workspace_id]\n"
        + "RETURN\n    NOT ISBLANK(_workspace)\n"
        + '    && _workspace <> ""\n'
        + f"    && COUNTROWS(FILTER({ACCESS_TABLE},\n"
        + f"        {ACCESS_TABLE}[workspace_id] == _workspace\n"
        + "        && " + _valid_grant() + "\n    )) > 0"
    )
    if table_name != WORKSPACE_TABLE:
        predicate += f'\n    && {table_name}[profile_id] == "{PROFILE_ID}"'
        predicate += f'\n    && {table_name}[profile_sha256] == "{PROFILE_SHA256}"'
    if table_name == FINDING_TABLE:
        rules = ", ".join(f'"{rule}"' for rule in sorted(ACTIVE_RULE_IDS))
        predicate += f"\n    && {table_name}[rule_id] IN {{{rules}}}"
    return predicate


def _current_count(aggregate: str, *predicates: str) -> str:
    filters = [f'{REVIEW_TABLE}[profile_id] = "{PROFILE_ID}"',
               f'{REVIEW_TABLE}[profile_sha256] = "{PROFILE_SHA256}"',
               f"{REVIEW_TABLE}[is_latest] = TRUE()", *predicates]
    return f"COALESCE(CALCULATE({aggregate}, " + ", ".join(f"KEEPFILTERS({p})" for p in filters) + "), 0)"


def _scoped_measures() -> list[dict]:
    definitions = [
        ("Scoped workspaces", _current_count(f"DISTINCTCOUNT({REVIEW_TABLE}[workspace_id])")),
        ("Scoped findings", _current_count(f"COUNTROWS({FINDING_TABLE})")),
        ("Scoped priority findings", _current_count(f"SUM({FINDING_TABLE}[is_fail])",
                                                   f'{FINDING_TABLE}[severity] IN {{"critical", "high"}}')),
        ("Scoped missing evidence", _current_count(f"DISTINCTCOUNT({REVIEW_TABLE}[workspace_id])",
                                                   f'{REVIEW_TABLE}[technical_evidence_status] = "missing"')),
        ("Scoped detail rows", _current_count(f"COUNTROWS({DETAIL_TABLE})")),
        ("Scoped items", _current_count(f"DISTINCTCOUNTNOBLANK({DETAIL_TABLE}[item_id])")),
        ("Scoped failures", _current_count(f"SUM({FINDING_TABLE}[is_fail])")),
        ("Scoped failing workspaces", _current_count(f"DISTINCTCOUNT({REVIEW_TABLE}[workspace_id])",
                                                     f"{REVIEW_TABLE}[owner_fail_count] > 0")),
        ("Scoped signals", _current_count(f"COUNTROWS({FINDING_TABLE})", f'{FINDING_TABLE}[rule_kind] = "signal"')),
        ("Scoped coverage rows", _current_count(f"COUNTROWS({COVERAGE_TABLE})")),
        ("Evidence notice", '"Partial architecture assessment. Zero failures does not mean pass. No global score is available."'),
    ]
    return [{"name": name, "expression": expr, "formatString": "" if name == "Evidence notice" else "0",
             "lineageTag": _lineage("measure", name)} for name, expr in definitions]


def _measures() -> list[dict]:
    return _scoped_measures()


def _table(table) -> dict:
    name = WORKSPACE_TABLE if table.name == "owner_workspaces" else table.name
    result = {"name": name, "description": table.description, "lineageTag": _lineage("table", name),
              "columns": [{"name": c.name, "dataType": c.kind, "sourceColumn": c.name,
                           "summarizeBy": "none", "lineageTag": _lineage("column", name, c.name),
                           "isHidden": c.name in {"review_key", "principal_object_id", "is_latest"}}
                          for c in table.columns],
              "partitions": [{"name": name, "mode": "directLake", "source": {
                  "type": "entity", "entityName": table.name, "schemaName": "dbo", "expressionSource": "DatabaseQuery"}}]}
    if name == ACCESS_TABLE:
        result["isHidden"] = True
    if name == WORKSPACE_TABLE:
        result["measures"] = _measures()
    return result


def _relationships() -> list[dict]:
    return [{"name": _lineage("relationship", child, parent, key),
             "fromTable": child, "fromColumn": key, "fromCardinality": "many",
             "toTable": parent, "toColumn": key, "toCardinality": "one",
             "crossFilteringBehavior": "oneDirection", "securityFilteringBehavior": "oneDirection", "isActive": True}
            for child, parent, key in (
                (REVIEW_TABLE, WORKSPACE_TABLE, "workspace_id"), (FINDING_TABLE, REVIEW_TABLE, "review_key"),
                (DETAIL_TABLE, REVIEW_TABLE, "review_key"), (COVERAGE_TABLE, REVIEW_TABLE, "review_key"))]


def _m_string(value: str) -> str:
    return '"' + value.replace("#", "#(#)").replace('"', '""').replace("\r", "#(cr)").replace("\n", "#(lf)").replace("\t", "#(tab)") + '"'


def build_bim(model_name: str, sql_endpoint: str, database_id: str) -> dict[str, Any]:
    if {t.name for t in OWNER_TABLES} != _SOURCE_TABLES:
        raise ValueError("Owner model contains an unapproved source.")
    tables = [_table(t) for t in OWNER_TABLES]
    return {"name": model_name, "compatibilityLevel": 1604, "model": {
        "culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3", "directLakeBehavior": "directLakeOnly",
        "expressions": [{"name": "DatabaseQuery", "kind": "m",
                         "expression": f"let\n database = Sql.Database({_m_string(sql_endpoint)}, {_m_string(database_id)})\nin database",
                         "lineageTag": _lineage("expression", "DatabaseQuery")}],
        "tables": tables, "relationships": _relationships(),
        "roles": [{"name": ROLE_NAME, "modelPermission": "read",
                   "description": "Explicit approved members only; synchronized direct-user Admin grants expire within 24 hours.",
                   "tablePermissions": [{"name": t["name"], "filterExpression": _rls(t["name"])} for t in tables]}],
        "annotations": [{"name": "OwnerContractVersion", "value": str(OWNER_CONTRACT_VERSION)},
                        {"name": "PBI_QueryOrder", "value": '["DatabaseQuery"]'},
                        {"name": "OwnerAccessPrerequisite", "value":
                         "Before sharing, manually configure fixed-identity connection with SSO disabled and approve WorkspaceOwner membership. "
                         "Never grant consumers source access or Contributor/Member/Admin roles."}],
    }}


def build_model_bim_json(model_name: str, sql_endpoint: str, database_id: str) -> str:
    return json.dumps(build_bim(model_name, sql_endpoint, database_id), indent=2)

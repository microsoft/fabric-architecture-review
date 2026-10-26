# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Architecture-only Direct Lake model; excluded physical history is not exposed."""
from __future__ import annotations

import json
import uuid
from typing import Any

from analyzers.far_profile import ACTIVE_RULE_IDS, PROFILE_ID, PROFILE_SHA256
from reports.powerbi.schema import GOLD_TABLES, KEY_COLUMNS

FACT_TABLE = "gold_findings"
RUN_TABLE = "gold_run_summary"
MODEL_TABLE = "gold_semantic_models"
RISK_TABLE = "gold_workspace_risk"
_NS = uuid.UUID("6f3b1c2a-0d4e-4a5b-9c7d-1e2f3a4b5c6d")


def _lineage(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _scope(table: str, *, rules: bool = False) -> str:
    """Profile-version safety filters shared by every measure, whatever table it reads."""
    filters = [f'KEEPFILTERS({table}[profile_id] = "{PROFILE_ID}")',
               f'KEEPFILTERS({table}[profile_sha256] = "{PROFILE_SHA256}")']
    if rules:
        rule_list = ", ".join(f'"{rule}"' for rule in sorted(ACTIVE_RULE_IDS))
        filters.append(f"KEEPFILTERS({table}[rule_id] IN {{{rule_list}}})")
    return ", ".join(filters)


def _measures() -> list[dict]:
    scope = _scope(FACT_TABLE, rules=True)
    single_run = f"HASONEVALUE({RUN_TABLE}[run_id]) && HASONEVALUE({RUN_TABLE}[configuration_sha256])"

    def scoped(aggregate: str, table: str, *extra: str, rules: bool = False) -> str:
        return f"CALCULATE({aggregate}, {_scope(table, rules=rules)}{''.join(', ' + e for e in extra)})"

    definitions = [
        ("Findings", f"CALCULATE(COUNTROWS({FACT_TABLE}), {scope})", "0"),
        ("Fail Count", f"CALCULATE(SUM({FACT_TABLE}[is_fail]), {scope})", "0"),
        ("Pass Count", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[is_scored] = 1), KEEPFILTERS({FACT_TABLE}[status] = "pass"))', "0"),
        ("Scored Count", f"CALCULATE(SUM({FACT_TABLE}[is_scored]), {scope})", "0"),
        ("Unknown Count", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[status] = "unknown"))', "0"),
        ("Missing Evidence Count", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[status] = "missing_evidence"))', "0"),
        ("Needs Attention", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[status] IN {{"unknown", "missing_evidence"}}))', "0"),
        ("Info Count", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[status] = "info"))', "0"),
        ("Signals To Review", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[review_suggested] = "Yes"))', "0"),
        ("Not Applicable Count", f'CALCULATE(COUNTROWS({FACT_TABLE}), {scope}, KEEPFILTERS({FACT_TABLE}[status] = "not_applicable"))', "0"),
        ("Failing Rules", f"CALCULATE(DISTINCTCOUNT({FACT_TABLE}[rule_id]), {scope}, KEEPFILTERS({FACT_TABLE}[is_fail] = 1))", "0"),
        ("Rules", scoped("COUNTROWS(gold_rules)", "gold_rules"), "0"),
        ("Architecture Score", f"IF(HASONEVALUE({FACT_TABLE}[run_id]) && HASONEVALUE({FACT_TABLE}[configuration_sha256]), DIVIDE([Pass Count], [Scored Count]) * 100)", "0.0"),
        ("Score Status", 'IF([Scored Count] > 0, "Assessed architecture controls", "No assessable controls")', ""),
        # Run-level: blank unless exactly one run/configuration is in scope.
        ("Assessment Coverage", f"IF({single_run}, {scoped(f'AVERAGE({RUN_TABLE}[assessment_coverage])', RUN_TABLE)})", "0.0"),
        ("Run Count", scoped(f"DISTINCTCOUNT({RUN_TABLE}[run_id])", RUN_TABLE), "0"),
        ("Configuration Versions", scoped(f"DISTINCTCOUNTNOBLANK({RUN_TABLE}[configuration_sha256])", RUN_TABLE), "0"),
        ("Workspaces", scoped("DISTINCTCOUNTNOBLANK(gold_workspaces[workspace_id])", "gold_workspaces"), "0"),
        ("Workspace Items", scoped("SUM(gold_workspaces[item_count])", "gold_workspaces"), "0"),
        ("Workspace Admins", scoped("SUM(gold_workspaces[admin_count])", "gold_workspaces"), "0"),
        ("Assessed Workspaces", scoped(f"DISTINCTCOUNTNOBLANK({RISK_TABLE}[workspace_id])", RISK_TABLE), "0"),
        ("Failing Issues", scoped(f"SUM({RISK_TABLE}[issue_count])", RISK_TABLE), "0"),
        ("Capacities", scoped("DISTINCTCOUNTNOBLANK(gold_capacities[capacity_id])", "gold_capacities"), "0"),
        ("Assigned Workspaces", scoped("SUM(gold_capacities[assigned_workspace_count])", "gold_capacities"), "0"),
        ("Semantic Models", scoped(f"DISTINCTCOUNTNOBLANK({MODEL_TABLE}[model_id])", MODEL_TABLE), "0"),
        ("Reports", scoped("DISTINCTCOUNTNOBLANK(gold_reports[report_id])", "gold_reports"), "0"),
        ("Lineage Links", scoped("COUNTROWS(gold_lineage_edges)", "gold_lineage_edges"), "0"),
        ("Graph Edges", scoped("COUNTROWS(gold_graph_edges)", "gold_graph_edges"), "0"),
        ("Attributed Findings", scoped("COUNTROWS(gold_finding_targets)", "gold_finding_targets", rules=True), "0"),
        ("Items To Review", scoped("COUNTROWS(gold_finding_items)", "gold_finding_items", rules=True), "0"),
        ("Workspaces With Items", scoped("DISTINCTCOUNTNOBLANK(gold_finding_items[workspace_id])", "gold_finding_items", rules=True), "0"),
        ("Coverage Records", scoped("COUNTROWS(gold_source_coverage)", "gold_source_coverage"), "0"),
        ("Evidence Families", scoped("DISTINCTCOUNTNOBLANK(gold_source_coverage[evidence_family])", "gold_source_coverage"), "0"),
        ("Covered Workspaces", scoped("DISTINCTCOUNTNOBLANK(gold_source_coverage[workspace_id])", "gold_source_coverage"), "0"),
        ("Tenant Settings Recorded", scoped("COUNTROWS(gold_tenant_settings)", "gold_tenant_settings"), "0"),
        ("Judged Settings", scoped("COUNTROWS(gold_tenant_settings)", "gold_tenant_settings",
                                   "KEEPFILTERS(NOT ISBLANK(gold_tenant_settings[rule_id]))"), "0"),
        ("Settings Open to Everyone", scoped("COUNTROWS(gold_tenant_settings)", "gold_tenant_settings",
                                             'KEEPFILTERS(gold_tenant_settings[posture] = "Open to everyone (can be limited)")'), "0"),
        ("Settings Limited to Groups", scoped("COUNTROWS(gold_tenant_settings)", "gold_tenant_settings",
                                              'KEEPFILTERS(gold_tenant_settings[posture] = "Limited to groups")'), "0"),
        ("Settings Changed", scoped("COUNTROWS(gold_tenant_settings)", "gold_tenant_settings",
                                    "KEEPFILTERS(gold_tenant_settings[change_count] > 0)"), "0"),
        ("Setting Changes", scoped("SUM(gold_tenant_settings[change_count])", "gold_tenant_settings"), "0"),
        ("Models Inspected", scoped("DISTINCTCOUNTNOBLANK(gold_model_security[model_id])", "gold_model_security"), "0"),
        ("Models With RLS", scoped("DISTINCTCOUNTNOBLANK(gold_model_security[model_id])", "gold_model_security",
                                   'KEEPFILTERS(gold_model_security[rls_status] = "present")'), "0"),
        ("Models With OLS", scoped("DISTINCTCOUNTNOBLANK(gold_model_security[model_id])", "gold_model_security",
                                   'KEEPFILTERS(gold_model_security[ols_status] = "present")'), "0"),
        ("Models Without RLS or OLS", scoped("DISTINCTCOUNTNOBLANK(gold_model_security[model_id])", "gold_model_security",
                                             'KEEPFILTERS(gold_model_security[rls_status] = "absent")',
                                             'KEEPFILTERS(gold_model_security[ols_status] = "absent")'), "0"),
        ("Models Not Inspected", scoped("DISTINCTCOUNTNOBLANK(gold_model_security[model_id])", "gold_model_security",
                                        'KEEPFILTERS(gold_model_security[security_inspection] <> "complete")'), "0"),
        ("Required Security Gaps", scoped("COUNTROWS(gold_model_security)", "gold_model_security",
                                          'FILTER(gold_model_security, gold_model_security[rls_outcome] = "fail" '
                                          '|| gold_model_security[ols_outcome] = "fail")'), "0"),
        ("Required Security Unverified", scoped("COUNTROWS(gold_model_security)", "gold_model_security",
                                                'FILTER(gold_model_security, gold_model_security[rls_outcome] = "missing_evidence" '
                                                '|| gold_model_security[ols_outcome] = "missing_evidence")'), "0"),
        ("Roles Listed", scoped("COUNTROWS(gold_model_security_roles)", "gold_model_security_roles"), "0"),
        ("Static Roles", scoped("COUNTROWS(gold_model_security_roles)", "gold_model_security_roles",
                                'KEEPFILTERS(gold_model_security_roles[filter_kind] = "static")'), "0"),
        ("Dynamic Roles", scoped("COUNTROWS(gold_model_security_roles)", "gold_model_security_roles",
                                 'KEEPFILTERS(gold_model_security_roles[filter_kind] IN {"dynamic", "mixed"})'), "0"),
        ("Roles Without Row Filter", scoped("COUNTROWS(gold_model_security_roles)", "gold_model_security_roles",
                                            'KEEPFILTERS(gold_model_security_roles[reads_data] = TRUE())',
                                            'KEEPFILTERS(gold_model_security_roles[filter_kind] IN {"none", "constant"})'), "0"),
        ("Administrator Roles", scoped("COUNTROWS(gold_model_security_roles)", "gold_model_security_roles",
                                       'KEEPFILTERS(gold_model_security_roles[role_permission] = "administrator")'), "0"),
    ]
    return [{"name": name, "expression": expression, "formatString": format_string,
             "lineageTag": _lineage("measure", name)}
            for name, expression, format_string in definitions]


def _table(table) -> dict:
    result = {
        "name": table.name, "description": table.description,
        "lineageTag": _lineage("table", table.name),
        "columns": [{"name": c.name, "dataType": c.kind, "sourceColumn": c.name,
                     "summarizeBy": "none", "lineageTag": _lineage(table.name, c.name),
                     **({"isHidden": True} if c.name in KEY_COLUMNS else {})}
                    for c in table.columns],
        "partitions": [{"name": table.name, "mode": "directLake",
                        "source": {"type": "entity", "entityName": table.name,
                                   "schemaName": "dbo", "expressionSource": "DatabaseQuery"}}],
    }
    if table.name == FACT_TABLE:
        result["measures"] = _measures()
    return result


ROLES_TABLE = "gold_model_security_roles"
SECURITY_TABLE = "gold_model_security"


def _relationships() -> list[dict]:
    # A model's roles reach the run through the model's security row. A second, direct run relationship would be
    # a second path, which Power BI deactivates; the roles are filtered by the run through the one path.
    relationships = [{"name": _lineage("rel", table.name), "fromTable": table.name,
                      "fromColumn": "run_id", "toTable": RUN_TABLE, "toColumn": "run_id",
                      "crossFilteringBehavior": "oneDirection"}
                     for table in GOLD_TABLES if table.name not in {RUN_TABLE, ROLES_TABLE}]
    relationships.append({"name": _lineage("rel", ROLES_TABLE), "fromTable": ROLES_TABLE, "fromColumn": "model_key",
                          "toTable": SECURITY_TABLE, "toColumn": "model_key", "crossFilteringBehavior": "oneDirection"})
    return relationships


def build_bim(model_name: str, sql_endpoint: str, database_id: str) -> dict[str, Any]:
    rules = ", ".join(f'"{rule}"' for rule in sorted(ACTIVE_RULE_IDS))
    permissions = []
    for table in GOLD_TABLES:
        expression = f'{table.name}[profile_id] = "{PROFILE_ID}" && {table.name}[profile_sha256] = "{PROFILE_SHA256}" && NOT ISBLANK({table.name}[configuration_sha256])'
        if any(c.name == "rule_id" for c in table.columns):
            expression += f" && {table.name}[rule_id] IN {{{rules}}}"
        permissions.append({"name": table.name, "filterExpression": expression})
    quote = lambda value: '"' + value.replace("#", "#(#)").replace('"', '""') + '"'
    return {"name": model_name, "compatibilityLevel": 1604, "model": {
        "culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3",
        "directLakeBehavior": "directLakeOnly",
        "expressions": [{"name": "DatabaseQuery", "kind": "m",
                         "expression": f"let\n database = Sql.Database({quote(sql_endpoint)}, {quote(database_id)})\nin database"}],
        "tables": [_table(table) for table in GOLD_TABLES], "relationships": _relationships(),
        "roles": [{"name": "ArchitectureReview", "modelPermission": "read", "tablePermissions": permissions}],
        "annotations": [{"name": "FARProfile", "value": PROFILE_ID},
                        {"name": "PBI_QueryOrder", "value": '["DatabaseQuery"]'}],
    }}


def build_model_bim_json(model_name: str, sql_endpoint: str, database_id: str) -> str:
    return json.dumps(build_bim(model_name, sql_endpoint, database_id), indent=2)

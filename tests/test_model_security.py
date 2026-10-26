# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Semantic model RLS/OLS: extraction, rule outcomes, declarations and data safety."""
import base64
import json

import pytest

from collectors.model_security import model_security
from tests.test_definition_signals import definition, part

SECRET = "SecretRegionLiteral-7f3a"


def tmsl(roles, tables=None):
    return definition(part("model.bim", json.dumps({"model": {"tables": tables or [], "roles": roles}})),
                      part("definition.pbism", "{}"))


def tmdl(*role_texts, model="model Model\n", refs=0):
    parts = [part("definition/model.tmdl", model + "".join(f"ref role R{i}\n" for i in range(refs)))]
    parts += [part(f"definition/roles/Role{i}.tmdl", text) for i, text in enumerate(role_texts)]
    return definition(*parts)


def counts(result):
    return {key: result[key] for key in ("roleCount", "readerRoleCount", "rlsFilterCount", "rlsConstantFilterCount",
                                         "olsTableCount", "olsColumnCount")}


# --- TMSL (model.bim) -------------------------------------------------------------------------

def test_tmsl_row_filter_is_rls_and_never_stored():
    roles = [{"name": "Regional", "modelPermission": "read", "tablePermissions": [
        {"name": "Sales", "filterExpression": f'[Region] = "{SECRET}"'}]}]
    result = model_security(tmsl(roles))
    assert result["inspection"] == "complete" and result["reason"] is None
    assert counts(result) == {"roleCount": 1, "readerRoleCount": 1, "rlsFilterCount": 1,
                              "rlsConstantFilterCount": 0, "olsTableCount": 0, "olsColumnCount": 0}
    assert SECRET not in json.dumps(result) and "Regional" not in json.dumps(result)


def test_tmsl_ols_counts_hidden_tables_and_columns_but_not_visible_ones():
    roles = [{"name": "Hr", "modelPermission": "read", "tablePermissions": [
        {"name": "Employees", "metadataPermission": "none"},
        {"name": "Pay", "columnPermissions": [{"name": "Salary", "metadataPermission": "none"},
                                               {"name": "Grade", "metadataPermission": "read"}]}]}]
    result = model_security(tmsl(roles))
    assert (result["olsTableCount"], result["olsColumnCount"], result["rlsFilterCount"]) == (1, 1, 0)


def test_a_role_with_no_filters_is_not_rls():
    result = model_security(tmsl([{"name": "Readers", "modelPermission": "read", "tablePermissions": []}]))
    assert (result["roleCount"], result["rlsFilterCount"]) == (1, 0)


@pytest.mark.parametrize("expression", ["TRUE()", "true()", " TRUE ( ) ", "1 = 1", "(TRUE())"])
def test_a_filter_that_is_always_true_restricts_nothing(expression):
    roles = [{"name": "All", "modelPermission": "read", "tablePermissions": [
        {"name": "Sales", "filterExpression": expression}]}]
    result = model_security(tmsl(roles))
    assert (result["rlsFilterCount"], result["rlsConstantFilterCount"]) == (0, 1)


@pytest.mark.parametrize("permission", ["administrator", "none", "refresh"])
def test_roles_that_cannot_read_do_not_count(permission):
    roles = [{"name": "Admin", "modelPermission": permission, "tablePermissions": [
        {"name": "Sales", "filterExpression": "[x] = 1", "metadataPermission": "none"}]}]
    result = model_security(tmsl(roles))
    assert (result["roleCount"], result["readerRoleCount"], result["rlsFilterCount"], result["olsTableCount"]) == (1, 0, 0, 0)


def test_model_without_roles_is_complete_and_empty():
    result = model_security(tmsl([]))
    assert result["inspection"] == "complete" and result["roleCount"] == 0


# --- TMDL -------------------------------------------------------------------------------------

def test_tmdl_single_line_filter_and_ols():
    role = ("role Regional\n\tmodelPermission: read\n\n"
            f"\ttablePermission Sales = [Region] = \"{SECRET}\"\n\n"
            "\ttablePermission Employees\n\t\tmetadataPermission: none\n\n"
            "\ttablePermission 'Pay Data'\n\t\tcolumnPermission Salary\n\t\t\tmetadataPermission: none\n")
    result = model_security(tmdl(role, refs=0))
    assert result["inspection"] == "complete"
    assert counts(result) == {"roleCount": 1, "readerRoleCount": 1, "rlsFilterCount": 1,
                              "rlsConstantFilterCount": 0, "olsTableCount": 1, "olsColumnCount": 1}
    assert SECRET not in json.dumps(result)


def test_tmdl_multiline_and_fenced_filters_are_read():
    role = ("role A\n\tmodelPermission: read\n\n"
            "\ttablePermission Sales =\n\t\t\t[Region] = \"x\"\n\t\t\t\t&& [Year] = 2025\n"
            "\ttablePermission Orders = ```\n\t\t\t[Owner] = USERPRINCIPALNAME()\n\t\t\t```\n")
    assert model_security(tmdl(role))["rlsFilterCount"] == 2


def test_tmdl_blank_expression_and_constant_true_are_not_rls():
    role = ("role A\n\tmodelPermission: read\n\n"
            "\ttablePermission Sales =\n\ttablePermission Orders = TRUE()\n")
    result = model_security(tmdl(role))
    assert (result["rlsFilterCount"], result["rlsConstantFilterCount"]) == (0, 1)


def test_tmdl_table_and_column_ols_are_distinguished_from_visible_metadata():
    role = ("role A\n\tmodelPermission: read\n\n"
            "\ttablePermission Sales\n\t\tmetadataPermission: read\n\t\tcolumnPermission Cost\n"
            "\t\t\tmetadataPermission: none\n\t\tcolumnPermission Price\n\t\t\tmetadataPermission: read\n")
    result = model_security(tmdl(role))
    assert (result["olsTableCount"], result["olsColumnCount"]) == (0, 1)


def test_tmdl_administrator_role_filters_do_not_count_and_several_roles_are_summed():
    admin = "role Admins\n\tmodelPermission: administrator\n\n\ttablePermission Sales = [x] = 1\n"
    reader = "role Readers\n\tmodelPermission: readRefresh\n\n\ttablePermission Sales = [y] = 2\n"
    result = model_security(tmdl(admin, reader))
    assert (result["roleCount"], result["readerRoleCount"], result["rlsFilterCount"]) == (2, 1, 1)


def test_tmdl_role_listed_in_the_model_but_not_returned_is_partial():
    role = "role Role0\n\tmodelPermission: read\n\n\ttablePermission Sales = [x] = 1\n"
    result = model_security(tmdl(role, refs=2))
    assert (result["inspection"], result["reason"]) == ("partial", "role_definitions_missing")
    assert result["rlsFilterCount"] == 1


def test_tmdl_column_permission_default_property_form_is_ols():
    role = ("role A\n\tmodelPermission: read\n\n"
            "\ttablePermission Pay = [Dept] = \"x\"\n\t\tcolumnPermission Salary = none\n\t\tcolumnPermission 'Gross Pay' = None\n"
            "\t\tcolumnPermission Grade = read\n\t\tcolumnPermission Band\n")
    result = model_security(tmdl(role))
    assert result["inspection"] == "complete"
    assert (result["rlsFilterCount"], result["olsColumnCount"], result["olsTableCount"]) == (1, 2, 0)
    # The same form works when the table permission carries no filter.
    bare = "role A\n\tmodelPermission: read\n\n\ttablePermission Pay\n\t\tcolumnPermission Salary = none\n"
    assert model_security(tmdl(bare))["olsColumnCount"] == 1


@pytest.mark.parametrize("line", ["\ttablePermission = [x] = 1\n", "\ttablePermission\n",
                                  "\ttablePermission Sales\n\t\tcolumnPermission = none\n"])
def test_a_permission_the_reader_does_not_recognize_makes_the_inspection_partial_never_absent(line):
    role = "role A\n\tmodelPermission: read\n\n" + line
    result = model_security(tmdl(role))
    assert (result["inspection"], result["reason"]) == ("partial", "role_syntax_unrecognized")
    assert result["unparsedCount"] >= 1


def test_tmdl_tables_after_an_unrecognized_permission_are_still_read():
    role = ("role A\n\tmodelPermission: read\n\n\ttablePermission\n"
            "\ttablePermission Sales = [x] = 1\n")
    result = model_security(tmdl(role))
    assert (result["inspection"], result["rlsFilterCount"]) == ("partial", 1)


# --- not inspectable ---------------------------------------------------------------------------

def test_unreadable_unrecognized_and_undecodable_definitions_are_never_complete():
    assert model_security(None, "http_403")["reason"] == "definition_unreadable"
    assert model_security({"definition": {"parts": []}})["inspection"] == "unavailable"
    assert model_security(definition(part("something.txt", "x")))["reason"] == "definition_format_unrecognized"
    broken = tmsl([])
    broken["definition"]["parts"].append({"path": "definition/roles/X.tmdl", "payloadType": "InlineBase64", "payload": "!!"})
    result = model_security(broken)
    assert (result["inspection"], result["reason"]) == ("partial", "definition_parts_undecodable")


def test_payload_is_never_copied_into_the_summary():
    roles = [{"name": "R", "modelPermission": "read", "tablePermissions": [
        {"name": "T", "filterExpression": f"[a] = \"{SECRET}\""}]}]
    text = json.dumps(model_security(tmsl(roles)))
    assert SECRET not in text and base64.b64encode(SECRET.encode()).decode() not in text


# --- per-model state ---------------------------------------------------------------------------

from analyzers import model_security_rules as rules  # noqa: E402
from analyzers.far_profile import RULE_KINDS  # noqa: E402
from analyzers.far_review import _assess, _assess_tenant, analyze  # noqa: E402
from analyzers.rule_support import Context  # noqa: E402
from orchestration.review_config import (  # noqa: E402
    ROOT, activate, capture, declared_profiles, local_configuration, read_snapshot, read_yaml, validate_configuration,
)
from orchestration.rule_policy import resolve_rule  # noqa: E402

WS, OTHER_WS = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
M1, M2, M3 = ("aaaaaaaa-0000-4000-8000-00000000000%d" % n for n in (1, 2, 3))
CAP = "cccccccc-cccc-cccc-cccc-cccccccccccc"
REPORT = "eeeeeeee-0000-4000-8000-000000000009"


def security(inspection="complete", rls=0, ols_t=0, ols_c=0, constant=0, reason=None):
    return {"inspection": inspection, "reason": reason, "roleCount": 1 if rls or ols_t or ols_c else 0,
            "readerRoleCount": 1 if rls or ols_t or ols_c else 0, "rlsFilterCount": rls,
            "rlsConstantFilterCount": constant, "olsTableCount": ols_t, "olsColumnCount": ols_c}


def row(model, sec, ws=WS, flag=None, **extra):
    return {"id": model, "workspaceId": ws, "security": sec, "effectiveIdentityRolesRequired": flag, **extra}


def inventory(*models, ws=WS, complete=True):
    return {"id": ws, "name": "ws", "type": "Workspace", "capacityId": CAP,
            "itemsCollectionStatus": "collected" if complete else "unavailable",
            "items": [{"id": m, "type": "SemanticModel"} for m in models] + [{"id": REPORT, "type": "Report"}],
            "usersCollectionStatus": "collected", "users": []}


def configured(rls="", ols="", **policy):
    values = read_yaml(ROOT / "config" / "policy.yaml")
    values["policy"].update({"rls_required_model_ids": rls, "ols_required_model_ids": ols, **policy})
    config = local_configuration()
    config["profiles"] = declared_profiles(values)
    validate_configuration(config)
    return config


def evaluate(rule_id, workspaces, models, config, tenant=False):
    context = Context({"configuration": config}, list(workspaces), {"semantic_model_definitions.json": {"models": models}})
    if tenant:
        return _assess_tenant(rule_id, resolve_rule(config, rule_id, {}), context)
    ws = workspaces[0]
    return _assess(rule_id, ws, resolve_rule(config, rule_id, ws), context)


def test_model_state_is_present_absent_or_unknown_and_never_guesses():
    state = rules.model_state(row(M1, security(rls=2)))
    assert (state["rls"], state["ols"], state["rls_source"]) == ("present", "absent", "definition")
    assert rules.model_state(row(M1, security(ols_c=1)))["ols"] == "present"
    absent = rules.model_state(row(M1, security()))
    assert (absent["rls"], absent["ols"]) == ("absent", "absent")
    for bad in (None, {"id": M1}, row(M1, security("partial", reason="role_definitions_missing")),
                row(M1, security("unavailable", reason="definition_unreadable")), {"id": M1, "error": "http_403"}):
        state = rules.model_state(bad)
        assert (state["rls"], state["ols"]) == ("unknown", "unknown") and state["inspection"] != "complete"


def test_the_dataset_flag_is_positive_evidence_only_when_the_definition_cannot_be_read():
    unreadable = row(M1, security("unavailable", reason="definition_unreadable"), flag=True)
    state = rules.model_state(unreadable)
    assert (state["rls"], state["rls_source"], state["ols"]) == ("present", "dataset_flag", "unknown")
    assert rules.model_state(row(M1, security("unavailable"), flag=False))["rls"] == "unknown"
    complete = rules.model_state(row(M1, security(), flag=True))
    assert complete["rls"] == "absent" and complete["roles_without_row_filters"] is True


def test_constant_true_filters_are_not_rls():
    assert rules.model_state(row(M1, security(constant=1)))["rls"] == "absent"


# --- SEC-024 / SEC-025 --------------------------------------------------------------------------

@pytest.mark.parametrize("rule_id, kind, good, bad", [
    ("SEC-024", "rls", security(rls=1), security(ols_t=1)),
    ("SEC-025", "ols", security(ols_t=1), security(rls=1)),
])
def test_declared_models_pass_fail_or_are_not_applicable(rule_id, kind, good, bad):
    config = configured(**{f"{kind}_required_model_ids": M1})
    ws = inventory(M1, M2)
    assert evaluate(rule_id, [ws], [row(M1, good), row(M2, bad)], config)[0] == "pass"
    status, evidence = evaluate(rule_id, [ws], [row(M1, bad), row(M2, good)], config)
    assert status == "fail" and evidence[rules.MISSING_IDS_KEY[kind]] == [M1]
    assert evidence["targeted_model_count"] == 1 and "model_security_unavailable" not in evidence.values()
    other = inventory(M3, ws=OTHER_WS)
    status, evidence = evaluate(rule_id, [other], [], config)
    assert (status, evidence["reason_code"]) == ("not_applicable", "no_declared_model_in_workspace")


def test_an_undeclared_requirement_is_unknown_and_never_a_pass():
    status, evidence = evaluate("SEC-024", [inventory(M1)], [row(M1, security(rls=1))], configured())
    assert (status, evidence["reason_code"]) == ("unknown", "policy_undeclared")


def test_a_model_that_cannot_be_inspected_is_missing_evidence_not_a_pass():
    config = configured(rls_required_model_ids=M1)
    for models in ([], [row(M1, security("partial", reason="role_definitions_missing"))],
                   [{"id": M1, "workspaceId": WS, "error": "http_403"}]):
        status, evidence = evaluate("SEC-024", [inventory(M1)], models, config)
        assert (status, evidence["reason_code"]) == ("missing_evidence", "model_security_unavailable")
        assert evidence["unverified_model_ids"] == [M1]


def test_a_confirmed_gap_fails_even_when_another_declared_model_could_not_be_inspected():
    config = configured(rls_required_model_ids=f"{M1}, {M2}")
    status, evidence = evaluate("SEC-024", [inventory(M1, M2)], [row(M1, security()), row(M2, security("unavailable"))], config)
    assert status == "fail"
    assert (evidence["models_missing_rls_ids"], evidence["unverified_model_ids"]) == ([M1], [M2])


def test_an_incomplete_item_inventory_is_missing_evidence():
    config = configured(rls_required_model_ids=M1)
    status, evidence = evaluate("SEC-024", [inventory(M1, complete=False)], [row(M1, security(rls=1))], config)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "model_inventory_incomplete")


def test_a_model_listed_twice_in_the_definitions_is_ambiguous_and_never_a_pass():
    config = configured(rls_required_model_ids=M1)
    status, _ = evaluate("SEC-024", [inventory(M1)], [row(M1, security(rls=1)), row(M1, security(rls=1))], config)
    assert status == "missing_evidence"


def test_both_requirements_can_name_the_same_model():
    config = configured(rls_required_model_ids=M1, ols_required_model_ids=M1)
    models = [row(M1, security(rls=1))]
    assert evaluate("SEC-024", [inventory(M1)], models, config)[0] == "pass"
    assert evaluate("SEC-025", [inventory(M1)], models, config)[0] == "fail"


# --- SEC-026 ------------------------------------------------------------------------------------

def test_posture_counts_models_and_flags_only_those_with_neither():
    models = [row(M1, security(rls=1, ols_t=1)), row(M2, security()), row(M3, security("partial", reason="x"))]
    status, evidence = evaluate("SEC-026", [inventory(M1, M2, M3)], models, configured())
    assert status == "info"
    assert (evidence["model_count"], evidence["both_count"], evidence["neither_count"], evidence["unverified_count"]) == (3, 1, 1, 1)
    assert (evidence["rls_present_count"], evidence["rls_absent_count"], evidence["rls_unknown_count"]) == (1, 1, 1)
    assert evidence["models_without_security_ids"] == [M2] and evidence["review_recommended"] is True
    status, evidence = evaluate("SEC-026", [inventory(M1)], [row(M1, security(rls=1))], configured())
    assert evidence["review_recommended"] is False


def test_posture_needs_no_declaration_and_respects_gaps_in_the_inventory():
    assert evaluate("SEC-026", [inventory()], [], configured())[0] == "not_applicable"
    status, evidence = evaluate("SEC-026", [inventory(M1, complete=False)], [], configured())
    assert (status, evidence["reason_code"]) == ("missing_evidence", "model_inventory_incomplete")
    status, evidence = evaluate("SEC-026", [inventory(M1)], [], configured())
    assert status == "info" and evidence["unverified_count"] == 1 and evidence["review_recommended"] is False


# --- SEC-027 ------------------------------------------------------------------------------------

def test_declared_models_are_resolved_against_the_reviewed_inventory(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    workspaces = [inventory(M1)]
    status, evidence = evaluate("SEC-027", workspaces, [], configured(), tenant=True)
    assert (status, evidence["coverage_status"]) == ("info", "not_declared")
    status, evidence = evaluate("SEC-027", workspaces, [], configured(rls_required_model_ids=M1, ols_required_model_ids=M1), tenant=True)
    assert (status, evidence["coverage_status"], evidence["declared_model_count"]) == ("info", "complete", 1)
    status, evidence = evaluate("SEC-027", workspaces, [], configured(rls_required_model_ids=f"{M1}, {M3}"), tenant=True)
    assert (status, evidence["reason_code"], evidence["unresolved_model_ids"]) == ("missing_evidence", "declared_models_not_found", [M3])
    status, evidence = evaluate("SEC-027", [inventory(M1, complete=False)], [], configured(rls_required_model_ids=M1), tenant=True)
    assert (status, evidence["reason_code"]) == ("missing_evidence", "model_inventory_incomplete")


def test_a_declared_model_outside_a_limited_review_is_not_a_gap(monkeypatch):
    monkeypatch.setenv("WORKSPACE_IDS", WS)
    status, evidence = evaluate("SEC-027", [inventory(M1)], [], configured(ols_required_model_ids=M3), tenant=True)
    assert (status, evidence["coverage_status"], evidence["review_scope_limited"]) == ("info", "outside_review_scope", True)


def test_a_declared_id_that_is_not_a_semantic_model_is_not_found(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    status, evidence = evaluate("SEC-027", [inventory(M1)], [], configured(rls_required_model_ids=REPORT), tenant=True)
    assert status == "missing_evidence" and evidence["unresolved_model_count"] == 1


def test_disabling_a_requirement_rule_removes_its_declaration(monkeypatch):
    monkeypatch.delenv("WORKSPACE_IDS", raising=False)
    config = configured(rls_required_model_ids=M3, disabled_rules="SEC-024")
    status, evidence = evaluate("SEC-027", [inventory(M1)], [], config, tenant=True)
    assert (status, evidence["coverage_status"]) == ("info", "not_declared")


# --- configuration ------------------------------------------------------------------------------

def test_the_two_model_lists_become_requirements_for_their_own_rule_only():
    config = configured(rls_required_model_ids=f"{M1.upper()}; {M2}", ols_required_model_ids=M3)
    rls = resolve_rule(config, "SEC-024", {})["policy"]
    ols = resolve_rule(config, "SEC-025", {})["policy"]
    assert (rls["required"], rls["required_model_ids"]) == (True, [M1, M2])
    assert ols["required_model_ids"] == [M3]
    assert resolve_rule(config, "SEC-026", {})["policy"] == {}


@pytest.mark.parametrize("value, message", [
    ("not-a-guid", "GUIDs"), (f"{M1}, {M1.upper()}", "Duplicate"), (f"{M1}, {M1}", "distinct"),
])
def test_invalid_model_declarations_stop_the_run(value, message):
    with pytest.raises(ValueError, match=message):
        configured(rls_required_model_ids=value)


def test_the_policy_variables_are_part_of_the_fixed_declaration_contract():
    policy = read_yaml(ROOT / "config" / "policy.yaml")["policy"]
    assert policy["rls_required_model_ids"] == "" and policy["ols_required_model_ids"] == ""
    assert {RULE_KINDS[rule] for rule in ("SEC-024", "SEC-025")} == {"control"}
    assert (RULE_KINDS["SEC-026"], RULE_KINDS["SEC-027"]) == ("signal", "coverage")


# --- the analyzer end to end ---------------------------------------------------------------------

def run_review(tmp_path, monkeypatch, config, workspaces, models):
    from tests.test_native_rules import stamp
    for key in ("FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG", "WORKSPACE_IDS"):
        monkeypatch.delenv(key, raising=False)
    snapshot = read_snapshot(capture(tmp_path, config))
    activate(tmp_path / "review_config.json")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    payloads = {"workspace_inventory.json": {"workspaceListComplete": True, "workspaces": workspaces},
                "semantic_model_definitions.json": {"models": models, "collectionComplete": True}}
    for name, payload in payloads.items():
        (raw_dir / name).write_text(json.dumps({**payload, "_provenance": stamp(snapshot)}), encoding="utf-8")
    return json.loads(analyze(raw_dir, tmp_path, ROOT / "config" / "review-checklist.yaml").read_text())


def test_the_analyzer_scores_declared_models_and_keeps_signals_and_coverage_out_of_the_score(tmp_path, monkeypatch):
    config = configured(rls_required_model_ids=f"{M1}, {M2}", ols_required_model_ids=M2)
    models = [row(M1, security(rls=1)), row(M2, security())]
    rows = run_review(tmp_path, monkeypatch, config, [inventory(M1, M2)], models)
    by_rule = {r["rule_id"]: r for r in rows if r["rule_id"] in {"SEC-024", "SEC-025", "SEC-026", "SEC-027"}}
    assert len([r for r in rows if r["rule_id"] == "SEC-027"]) == 1
    assert (by_rule["SEC-024"]["status"], by_rule["SEC-024"]["scored"]) == ("fail", True)
    assert by_rule["SEC-024"]["evidence"]["models_missing_rls_ids"] == [M2]
    assert (by_rule["SEC-025"]["status"], by_rule["SEC-025"]["scored"]) == ("fail", True)
    assert "row-level" in by_rule["SEC-024"]["recommendation"] and "object-level" in by_rule["SEC-025"]["recommendation"]
    assert (by_rule["SEC-026"]["status"], by_rule["SEC-026"]["scored"]) == ("info", False)
    assert (by_rule["SEC-027"]["status"], by_rule["SEC-027"]["scored"]) == ("info", False)
    serialized = json.dumps(rows)
    assert SECRET not in serialized


def test_an_undeclared_review_still_reports_posture_and_judges_nothing(tmp_path, monkeypatch):
    rows = run_review(tmp_path, monkeypatch, configured(), [inventory(M1)], [row(M1, security())])
    by_rule = {r["rule_id"]: r for r in rows if r["rule_id"] in {"SEC-024", "SEC-025", "SEC-026", "SEC-027"}}
    assert by_rule["SEC-024"]["status"] == by_rule["SEC-025"]["status"] == "unknown"
    assert by_rule["SEC-026"]["evidence"]["neither_count"] == 1
    assert by_rule["SEC-027"]["evidence"]["coverage_status"] == "not_declared"
    assert not any(r["scored"] for r in by_rule.values())


# --- collector -----------------------------------------------------------------------------------

def test_the_model_security_signal_alone_demands_the_model_inventory_and_definitions():
    from analyzers.far_profile import ACTIVE_RULE_IDS, TENANT_RULES
    from collectors import far_collection
    from tests.test_far_collection_runtime import WORKSPACE, snapshot
    frozen = snapshot()
    frozen["configuration"]["profiles"]["rule_profiles"] = {"declared": {"rule_overrides": {
        rule: {"applicability": "disabled", "reason": "Explicit review policy."}
        for rule in ACTIVE_RULE_IDS - TENANT_RULES - {"SEC-026"}}}}
    frozen["configuration"]["profiles"]["default_rule_profile"] = "declared"
    result = far_collection.plan(frozen, [{"id": WORKSPACE}])
    assert {"collectors.semantic_models", "collectors.semantic_model_definitions"} <= set(result["collectors"])


def test_the_definition_collector_records_security_counts_and_the_dataset_flag_only():
    from collectors.semantic_model_definitions import _normalise
    roles = [{"name": "R", "modelPermission": "read", "tablePermissions": [
        {"name": "T", "filterExpression": f"[a] = \"{SECRET}\"", "metadataPermission": "none"}]}]
    record = _normalise({"id": M1, "workspaceId": WS, "isEffectiveIdentityRolesRequired": True}, tmsl(roles), None)
    assert record["effectiveIdentityRolesRequired"] is True
    assert (record["security"]["rlsFilterCount"], record["security"]["olsTableCount"]) == (1, 1)
    assert SECRET not in json.dumps(record) and record["parts"] == []
    failed = _normalise({"id": M1, "workspaceId": WS}, None, "http_403")
    assert failed["security"]["inspection"] == "unavailable" and failed["effectiveIdentityRolesRequired"] is None


# --- Gold and owner projection ---------------------------------------------------------------------

def test_gold_carries_posture_requirements_and_outcomes_for_every_model(tmp_path, monkeypatch):
    from analyzers.far_profile import normalize_findings
    from reports.gold_layer import build_gold
    from tests import _far_surfaces as surfaces
    raw_dir = surfaces.raw(tmp_path, monkeypatch)
    (raw_dir / "semantic_model_definitions.json").write_text(json.dumps({"models": [
        {"id": surfaces.MID, "workspaceId": surfaces.WID, "metadataOnly": True, "storageModes": ["DirectLake"], "parts": [],
         "security": security(ols_t=1), "effectiveIdentityRolesRequired": False}]}), encoding="utf-8")
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    config = configured(rls_required_model_ids=surfaces.MID, ols_required_model_ids=surfaces.MID)
    monkeypatch.setenv("FAR_CONFIG_SNAPSHOT", str(capture(snapshot_dir, config)))
    base = {"status": "fail", "dimension": "security", "severity": "high", "recommendation": "x",
            "profile_id": surfaces.META["profile_id"], "profile_sha256": surfaces.META["profile_sha256"]}
    findings = normalize_findings([{**base, "rule_id": "SEC-024", "title": "RLS", "evidence": {
        "workspace_id": surfaces.WID, "workspace_name": "Architecture", "models_missing_rls_ids": [surfaces.MID]}}])
    tables = build_gold(findings, raw_dir, run_id="review", run_timestamp="2026-09-01T00:00:00Z", check_remote=False)
    [security_row] = tables["gold_model_security"]
    assert (security_row["rls_status"], security_row["ols_status"], security_row["security_inspection"]) == ("absent", "present", "complete")
    assert (security_row["rls_required"], security_row["ols_required"]) == (True, True)
    assert (security_row["rls_outcome"], security_row["ols_outcome"]) == ("fail", "pass")
    assert (security_row["capacity_name"], security_row["model_name"], security_row["ols_table_count"]) == ("Hosting", "Model", 1)
    [model_row] = tables["gold_semantic_models"]
    assert (model_row["rls_status"], model_row["ols_status"], model_row["storage_mode"]) == ("absent", "present", "DirectLake")
    # The Lakehouse-exposed inventory never carries a requirement or an outcome.
    assert not {key for key in model_row if "required" in key or "outcome" in key}
    items = {(row["rule_id"], row["item_id"]): row for row in tables["gold_finding_items"]}
    assert items[("SEC-024", surfaces.MID.lower())]["reason"] == "Required to enforce row-level security, and none is configured"


def test_gold_never_invents_security_for_a_model_that_was_not_inspected_or_declared(tmp_path, monkeypatch):
    from reports.gold_layer import build_gold
    from tests import _far_surfaces as surfaces
    raw_dir = surfaces.raw(tmp_path, monkeypatch)
    monkeypatch.delenv("FAR_CONFIG_SNAPSHOT", raising=False)
    tables = build_gold([], raw_dir, run_id="review", run_timestamp="2026-09-01T00:00:00Z", check_remote=False)
    [row_] = tables["gold_model_security"]
    assert (row_["rls_status"], row_["ols_status"], row_["security_inspection"]) == ("unknown", "unknown", "unavailable")
    assert (row_["rls_required"], row_["rls_outcome"], row_["ols_outcome"]) == (False, "not_required", "not_required")


def test_the_owner_projection_lists_the_posture_of_the_owners_own_models_only():
    from reports.owner.gold import build_owner_gold
    from tests import _far_surfaces as surfaces
    source = surfaces.owner_input()
    other = "99999999-9999-4999-8999-999999999999"
    source["gold_model_security"] = [
        {**surfaces.META, "workspace_id": surfaces.WID, "model_id": surfaces.MID, "model_name": "Model",
         "rls_status": "absent", "ols_status": "present", "security_inspection": "complete",
         "rls_required": True, "rls_outcome": "fail"},
        {**surfaces.META, "workspace_id": other, "model_id": other, "model_name": "Elsewhere",
         "rls_status": "present", "ols_status": "present", "security_inspection": "complete"}]
    details = [d for d in build_owner_gold(source)["owner_details"] if d["detail_type"] == "model_security"]
    assert [(d["item_id"], d["detail"]) for d in details] == [
        (surfaces.MID, "Row-level security: absent; object-level security: present; inspection: complete")]
    assert not any("required" in json.dumps(d) or "fail" in d["detail"] for d in details)


def test_both_agents_and_the_app_chat_are_told_what_model_security_does_and_does_not_prove():
    from reports.agent.data_agent import DEFAULT_AI_INSTRUCTIONS
    from reports.agent.owner_agent import owner_instructions
    central, owner = " ".join(DEFAULT_AI_INSTRUCTIONS.split()), " ".join(owner_instructions("1").split())
    assert "gold_model_security" in central and "Unknown is not \"no security\"" in central
    assert "Row-level security:" in owner and "never \"no security\"" in owner and "owner_access" in owner
    for text in (central, owner):
        assert "SEC-024" in text and "role membership is not" in text and "Copilot" in text
    assert len(owner_instructions("1")) <= 15000


def test_every_surface_knows_the_new_table_and_page():
    from reports.agent.sdk_deploy import APPROVED_GOLD_TABLES, _LAKEHOUSE_TABLES
    from reports.ontology.ontology import ENTITIES
    from reports.powerbi.report import _PAGE_INDEX, _content_specs
    from reports.powerbi.schema import GOLD_TABLES_BY_NAME
    from reports.powerbi.semantic_model import _measures
    columns = {c.name for c in GOLD_TABLES_BY_NAME["gold_model_security"].columns}
    spec = _content_specs()["modelsecurity"]
    assert "modelsecurity" in [key for key, _, _ in _PAGE_INDEX]
    assert set(spec["tables"][0]["columns"]) <= columns
    assert all(entity == "gold_model_security" and column in columns for entity, column, _ in spec["slicers"])
    assert {"Models Without RLS or OLS", "Required Security Gaps", "Models Not Inspected"} <= {m["name"] for m in _measures()}
    # Decisions stay in the secured model; the unsecured Lakehouse and the ontology carry observed posture only.
    assert "gold_model_security" in APPROVED_GOLD_TABLES and "gold_model_security" not in _LAKEHOUSE_TABLES
    model = next(entity for entity in ENTITIES if entity.name == "SemanticModel")
    assert {"RlsStatus", "OlsStatus", "SecurityInspection"} <= {prop.name for prop in model.props}
    assert not {prop.column for prop in model.props} & {"rls_required", "ols_required", "rls_outcome", "ols_outcome"}

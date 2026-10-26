# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Per-role detail of a semantic model: parsing, the opt-in setting, Gold, relationships, owner and agents."""
import json

import pytest

from analyzers import model_security_rules as rules
from collectors import model_security as parser
from collectors.model_security import model_security, role_detail_enabled
from orchestration.environment import (
    fabric_model_security, model_security_environment, validate_model_security,
)
from tests.test_model_security import M1, WS, capture, configured, security, tmdl, tmsl

SECRET = "SecretRegionLiteral-7f3a"


def roles_of(definition):
    return {role["name"]: role for role in model_security(definition, keep_roles=True)["roles"]}


def tmsl_role(name, permission="read", *tables):
    return {"name": name, "modelPermission": permission, "tablePermissions": list(tables)}


def filtered(expression, **extra):
    return {"name": "T", "filterExpression": expression, **extra}


# --- identity detection ------------------------------------------------------------------------

@pytest.mark.parametrize("expression,kind", [
    ('[Email] = USERPRINCIPALNAME()', "dynamic"),
    ('[Login] = username ()', "dynamic"),
    ('[Id] = USEROBJECTID()', "dynamic"),
    ('[Tenant] = CUSTOMDATA()', "dynamic"),
    ('[Region] = "East"', "static"),
    # Mentions inside a string, a quoted name, a column reference or a comment are not calls.
    ('[Region] = "USERPRINCIPALNAME()"', "static"),
    ("'USERNAME()'[Region] = \"East\"", "static"),
    ('[USERNAME()] = 1', "static"),
    ('[Region] = "East" // USERPRINCIPALNAME()', "static"),
    ('[Region] = "East" -- USERNAME()', "static"),
    ('[Region] = "East" /* USERPRINCIPALNAME() */', "static"),
    ('[Region] = "it""s" && [A] = USERNAME()', "dynamic"),
])
def test_a_filter_is_dynamic_only_when_it_calls_an_identity_function(expression, kind):
    role = roles_of(tmsl([tmsl_role("R", "read", filtered(expression))]))["R"]
    assert (role["filterKind"], role["rowFilterTables"]) == (kind, 1)
    assert role["dynamicFilterTables"] == (1 if kind == "dynamic" else 0)


def test_a_role_with_dynamic_and_static_filters_is_mixed():
    role = roles_of(tmsl([tmsl_role("R", "read", filtered("[A] = USERNAME()"), filtered('[B] = "x"'))]))["R"]
    assert (role["filterKind"], role["rowFilterTables"], role["dynamicFilterTables"]) == ("mixed", 2, 1)


def test_a_comment_cannot_hide_a_constant_filter_or_swallow_the_rest_of_a_multiline_tmdl_filter():
    constant = tmsl([tmsl_role("R", "read", filtered("TRUE() // everyone"))])
    assert roles_of(constant)["R"]["filterKind"] == "constant"
    assert model_security(constant)["rlsFilterCount"] == 0
    role = ("role R\n\tmodelPermission: read\n\n\ttablePermission Sales =\n\t\t\t[A] = \"x\" // note\n"
            "\t\t\t\t&& [B] = USERPRINCIPALNAME()\n")
    assert roles_of(tmdl(role))["R"]["filterKind"] == "dynamic"


# --- role kinds ---------------------------------------------------------------------------------

def test_roles_are_described_by_permission_filter_and_hidden_objects_and_match_the_model_totals():
    definition = tmsl([
        tmsl_role("Regional", "read", filtered(f'[Region] = "{SECRET}"'), filtered("[Owner] = USERNAME()")),
        tmsl_role("Hidden", "readRefresh", {"name": "Pay", "metadataPermission": "none", "columnPermissions": [
            {"name": "Salary", "metadataPermission": "none"}, {"name": "Grade", "metadataPermission": "read"}]}),
        tmsl_role("Everyone", "read", filtered("TRUE()")),
        tmsl_role("Plain", "read"),
        tmsl_role("Admins", "administrator", filtered('[x] = "ignored"')),
        tmsl_role("Nobody", "none"),
        {"name": "Unstated", "tablePermissions": [filtered('[x] = "y"')]},
    ])
    result = model_security(definition, keep_roles=True)
    by_name = {role["name"]: role for role in result["roles"]}
    assert [role["name"] for role in result["roles"]] == ["Regional", "Hidden", "Everyone", "Plain", "Admins", "Nobody", "Unstated"]
    assert by_name["Regional"] == {"name": "Regional", "permission": "read", "readsData": True, "rowFilterTables": 2,
                                   "dynamicFilterTables": 1, "constantFilterTables": 0, "hiddenTables": 0,
                                   "hiddenColumns": 0, "filterKind": "mixed"}
    assert (by_name["Hidden"]["permission"], by_name["Hidden"]["hiddenTables"], by_name["Hidden"]["hiddenColumns"],
            by_name["Hidden"]["filterKind"]) == ("readRefresh", 1, 1, "none")
    assert by_name["Everyone"]["filterKind"] == "constant" and by_name["Everyone"]["constantFilterTables"] == 1
    assert by_name["Plain"]["filterKind"] == "none"
    assert (by_name["Admins"]["permission"], by_name["Admins"]["readsData"], by_name["Admins"]["filterKind"],
            by_name["Admins"]["rowFilterTables"]) == ("administrator", False, "not_applicable", 0)
    assert (by_name["Nobody"]["readsData"], by_name["Nobody"]["filterKind"]) == (False, "not_applicable")
    assert (by_name["Unstated"]["permission"], by_name["Unstated"]["readsData"]) == ("unspecified", True)
    # The per-role records add up to the totals the rules use.
    readers = [role for role in result["roles"] if role["readsData"]]
    assert result["roleCount"] == len(result["roles"]) and result["readerRoleCount"] == len(readers)
    assert result["rlsFilterCount"] == sum(role["rowFilterTables"] for role in readers)
    assert result["rlsConstantFilterCount"] == sum(role["constantFilterTables"] for role in readers)
    assert result["olsTableCount"] == sum(role["hiddenTables"] for role in readers)
    assert result["olsColumnCount"] == sum(role["hiddenColumns"] for role in readers)


def test_tmdl_roles_carry_their_name_permission_and_the_service_column_form():
    reader = ("role 'Regional Sales'\n\tmodelPermission: read\n\n\ttablePermission dimension_city = [T] = \"x\"\n")
    ols = ("role Restricted\n\tmodelPermission: read\n\n\ttablePermission dimension_customer\n"
           "\t\tcolumnPermission PrimaryContact = none\n\n\t\tcolumnPermission PostalCode = none\n\n"
           "\ttablePermission dimension_employee\n\t\tmetadataPermission: none\n")
    admin = "role Admins\n\tmodelPermission: administrator\n\n\ttablePermission Sales = [x] = 1\n"
    quoted = "role 'O''Brien team'\n\tmodelPermission: read\n"
    result = model_security(tmdl(reader, ols, admin, quoted), keep_roles=True)
    by_name = {role["name"]: role for role in result["roles"]}
    assert set(by_name) == {"Regional Sales", "Restricted", "Admins", "O'Brien team"}
    assert by_name["Regional Sales"]["filterKind"] == "static"
    assert (by_name["Restricted"]["hiddenColumns"], by_name["Restricted"]["hiddenTables"]) == (2, 1)
    assert (by_name["Admins"]["readsData"], by_name["Admins"]["rowFilterTables"]) == (False, 0)
    assert result["inspection"] == "complete"


def test_a_role_with_an_unrecognized_permission_has_an_undetermined_filter_kind():
    role = "role A\n\tmodelPermission: read\n\n\ttablePermission\n\ttablePermission Sales = [x] = 1\n"
    result = model_security(tmdl(role), keep_roles=True)
    assert result["inspection"] == "partial"
    assert result["roles"][0]["filterKind"] == "unknown"


def test_names_are_cleaned_and_bounded_and_other_permissions_are_named_other():
    definition = tmsl([{"name": "Line\x00 break\n" + "x" * 300, "modelPermission": "surprising"},
                       {"name": "   ", "modelPermission": "read"}])
    first, second = model_security(definition, keep_roles=True)["roles"]
    assert len(first["name"]) <= parser.MAX_ROLE_NAME and "\x00" not in first["name"] and "\n" not in first["name"]
    assert first["permission"] == "other" and first["readsData"] is False
    assert second["name"] == "(unnamed)"


def test_the_role_list_is_bounded_but_the_totals_still_count_every_role():
    definition = tmsl([tmsl_role(f"R{i}", "read", filtered('[x] = "y"')) for i in range(parser.MAX_ROLE_RECORDS + 5)])
    result = model_security(definition, keep_roles=True)
    assert len(result["roles"]) == parser.MAX_ROLE_RECORDS and result["rolesTruncated"] is True
    assert result["roleCount"] == parser.MAX_ROLE_RECORDS + 5 and result["rlsFilterCount"] == parser.MAX_ROLE_RECORDS + 5


# --- role names are kept only when asked -----------------------------------------------------------

def test_roles_and_names_are_absent_unless_role_detail_is_requested():
    definition = tmsl([tmsl_role("Payroll Admins", "read", filtered(f'[Region] = "{SECRET}"'))])
    default = model_security(definition)
    assert "roles" not in default and "rolesTruncated" not in default
    assert "Payroll Admins" not in json.dumps(default) and SECRET not in json.dumps(default)
    # With role detail on, the name is the only new label; the filter text is still never kept.
    kept = json.dumps(model_security(definition, keep_roles=True))
    assert "Payroll Admins" in kept and SECRET not in kept and "Region" not in kept


def test_an_unreadable_definition_has_no_roles_when_detail_is_requested():
    for result in (model_security(None, "http_403", keep_roles=True), model_security({"definition": {"parts": []}}, keep_roles=True)):
        assert result["inspection"] == "unavailable" and result["roles"] == []


def test_the_collector_keeps_role_detail_only_when_the_setting_is_on(monkeypatch):
    from collectors.semantic_model_definitions import _normalise
    definition = tmsl([tmsl_role("Payroll", "read", filtered('[x] = "y"'))])
    monkeypatch.delenv("MODEL_SECURITY_ROLE_DETAIL", raising=False)
    assert "roles" not in _normalise({"id": M1, "workspaceId": WS}, definition, None)["security"]
    monkeypatch.setenv("MODEL_SECURITY_ROLE_DETAIL", "true")
    record = _normalise({"id": M1, "workspaceId": WS}, definition, None)
    assert [role["name"] for role in record["security"]["roles"]] == ["Payroll"]
    assert record["parts"] == [] and "[x]" not in json.dumps(record)
    monkeypatch.setenv("MODEL_SECURITY_ROLE_DETAIL", "false")
    assert "roles" not in _normalise({"id": M1, "workspaceId": WS}, definition, None)["security"]


# --- the setting -------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [("true", True), (" TRUE ", True), ("false", False), ("", False),
                                            (None, False), (True, True), (False, False)])
def test_the_setting_accepts_true_false_and_blank(value, expected):
    assert validate_model_security({"role_detail": value}) == {"role_detail": expected}


@pytest.mark.parametrize("value", ["yes", "1", "maybe", 1, ["true"]])
def test_an_unrecognized_value_is_rejected_rather_than_guessed(value):
    with pytest.raises(ValueError, match="role_detail"):
        validate_model_security({"role_detail": value})
    with pytest.raises(ValueError, match="role_detail"):
        role_detail_enabled({"MODEL_SECURITY_ROLE_DETAIL": str(value)})


def test_the_setting_has_exactly_one_key():
    for values in ({}, {"role_detail": "true", "extra": "x"}, None):
        with pytest.raises(ValueError, match="exactly"):
            validate_model_security(values)


def test_a_library_from_before_the_setting_existed_means_off_and_a_set_value_reaches_the_collector():
    class Old:
        def getVariable(self, name):
            raise KeyError(name)

    class On:
        def getVariable(self, name):
            assert name == "model_security__role_detail"
            return "true"

    assert fabric_model_security(lambda _: Old(), "Env") == {"role_detail": False}
    settings = fabric_model_security(lambda _: On(), "Env")
    assert model_security_environment(settings) == {"MODEL_SECURITY_ROLE_DETAIL": "true"}
    assert role_detail_enabled(model_security_environment(settings)) is True
    assert role_detail_enabled({}) is False


def test_the_environment_defaults_and_the_collect_notebook_wire_the_setting():
    from orchestration.review_config import ROOT, read_yaml
    assert read_yaml(ROOT / "config" / "environment.yaml")["model_security"] == {"role_detail": "false"}
    notebook = (ROOT / "fabric" / "notebooks" / "01_collect.ipynb").read_text(encoding="utf-8")
    assert "fabric_model_security" in notebook and "model_security_environment" in notebook
    assert "MODEL_SECURITY_ROLE_DETAIL" in (ROOT / ".env.example").read_text(encoding="utf-8")


# --- state read back from the raw file -----------------------------------------------------------------

def stored(**extra):
    return {**security(rls=1), "roles": [], **extra}


@pytest.mark.parametrize("sec,inspection,expected", [
    (None, "unavailable", "not_collected"),
    ({"inspection": "complete"}, "complete", "not_collected"),
    ({"roles": "x"}, "complete", "not_collected"),
    (stored(), "complete", "collected"),
    (stored(rolesTruncated=True), "complete", "partial"),
    (stored(), "partial", "partial"),
    (stored(), "unavailable", "unavailable"),
])
def test_role_detail_state_distinguishes_not_collected_from_no_roles(sec, inspection, expected):
    assert rules.role_detail_state(sec, inspection) == expected


def test_model_state_reports_role_detail_and_roles_are_validated_when_read_back():
    row = {"id": M1, "workspaceId": WS, "security": {**security(rls=1), "roles": [
        {"name": "Ok", "permission": "read", "readsData": True, "rowFilterTables": 2, "dynamicFilterTables": 1,
         "constantFilterTables": 0, "hiddenTables": 1, "hiddenColumns": 3, "filterKind": "mixed"},
        {"name": "Weird\x01" + "n" * 400, "permission": "root", "readsData": "yes", "rowFilterTables": -4,
         "dynamicFilterTables": True, "hiddenTables": "3", "filterKind": "surprising"},
        "not a role", None]}}
    assert rules.model_state(row)["role_detail"] == "collected"
    ok, weird = rules.model_roles(row)
    assert ok == {"role_name": "Ok", "role_permission": "read", "reads_data": True, "row_filter_table_count": 2,
                  "dynamic_filter_table_count": 1, "constant_filter_table_count": 0, "ols_table_count": 1,
                  "ols_column_count": 3, "filter_kind": "mixed"}
    assert len(weird["role_name"]) <= parser.MAX_ROLE_NAME and "\x01" not in weird["role_name"]
    assert (weird["role_permission"], weird["reads_data"], weird["filter_kind"]) == ("other", False, "unknown")
    assert (weird["row_filter_table_count"], weird["dynamic_filter_table_count"], weird["ols_table_count"]) == (0, 0, 0)
    assert rules.model_roles(None) == [] and rules.model_roles({"security": {}}) == []
    assert rules.model_state({"id": M1, "security": security(rls=1)})["role_detail"] == "not_collected"


def test_role_detail_never_changes_the_rls_or_ols_verdict():
    with_roles = {"id": M1, "workspaceId": WS, "security": {**security(rls=1, ols_t=1), "roles": []}}
    without = {"id": M1, "workspaceId": WS, "security": security(rls=1, ols_t=1)}
    keys = ("rls", "ols", "inspection", "rls_filter_count", "ols_table_count")
    assert {k: rules.model_state(with_roles)[k] for k in keys} == {k: rules.model_state(without)[k] for k in keys}


# --- Gold ------------------------------------------------------------------------------------------------

def gold(tmp_path, monkeypatch, models):
    from reports.gold_layer import build_gold
    from tests import _far_surfaces as surfaces
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_dir = surfaces.raw(tmp_path, monkeypatch)
    (raw_dir / "semantic_model_definitions.json").write_text(json.dumps({"models": models}), encoding="utf-8")
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    monkeypatch.setenv("FAR_CONFIG_SNAPSHOT", str(capture(snapshot_dir, configured())))
    return surfaces, build_gold([], raw_dir, run_id="review", run_timestamp="2026-09-01T00:00:00Z", check_remote=False)


ROLE = {"name": "Regional", "permission": "read", "readsData": True, "rowFilterTables": 2, "dynamicFilterTables": 1,
        "constantFilterTables": 0, "hiddenTables": 1, "hiddenColumns": 3, "filterKind": "mixed"}


def test_gold_lists_each_role_and_links_it_to_its_model_by_key(tmp_path, monkeypatch):
    from tests import _far_surfaces as surfaces
    admin = {**ROLE, "name": "Admins", "permission": "administrator", "readsData": False, "rowFilterTables": 0,
             "dynamicFilterTables": 0, "hiddenTables": 0, "hiddenColumns": 0, "filterKind": "not_applicable"}
    _, tables = gold(tmp_path, monkeypatch, [{
        "id": surfaces.MID, "workspaceId": surfaces.WID, "metadataOnly": True, "storageModes": ["Import"], "parts": [],
        "security": {**security(rls=1, ols_t=1), "roles": [ROLE, admin]}, "effectiveIdentityRolesRequired": False}])
    [model_row] = tables["gold_model_security"]
    roles = tables["gold_model_security_roles"]
    assert model_row["role_detail"] == "collected"
    assert [r["role_name"] for r in roles] == ["Regional", "Admins"]
    assert model_row["model_key"] and {r["model_key"] for r in roles} == {model_row["model_key"]}
    regional = roles[0]
    assert (regional["role_permission"], regional["filter_kind"], regional["reads_data"]) == ("read", "mixed", True)
    assert (regional["row_filter_table_count"], regional["dynamic_filter_table_count"], regional["ols_table_count"],
            regional["ols_column_count"]) == (2, 1, 1, 3)
    assert (regional["model_name"], regional["capacity_name"], regional["workspace_id"]) == ("Model", "Hosting", surfaces.WID)
    assert roles[1]["filter_kind"] == "not_applicable" and roles[1]["reads_data"] is False
    # The observed-posture inventory the Lakehouse exposes never carries roles.
    assert not {key for key in tables["gold_semantic_models"][0] if "role" in key}


def test_gold_has_no_role_rows_and_says_not_collected_when_role_detail_was_off(tmp_path, monkeypatch):
    from tests import _far_surfaces as surfaces
    _, tables = gold(tmp_path, monkeypatch, [{
        "id": surfaces.MID, "workspaceId": surfaces.WID, "metadataOnly": True, "storageModes": ["Import"], "parts": [],
        "security": security(rls=1), "effectiveIdentityRolesRequired": False}])
    assert tables["gold_model_security"][0]["role_detail"] == "not_collected"
    assert tables["gold_model_security_roles"] == []


def test_a_model_with_no_roles_is_collected_with_no_rows_and_an_unreadable_one_is_unavailable(tmp_path, monkeypatch):
    from tests import _far_surfaces as surfaces
    _, tables = gold(tmp_path, monkeypatch, [{
        "id": surfaces.MID, "workspaceId": surfaces.WID, "metadataOnly": True, "storageModes": ["Import"], "parts": [],
        "security": {**security(), "roles": []}, "effectiveIdentityRolesRequired": False}])
    assert tables["gold_model_security"][0]["role_detail"] == "collected" and tables["gold_model_security_roles"] == []
    _, tables = gold(tmp_path / "again", monkeypatch, [{
        "id": surfaces.MID, "workspaceId": surfaces.WID, "error": "http_403",
        "security": {**security(inspection="unavailable", reason="definition_unreadable"), "roles": []}}])
    assert tables["gold_model_security"][0]["role_detail"] == "unavailable"


def test_the_one_side_of_the_relationship_is_unique_and_every_role_belongs_to_a_listed_model(tmp_path, monkeypatch):
    from tests import _far_surfaces as surfaces
    from reports.powerbi.schema import ONTOLOGY_KEYS
    definition = {"id": surfaces.MID, "workspaceId": surfaces.WID, "metadataOnly": True, "storageModes": ["Import"],
                  "parts": [], "security": {**security(rls=1), "roles": [ROLE]}, "effectiveIdentityRolesRequired": False}
    _, tables = gold(tmp_path, monkeypatch, [definition])
    keys = [row["model_key"] for row in tables["gold_model_security"]]
    assert len(keys) == len(set(keys)) and ONTOLOGY_KEYS["gold_model_security"] == ONTOLOGY_KEYS["gold_model_security_roles"]
    assert {row["model_key"] for row in tables["gold_model_security_roles"]} <= set(keys)


# --- central semantic model and report ---------------------------------------------------------------------

def test_the_model_relates_roles_to_their_model_and_the_run_reaches_them_through_it():
    from reports.powerbi.semantic_model import build_bim
    relationships = build_bim("m", "s", "d")["model"]["relationships"]
    touching = [r for r in relationships if "gold_model_security_roles" in (r["fromTable"], r["toTable"])]
    assert len(touching) == 1
    only = touching[0]
    assert (only["fromTable"], only["fromColumn"], only["toTable"], only["toColumn"]) == (
        "gold_model_security_roles", "model_key", "gold_model_security", "model_key")
    assert only["crossFilteringBehavior"] == "oneDirection"
    # One path from the run to the roles: no direct run relationship, so Power BI keeps every relationship active.
    assert any(r["fromTable"] == "gold_model_security" and r["toTable"] == "gold_run_summary" for r in relationships)
    names = [r["name"] for r in relationships]
    assert len(names) == len(set(names))
    tables = {t["name"]: t for t in build_bim("m", "s", "d")["model"]["tables"]}
    for table in ("gold_model_security", "gold_model_security_roles"):
        assert next(c for c in tables[table]["columns"] if c["name"] == "model_key").get("isHidden") is True


def test_the_role_measures_read_only_the_roles_table_under_the_profile_scope():
    from reports.powerbi.semantic_model import _measures
    by_name = {m["name"]: m["expression"] for m in _measures()}
    for name in ("Roles Listed", "Static Roles", "Dynamic Roles", "Roles Without Row Filter", "Administrator Roles"):
        assert "COUNTROWS(gold_model_security_roles)" in by_name[name]
        assert "gold_model_security_roles[profile_sha256]" in by_name[name]
    assert '"dynamic", "mixed"' in by_name["Dynamic Roles"]
    assert 'filter_kind] IN {"none", "constant"}' in by_name["Roles Without Row Filter"]
    assert "reads_data" in by_name["Roles Without Row Filter"]


def test_the_central_report_has_one_hidden_drill_through_page_for_the_model_roles():
    from reports.powerbi.report import _pages
    pages = _pages()
    drill = [p for p in pages if p.get("drill")]
    assert [p["display"] for p in drill] == ["Model roles"] and pages[-1] is drill[0]
    page = drill[0]
    assert page["drill"]["field"]["Column"]["Property"] == "model_id"
    assert page["drill"]["field"]["Column"]["Expression"]["SourceRef"]["Entity"] == "gold_model_security"
    open_filters = [f for f in page["filters"] if "filter" not in f]
    assert [f["name"] for f in open_filters] == [page["drill"]["filter"]]
    text = json.dumps(page["visuals"])
    assert "gold_model_security_roles" in text and "Roles Listed" in text and '"Back"' in text.replace("'", '"')
    source = next(p for p in pages if p["display"] == "Model security")
    source_text = json.dumps(source["visuals"])
    assert '"model_id"' in source_text and "role_detail" in source_text


# --- owner, agents, approved tables --------------------------------------------------------------------------

def test_the_owner_projection_lists_the_roles_of_the_owners_own_models_only():
    from reports.owner.gold import build_owner_gold
    from tests import _far_surfaces as surfaces
    source = surfaces.owner_input()
    other = "99999999-9999-4999-8999-999999999999"
    base = {**surfaces.META, "model_name": "Model", "role_permission": "read", "reads_data": True, "filter_kind": "dynamic",
            "row_filter_table_count": 2, "dynamic_filter_table_count": 2, "ols_table_count": 1, "ols_column_count": 3}
    source["gold_model_security_roles"] = [
        {**base, "workspace_id": surfaces.WID, "model_id": surfaces.MID, "role_name": "Regional"},
        {**base, "workspace_id": surfaces.WID, "model_id": surfaces.MID, "role_name": "Other role", "filter_kind": "none",
         "row_filter_table_count": 0, "dynamic_filter_table_count": 0},
        {**base, "workspace_id": other, "model_id": other, "role_name": "Elsewhere", "model_name": "Elsewhere"}]
    details = [d for d in build_owner_gold(source)["owner_details"] if d["detail_type"] == "model_security_role"]
    assert sorted(d["detail"] for d in details) == [
        "Role Other role: permission read; tables with a row filter 0 (0 use the user's identity); hidden tables 1; hidden columns 3; filter none",
        "Role Regional: permission read; tables with a row filter 2 (2 use the user's identity); hidden tables 1; hidden columns 3; filter dynamic"]
    assert {d["item_id"] for d in details} == {surfaces.MID} and len({d["detail_key"] for d in details}) == 2
    assert not any("Elsewhere" in json.dumps(d) for d in details)
    # Without the table (role detail off) the projection is exactly what it was.
    del source["gold_model_security_roles"]
    assert not [d for d in build_owner_gold(source)["owner_details"] if d["detail_type"] == "model_security_role"]


def test_agents_and_chat_explain_role_detail_and_the_secured_model_alone_exposes_the_table():
    from reports.agent.data_agent import DEFAULT_AI_INSTRUCTIONS
    from reports.agent.owner_agent import owner_instructions
    from reports.agent.sdk_deploy import APPROVED_GOLD_TABLES, _LAKEHOUSE_TABLES
    from reports.powerbi.schema import KEY_COLUMNS
    central, owner = " ".join(DEFAULT_AI_INSTRUCTIONS.split()), " ".join(owner_instructions("1").split())
    assert "gold_model_security_roles" in central and "not_collected" in central and "never stored" in central
    assert 'detail starts with "Role "' in owner and "never say a model has no roles" in owner
    assert len(owner_instructions("1")) <= 15000
    assert "gold_model_security_roles" in APPROVED_GOLD_TABLES and "gold_model_security_roles" not in _LAKEHOUSE_TABLES
    assert "model_key" in KEY_COLUMNS
    from pathlib import Path
    chat = (Path(__file__).resolve().parents[1] / "fabric" / "app" / "src" / "lib" / "native-agent-grounding.ts").read_text(encoding="utf-8")
    assert "Role detail is optional" in chat and "never say a model has no roles" in chat


def test_the_role_table_contract_stores_names_and_counts_only():
    from reports.powerbi.schema import GOLD_TABLES_BY_NAME
    columns = {c.name for c in GOLD_TABLES_BY_NAME["gold_model_security_roles"].columns}
    assert {"role_name", "role_permission", "reads_data", "filter_kind", "row_filter_table_count",
            "dynamic_filter_table_count", "constant_filter_table_count", "ols_table_count", "ols_column_count"} <= columns
    assert not {c for c in columns if any(word in c for word in ("expression", "filter_text", "member", "table_name", "column_name"))}


# --- rows written before the key existed ------------------------------------------------------------------------

def test_the_backfill_fills_only_rows_without_a_key_with_the_value_new_rows_get():
    import sqlite3
    from reports.powerbi.schema import key_backfill, ontology_key
    [(key, condition, value)] = key_backfill("gold_model_security")
    assert key == "model_key"
    db = sqlite3.connect(":memory:")
    try:
        db.execute("SELECT concat_ws('|', 'a', 'b')")
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no concat_ws")
    db.execute("CREATE TABLE t (run_id TEXT, model_id TEXT, model_key TEXT)")
    db.executemany("INSERT INTO t VALUES (?, ?, ?)", [
        ("r1", "m1", None), ("r1", "m2", "already|set"), ("r2", "m1", None),
        ("r3", "", None), ("r3", None, None), ("", "m1", None), (None, "m1", None)])
    db.execute(f"UPDATE t SET {key} = {value} WHERE {condition}")
    rows = {(run, model): k for run, model, k in db.execute("SELECT run_id, model_id, model_key FROM t")}
    assert rows[("r1", "m1")] == ontology_key("r1", "m1") == "r1|m1"
    assert rows[("r2", "m1")] == ontology_key("r2", "m1")
    assert rows[("r1", "m2")] == "already|set"
    # A row whose key cannot be built stays blank, exactly as a new row would.
    assert [rows[k] for k in (("r3", ""), ("r3", None), ("", "m1"), (None, "m1"))] == [None] * 4
    assert ontology_key("r3", "") is None and ontology_key("", "m1") is None


def test_only_the_key_a_model_relationship_depends_on_is_backfilled():
    from reports.powerbi.schema import BACKFILLED_KEY_TABLES, ONTOLOGY_KEYS, key_backfill
    assert BACKFILLED_KEY_TABLES == ("gold_model_security",)
    assert all(not key_backfill(table) for table in ONTOLOGY_KEYS if table not in BACKFILLED_KEY_TABLES)
    assert key_backfill("gold_model_security_roles") == [] and key_backfill("unknown") == []


@pytest.mark.parametrize("notebook", ["setup.ipynb", "setup_private_far_DISPOSABLE.ipynb"])
def test_setup_fills_the_key_after_adding_the_column_and_before_the_model_is_deployed(notebook):
    from orchestration.review_config import ROOT
    path = ROOT / "fabric" / notebook
    if not path.exists():
        pytest.skip("private notebook is not part of a clean checkout")
    text = path.read_text(encoding="utf-8")
    assert text.index("mergeSchema") < text.index("key_backfill(_t.name)") < text.index("DeltaTable.forPath")
    assert "from delta.tables import DeltaTable" in text and "import GOLD_TABLES, key_backfill" in text
    assert text.index("key_backfill(_t.name)") < text.index('upsert_definition(wid, \\"reports\\"')

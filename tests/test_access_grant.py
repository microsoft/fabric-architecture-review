# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Temporary workspace access: it is scoped, never touches existing roles, and is always removed."""
import base64
import json

import pytest

from collectors import access_grant
from collectors.access_grant import (
    AccessGrant, AccessGrantError, AdminApi, Ledger, Principal, Settings, principal_from_token, settings_from_environment,
)
from orchestration.environment import (
    access_grant_environment, fabric_access_grant, validate_access_grant,
)

WS, WS2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
M1, M2, M3 = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002", "aaaaaaaa-0000-4000-8000-000000000003"
OID = "99999999-9999-4999-8999-999999999999"
UPN = "reviewer@contoso.com"


def jwt(**claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


class Provider:
    def __init__(self, token):
        self.token = token
        self.scopes = []

    def get_token(self, scope):
        self.scopes.append(scope)
        return self.token

    def headers(self, scope):
        return {"Authorization": "x"}


class Api:
    """Records the administrator calls; the ledger must already list a grant when it is made.

    ``present`` tracks whether the identity holds a role, so a status that is not a success can still be applied
    (``applied``) and the workspace's users can be read (or not, with ``list_error``).
    """

    def __init__(self, ledger, add=200, remove=200, present=False, applied=False, list_error=False):
        self.ledger, self.calls, self.add_status, self.remove_status = ledger, [], add, remove
        self.present, self.applied, self.list_error = present, applied, list_error

    def add(self, workspace_id, principal, role):
        self.calls.append(("add", workspace_id, role))
        assert any(e["workspace_id"] == workspace_id and e["role"] == role for e in self.ledger.entries()), \
            "a grant must be written to the ledger before it is made"
        if self.add_status is None:
            self.present = self.present or self.applied
            raise ConnectionError("lost the response")
        if 200 <= self.add_status < 300 or self.applied:
            self.present = True
        return self.add_status

    def remove(self, workspace_id, principal):
        self.calls.append(("remove", workspace_id))
        if 200 <= self.remove_status < 300:
            self.present = False
        return self.remove_status

    def listed(self, workspace_id, principal):
        self.calls.append(("listed", workspace_id))
        if self.list_error:
            raise RuntimeError("cannot read users")
        return self.present


def make(tmp_path, settings=None, token=None, add=200, remove=200, delays=(0, 0), **api_options):
    sleeps = []
    ledger_path = tmp_path / "ledger.json"
    provider = Provider(token or jwt(upn=UPN, oid=OID))
    holder = {}

    def api_factory(_provider):
        holder["api"] = Api(Ledger(ledger_path), add=add, remove=remove, **api_options)
        return holder["api"]

    grant = AccessGrant(settings or Settings(True, "auto"), ledger_path, provider_factory=lambda: provider,
                        sleep=sleeps.append, delays=delays, api_factory=api_factory)
    return grant, holder, sleeps, provider


def model(model_id, workspace=WS, error="http_403"):
    row = {"id": model_id, "workspaceId": workspace, "name": "m", "workspaceName": "w", "targetStorageMode": None,
           "effectiveIdentityRolesRequired": None}
    if error:
        row["error"] = error
    return row


def definitions(tmp_path, *rows):
    path = tmp_path / "semantic_model_definitions.json"
    path.write_text(json.dumps({"models": list(rows), "errors": sum(1 for r in rows if r.get("error")),
                                "status": "completed", "collectionComplete": False}), encoding="utf-8")
    return path


def workspace(wid=WS, users=(), status="collected"):
    return {"id": wid, "usersCollectionStatus": status, "users": list(users)}


def readable(row):
    return {k: v for k, v in row.items() if k != "error"} | {"security": {"inspection": "complete"}}


def declared(*ids):
    return frozenset(ids)


# --- settings ---------------------------------------------------------------------------------

@pytest.mark.parametrize("values, expected", [
    ({"enabled": "true", "role": "auto"}, {"enabled": True, "role": "auto"}),
    ({"enabled": " TRUE ", "role": "contributor"}, {"enabled": True, "role": "Contributor"}),
    ({"enabled": "", "role": ""}, {"enabled": False, "role": "auto"}),
    ({"enabled": False, "role": None}, {"enabled": False, "role": "auto"}),
    ({"enabled": "false", "role": "MEMBER"}, {"enabled": False, "role": "Member"}),
])
def test_settings_are_normalized(values, expected):
    assert validate_access_grant(values) == expected


@pytest.mark.parametrize("values", [{"enabled": "yes", "role": "auto"}, {"enabled": "true", "role": "Admin"},
                                    {"enabled": "true", "role": "Viewer"}, {"enabled": "true"}, {"role": "auto"}])
def test_unrecognized_settings_stop_the_run_instead_of_being_guessed(values):
    with pytest.raises(ValueError):
        validate_access_grant(values)


def test_an_environment_library_from_before_the_setting_means_disabled():
    class Library:
        def getVariable(self, name):
            raise KeyError(name)
    assert fabric_access_grant(lambda _: Library(), "Env") == {"enabled": False, "role": "auto"}

    class Set:
        def getVariable(self, name):
            return {"access_grant__enabled": "true", "access_grant__role": "Member"}[name]
    settings = fabric_access_grant(lambda _: Set(), "Env")
    assert access_grant_environment(settings) == {"ACCESS_GRANT_ENABLED": "true", "ACCESS_GRANT_ROLE": "Member"}
    assert settings_from_environment(access_grant_environment(settings)) == Settings(True, "Member")
    assert settings_from_environment({}) == Settings(False, "auto")


def test_the_default_deployment_has_the_setting_off():
    from orchestration.review_config import ROOT, read_yaml
    assert read_yaml(ROOT / "config" / "environment.yaml")["access_grant"] == {"enabled": "false", "role": "auto"}


# --- who is granted ------------------------------------------------------------------------------

def test_the_principal_is_read_from_the_token_claims():
    assert principal_from_token(jwt(upn="Reviewer@Contoso.com", oid=OID)) == Principal("User", UPN, OID)
    assert principal_from_token(jwt(preferred_username=UPN)) == Principal("User", UPN, None)
    assert principal_from_token(jwt(oid=OID.upper(), idtyp="app")) == Principal("App", OID, OID)
    for bad in ("not-a-token", "a.b.c", jwt(foo="bar"), ""):
        assert principal_from_token(bad) is None


def test_the_request_bodies_and_the_encoded_removal_path(monkeypatch):
    assert Principal("User", UPN).body("Contributor") == {"emailAddress": UPN, "groupUserAccessRight": "Contributor"}
    assert Principal("App", OID).body("Member") == {"identifier": OID, "principalType": "App", "groupUserAccessRight": "Member"}
    seen = []

    class Response:
        status_code = 200

    monkeypatch.setattr(access_grant, "request", lambda *args, **kwargs: seen.append((args, kwargs)) or Response())
    api = AdminApi(Provider(jwt(upn=UPN)))
    api.add(WS, Principal("App", OID), "Contributor")
    api.remove(WS, Principal("User", "john_contoso.com#EXT#@test.com"))
    (post, post_kwargs), (delete, _) = seen
    assert post[:2] == ("POST", f"https://api.powerbi.com/v1.0/myorg/admin/groups/{WS}/users")
    assert post_kwargs["json_body"]["principalType"] == "App"
    assert delete[:2] == ("DELETE", f"https://api.powerbi.com/v1.0/myorg/admin/groups/{WS}/users/john_contoso.com%23EXT%23@test.com")


# --- grant, read, remove -------------------------------------------------------------------------

def test_a_refused_declared_model_is_read_after_a_contributor_grant_and_the_grant_is_removed(tmp_path):
    grant, holder, sleeps, provider = make(tmp_path)
    path = definitions(tmp_path, model(M1), model(M2, workspace=WS2), model(M3, error=None))
    grant.remediate(path, declared(M1), [workspace(WS), workspace(WS2)], readable)
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]
    assert Ledger(tmp_path / "ledger.json").entries() == []
    payload = json.loads(path.read_text())
    by_id = {m["id"]: m for m in payload["models"]}
    assert "error" not in by_id[M1] and by_id[M2]["error"] == "http_403"
    assert (payload["errors"], payload["collectionComplete"]) == (1, False)
    assert grant.audit == [{"workspace_id": WS, "model_count": 1, "role_used": "Contributor", "removed": True,
                            "status": "read_after_grant"}]
    assert grant.failures == [] and sleeps == []


def test_every_refused_model_resolved_makes_the_collection_complete_again(tmp_path):
    grant, _, _, _ = make(tmp_path)
    path = definitions(tmp_path, model(M1))
    grant.remediate(path, declared(M1), [workspace()], readable)
    payload = json.loads(path.read_text())
    assert (payload["errors"], payload["collectionComplete"]) == (0, True)


def test_member_is_tried_only_when_contributor_is_not_enough(tmp_path):
    grant, holder, sleeps, _ = make(tmp_path, delays=(3, 7))

    def refetch(row):
        # The read only works once the identity holds Member.
        return readable(row) if ("add", WS, "Member") in holder["api"].calls else row

    path = definitions(tmp_path, model(M1))
    grant.remediate(path, declared(M1), [workspace()], refetch)
    assert holder["api"].calls == [
        ("listed", WS), ("add", WS, "Contributor"), ("remove", WS), ("add", WS, "Member"), ("remove", WS),
    ]
    assert sleeps == [3, 7]
    assert grant.audit[0]["role_used"] == "Member" and grant.audit[0]["removed"] is True
    assert Ledger(tmp_path / "ledger.json").entries() == []


def test_a_single_configured_role_is_never_escalated(tmp_path):
    grant, holder, _, _ = make(tmp_path, settings=Settings(True, "Contributor"))
    path = definitions(tmp_path, model(M1))
    grant.remediate(path, declared(M1), [workspace()], lambda row: row)
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]
    assert grant.audit[0]["status"] == "still_refused"
    assert json.loads(path.read_text())["models"][0]["error"] == "http_403"


def test_a_propagation_delay_is_retried_before_giving_up_on_a_role(tmp_path):
    grant, holder, sleeps, _ = make(tmp_path, delays=(5, 15, 30))
    attempts = []

    def refetch(row):
        attempts.append(1)
        return readable(row) if len(attempts) == 3 else row

    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], refetch)
    assert sleeps == [5, 15]
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]


def test_an_existing_role_is_never_changed_or_removed(tmp_path):
    grant, holder, _, provider = make(tmp_path, present=True)
    users = [{"principalType": "User", "identifier": UPN.upper(), "groupUserAccessRight": "Viewer"}]
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace(users=users)], readable)
    assert holder["api"].calls == [("listed", WS)]
    assert grant.audit[0]["status"] == "role_already_present"
    by_object = [{"graphId": OID.upper(), "groupUserAccessRight": "Viewer"}]
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace(users=by_object)], readable)
    assert holder["api"].calls == [("listed", WS), ("listed", WS)]
    assert grant.audit[1]["status"] == "role_already_present"


def test_unknown_membership_or_identity_is_never_guessed(tmp_path):
    grant, holder, _, _ = make(tmp_path)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace(status="unavailable")], readable)
    assert grant.audit[0]["status"] == "membership_unknown" and "api" not in holder
    grant, holder, _, _ = make(tmp_path, token="opaque")
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert grant.audit[0]["status"] == "identity_unresolved" and "api" not in holder


def test_nothing_happens_when_disabled_nothing_is_declared_or_nothing_was_refused(tmp_path):
    for settings, ids, rows in ((Settings(False, "auto"), declared(M1), [model(M1)]), (Settings(True, "auto"), declared(), [model(M1)]),
                                (Settings(True, "auto"), declared(M1), [model(M1, error=None)]),
                                (Settings(True, "auto"), declared(M1), [model(M1, error="http_500")]),
                                (Settings(True, "auto"), declared(M2), [model(M1)])):
        grant, holder, _, provider = make(tmp_path, settings=settings)
        grant.remediate(definitions(tmp_path, *rows), ids, [workspace()], readable)
        assert "api" not in holder and grant.audit == [] and provider.scopes == []


def test_only_refused_codes_are_remediated_and_404_counts_as_refused(tmp_path):
    grant, holder, _, _ = make(tmp_path)
    grant.remediate(definitions(tmp_path, model(M1, error="http_404")), declared(M1), [workspace()], readable)
    assert holder["api"].calls[:2] == [("listed", WS), ("add", WS, "Contributor")]


def test_a_refused_grant_leaves_nothing_behind_and_the_model_stays_missing_evidence(tmp_path):
    grant, holder, _, _ = make(tmp_path, add=403)
    path = definitions(tmp_path, model(M1))
    grant.remediate(path, declared(M1), [workspace()], readable)
    # The refusal is confirmed against the workspace's users before the ledger entry is dropped.
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("listed", WS)]
    assert grant.audit[0]["status"] == "grant_refused_http_403" and grant.audit[0]["removed"] is None
    assert Ledger(tmp_path / "ledger.json").entries() == [] and grant.failures == []
    assert json.loads(path.read_text())["models"][0]["error"] == "http_403"


@pytest.mark.parametrize("status", [503, 409, 400, None])
def test_a_grant_that_was_applied_despite_a_failing_answer_is_still_removed(tmp_path, status):
    # The request is retried on a network error or a 5xx; the retry can answer "already a member".
    grant, holder, _, _ = make(tmp_path, add=status, applied=True)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert holder["api"].calls[-1] == ("remove", WS) and holder["api"].present is False
    assert Ledger(tmp_path / "ledger.json").entries() == [] and grant.failures == []
    assert grant.audit[0]["removed"] is True


def test_when_the_workspace_users_cannot_be_read_a_refused_grant_is_treated_as_possibly_applied(tmp_path, monkeypatch):
    grant, holder, _, _ = make(tmp_path, add=409, applied=True)
    api = grant._admin()
    original_add = api.add

    def add(*args):
        status = original_add(*args)
        api.list_error = True
        return status

    monkeypatch.setattr(api, "add", add)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert ("remove", WS) in holder["api"].calls and Ledger(tmp_path / "ledger.json").entries() == []


def test_a_removal_answered_with_an_error_is_accepted_only_when_the_identity_is_confirmed_gone(tmp_path):
    grant, holder, _, _ = make(tmp_path, remove=400)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert grant.failures == [WS]                      # still listed: the removal is not believed
    holder["api"].present = False
    grant.failures.clear()
    grant.finish()
    assert grant.failures == [] and Ledger(tmp_path / "ledger.json").entries() == []


def test_the_not_found_error_the_collector_really_returns_counts_as_a_refusal(tmp_path):
    grant, holder, _, _ = make(tmp_path)
    grant.remediate(definitions(tmp_path, model(M1, error="not_found")), declared(M1), [workspace()],
                    lambda row: {**row, "error": "not_found"} if ("add", WS, "Member") not in holder["api"].calls else readable(row))
    assert [c for c in holder["api"].calls if c[0] == "add"] == [("add", WS, "Contributor"), ("add", WS, "Member")]
    assert grant.audit[0]["status"] == "read_after_grant"


def test_only_the_workspace_limit_of_grants_is_made_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(access_grant, "MAX_WORKSPACES", 1)
    grant, holder, _, _ = make(tmp_path)
    path = definitions(tmp_path, model(M1), model(M2, workspace=WS2))
    grant.remediate(path, declared(M1, M2), [workspace(WS), workspace(WS2)], readable)
    assert [row["status"] for row in grant.audit] == ["read_after_grant", "deferred_run_limit"]
    assert [call for call in holder["api"].calls if call[0] == "add"] == [("add", WS, "Contributor")]


# --- failure and interruption ----------------------------------------------------------------------

def test_an_error_while_reading_still_removes_the_access(tmp_path):
    grant, holder, _, _ = make(tmp_path)

    def explode(row):
        raise RuntimeError("secret detail that must not be kept")

    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], explode)
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]
    assert grant.audit[0]["status"] == "grant_error" and grant.audit[0]["removed"] is True
    assert "secret detail" not in json.dumps(grant.audit) and Ledger(tmp_path / "ledger.json").entries() == []


def test_an_interrupt_still_removes_the_access_and_is_not_swallowed(tmp_path):
    grant, holder, _, _ = make(tmp_path)

    def interrupted(row):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], interrupted)
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]
    assert Ledger(tmp_path / "ledger.json").entries() == []


def test_a_removal_that_fails_is_recorded_kept_in_the_ledger_and_retried(tmp_path):
    grant, holder, _, _ = make(tmp_path, remove=500)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert grant.failures == [WS] and grant.audit[0]["removed"] is False
    assert [e["workspace_id"] for e in Ledger(tmp_path / "ledger.json").entries()] == [WS]
    holder["api"].remove_status = 200
    grant.failures.clear()
    grant.finish()
    assert grant.failures == [] and Ledger(tmp_path / "ledger.json").entries() == []


def test_a_killed_run_is_cleaned_up_by_the_next_one_even_when_the_setting_is_off(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.add(WS, Principal("User", UPN, OID), "Contributor")
    ledger.add(WS2, Principal("App", OID, OID), "Member")
    grant, holder, _, _ = make(tmp_path, settings=Settings(False, "auto"))
    grant.recover()
    assert holder["api"].calls == [("remove", WS), ("remove", WS2)]
    assert ledger.entries() == [] and grant.failures == []
    assert [r["status"] for r in grant.audit] == ["leftover_removed", "leftover_removed"]


def test_a_leftover_that_is_already_gone_counts_as_removed_and_one_that_fails_is_kept(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.add(WS, Principal("User", UPN, OID), "Contributor")
    grant, _, _, _ = make(tmp_path, remove=404)
    grant.recover()
    assert ledger.entries() == [] and grant.failures == []
    ledger.add(WS, Principal("User", UPN, OID), "Contributor")
    grant, _, _, _ = make(tmp_path, remove=500, present=True)
    grant.recover()
    assert [e["workspace_id"] for e in ledger.entries()] == [WS] and grant.failures == [WS]
    assert grant.audit[0]["status"] == "leftover_removal_failed"


def test_a_leftover_for_another_identity_is_never_hidden_by_a_new_grant(tmp_path):
    other = Principal("User", "someone.else@contoso.com", None)
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.add(WS, other, "Member")
    grant, holder, _, _ = make(tmp_path, remove=500, present=True)
    grant.recover()
    assert grant.failures == [WS]
    holder["api"].remove_status = 200
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    # The new identity's grant was removed on its own; the other identity's entry is still there.
    assert [(e["workspace_id"], e["identifier"]) for e in ledger.entries()] == [(WS, other.identifier)]


def test_with_an_empty_ledger_recovery_makes_no_call_and_needs_no_credentials(tmp_path):
    grant, holder, _, provider = make(tmp_path)
    grant.recover()
    grant.finish()
    assert "api" not in holder and provider.scopes == [] and grant.audit == []


def test_an_unreadable_ledger_blocks_new_grants_and_fails_the_run(tmp_path):
    (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
    grant, holder, _, _ = make(tmp_path)
    grant.recover()
    assert grant.failures == ["ledger"]
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert "api" not in holder
    with pytest.raises(AccessGrantError):
        Ledger(tmp_path / "ledger.json").entries()


def test_the_ledger_keeps_one_entry_per_workspace_and_identity(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.add(WS, Principal("User", UPN), "Contributor")
    ledger.add(WS, Principal("User", UPN.upper()), "Member")
    assert [e["role"] for e in ledger.entries()] == ["Member"]
    ledger.add(WS, Principal("App", OID), "Contributor")
    assert len(ledger.entries()) == 2
    ledger.clear(WS, Principal("User", UPN))
    assert [e["identifier"] for e in ledger.entries()] == [OID]


# --- the audit and the collection -------------------------------------------------------------------

def test_the_audit_holds_statuses_and_counts_but_never_an_identity(tmp_path):
    grant, _, _, _ = make(tmp_path)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    grant.write_audit(tmp_path)
    text = (tmp_path / "access_grants.json").read_text()
    summary = json.loads(text)
    assert (summary["granted_count"], summary["removed_count"], summary["removal_failed_count"]) == (1, 1, 0)
    assert UPN not in text and OID not in text


def test_no_audit_file_is_written_when_the_setting_is_off_and_nothing_happened(tmp_path):
    quiet, _, _, _ = make(tmp_path, settings=Settings(False, "auto"))
    quiet.write_audit(tmp_path)
    assert not (tmp_path / "access_grants.json").exists()


def test_a_removal_failure_fails_the_collection_so_it_is_noticed(tmp_path, monkeypatch):
    from collectors import far_collection
    monkeypatch.setenv("ACCESS_GRANT_ENABLED", "true")
    monkeypatch.setenv("ACCESS_GRANT_LEDGER", str(tmp_path / "ledger.json"))
    raw = tmp_path / "raw"

    def pretend(output_dir, grant):
        grant.failures.append(WS)
        return raw / "collection_plan.json"

    monkeypatch.setattr(far_collection, "_collect", pretend)
    with pytest.raises(RuntimeError, match="could not be removed"):
        far_collection.collect(raw)
    assert json.loads((raw / "access_grants.json").read_text())["removal_failed_count"] == 1


def test_an_invalid_setting_stops_the_collection_before_anything_is_changed(tmp_path, monkeypatch):
    from collectors import far_collection
    monkeypatch.setenv("ACCESS_GRANT_ENABLED", "maybe")
    monkeypatch.delenv(far_collection.RUN_SCOPE_ENV, raising=False)
    monkeypatch.setattr(far_collection, "_collect", lambda *_: pytest.fail("must not collect"))
    with pytest.raises(ValueError, match="true or false"):
        far_collection.collect(tmp_path / "raw")
    import os
    assert far_collection.RUN_SCOPE_ENV not in os.environ


def test_a_clean_collection_returns_its_plan_and_leaves_no_audit_when_the_setting_is_off(tmp_path, monkeypatch):
    from collectors import far_collection
    monkeypatch.delenv("ACCESS_GRANT_ENABLED", raising=False)
    monkeypatch.setenv("ACCESS_GRANT_LEDGER", str(tmp_path / "ledger.json"))
    raw = tmp_path / "raw"
    monkeypatch.setattr(far_collection, "_collect", lambda output_dir, grant: raw / "collection_plan.json")
    assert far_collection.collect(raw) == raw / "collection_plan.json"
    assert not (raw / "access_grants.json").exists()


def test_the_definition_producer_re_reads_only_through_the_grant(tmp_path, monkeypatch):
    from collectors import far_collection, semantic_model_definitions
    path = definitions(tmp_path, model(M1))
    monkeypatch.setattr(semantic_model_definitions, "collect", lambda output_dir: path)
    calls = []

    class Grant:
        def remediate(self, definitions_path, ids, workspaces, refetch):
            calls.append((definitions_path, ids, workspaces, refetch))

    result = far_collection._definitions_with_access(tmp_path, Grant(), declared(M1), [workspace()])
    assert result == path and calls[0][3] is semantic_model_definitions.refetch_model


@pytest.mark.parametrize("enabled", [False, True])
def test_scoped_admin_discovers_inaccessible_models_and_only_grants_for_declared_models(tmp_path, monkeypatch, enabled):
    from collectors import far_collection, semantic_models, semantic_model_definitions
    from tests.test_model_security import tmsl

    monkeypatch.setenv("WORKSPACE_IDS", WS)
    grant, holder, _, provider = make(tmp_path, settings=Settings(enabled, "auto"))
    monkeypatch.setattr(semantic_models, "get_default_provider", lambda: provider)
    monkeypatch.setattr(semantic_model_definitions, "get_default_provider", lambda: provider)
    monkeypatch.setattr(semantic_model_definitions, "MODEL_DELAY_SECONDS", 0)
    monkeypatch.setattr(semantic_models, "collect_workspace_groups", lambda url, _: (
        [{"id": WS}, {"id": WS2}] if url.endswith("/admin/groups") else []
    ))
    discovery = []

    def datasets(url, _):
        discovery.append(url)
        assert url.endswith(f"/admin/groups/{WS}/datasets")
        return [{"id": mid, "name": mid} for mid in (M1, M2)]

    monkeypatch.setattr(semantic_models, "collect_value", datasets)
    catalog = json.loads(semantic_models.collect(tmp_path).read_text())
    assert catalog["adminMode"] is True
    assert [m["id"] for m in catalog["datasets"]] == [M1, M2]
    assert len(discovery) == 1
    definition = tmsl([{"name": "Restricted", "modelPermission": "read", "tablePermissions": [
        {"name": "Sales", "filterExpression": "[Region] = 1"},
        {"name": "Private", "metadataPermission": "none"},
    ]}])
    reads = []

    def get_definition(_, workspace_id, model_id):
        reads.append((workspace_id, model_id))
        api = holder.get("api")
        return (definition, None) if api and api.present else (None, "not_found")

    monkeypatch.setattr(semantic_model_definitions, "_get_definition", get_definition)
    cached = {**workspace(users=[{"identifier": UPN, "groupUserAccessRight": "Admin"}]),
              "usersDataSource": "fuam"}
    path = far_collection._definitions_with_access(tmp_path, grant, declared(M1), [cached])
    grant.finish()
    result = json.loads(path.read_text())
    models = {m["id"]: m for m in result["models"]}
    assert models[M2]["error"] == "not_found"
    assert reads[:2] == [(WS, M1), (WS, M2)]
    if enabled:
        assert reads[2:] == [(WS, M1)]
        assert models[M1]["security"]["inspection"] == "complete"
        assert models[M1]["security"]["rlsFilterCount"] == 1
        assert models[M1]["security"]["olsTableCount"] == 1
        assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]
        assert holder["api"].present is False
        assert Ledger(tmp_path / "ledger.json").entries() == []
        grant.write_audit(tmp_path)
        audit = json.loads((tmp_path / "access_grants.json").read_text())
        assert (audit["granted_count"], audit["removed_count"], audit["removal_failed_count"]) == (1, 1, 0)
    else:
        assert models[M1]["error"] == "not_found"
        assert len(reads) == 2 and "api" not in holder


def test_cached_membership_cannot_hide_a_removed_role(tmp_path):
    grant, holder, _, _ = make(tmp_path)
    cached = {**workspace(users=[{"identifier": UPN, "groupUserAccessRight": "Admin"}]),
              "usersDataSource": "fuam"}
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [cached], readable)
    assert grant.audit[0]["status"] == "read_after_grant"
    assert holder["api"].calls == [("listed", WS), ("add", WS, "Contributor"), ("remove", WS)]


def test_live_membership_protects_a_role_missing_from_cached_inventory(tmp_path):
    grant, holder, _, _ = make(tmp_path, present=True)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert grant.audit[0]["status"] == "role_already_present"
    assert holder["api"].calls == [("listed", WS)]
    assert holder["api"].present is True
    assert Ledger(tmp_path / "ledger.json").entries() == []


def test_live_membership_failure_prevents_any_permission_change(tmp_path):
    grant, holder, _, _ = make(tmp_path, list_error=True)
    grant.remediate(definitions(tmp_path, model(M1)), declared(M1), [workspace()], readable)
    assert grant.audit[0]["status"] == "membership_unknown"
    assert holder["api"].calls == [("listed", WS)]
    assert Ledger(tmp_path / "ledger.json").entries() == []


def test_a_refetched_model_keeps_its_identity_and_dataset_flag(monkeypatch):
    from collectors import semantic_model_definitions as module
    seen = {}

    def fake(headers, workspace_id, model_id):
        seen["call"] = (workspace_id, model_id)
        return None, "http_403"

    monkeypatch.setattr(module, "_get_definition", fake)
    row = {"id": M1, "workspaceId": WS, "name": "Sales", "workspaceName": "W", "targetStorageMode": "Import",
           "effectiveIdentityRolesRequired": True}
    result = module.refetch_model(row, headers=lambda: {})
    assert seen["call"] == (WS, M1)
    assert (result["id"], result["name"], result["error"], result["effectiveIdentityRolesRequired"]) == (M1, "Sales", "http_403", True)

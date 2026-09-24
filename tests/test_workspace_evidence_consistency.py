# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Scanner and REST evidence must produce the same workspace decisions."""
import json
import copy
import sys
from types import SimpleNamespace

import pytest

from analyzers import architecture_review, cost_review, governance_review, operational_excellence_review, security_review
from analyzers.applicability import classify_workspace
from collectors._common import load_workspace_inventory
from collectors.workspace_evidence import merge_workspace_evidence, workspace_items, workspace_users


def write_raw(path, filename, payload):
    (path / filename).write_text(json.dumps(payload), encoding="utf-8")


def finding(module, rule, path, monkeypatch):
    monkeypatch.setattr(module, "load_rules", lambda _: {rule: {"id": rule, "severity": "high"}})
    monkeypatch.setenv("WORKSPACES_CONFIG", str(path / "no-overrides.yaml"))
    return module.analyze(path)[0]


def workspace(**fields):
    return {"id": "ws-a", "name": "engineering-prod", "type": "Workspace", **fields}


@pytest.mark.parametrize("bucket", ["Notebook", "Lakehouse", "DataPipeline", "Warehouse", "Eventhouse", "MLModel"])
def test_native_scanner_items_reach_governance_admin_rule(tmp_path, monkeypatch, bucket):
    write_raw(tmp_path, "scanner.json", {"workspaces": [
        workspace(**{bucket: [{"id": "item-a", "name": "Native item"}]})
    ]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(users=[{"identifier": "owner", "role": "Admin"}], usersCollectionStatus="collected")
    ]})
    result = finding(governance_review, "GOV-001", tmp_path, monkeypatch)
    assert result["status"] == "fail"
    assert result["evidence"]["evaluatedWorkspaces"] == 1
    assert result["evidence"]["underAdminCount"] == 1


@pytest.mark.parametrize("users,status,expected", [
    (None, "unavailable", "missing_evidence"),
    ([], "unavailable", "missing_evidence"),
    ([], "collected", "fail"),
    ([{"identifier": "owner", "role": "Admin"}, {"identifier": "app", "role": "Admin"}], "collected", "pass"),
    ([{"identifier": "owner", "role": "Admin"}, {"identifier": "owner", "role": "Admin"}], "collected", "fail"),
    ([{"identifier": "owner"}], "collected", "missing_evidence"),
])
def test_admin_evidence_distinguishes_unknown_empty_and_duplicate(tmp_path, monkeypatch, users, status, expected):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "item-a", "type": "Notebook"}], users=users, usersCollectionStatus=status)
    ]})
    result = finding(governance_review, "GOV-001", tmp_path, monkeypatch)
    assert result["status"] == expected


def test_valid_roles_survive_unrelated_item_collection_failure(tmp_path, monkeypatch):
    write_raw(tmp_path, "scanner.json", {"workspaces": [
        workspace(Notebook=[{"id": "item-a"}])
    ]})
    write_raw(tmp_path, "workspace_inventory.json", {
        "workspaceListComplete": True, "collectionComplete": False,
        "workspaces": [workspace(
            users=[{"identifier": "owner", "role": "Admin"}, {"identifier": "app", "role": "Admin"}],
            usersCollectionStatus="collected", items=None, itemsCollectionStatus="unavailable",
        )],
    })
    assert finding(governance_review, "GOV-001", tmp_path, monkeypatch)["status"] == "pass"


@pytest.mark.parametrize("module,loader", [
    (architecture_review, "_workspaces_from_scanner_or_inventory"),
    (cost_review, "_workspaces"),
    (governance_review, "_workspaces"),
    (operational_excellence_review, "_workspaces"),
    (security_review, "_workspaces"),
])
def test_analyzers_reconcile_workspace_ids_and_preserve_scanner_metadata(tmp_path, module, loader):
    write_raw(tmp_path, "scanner.json", {"workspaces": [
        workspace(id="WS-A", datasets=[{"id": "MODEL-A", "name": "Model", "endorsementDetails": {"endorsement": "Certified"}}])
    ]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "model-a", "displayName": "Model", "type": "SemanticModel"}], users=[]),
        workspace(id="ws-b", name="other-prod", items=[{"id": "notebook-b", "type": "Notebook"}]),
    ]})
    rows = getattr(module, loader)(tmp_path)
    assert {w["id"].lower() for w in rows} == {"ws-a", "ws-b"}
    assert sum(classify_workspace(rows[0])["itemTypeCounts"].values()) == 1
    assert rows[0]["users"] == []
    assert rows[0]["datasets"][0]["endorsementDetails"]["endorsement"] == "Certified"


def test_child_collectors_include_rest_only_workspaces(tmp_path):
    write_raw(tmp_path, "scanner.json", {"workspaces": [workspace()]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [workspace(id="ws-b")]})
    assert {w["id"] for w in load_workspace_inventory(tmp_path)} == {"ws-a", "ws-b"}


def test_mixed_item_shapes_are_deduplicated_and_empty_flat_list_does_not_hide_scanner():
    ws = workspace(
        Notebook=[{"id": "ITEM-A"}],
        notebooks=[{"id": "item-a"}],
        items=[{"id": "item-a", "type": "Notebook"}, {"id": "item-b", "type": "Lakehouse"}],
    )
    assert architecture_review._item_count(ws) == 2
    assert len(governance_review._items(ws)) == 2
    assert len(cost_review._workspace_items(ws)) == 2
    ws["items"] = []
    assert len(governance_review._items(ws)) == 1


@pytest.mark.parametrize("rule", ["GOV-003", "GOV-008"])
def test_labels_and_endorsement_include_pascalcase_and_flat_items(tmp_path, monkeypatch, rule):
    write_raw(tmp_path, "scanner.json", {"workspaces": [workspace(Lakehouse=[{
        "id": "lake-a", "name": "Lake", "sensitivityLabel": {"labelId": "label-a"},
        "endorsementDetails": {"endorsement": "Certified"},
    }])]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [workspace(items=[
        {"id": "lake-a", "type": "Lakehouse", "displayName": "Lake"},
    ])]})
    result = finding(governance_review, rule, tmp_path, monkeypatch)
    assert result["status"] == "pass"
    assert result["evidence"]["ratio"] == 1


def test_unknown_membership_cannot_pass_security(tmp_path, monkeypatch):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(users=[], usersCollectionStatus="unavailable")
    ]})
    assert finding(security_review, "SEC-004", tmp_path, monkeypatch)["status"] == "missing_evidence"


def test_endorsement_uses_explicit_production_profile_and_rest_semantic_models(tmp_path, monkeypatch):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [workspace(
        name="Finance", items=[{"id": "model-a", "type": "SemanticModel", "endorsementDetails": {}}],
    )]})
    monkeypatch.setattr(governance_review, "load_workspace_overrides",
                        lambda _: {"ws-a": {"profile": {"environment": "production"}}})
    result = finding(governance_review, "GOV-009", tmp_path, monkeypatch)
    assert result["status"] == "fail"
    assert result["evidence"]["productionWorkspaces"] == 1


@pytest.mark.parametrize("module,rule", [
    (governance_review, "GOV-001"), (governance_review, "GOV-003"),
    (governance_review, "GOV-008"), (governance_review, "GOV-009"),
    (architecture_review, "ARCH-005"), (architecture_review, "ARCH-006"),
    (architecture_review, "ARCH-007"), (architecture_review, "ARCH-015"),
    (operational_excellence_review, "OPS-001"), (operational_excellence_review, "OPS-002"),
])
def test_unavailable_items_cannot_imply_empty_or_compliant(tmp_path, monkeypatch, module, rule):
    write_raw(tmp_path, "workspace_inventory.json", {
        "workspaceListComplete": True, "collectionComplete": False,
        "workspaces": [workspace(items=None, itemsCollectionStatus="unavailable", users=[])],
    })
    write_raw(tmp_path, "deployment_pipelines.json", {"pipelines": []})
    write_raw(tmp_path, "git_integration.json", {"workspaces": []})
    result = finding(module, rule, tmp_path, monkeypatch)
    assert result["status"] == "missing_evidence"
    if rule == "ARCH-007":
        assert result["evidence"]["emptyCount"] == 0


@pytest.mark.parametrize("rule", ["GOV-003", "GOV-008", "GOV-009"])
def test_basic_rest_items_do_not_prove_optional_metadata_absence(tmp_path, monkeypatch, rule):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "model-a", "type": "SemanticModel"}]),
    ]})
    result = finding(governance_review, rule, tmp_path, monkeypatch)
    assert result["status"] == "missing_evidence"
    if rule in ("GOV-003", "GOV-008"):
        assert result["evidence"]["ratio"] is None


def test_reconciliation_preserves_known_children_and_inputs():
    scanner = [workspace(
        users=[{"identifier": "owner", "role": "Admin"}],
        Notebook=[{"id": "ITEM-A", "name": "Detailed notebook"}],
    )]
    rest = [workspace(users=[], usersCollectionStatus="unavailable",
                      items=None, itemsCollectionStatus="unavailable")]
    before = copy.deepcopy((scanner, rest))
    row = merge_workspace_evidence(scanner, rest)[0]
    assert workspace_users(row) == scanner[0]["users"]
    assert workspace_items(row)[0]["id"] == "ITEM-A"
    assert (scanner, rest) == before


def test_failed_previous_child_is_not_resurrected():
    row = merge_workspace_evidence(
        [workspace(users=[], usersCollectionStatus="unavailable")],
        [workspace(users=None, usersCollectionStatus="unavailable")],
    )[0]
    assert workspace_users(row) is None


def test_collected_empty_membership_overrides_previous_users():
    row = merge_workspace_evidence(
        [workspace(users=[{"role": "Admin", "identifier": "old-owner"}])],
        [workspace(users=[], usersCollectionStatus="collected")],
    )[0]
    assert workspace_users(row) == []


def test_unrelated_metadata_lists_are_not_items_and_future_types_remain_visible():
    row = workspace(
        Users=[{"id": "user-a"}], DatasourceInstances=[{"id": "source-a"}],
        Roles=[{"id": "role-a"}], FutureArtifact=[{"id": "future-a"}],
    )
    assert [item["type"] for item in workspace_items(row)] == ["FutureArtifact"]
    assert classify_workspace(row)["archetype"] == "unknown"


def test_sql_endpoint_does_not_hide_primary_lakehouse_workload():
    row = workspace(items=[{"id": "lake-a", "type": "Lakehouse"},
                           {"id": "sql-a", "type": "SQLEndpoint"}])
    assert classify_workspace(row)["archetype"] == "batch_engineering"
    assert classify_workspace(row)["itemTypeCounts"] == {"Lakehouse": 1, "SQLEndpoint": 1}


@pytest.mark.parametrize("roles", [("Admin", "Member"), ("Member", "Admin")])
def test_conflicting_duplicate_roles_are_unknown(tmp_path, monkeypatch, roles):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "item-a", "type": "Notebook"}],
                  users=[{"identifier": "owner", "role": role} for role in roles]),
    ]})
    assert finding(governance_review, "GOV-001", tmp_path, monkeypatch)["status"] == "missing_evidence"


@pytest.mark.parametrize("roles", [("Admin", "Member"), ("Member", "Admin")])
def test_conflicting_roles_cannot_assert_individual_admins(tmp_path, monkeypatch, roles):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(users=[{"identifier": "owner", "principalType": "User", "role": role} for role in roles]),
    ]})
    assert finding(security_review, "SEC-004", tmp_path, monkeypatch)["status"] == "missing_evidence"


def test_null_items_without_status_are_not_empty(tmp_path, monkeypatch):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [workspace(items=None)]})
    result = finding(architecture_review, "ARCH-007", tmp_path, monkeypatch)
    assert result["status"] == "missing_evidence"
    assert result["evidence"]["emptyCount"] == 0


def test_untyped_items_are_unknown_not_empty(tmp_path, monkeypatch):
    row = workspace(items=[{"id": "item-a"}], users=[{"identifier": "owner", "role": "Admin"}])
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [row]})
    assert classify_workspace(row)["archetype"] == "unknown"
    result = finding(governance_review, "GOV-001", tmp_path, monkeypatch)
    assert result["status"] == "fail"
    assert result["evidence"]["evaluatedWorkspaces"] == 1


def test_service_principal_role_shapes_do_not_double_count():
    row = workspace(users=[
        {"principalType": "App", "graphId": "APP-A", "role": "Admin"},
        {"principal": {"type": "ServicePrincipal", "id": "app-a"}, "role": "Admin"},
    ])
    assert len(workspace_users(row)) == 1


def test_workspace_names_never_join_different_ids():
    rows = merge_workspace_evidence([workspace(id="ws-a")], [workspace(id="ws-b", users=[])])
    assert len(rows) == 2
    assert workspace_users(rows[0]) is None
    assert workspace_users(rows[1]) == []


def test_dataflow_generations_are_normalized_without_double_counting(tmp_path, monkeypatch):
    write_raw(tmp_path, "scanner.json", {"workspaces": [
        workspace(dataflows=[{"id": "gen1"}], Dataflow=[{"id": "GEN2"}]),
    ]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "gen2", "type": "Dataflow"}, {"id": "gen2-b", "type": "Dataflow2"}]),
    ]})
    result = finding(architecture_review, "ARCH-015", tmp_path, monkeypatch)
    assert result["status"] == "fail"
    assert result["evidence"]["gen1Total"] == 1
    assert result["evidence"]["gen2Total"] == 2


def test_unknown_items_are_null_in_cost_details():
    rows = cost_review._capacity_workspace_details(
        {"id": "cap-a"}, [workspace(capacityId="cap-a", items=None, itemsCollectionStatus="unavailable")],
    )
    assert rows[0]["itemCount"] is None


def test_cost_counts_items_without_identity_without_inventing_actionable_ids():
    rows = cost_review._capacity_workspace_details(
        {"id": "cap-a"}, [workspace(capacityId="cap-a", Notebook=[{"name": "Notebook"}])],
    )
    assert rows[0]["itemCount"] == 1
    assert rows[0]["items"] == []


def test_cost_production_scope_honors_profile_and_token_boundaries(tmp_path, monkeypatch):
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(id="ws-a", name="Finance"),
        workspace(id="ws-b", name="Productivity"),
    ]})
    monkeypatch.setattr(cost_review, "load_workspace_overrides",
                        lambda _: {"ws-a": {"profile": {"environment": "production"}}})
    result = finding(cost_review, "COST-004", tmp_path, monkeypatch)
    assert result["evidence"]["examples"] == [{"workspace": "Finance", "type": "Workspace", "capacityId": None}]


def test_best_practice_reports_use_both_sources_once(tmp_path, monkeypatch):
    from collectors import best_practices

    monkeypatch.delenv("BEST_PRACTICES_SKIP", raising=False)
    monkeypatch.setattr(best_practices, "_ensure_sempy_labs", lambda: None)
    monkeypatch.setattr(best_practices, "_shim_fabric_rest_client", lambda: None)
    monkeypatch.setitem(sys.modules, "sempy_labs", SimpleNamespace())
    calls = []
    monkeypatch.setattr(best_practices, "_report_bpa", lambda _, report, ws: calls.append((report, ws)) or [])
    write_raw(tmp_path, "semantic_models.json", {"datasets": []})
    write_raw(tmp_path, "scanner.json", {"workspaces": [workspace(Report=[{"id": "REPORT-A"}])]})
    write_raw(tmp_path, "workspace_inventory.json", {"workspaces": [
        workspace(items=[{"id": "report-a", "type": "Report"}]),
        workspace(id="ws-b", items=[{"id": "report-b", "type": "Report"}]),
    ]})
    payload = json.loads(best_practices.collect(tmp_path).read_text(encoding="utf-8"))
    assert calls == [("REPORT-A", "ws-a"), ("report-b", "ws-b")]
    assert len(payload["reports"]) == 2
    assert payload["errors"] == []

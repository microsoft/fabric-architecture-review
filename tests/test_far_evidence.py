# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import datetime, timedelta, timezone
import json

import pytest

from collectors.enrichment_facts import ENRICHMENT_FAMILIES, FUAM_FAMILIES
from collectors.far_evidence import CONTRACTS, TENANT_HISTORY_TARGET, bind, _current_review_workspaces
from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256
from orchestration.review_config import capture, local_configuration, read_snapshot

WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CAPACITY = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OTHER = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ARM = f"/subscriptions/{OTHER}/resourceGroups/rg/providers/Microsoft.Fabric/capacities/cap".lower()
NOW = datetime(2026, 1, 20, 12, tzinfo=timezone.utc)


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    for name in ("WORKSPACES_CONFIG", "FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    config = local_configuration()
    config["sources"]["fuam"].update(enabled=True, workspace_id=WORKSPACE, lakehouse_id=OTHER)
    return read_snapshot(capture(tmp_path, config))


def tenant_history(statement):
    assert "dbo.tenant_settings" in statement and "fuam_deleted" not in statement
    names = ("PublishToWeb", "AllowExternalDataSharingSwitch")
    return [
        {"settingName": name, "enabled": enabled, "TransferDateTime": NOW - timedelta(days=days)}
        for name in names for days, enabled in ((10, True), (5, True), (1, False))
    ]


def fuam_query(source, statement):
    assert source == "fuam"
    if "dbo.tenant_settings" in statement:
        return tenant_history(statement)
    if not any(statement.startswith(f"SELECT {', '.join(columns)} FROM dbo.{table} ")
               for table, (_, columns, _) in CONTRACTS.items()):
        return []  # the aggregated rule-fact queries; their shapes are covered by the enrichment tests
    assert "fuam_deleted = 0" in statement
    table = next(table for table in CONTRACTS if f"FROM dbo.{table} " in statement)
    id_column, columns, _ = CONTRACTS[table]
    row = {column: "metadata" for column in columns}
    row[id_column] = CAPACITY if table == "capacities" else WORKSPACE
    if table == "active_items":
        row["id"] = OTHER
    if table in ("active_items", "workspaces"):
        row["capacityId" if table == "active_items" else "CapacityId"] = CAPACITY
    row["fuam_modified_at"] = NOW - timedelta(hours=1)
    assert row[id_column] in statement
    return [row]


def test_scoped_metadata_contract_and_provenance(snapshot):
    result = bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], fuam_query, now=NOW)
    assert set(result["tables"]) == {value[2] for value in CONTRACTS.values()} | {TENANT_HISTORY_TARGET}
    for name, rows in result["tables"].items():
        if name == TENANT_HISTORY_TARGET:
            assert len(rows) == 6
            assert {row["source_table"] for row in rows} == {"tenant_settings"}
            continue
        assert len(rows) == 1
        assert rows[0]["configuration_sha256"] == snapshot["sha256"]
        assert rows[0]["source_lakehouse_id"] == OTHER
        assert rows[0]["freshness_status"] == "recently_modified"
    assert all(entry["status"] == "context_only"
               for entry in result["coverage"] if entry["source"] == "fuam" and "family" not in entry)
    assert set(result["rule_facts"]) == set(FUAM_FAMILIES)
    assert {entry["family"] for entry in result["coverage"] if entry["source"] == "fuam" and "family" in entry} \
        == set(FUAM_FAMILIES)
    assert result["profile_sha256"] == snapshot["profile_sha256"]
    assert result["configuration_captured_at_utc"] == snapshot["captured_at_utc"]
    assert result["_provenance"]["configuration_sha256"] == snapshot["sha256"]
    serialized = json.dumps(result).lower()
    assert "vertipaq" not in serialized and "effectivecost" not in serialized


def test_empty_scope_never_becomes_tenant_wide(snapshot):
    def forbidden(*args):
        pytest.fail("Empty scope must not query a source.")
    result = bind(snapshot, [], forbidden, now=NOW)
    assert result["tables"] == {}
    assert result["coverage"][0]["status"] == "empty_review_scope"


@pytest.mark.parametrize("mismatch", [
    None, "unstamped", "profile_id", "profile_sha256", "configuration_sha256",
    "configuration_captured_at_utc", "workspace_scope",
])
def test_context_collection_rejects_stale_local_workspace_files(snapshot, tmp_path, monkeypatch, mismatch):
    monkeypatch.setenv("WORKSPACE_IDS", WORKSPACE)
    provenance = {
        "source": "native", "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "configuration_sha256": snapshot["sha256"],
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
        "workspace_scope": WORKSPACE,
    }
    if mismatch and mismatch != "unstamped":
        provenance[mismatch] = "historical"
    payload = {"collectionComplete": True, "workspaces": [{"id": WORKSPACE}],
               "_provenance": provenance}
    if mismatch == "unstamped":
        payload.pop("_provenance")
    (tmp_path / "workspace_inventory.json").write_text(json.dumps(payload))
    if mismatch:
        with pytest.raises(ValueError, match="current frozen architectural collection"):
            _current_review_workspaces(tmp_path, snapshot)
    else:
        assert _current_review_workspaces(tmp_path, snapshot)[0]["id"] == WORKSPACE


@pytest.mark.parametrize("mutation", ["out_of_scope", "duplicate", "missing_column", "future_timestamp"])
def test_source_contract_failure_is_explicit(snapshot, mutation):
    def query(source, statement):
        rows = fuam_query(source, statement)
        if mutation == "out_of_scope":
            rows[0]["WorkspaceId"] = OTHER
        elif mutation == "duplicate":
            rows *= 2
        elif mutation == "missing_column":
            del rows[0]["Description"]
        else:
            rows[0]["fuam_modified_at"] = NOW + timedelta(hours=1)
        return rows
    with pytest.raises(ValueError):
        bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], query, now=NOW)


def test_stale_rows_are_labeled_not_treated_as_current_authoritative_evidence(snapshot):
    def query(source, statement):
        rows = fuam_query(source, statement)
        if "dbo.tenant_settings" in statement:
            return [{**row, "TransferDateTime": NOW - timedelta(hours=25)} for row in rows]
        rows[0]["fuam_modified_at"] = NOW - timedelta(hours=25)
        return rows
    result = bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], query, now=NOW)
    assert all(rows[0]["freshness_status"] == "stale" for rows in result["tables"].values())


def test_fca_only_queries_explicit_resource_mapping(snapshot):
    snapshot["configuration"]["sources"]["fuam"]["enabled"] = False
    snapshot["configuration"]["sources"]["fca"].update(enabled=True, workspace_id=WORKSPACE, lakehouse_id=OTHER)
    snapshot["configuration"]["profiles"]["capacities"] = [{"id": CAPACITY, "arm_resource_id": ARM}]
    calls = []
    def query(source, statement):
        calls.append((source, statement))
        assert ("FROM dbo.resources" in statement or "FROM dbo.focus" in statement) and ARM in statement
        if "dbo.focus" in statement:
            return [{"ResourceId": ARM, "Tags": '{"owner": "x"}'}]
        return [{"ResourceId": ARM, "ResourceName": "cap", "RegionId": "westeurope", "ResourceKey": 1}]
    result = bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], query, now=NOW)
    row = result["tables"]["far_evidence_capacity_resources"][0]
    assert row["capacity_id"] == CAPACITY
    assert row["freshness_status"] == "unknown"
    assert len(calls) == 2
    assert result["rule_facts"]["capacity_tags"]["rows"] == [{"capacity_id": CAPACITY, "tag_names": ["owner"]}]
    assert "capacity_tags" in ENRICHMENT_FAMILIES


def test_failing_fact_queries_are_isolated_per_family_and_never_raise(snapshot):
    def query(source, statement):
        if any(statement.startswith(f"SELECT {', '.join(columns)} FROM dbo.{table} ")
               for table, (_, columns, _) in CONTRACTS.items()) or "dbo.tenant_settings" in statement:
            return fuam_query(source, statement)
        raise RuntimeError("secret-table-name is unreachable")
    result = bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], query, now=NOW)
    assert set(result["rule_facts"]) == set(FUAM_FAMILIES)
    for entry in result["rule_facts"].values():
        assert (entry["status"], entry["reason_code"], entry["rows"]) == ("unavailable", "query_failed", [])
    assert "secret-table-name" not in json.dumps(result)
    assert set(result["tables"]) == {value[2] for value in CONTRACTS.values()} | {TENANT_HISTORY_TARGET}


def test_fca_without_bridge_does_not_name_match_or_read_billing_facts(snapshot):
    snapshot["configuration"]["sources"]["fuam"]["enabled"] = False
    snapshot["configuration"]["sources"]["fca"].update(enabled=True, workspace_id=WORKSPACE, lakehouse_id=OTHER)
    snapshot["configuration"]["profiles"]["capacities"] = []
    def forbidden(*args):
        pytest.fail("No verified mapping must mean no FCA query.")
    result = bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], forbidden, now=NOW)
    assert result["coverage"][-1]["status"] == "missing_capacity_resource_bridge"
    assert result["coverage"][-1]["unmapped_capacity_count"] == 1


def test_sql_auth_failure_does_not_fall_back_to_another_source(snapshot):
    def failing(*args):
        raise PermissionError("Access denied")
    with pytest.raises(PermissionError, match="Access denied"):
        bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], failing, now=NOW)


@pytest.mark.parametrize("mutation", ["unexpected_setting", "non_boolean_state", "future_transfer", "missing_column"])
def test_tenant_history_contract_failures_are_explicit(snapshot, mutation):
    def query(source, statement):
        if "dbo.tenant_settings" not in statement:
            return fuam_query(source, statement)
        rows = tenant_history(statement)
        if mutation == "unexpected_setting":
            rows[0]["settingName"] = "SomethingElse"
        elif mutation == "non_boolean_state":
            rows[0]["enabled"] = "yes"
        elif mutation == "future_transfer":
            rows[0]["TransferDateTime"] = NOW + timedelta(days=1)
        else:
            del rows[0]["enabled"]
        return rows
    with pytest.raises(ValueError):
        bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], query, now=NOW)


def test_tenant_history_only_requests_settings_the_rules_evaluate_and_a_bounded_window(snapshot):
    from analyzers.far_profile import TENANT_SETTING_NAMES
    statements = []
    def query(source, statement):
        statements.append(statement)
        return fuam_query(source, statement)
    bind(snapshot, [{"id": WORKSPACE, "capacityId": CAPACITY}], query, now=NOW)
    statement = next(item for item in statements if "dbo.tenant_settings" in item)
    for names in TENANT_SETTING_NAMES.values():
        for name in names:
            assert f"'{name}'" in statement
    assert "TransferDateTime >= '2025-10-22 12:00:00'" in statement

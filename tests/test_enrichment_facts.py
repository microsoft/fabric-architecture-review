# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Aggregated FUAM and FCA facts: shapes, freshness, family isolation and bounded queries."""
import copy
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from collectors import enrichment_facts
from collectors.enrichment_facts import CHUNK, ENRICHMENT_FAMILIES, FUAM_FAMILIES, fca_capacity_tags, fuam_facts
from collectors.far_evidence import CONTRACTS, bind
from orchestration.review_config import (
    ROOT, capture, declared_profiles, local_configuration, read_snapshot, read_yaml,
)

WS = "11111111-1111-1111-1111-111111111111"
OTHER_WS = "22222222-2222-2222-2222-222222222222"
CAP = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_CAP = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UNUSED_CAP = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ITEM = "dddddddd-dddd-dddd-dddd-dddddddddddd"
DOMAIN = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
SUBSCRIPTION = "44444444-4444-4444-4444-444444444444"
ARM = f"/subscriptions/{SUBSCRIPTION}/resourcegroups/rg/providers/microsoft.fabric/capacities/prod"
OTHER_ARM = f"/subscriptions/{SUBSCRIPTION}/resourcegroups/rg/providers/microsoft.fabric/capacities/other"
NOW = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
CONNECTION = {"workspace_id": WS, "lakehouse_id": OTHER_WS, "max_age_hours": 24}
THRESHOLDS = local_configuration()["thresholds"]
SECRET = "SECRET-table-name-and-value"

ACTIVITY_BOUNDS = "MIN(CreationDate)"
ACTIVITY_ROWS = "FROM dbo.activities WHERE"
ITEM_AGE = "dated_item_count"
STALE_IDS = "ROW_NUMBER() OVER (PARTITION BY workspaceId"
DOMAINS = "FROM dbo.workspaces"
OPERATIONS = "SUM(FailureOperationCount)"
TOTALS = "SELECT CapacityId, SUM(TotalCUs)"
THROTTLING = "ThrottlingInMin"
ITEM_NEWEST = "MAX([Date]) AS newest FROM dbo.capacity_metrics_by_item_by_operation_by_day"
KIND_NEWEST = "MAX([Date]) AS newest FROM dbo.capacity_metrics_by_item_kind_by_day"
SNAPSHOT = "MAX(TransferDateTime) AS snapshot_at FROM dbo.widelyshared_publishtoweb"
PUBLISHED = "widelyshared_publishtoweb p JOIN"
ORG_SNAPSHOT = "MAX(TransferDateTime) AS snapshot_at FROM dbo.widelyshared_organization_links"
ORG_LINKS = "widelyshared_organization_links p JOIN"
REFRESHES = "SUM(CASE WHEN Status"
REFRESH_NEWEST = "MAX(LastRefreshStartTime)"
USAGE = "OperationName = 'Query'"
LOOKUP_IDS = "type = 'SemanticModel'"
LOOKUP_NEWEST = "MAX(fuam_modified_at) AS newest FROM dbo.active_items"

# The marker whose failure affects exactly one family.
OWN_MARKER = {
    "workspace_activity": ACTIVITY_BOUNDS, "item_age": ITEM_AGE, "domains": DOMAINS,
    "item_operations": OPERATIONS, "capacity_totals": TOTALS, "capacity_throttling": THROTTLING,
    "publish_to_web": SNAPSHOT, "org_links": ORG_SNAPSHOT, "refresh_health": REFRESHES,
    "model_usage": USAGE, "model_lookup": LOOKUP_NEWEST,
}
TABLES = {
    "workspace_activity": "activities", "item_age": "active_items", "domains": "workspaces",
    "item_operations": enrichment_facts.ITEM_METRICS, "capacity_totals": enrichment_facts.ITEM_METRICS,
    "capacity_throttling": enrichment_facts.KIND_METRICS, "publish_to_web": "widelyshared_publishtoweb",
    "org_links": "widelyshared_organization_links", "refresh_health": "capacity_refreshable_details",
    "model_usage": enrichment_facts.ITEM_METRICS, "model_lookup": "active_items",
}


def happy():
    recent = NOW - timedelta(hours=2)
    return {
        ACTIVITY_BOUNDS: [{"earliest": datetime(2025, 1, 1), "newest": recent}],
        ACTIVITY_ROWS: [{"WorkspaceId": WS.upper(), "last_activity": "2026-02-26T12:00:00Z"}],
        ITEM_AGE: [{"workspaceId": WS, "item_count": Decimal(10), "dated_item_count": 8, "stale_item_count": 2,
                    "newest": recent}],
        STALE_IDS: [{"workspaceId": WS.upper(), "id": ITEM.upper()}, {"workspaceId": OTHER_WS, "id": WS}],
        DOMAINS: [{"WorkspaceId": WS, "DomainId": DOMAIN.upper(), "fuam_modified_at": recent},
                  {"WorkspaceId": OTHER_WS, "DomainId": None, "fuam_modified_at": recent - timedelta(hours=3)}],
        OPERATIONS: [
            {"CapacityId": CAP, "WorkspaceId": WS, "ItemId": ITEM, "ItemKind": "Notebook",
             "operation_count": Decimal(120), "failure_count": 12, "total_cus": Decimal("250.5")},
            {"CapacityId": None, "WorkspaceId": WS, "ItemId": OTHER_WS, "ItemKind": None,
             "operation_count": None, "failure_count": None, "total_cus": None},
            {"CapacityId": CAP, "WorkspaceId": WS, "ItemId": None, "ItemKind": "Notebook",
             "operation_count": 5, "failure_count": 0, "total_cus": 1},
        ],
        TOTALS: [{"CapacityId": CAP.upper(), "total_cus": Decimal("1000.5")}],
        THROTTLING: [{"CapacityId": CAP, "observed_days": 30, "throttled_days": 4}],
        ITEM_NEWEST: [{"newest": NOW - timedelta(days=1)}],
        KIND_NEWEST: [{"newest": date(2026, 2, 28)}],
        SNAPSHOT: [{"snapshot_at": NOW - timedelta(hours=5)}],
        PUBLISHED: [{"WorkspaceId": WS, "ItemId": ITEM.upper(), "artifactType": "Report"}],
        ORG_SNAPSHOT: [{"snapshot_at": NOW - timedelta(hours=5)}],
        ORG_LINKS: [{"WorkspaceId": WS, "ItemId": ITEM.upper(), "artifactType": "Report"}],
        REFRESHES: [{"WorkspaceId": WS.upper(), "ItemId": ITEM.upper(), "refresh_count": Decimal(10),
                     "failure_count": Decimal(3)}],
        REFRESH_NEWEST: [{"newest": NOW - timedelta(hours=3)}],
        USAGE: [{"ItemId": ITEM.upper(), "operation_count": Decimal(7)}],
        LOOKUP_IDS: [{"id": ITEM.upper(), "workspaceId": OTHER_WS.upper()}],
        LOOKUP_NEWEST: [{"newest": recent}],
    }


class Lakehouse:
    """A fake SQL endpoint keyed on substrings of the statement; it records every statement."""

    def __init__(self, **overrides):
        self.statements: list[str] = []
        self.responses = happy()
        for marker, response in overrides.items():
            self.responses[marker] = response

    def override(self, marker, response):
        self.responses[marker] = response
        return self

    def __call__(self, statement):
        self.statements.append(statement)
        for marker, response in self.responses.items():
            if marker in statement:
                if isinstance(response, Exception):
                    raise response
                return copy.deepcopy(response)
        raise AssertionError(f"Unexpected statement: {statement}")

    def matching(self, marker):
        return [statement for statement in self.statements if marker in statement]


def collect(lakehouse=None, workspace_ids=None, capacity_ids=None, now=NOW, model_ids=()):
    lakehouse = lakehouse or Lakehouse()
    result = fuam_facts(lakehouse, CONNECTION, THRESHOLDS,
                        {WS, OTHER_WS} if workspace_ids is None else workspace_ids,
                        {CAP, OTHER_CAP} if capacity_ids is None else capacity_ids, now, set(model_ids))
    return result


def in_lists(statement):
    return [[value.strip().strip("'") for value in group.split(",")] for group in re.findall(r"IN \(([^)]*)\)", statement)]


# --- happy path --------------------------------------------------------------------------------

def test_every_family_is_collected_with_provenance():
    result = collect()
    assert set(result) == set(FUAM_FAMILIES)
    for family, entry in result.items():
        assert entry["status"] == "collected", family
        assert entry["table"] == TABLES[family]
        assert (entry["source_workspace_id"], entry["source_lakehouse_id"]) == (WS, OTHER_WS)
        assert entry["freshness_status"] == "recent", family


def test_workspace_activity_shape():
    entry = collect()["workspace_activity"]
    assert entry["rows"] == [{"workspace_id": WS, "last_activity_at": "2026-02-26T12:00:00+00:00"}]
    assert entry["earliest_at"] == "2025-01-01T00:00:00+00:00"
    assert entry["observed_at"] == (NOW - timedelta(hours=2)).isoformat()


def test_item_age_shape_and_counts_are_integers():
    entry = collect()["item_age"]
    assert entry["rows"] == [{"workspace_id": WS, "item_count": 10, "dated_item_count": 8, "stale_item_count": 2,
                              "stale_item_ids": [ITEM]}]
    assert all(type(value) is int for value in list(entry["rows"][0].values())[1:4])
    assert entry["stale_item_days"] == THRESHOLDS["governance"]["stale_item_days"]
    assert entry["observed_at"] == (NOW - timedelta(hours=2)).isoformat()


def test_stale_items_are_named_oldest_first_and_capped_per_workspace():
    lakehouse = Lakehouse()
    entry = collect(lakehouse)["item_age"]
    statement = lakehouse.matching(STALE_IDS)[0]
    cutoff = (NOW - timedelta(days=THRESHOLDS["governance"]["stale_item_days"])).strftime("%Y-%m-%d")
    assert f"TRY_CAST(lastUpdatedDate AS DATETIME2) < '{cutoff}'" in statement
    assert "ORDER BY lastUpdatedDate" in statement and "fuam_deleted = 0" in statement
    assert f"rn <= {enrichment_facts.STALE_ITEMS_LISTED}" in statement
    assert [row["stale_item_ids"] for row in entry["rows"]] == [[ITEM]]  # a workspace with no rows gets no ids


def test_a_workspace_without_stale_items_names_none_and_an_unreadable_list_fails_the_family():
    quiet = collect(Lakehouse().override(STALE_IDS, []))["item_age"]
    assert quiet["status"] == "collected" and [row["stale_item_ids"] for row in quiet["rows"]] == [[]]
    broken = collect(Lakehouse().override(STALE_IDS, RuntimeError(SECRET)))["item_age"]
    assert broken["status"] == "unavailable" and SECRET not in json.dumps(broken, default=str)


def test_domains_shape_lowercases_ids_and_keeps_unassigned():
    entry = collect()["domains"]
    assert entry["rows"] == [{"workspace_id": WS, "domain_id": DOMAIN}, {"workspace_id": OTHER_WS, "domain_id": None}]
    assert entry["observed_at"] == (NOW - timedelta(hours=2)).isoformat()


def test_item_operations_shape_types_and_null_handling():
    entry = collect()["item_operations"]
    assert entry["window_days"] == THRESHOLDS["operations"]["window_days"]
    assert len(entry["rows"]) == 2  # a row without an item identity is dropped
    first, second = entry["rows"]
    assert first == {"capacity_id": CAP, "workspace_id": WS, "item_id": ITEM, "item_kind": "Notebook",
                     "operation_count": 120, "failure_count": 12, "total_cus": 250.5}
    assert type(first["operation_count"]) is int and type(first["total_cus"]) is float
    assert second == {"capacity_id": None, "workspace_id": WS, "item_id": OTHER_WS, "item_kind": "",
                      "operation_count": 0, "failure_count": 0, "total_cus": 0.0}


def test_capacity_totals_and_throttling_shape():
    result = collect()
    assert result["capacity_totals"]["rows"] == [{"capacity_id": CAP, "total_cus": 1000.5}]
    assert result["capacity_totals"]["window_days"] == THRESHOLDS["operations"]["window_days"]
    assert result["capacity_throttling"]["rows"] == [{"capacity_id": CAP, "observed_days": 30, "throttled_days": 4}]
    assert result["capacity_throttling"]["observed_at"] == "2026-02-28T00:00:00+00:00"  # a date value


def test_publish_to_web_shape_and_snapshot():
    entry = collect()["publish_to_web"]
    assert entry["rows"] == [{"workspace_id": WS, "item_id": ITEM, "artifact_type": "Report"}]
    assert entry["snapshot_at"] == entry["observed_at"] == (NOW - timedelta(hours=5)).isoformat()


@pytest.mark.parametrize("stamp, expected", [
    (datetime(2026, 2, 20, 10), "2026-02-20T10:00:00+00:00"),
    ("2026-02-20T10:00:00+02:00", "2026-02-20T08:00:00+00:00"),
    ("2026-02-20T10:00:00Z", "2026-02-20T10:00:00+00:00"),
    (datetime(2026, 2, 20, 10, tzinfo=timezone(timedelta(hours=-5))), "2026-02-20T15:00:00+00:00"),
])
def test_timestamps_become_utc_iso_strings(stamp, expected):
    lakehouse = Lakehouse().override(ACTIVITY_ROWS, [{"WorkspaceId": WS, "last_activity": stamp}])
    assert collect(lakehouse)["workspace_activity"]["rows"][0]["last_activity_at"] == expected


def test_queries_are_bounded_to_the_window_and_the_stale_cutoff():
    lakehouse = Lakehouse()
    collect(lakehouse)
    window = (NOW - timedelta(days=THRESHOLDS["operations"]["window_days"])).strftime("%Y-%m-%d")
    cutoff = (NOW - timedelta(days=THRESHOLDS["governance"]["stale_item_days"])).strftime("%Y-%m-%d")
    for marker in (OPERATIONS, TOTALS, THROTTLING):
        assert all(f"[Date] >= '{window}'" in statement for statement in lakehouse.matching(marker))
    assert f"< '{cutoff}'" in lakehouse.matching(ITEM_AGE)[0]
    assert all("fuam_deleted = 0" in statement for statement in lakehouse.matching(ITEM_AGE) + lakehouse.matching(DOMAINS))


def test_queries_never_read_row_level_activity_or_free_text():
    lakehouse = Lakehouse()
    collect(lakehouse)
    joined = "\n".join(lakehouse.statements).lower()
    for forbidden in ("tagvalue", "description", "displayname", "username", "userid"):
        assert forbidden not in joined


# --- freshness ---------------------------------------------------------------------------------

@pytest.mark.parametrize("hours, expected", [(1, "recent"), (24, "recent"), (25, "stale"), (400, "stale")])
def test_current_data_family_freshness_follows_the_age_limit(hours, expected):
    newest = NOW - timedelta(hours=hours)
    lakehouse = Lakehouse().override(ITEM_AGE, [{"workspaceId": WS, "item_count": 1, "dated_item_count": 1,
                                                   "stale_item_count": 0, "newest": newest}])
    lakehouse.override(DOMAINS, [{"WorkspaceId": WS, "DomainId": None, "fuam_modified_at": newest}])
    lakehouse.override(SNAPSHOT, [{"snapshot_at": newest}])
    result = collect(lakehouse)
    assert [result[name]["freshness_status"] for name in ("item_age", "domains", "publish_to_web")] == [expected] * 3


@pytest.mark.parametrize("hours, expected", [(1, "recent"), (24, "recent"), (25, "recent"), (48, "recent"),
                                              (49, "stale")])
def test_daily_metrics_get_a_day_of_grace(hours, expected):
    newest = NOW - timedelta(hours=hours)
    lakehouse = Lakehouse().override(ACTIVITY_BOUNDS, [{"earliest": newest - timedelta(days=9), "newest": newest}])
    lakehouse.override(ITEM_NEWEST, [{"newest": newest}]).override(KIND_NEWEST, [{"newest": newest}])
    result = collect(lakehouse)
    assert result["workspace_activity"]["freshness_status"] == expected
    for family in ("item_operations", "capacity_totals", "capacity_throttling"):
        assert result[family]["freshness_status"] == expected, family


def test_the_grace_follows_the_configured_age_limit():
    newest = NOW - timedelta(hours=60)
    lakehouse = Lakehouse().override(ITEM_NEWEST, [{"newest": newest}])
    wide = fuam_facts(lakehouse, {**CONNECTION, "max_age_hours": 48}, THRESHOLDS, {WS}, {CAP}, NOW)
    assert wide["item_operations"]["freshness_status"] == "recent"
    narrow = fuam_facts(lakehouse, {**CONNECTION, "max_age_hours": 12}, THRESHOLDS, {WS}, {CAP}, NOW)
    assert narrow["item_operations"]["freshness_status"] == "stale"


def test_a_family_without_any_timestamp_is_stale():
    lakehouse = Lakehouse().override(ITEM_AGE, []).override(DOMAINS, [])
    result = collect(lakehouse)
    for family in ("item_age", "domains"):
        assert result[family]["status"] == "collected"
        assert (result[family]["rows"], result[family]["observed_at"], result[family]["freshness_status"]) == (
            [], None, "stale")
    lakehouse = Lakehouse().override(ITEM_NEWEST, [{"newest": None}]).override(KIND_NEWEST, [{"newest": None}])
    result = collect(lakehouse)
    assert [result[name]["freshness_status"] for name in ("item_operations", "capacity_totals", "capacity_throttling")] == [
        "stale"] * 3


# --- family isolation --------------------------------------------------------------------------

@pytest.mark.parametrize("family", sorted(OWN_MARKER))
def test_a_failing_statement_marks_only_its_family_unavailable_without_leaking(family):
    lakehouse = Lakehouse().override(OWN_MARKER[family], RuntimeError(SECRET))
    result = collect(lakehouse)
    assert result[family] == {"table": TABLES[family], "status": "unavailable", "reason_code": "query_failed",
                              "rows": [], "source_workspace_id": WS, "source_lakehouse_id": OTHER_WS}
    assert all(entry["status"] == "collected" for name, entry in result.items() if name != family)
    assert SECRET not in json.dumps(result, default=str)


def test_every_family_failing_still_returns_every_family():
    lakehouse = Lakehouse(**{marker: ValueError(SECRET) for marker in OWN_MARKER.values()})
    result = collect(lakehouse)
    assert set(result) == set(FUAM_FAMILIES)
    assert {entry["reason_code"] for entry in result.values()} == {"query_failed"}
    assert SECRET not in json.dumps(result, default=str)


@pytest.mark.parametrize("family, marker", [
    ("workspace_activity", ACTIVITY_BOUNDS), ("workspace_activity", ACTIVITY_ROWS), ("item_age", ITEM_AGE),
    ("domains", DOMAINS), ("item_operations", OPERATIONS), ("capacity_totals", TOTALS),
    ("capacity_throttling", THROTTLING), ("capacity_throttling", KIND_NEWEST), ("item_operations", ITEM_NEWEST),
    ("publish_to_web", SNAPSHOT), ("publish_to_web", PUBLISHED), ("org_links", ORG_SNAPSHOT),
    ("org_links", ORG_LINKS), ("refresh_health", REFRESHES), ("refresh_health", REFRESH_NEWEST),
    ("model_usage", USAGE), ("model_lookup", LOOKUP_NEWEST),
])
@pytest.mark.parametrize("columns", [{"unexpected": 1}, "extra", "missing"])
def test_incompatible_column_sets_are_reported(family, marker, columns):
    lakehouse = Lakehouse()
    rows = lakehouse.responses[marker]
    if columns == "extra":
        rows = [{**rows[0], "surprise": 1}]
    elif columns == "missing":
        rows = [{key: value for key, value in list(rows[0].items())[:-1]}]
    else:
        rows = [columns]
    lakehouse.override(marker, rows)
    result = collect(lakehouse)
    assert (result[family]["status"], result[family]["reason_code"]) == ("unavailable", "incompatible_schema")
    shared = {"capacity_totals", "model_usage"} if marker == ITEM_NEWEST else set()  # both read the same newest-date statement
    assert all(entry["status"] == "collected" for name, entry in result.items() if name != family and name not in shared)


IDENTITY_COLUMNS = [
    ("workspace_activity", ACTIVITY_ROWS, "WorkspaceId"), ("item_age", ITEM_AGE, "workspaceId"),
    ("domains", DOMAINS, "WorkspaceId"), ("item_operations", OPERATIONS, "WorkspaceId"),
    ("item_operations", OPERATIONS, "ItemId"), ("item_operations", OPERATIONS, "CapacityId"),
    ("capacity_totals", TOTALS, "CapacityId"), ("capacity_throttling", THROTTLING, "CapacityId"),
    ("publish_to_web", PUBLISHED, "WorkspaceId"), ("publish_to_web", PUBLISHED, "ItemId"),
    ("org_links", ORG_LINKS, "WorkspaceId"), ("org_links", ORG_LINKS, "ItemId"),
    ("refresh_health", REFRESHES, "WorkspaceId"), ("refresh_health", REFRESHES, "ItemId"),
    ("model_usage", USAGE, "ItemId"),
]
INVALID_IDENTITIES = ["not-a-guid", "'; DROP TABLE x; --", "", None, 7]
# An item without a capacity, or without an item identity, is skipped rather than rejected.
TOLERATED = {(OPERATIONS, "CapacityId", ""), (OPERATIONS, "CapacityId", None), (OPERATIONS, "ItemId", None),
             (USAGE, "ItemId", None)}


@pytest.mark.parametrize("family, marker, key, identity", [
    (family, marker, key, identity) for family, marker, key in IDENTITY_COLUMNS for identity in INVALID_IDENTITIES
    if (marker, key, identity) not in TOLERATED
])
def test_an_invalid_identity_returned_by_the_source_is_rejected(family, marker, key, identity):
    lakehouse = Lakehouse()
    lakehouse.responses[marker][0][key] = identity
    result = collect(lakehouse)
    assert (result[family]["status"], result[family]["reason_code"]) == ("unavailable", "incompatible_identity")
    assert "DROP" not in json.dumps(result, default=str)


@pytest.mark.parametrize("key, identity", [("CapacityId", None), ("CapacityId", ""), ("ItemId", None)])
def test_an_item_without_a_capacity_or_identity_is_not_an_error(key, identity):
    lakehouse = Lakehouse()
    lakehouse.responses[OPERATIONS][0][key] = identity
    entry = collect(lakehouse)["item_operations"]
    assert entry["status"] == "collected"
    assert len(entry["rows"]) == (1 if key == "ItemId" else 2)


@pytest.mark.parametrize("bounds", [[], [{"earliest": None, "newest": None}]])
def test_an_empty_activity_history_is_reported(bounds):
    result = collect(Lakehouse().override(ACTIVITY_BOUNDS, bounds))
    assert (result["workspace_activity"]["status"], result["workspace_activity"]["reason_code"]) == (
        "unavailable", "no_activity_history")
    assert result["workspace_activity"]["rows"] == []


@pytest.mark.parametrize("snapshot", [[], [{"snapshot_at": None}]])
def test_publish_to_web_without_a_transfer_is_unavailable_not_empty(snapshot):
    lakehouse = Lakehouse().override(SNAPSHOT, snapshot)
    result = collect(lakehouse)
    assert (result["publish_to_web"]["status"], result["publish_to_web"]["reason_code"]) == (
        "unavailable", "no_transfer_snapshot")
    assert result["publish_to_web"]["rows"] == []
    assert not lakehouse.matching(PUBLISHED)  # nothing is asked once there is no snapshot
    assert result["domains"]["status"] == "collected"


def test_publish_to_web_with_a_snapshot_and_no_items_is_a_collected_empty_result():
    result = collect(Lakehouse().override(PUBLISHED, []))
    assert (result["publish_to_web"]["status"], result["publish_to_web"]["rows"]) == ("collected", [])


def test_the_publish_statement_reads_only_the_latest_transfer_of_active_items():
    lakehouse = Lakehouse()
    collect(lakehouse)
    statement = lakehouse.matching(PUBLISHED)[0]
    assert "SELECT MAX(TransferDate) FROM dbo.widelyshared_publishtoweb" in statement
    assert "i.fuam_deleted = 0" in statement


def test_refresh_health_shape_counts_only_completed_and_failed_in_the_window():
    lakehouse = Lakehouse()
    entry = collect(lakehouse)["refresh_health"]
    assert entry["rows"] == [{"workspace_id": WS, "item_id": ITEM, "refresh_count": 10, "failure_count": 3}]
    assert all(type(value) is int for value in list(entry["rows"][0].values())[2:])
    assert entry["window_days"] == THRESHOLDS["operations"]["window_days"]
    assert entry["observed_at"] == (NOW - timedelta(hours=3)).isoformat()
    window = (NOW - timedelta(days=THRESHOLDS["operations"]["window_days"])).strftime("%Y-%m-%d")
    statement = lakehouse.matching(REFRESHES)[0]
    assert "(Status = 'Completed' OR Status = 'Failed')" in statement and f"LastRefreshStartDate >= '{window}'" in statement
    assert "ItemName" not in statement and "RequestId" not in statement


def test_org_links_share_the_published_shape_and_read_their_own_table():
    lakehouse = Lakehouse()
    entry = collect(lakehouse)["org_links"]
    assert entry["rows"] == [{"workspace_id": WS, "item_id": ITEM, "artifact_type": "Report"}]
    assert entry["snapshot_at"] == (NOW - timedelta(hours=5)).isoformat()
    statement = lakehouse.matching(ORG_LINKS)[0]
    assert "SELECT MAX(TransferDate) FROM dbo.widelyshared_organization_links" in statement
    assert "i.fuam_deleted = 0" in statement and "publishtoweb" not in statement


@pytest.mark.parametrize("snapshot", [[], [{"snapshot_at": None}]])
def test_org_links_without_a_transfer_are_unavailable_not_empty(snapshot):
    result = collect(Lakehouse().override(ORG_SNAPSHOT, snapshot))
    assert (result["org_links"]["status"], result["org_links"]["reason_code"]) == ("unavailable", "no_transfer_snapshot")
    assert result["publish_to_web"]["status"] == "collected"


def test_model_usage_counts_only_consumer_operations_per_item():
    lakehouse = Lakehouse()
    entry = collect(lakehouse)["model_usage"]
    assert entry["rows"] == [{"item_id": ITEM, "query_count": 7}]
    assert type(entry["rows"][0]["query_count"]) is int
    assert entry["window_days"] == THRESHOLDS["operations"]["window_days"]
    statement = lakehouse.matching(USAGE)[0]
    assert "(OperationName = 'Query' OR OperationName = 'XMLA Read Operation')" in statement
    assert "Refresh" not in statement and "[Date] >= '" in statement


def test_model_lookup_asks_only_for_the_unresolved_ids_and_keeps_workspace_and_model():
    lakehouse = Lakehouse()
    entry = collect(lakehouse, model_ids={ITEM, DOMAIN})["model_lookup"]
    assert entry["rows"] == [{"model_id": ITEM, "workspace_id": OTHER_WS}]
    (statement,) = lakehouse.matching(LOOKUP_IDS)
    assert {ITEM, DOMAIN} == set(in_lists(statement)[0]) and "fuam_deleted = 0" in statement


def test_model_lookup_without_ids_runs_no_identity_statement():
    lakehouse = Lakehouse()
    entry = collect(lakehouse)["model_lookup"]
    assert entry["status"] == "collected" and entry["rows"] == [] and not lakehouse.matching(LOOKUP_IDS)


@pytest.mark.parametrize("hours, expected", [(2, "recent"), (25, "stale")])
def test_model_lookup_freshness_follows_the_age_limit(hours, expected):
    lakehouse = Lakehouse().override(LOOKUP_NEWEST, [{"newest": NOW - timedelta(hours=hours)}])
    assert collect(lakehouse)["model_lookup"]["freshness_status"] == expected


# --- bounded statements ------------------------------------------------------------------------

def guids(count, offset=1):
    return {str(UUID(int=offset + index)) for index in range(count)}


def test_large_scopes_are_chunked_to_at_most_500_ids_per_statement():
    workspaces, capacities = guids(CHUNK * 2 + 201), guids(CHUNK + 1, offset=10_000)
    lakehouse = Lakehouse(**{ACTIVITY_ROWS: [], ITEM_AGE: [], DOMAINS: [], OPERATIONS: [], TOTALS: [],
                             THROTTLING: [], PUBLISHED: [], ORG_LINKS: [], REFRESHES: [], USAGE: [],
                             LOOKUP_IDS: []})
    collect(lakehouse, workspace_ids=workspaces, capacity_ids=capacities, model_ids=guids(CHUNK + 1, offset=50_000))
    for marker, scope, expected in ((ACTIVITY_ROWS, workspaces, 3), (ITEM_AGE, workspaces, 3), (DOMAINS, workspaces, 3),
                                    (OPERATIONS, workspaces, 3), (PUBLISHED, workspaces, 3), (ORG_LINKS, workspaces, 3), (USAGE, workspaces, 3),
                                    (REFRESHES, workspaces, 3),
                                    (TOTALS, capacities, 2), (THROTTLING, capacities, 2)):
        statements = lakehouse.matching(marker)
        assert len(statements) == expected, marker
        lists = [group for statement in statements for group in in_lists(statement)]
        assert len(lists) == expected
        assert all(len(group) <= CHUNK for group in lists), marker
        assert sorted(value for group in lists for value in group) == sorted(scope), marker


def test_an_empty_scope_asks_for_no_identity_statements():
    lakehouse = Lakehouse()
    result = collect(lakehouse, workspace_ids=set(), capacity_ids=set())
    assert not any(in_lists(statement) for statement in lakehouse.statements)
    assert result["domains"]["rows"] == [] and result["capacity_totals"]["rows"] == []


def test_every_in_list_holds_only_canonical_guid_literals():
    lakehouse = Lakehouse()
    collect(lakehouse)
    lists = [group for statement in lakehouse.statements for group in in_lists(statement)]
    assert lists
    for group in lists:
        for value in group:
            assert value == str(UUID(value))


def test_statements_contain_no_string_literal_other_than_guids_and_dates():
    lakehouse = Lakehouse()
    collect(lakehouse)
    for statement in lakehouse.statements:
        for literal in re.findall(r"'([^']*)'", statement):
            assert re.fullmatch(r"[0-9a-f-]{36}|\d{4}-\d{2}-\d{2}|Completed|Failed|Query|XMLA Read Operation|SemanticModel", literal), (literal, statement)


# --- FCA ---------------------------------------------------------------------------------------

FCA_CONNECTION = {"workspace_id": WS, "lakehouse_id": OTHER_WS, "max_age_hours": 48}


def tag_rows(*pairs):
    return [{"ResourceId": resource, "Tags": json.dumps({tag: "private-value"})} for resource, tag in pairs]


def test_capacity_tags_shape_includes_untagged_mapped_capacities():
    statements = []

    def query(statement):
        statements.append(statement)
        return tag_rows((ARM.upper(), "Owner"), (ARM, "cost-center"), (ARM, "Owner"), ("/unmapped/resource", "x"))

    entry = fca_capacity_tags(query, FCA_CONNECTION, {ARM: CAP, OTHER_ARM: OTHER_CAP})
    assert entry["status"] == "collected" and entry["table"] == "focus"
    assert entry["rows"] == [{"capacity_id": OTHER_CAP, "tag_names": []},
                             {"capacity_id": CAP, "tag_names": ["Owner", "cost-center"]}]
    assert (entry["observed_at"], entry["freshness_status"], entry["mapped_capacity_count"]) == (None, "unknown", 2)
    assert (entry["source_workspace_id"], entry["source_lakehouse_id"]) == (WS, OTHER_WS)
    assert len(statements) == 1
    assert ARM in statements[0] and OTHER_ARM in statements[0]
    assert "dbo.focus" in statements[0] and "BilledCost" not in statements[0]
    assert "private-value" not in json.dumps(entry)


@pytest.mark.parametrize("raw, expected", [
    (None, []), ("", []), ("   ", []), ("{}", []),
    ('{"Owner": "a", "cost-center": "b"}', ["Owner", "cost-center"]),
    # Azure exports sometimes omit the surrounding braces.
    ('"Owner": "a", "env": "b"', ["Owner", "env"]),
])
def test_capacity_tag_names_are_taken_from_the_focus_tags_text(raw, expected):
    entry = fca_capacity_tags(lambda statement: [{"ResourceId": ARM, "Tags": raw}], FCA_CONNECTION, {ARM: CAP})
    assert entry["status"] == "collected"
    assert entry["rows"] == [{"capacity_id": CAP, "tag_names": sorted(expected)}]
    assert "a" not in [value for row in entry["rows"] for value in row["tag_names"] if value in ("a", "b")]


@pytest.mark.parametrize("raw", ["not json at all", "[1, 2]", "42"])
def test_unreadable_focus_tags_make_the_family_unavailable_without_leaking(raw):
    entry = fca_capacity_tags(lambda statement: [{"ResourceId": ARM, "Tags": raw}], FCA_CONNECTION, {ARM: CAP})
    assert (entry["status"], entry["reason_code"]) == ("unavailable", "incompatible_tags")
    assert raw not in json.dumps(entry)


def test_capacity_tags_without_a_mapping_asks_nothing():
    def forbidden(statement):
        pytest.fail("No statement is expected without a mapping.")

    entry = fca_capacity_tags(forbidden, FCA_CONNECTION, {})
    assert (entry["status"], entry["rows"], entry["mapped_capacity_count"]) == ("collected", [], 0)


def test_capacity_tags_are_chunked_to_at_most_500_resources():
    mapping = {f"/subscriptions/{SUBSCRIPTION}/resourcegroups/rg/providers/microsoft.fabric/capacities/c{index}": str(
        UUID(int=index)) for index in range(CHUNK + 120)}
    statements = []

    def query(statement):
        statements.append(statement)
        return []

    entry = fca_capacity_tags(query, FCA_CONNECTION, mapping)
    assert len(statements) == 2
    lists = [group for statement in statements for group in in_lists(statement)]
    assert [len(group) for group in lists] == [CHUNK, 120]
    assert sorted(value for group in lists for value in group) == sorted(mapping)
    assert len(entry["rows"]) == len(mapping)


def test_capacity_tags_failure_is_isolated_and_leaks_nothing():
    def query(statement):
        raise RuntimeError(SECRET)

    entry = fca_capacity_tags(query, FCA_CONNECTION, {ARM: CAP})
    assert (entry["status"], entry["reason_code"], entry["rows"]) == ("unavailable", "query_failed", [])
    assert SECRET not in json.dumps(entry)


@pytest.mark.parametrize("row", [{"ResourceId": ARM}, {"ResourceId": ARM, "Tags": "{}", "BilledCost": "secret"}])
def test_capacity_tags_incompatible_schema(row):
    entry = fca_capacity_tags(lambda statement: [row], FCA_CONNECTION, {ARM: CAP})
    assert (entry["status"], entry["reason_code"]) == ("unavailable", "incompatible_schema")
    assert "secret" not in json.dumps(entry)


# --- bind --------------------------------------------------------------------------------------

@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    for name in ("WORKSPACES_CONFIG", "FAR_CONFIG_SNAPSHOT", "THRESHOLDS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    values = copy.deepcopy(read_yaml(ROOT / "config" / "policy.yaml"))
    values["scope"]["capacity_arm_resource_ids"] = f"{CAP}={ARM}, {UNUSED_CAP}={OTHER_ARM}"
    config = local_configuration()
    config["profiles"] = declared_profiles(values)
    config["sources"]["fuam"].update(enabled=True, workspace_id=WS, lakehouse_id=OTHER_WS)
    config["sources"]["fca"].update(enabled=True, workspace_id=OTHER_WS, lakehouse_id=WS)
    return read_snapshot(capture(tmp_path, config))


class Estate:
    """Answers the bind queries of both modules; fact statements go to a Lakehouse."""

    def __init__(self, lakehouse=None, tags=None, fail_tags=False):
        self.lakehouse = lakehouse or Lakehouse()
        self.calls: list[tuple[str, str]] = []
        self.tags = tags if tags is not None else [(ARM, "owner")]
        self.fail_tags = fail_tags

    def __call__(self, source, statement):
        self.calls.append((source, statement))
        if source == "fca":
            if "dbo.focus" in statement:
                if self.fail_tags:
                    raise RuntimeError(SECRET)
                return tag_rows(*self.tags)
            return [{"ResourceId": ARM, "ResourceName": "prod", "RegionId": "westeurope", "ResourceKey": 1}]
        if "dbo.tenant_settings" in statement:
            return []
        for table, (id_column, columns, _) in CONTRACTS.items():
            if statement.startswith(f"SELECT {', '.join(columns)} FROM dbo.{table} "):
                row = {column: "metadata" for column in columns}
                row[id_column] = in_lists(statement)[0][0]
                if table == "active_items":
                    row.update(id=ITEM, capacityId=CAP)
                if table == "workspaces":
                    row["CapacityId"] = CAP
                row["fuam_modified_at"] = NOW - timedelta(hours=1)
                return [row]
        return self.lakehouse(statement)

    def statements(self, source):
        return [statement for origin, statement in self.calls if origin == source]


SCOPE = [{"id": WS, "capacityId": CAP}, {"id": OTHER_WS, "capacityId": OTHER_CAP}]


def test_bind_populates_every_rule_fact_family_and_coverage(snapshot):
    result = bind(snapshot, SCOPE, Estate(), now=NOW)
    assert set(result["rule_facts"]) == ENRICHMENT_FAMILIES
    assert all(entry["status"] == "collected" for entry in result["rule_facts"].values())
    covered = {entry["family"]: entry for entry in result["coverage"] if "family" in entry}
    assert set(covered) == ENRICHMENT_FAMILIES
    for family, entry in covered.items():
        assert entry["status"] == "collected"
        assert entry["source"] == ("fca" if family == "capacity_tags" else "fuam")
        assert entry["table"] == result["rule_facts"][family]["table"]
        assert entry["row_count"] == len(result["rule_facts"][family]["rows"])
    json.dumps(result, default=str)


def test_bind_isolates_a_failing_family_without_raising(snapshot):
    estate = Estate(Lakehouse().override(OPERATIONS, RuntimeError(SECRET)), fail_tags=True)
    result = bind(snapshot, SCOPE, estate, now=NOW)
    covered = {entry["family"]: entry for entry in result["coverage"] if "family" in entry}
    for family in ("item_operations", "capacity_tags"):
        assert result["rule_facts"][family]["status"] == "unavailable"
        assert (covered[family]["status"], covered[family]["reason"], covered[family]["row_count"]) == (
            "unavailable", "query_failed", 0)
    assert all(entry["status"] == "collected" for family, entry in result["rule_facts"].items()
               if family not in {"item_operations", "capacity_tags"})
    assert SECRET not in json.dumps(result, default=str)
    assert result["tables"]  # the baseline enrichment tables are still produced


def test_bind_builds_capacity_tags_only_for_mapped_capacities_in_scope(snapshot):
    estate = Estate()
    result = bind(snapshot, SCOPE, estate, now=NOW)
    # CAP is mapped and reviewed; OTHER_CAP is reviewed but unmapped; UNUSED_CAP is mapped but not reviewed.
    assert result["rule_facts"]["capacity_tags"]["rows"] == [{"capacity_id": CAP, "tag_names": ["owner"]}]
    assert result["rule_facts"]["capacity_tags"]["mapped_capacity_count"] == 1
    fca = "\n".join(estate.statements("fca"))
    assert ARM in fca and OTHER_ARM not in fca
    resources = next(entry for entry in result["coverage"] if entry.get("table") == "resources")
    assert resources["unmapped_capacity_count"] == 1


def test_bind_without_any_mapped_review_capacity_collects_empty_tags(snapshot):
    estate = Estate()
    result = bind(snapshot, [{"id": WS, "capacityId": OTHER_CAP}, {"id": OTHER_WS, "capacityId": None}], estate, now=NOW)
    assert result["rule_facts"]["capacity_tags"]["rows"] == []
    assert not any("dbo.focus" in statement for statement in estate.statements("fca"))


def test_bind_scopes_every_fact_statement_to_canonical_review_ids(snapshot):
    estate = Estate()
    bind(snapshot, [{"id": WS.upper(), "capacityId": CAP.upper()}, {"id": OTHER_WS, "capacityId": None}], estate, now=NOW)
    fuam = [statement for statement in estate.statements("fuam") if "dbo.tenant_settings" not in statement]
    lists = [group for statement in fuam for group in in_lists(statement)]
    assert lists
    allowed = {WS, OTHER_WS, CAP}
    assert {value for group in lists for value in group} <= allowed
    totals = [group for statement in fuam if TOTALS in statement for group in in_lists(statement)]
    assert totals == [[CAP]]


@pytest.mark.parametrize("scope", [
    [{"id": "x'; DROP TABLE dbo.workspaces; --", "capacityId": CAP}],
    [{"id": WS, "capacityId": "x') OR 1=1 --"}],
    [{"id": WS + "'", "capacityId": None}],
])
def test_bind_rejects_non_guid_identities_before_any_query(snapshot, scope):
    def forbidden(source, statement):
        pytest.fail("No statement may be built from an invalid identity.")

    with pytest.raises(ValueError, match="GUID"):
        bind(snapshot, scope, forbidden, now=NOW)


def test_bind_without_fuam_records_only_the_fca_family(snapshot, tmp_path_factory):
    config = copy.deepcopy(snapshot["configuration"])
    config["sources"]["fuam"].update(enabled=False)
    config["sources"]["fca"].update(enabled=True)
    estate = Estate()
    result = bind({**snapshot, "configuration": config}, SCOPE, estate, now=NOW)
    assert set(result["rule_facts"]) == {"capacity_tags"}
    assert not estate.statements("fuam")
    assert {"source": "fuam", "status": "disabled"} in result["coverage"]


def test_bind_with_an_empty_scope_collects_no_facts(snapshot):
    def forbidden(source, statement):
        pytest.fail("An empty scope must not query a source.")

    result = bind(snapshot, [], forbidden, now=NOW)
    assert result["rule_facts"] == {}
    assert all(entry["status"] == "empty_review_scope" for entry in result["coverage"])

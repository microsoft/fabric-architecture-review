# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Native execution evidence contracts, including intentionally incomplete data."""
import json

import pytest

from reports.execution_history import build_execution_evidence


NOW = "2026-09-22T06:00:00Z"
START = "2026-09-21T12:00:00Z"
END = "2026-09-21T12:00:01.234Z"


def write(tmp_path, filename, value):
    (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")


def snapshot(tmp_path, records, *, kind="SemanticModel", status="collected", **extra):
    semantic = kind == "SemanticModel"
    entry = {
        "workspaceId": "ws", "workspaceName": "Workspace", "itemId": "item",
        "itemName": "Item", "itemType": kind, "collectionStatus": status,
        "noticeCode": "refresh_top_limit" if semantic else "retained_jobs",
        "executions": records,
    }
    entry.update(extra)
    write(tmp_path, "semantic_models.json" if semantic else "pipelines_notebooks.json", {
        "datasets" if semantic else "pipelines": [], "notebooks": [],
        "executionEvidence": [entry],
    })
    return entry


def refresh(**changes):
    return {"requestId": "execution", "status": "Completed", "startTime": START, "endTime": END, **changes}


def job(**changes):
    return {"id": "execution", "status": "Completed", "startTimeUtc": START, "endTimeUtc": END, **changes}


def item_coverage(result):
    return next(row for row in result["gold_execution_coverage"] if row["item_id"] == "item")


def test_exact_shapes_int64_duration_and_dedup_across_pages(tmp_path):
    snapshot(tmp_path, [refresh(), refresh()])
    result = build_execution_evidence(tmp_path, "run-1", NOW)
    assert len(result["gold_item_executions"]) == 1
    row = result["gold_item_executions"][0]
    assert set(row) == {
        "run_id", "run_timestamp", "execution_key", "execution_id", "workspace_id", "workspace_name",
        "item_id", "item_name", "item_type", "execution_type", "status", "start_time", "end_time",
        "duration_ms", "source",
    }
    assert row["duration_ms"] == 1234
    assert type(row["duration_ms"]) is int
    assert row["execution_type"] == "refresh"
    assert row["status"] == "completed"
    assert row["run_timestamp"] == NOW
    coverage = item_coverage(result)
    assert set(coverage) == {
        "run_id", "run_timestamp", "workspace_id", "workspace_name", "item_id", "item_name", "item_type",
        "collection_status", "observed_execution_count", "oldest_start_time", "newest_start_time",
        "history_scope", "notice", "source",
    }
    assert type(coverage["observed_execution_count"]) is int
    assert coverage["observed_execution_count"] == 1
    assert coverage["collection_status"] == "collected"
    assert coverage["history_scope"] == "recent_retained_observations"
    assert "not full weekly history" in coverage["notice"]


@pytest.mark.parametrize("native,expected", [
    ("Completed", "completed"), ("Failed", "failed"), ("Cancelled", "cancelled"),
    ("Canceled", "cancelled"), ("InProgress", "in_progress"), ("NotStarted", "not_started"),
    ("Deduped", "deduped"), ("Disabled", "disabled"), ("Unknown", "unknown"),
    ("future-status", "unknown"), (None, "unknown"), ({}, "unknown"),
])
def test_status_mapping_does_not_turn_active_or_cancelled_into_failed(tmp_path, native, expected):
    snapshot(tmp_path, [job(status=native, endTimeUtc=None)], kind="DataPipeline")
    result = build_execution_evidence(tmp_path, "run", NOW)
    row = result["gold_item_executions"][0]
    assert row["status"] == expected
    assert row["duration_ms"] is None
    assert row["end_time"] is None
    if native in ("future-status", None) or isinstance(native, dict):
        assert item_coverage(result)["collection_status"] == "partial"
        assert "unrecognized_status=1" in item_coverage(result)["notice"]


def test_powerbi_unknown_is_not_inferred_to_be_active(tmp_path):
    snapshot(tmp_path, [refresh(status="Unknown", endTime=None)])
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"][0]["status"] == "unknown"
    assert "unknown_status_may_include_active_execution" in item_coverage(result)["notice"]


def test_observations_change_but_key_does_not_and_builder_is_pure(tmp_path):
    snapshot(tmp_path, [refresh(status="Unknown", endTime=None)])
    first = build_execution_evidence(tmp_path, "run-1", NOW)
    snapshot(tmp_path, [refresh()])
    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    second = build_execution_evidence(tmp_path, "run-2", "2026-09-23T06:00:00Z")
    assert before == {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    assert first["gold_item_executions"][0]["execution_key"] == second["gold_item_executions"][0]["execution_key"]
    assert first["gold_item_executions"][0]["run_id"] != second["gold_item_executions"][0]["run_id"]
    assert first["gold_item_executions"][0]["status"] == "unknown"
    assert second["gold_item_executions"][0]["status"] == "completed"


def test_workspace_and_type_are_part_of_stable_identity(tmp_path):
    entry = snapshot(tmp_path, [job()], kind="DataPipeline")
    write(tmp_path, "pipelines_notebooks.json", {"executionEvidence": [
        entry, {**entry, "workspaceId": "other"}, {**entry, "itemType": "Notebook"},
    ]})
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert len({row["execution_key"] for row in result["gold_item_executions"]}) == 3
    assert len([row for row in result["gold_execution_coverage"] if row["item_id"] == "item"]) == 3


@pytest.mark.parametrize("value", [None, "", "bad", "2026-09-21", 42, float("nan"), {}, "9999-12-31T23:59:59-10:00"])
def test_missing_malformed_or_overflow_timestamp_is_evidenced(tmp_path, value):
    snapshot(tmp_path, [refresh(startTime=value)])
    result = build_execution_evidence(tmp_path, "run", NOW)
    row = result["gold_item_executions"][0]
    assert row["start_time"] is None
    assert row["duration_ms"] is None
    coverage = item_coverage(result)
    assert coverage["oldest_start_time"] is None
    assert coverage["newest_start_time"] is None
    assert coverage["collection_status"] == "partial"
    assert "missing_or_invalid_start_time=1" in coverage["notice"]


def test_native_utc_naive_and_offset_times_are_normalized(tmp_path):
    snapshot(tmp_path, [job(startTimeUtc="2026-09-21T12:00:00.0000000",
                            endTimeUtc="2026-09-21T14:00:01.2345678+02:00")], kind="Notebook")
    result = build_execution_evidence(tmp_path, "run", NOW)
    row = result["gold_item_executions"][0]
    assert row["start_time"] == START
    assert row["end_time"] == "2026-09-21T12:00:01.234567Z"
    assert row["duration_ms"] == 1234


@pytest.mark.parametrize("changes,issue", [
    ({"endTime": "not-a-time"}, "invalid_end_time"),
    ({"endTime": "2026-09-20T00:00:00Z"}, "invalid_time_order"),
])
def test_invalid_end_times_are_not_negative_or_zero_duration(tmp_path, changes, issue):
    snapshot(tmp_path, [refresh(**changes)])
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"][0]["duration_ms"] is None
    assert issue in item_coverage(result)["notice"]


@pytest.mark.parametrize("native_id", [None, "", False, [], {}, float("nan"), float("inf"), 2**70, "https://secret/token"])
def test_invalid_ids_never_fabricate_execution_identity(tmp_path, native_id):
    snapshot(tmp_path, [refresh(requestId=native_id)])
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"] == []
    coverage = item_coverage(result)
    assert coverage["collection_status"] == "invalid_data"
    assert coverage["observed_execution_count"] == 0
    assert "missing_or_invalid_execution_id=1" in coverage["notice"]


def test_legacy_numeric_refresh_id_supported_and_request_id_preferred(tmp_path):
    snapshot(tmp_path, [refresh(requestId=None, id=12), refresh(id=34)])
    rows = build_execution_evidence(tmp_path, "run", NOW)["gold_item_executions"]
    assert {row["execution_id"] for row in rows} == {"12", "execution"}


@pytest.mark.parametrize("status", ["forbidden", "not_found", "error", "not_collected", "invalid_data", "partial"])
def test_failed_collection_is_never_empty_success(tmp_path, status):
    snapshot(tmp_path, [], status=status, noticeCode="request_failed")
    coverage = item_coverage(build_execution_evidence(tmp_path, "run", NOW))
    assert coverage["collection_status"] == status
    assert coverage["observed_execution_count"] == 0


def test_successful_empty_distinct_from_missing_source_and_history(tmp_path):
    snapshot(tmp_path, [])
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert item_coverage(result)["collection_status"] == "empty"
    assert all(row["collection_status"] == "not_collected"
               for row in result["gold_execution_coverage"] if row["item_type"] != "SemanticModel")
    write(tmp_path, "semantic_models.json", {"datasets": [{"id": "item", "workspaceId": "ws"}]})
    assert item_coverage(build_execution_evidence(tmp_path, "run", NOW))["collection_status"] == "not_collected"


def test_service_diagnostics_urls_connection_strings_and_unknown_status_never_leak(tmp_path):
    secret = "https://private.invalid/?token=secret;Server=private;Password=secret"
    snapshot(tmp_path, [refresh(status=secret, serviceExceptionJson=secret, error={"message": secret})],
             itemName=secret, workspaceName="Bearer private-token", noticeCode=secret, message=secret)
    public = json.dumps(build_execution_evidence(tmp_path, "run", NOW))
    for value in ("https://", "private", "secret", "Password=", "Bearer"):
        assert value not in public
    assert "[redacted]" in public


def test_malformed_rows_explicitly_degrade_coverage(tmp_path):
    snapshot(tmp_path, [None, refresh(), refresh(requestId=None)])
    coverage = item_coverage(build_execution_evidence(tmp_path, "run", NOW))
    assert coverage["collection_status"] == "partial"
    assert coverage["observed_execution_count"] == 1
    assert "invalid_execution_record=1" in coverage["notice"]
    assert "missing_or_invalid_execution_id=1" in coverage["notice"]


def test_repeated_entries_merge_and_conflicting_copies_prefer_ended_observation(tmp_path):
    entry = snapshot(tmp_path, [refresh(status="Unknown", endTime=None)])
    write(tmp_path, "semantic_models.json", {"datasets": [], "executionEvidence": [
        entry, {**entry, "executions": [refresh()]},
    ]})
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert len(result["gold_item_executions"]) == 1
    assert result["gold_item_executions"][0]["status"] == "completed"
    assert item_coverage(result)["collection_status"] == "partial"


def test_missing_collector_uses_known_scanner_items_for_coverage(tmp_path):
    write(tmp_path, "scanner.json", {"workspaces": [{
        "id": "ws", "name": "Workspace", "datasets": [{"id": "item", "name": "Model"}],
        "dataPipelines": [{"id": "pipeline", "name": "Pipeline"}],
    }]})
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"] == []
    assert item_coverage(result)["collection_status"] == "not_collected"
    assert item_coverage(result)["workspace_name"] == "Workspace"


def test_partial_legacy_jobs_preserve_observations_without_claiming_completeness(tmp_path):
    write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": [{"id": "item", "workspaceId": "ws"}], "notebooks": [],
        "jobs": {"item": [job()]}, "failedWorkspaces": ["ws"], "collectionComplete": False,
    })
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert len(result["gold_item_executions"]) == 1
    assert item_coverage(result)["collection_status"] == "partial"


def test_inventory_failure_is_attributed_to_known_items(tmp_path):
    write(tmp_path, "scanner.json", {"workspaces": [{
        "id": "ws", "dataPipelines": [{"id": "item"}],
    }]})
    write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": [], "notebooks": [], "executionEvidence": [],
        "collectionComplete": False, "failedWorkspaces": ["ws"],
        "inventoryErrors": [{"workspaceId": "ws", "itemType": "DataPipeline", "statusCode": 403,
                             "message": "secret"}],
    })
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert item_coverage(result)["collection_status"] == "forbidden"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("known_item", [False, True])
def test_inventory_gap_keeps_workspace_even_when_other_workspaces_succeeded(tmp_path, known_item):
    entry = snapshot(tmp_path, [], kind="DataPipeline", status="empty")
    write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": [{"workspaceId": "denied", "id": "known"}] if known_item else [],
        "notebooks": [], "executionEvidence": [entry], "collectionComplete": False,
        "inventoryErrors": [
            {"workspaceId": "denied", "workspaceName": "Denied workspace",
             "itemType": "DataPipeline", "statusCode": 403},
            {"workspaceId": "outside", "itemType": "DataPipeline", "statusCode": 403},
        ],
        "workspaceScope": ["ws", "denied"],
    })
    rows = build_execution_evidence(tmp_path, "run", NOW)["gold_execution_coverage"]
    gaps = [row for row in rows if row["workspace_id"] == "denied" and not row["item_id"]]
    assert len(gaps) == 1
    assert gaps[0]["collection_status"] == "forbidden"
    assert gaps[0]["workspace_name"] == "Denied workspace"
    assert gaps[0]["item_type"] == "DataPipeline"
    assert not any(row["workspace_id"] == "outside" for row in rows)
    assert not any(row["item_type"] == "Notebook" for row in rows)


def test_item_history_failure_does_not_invent_notebook_inventory_gap(tmp_path):
    entry = snapshot(tmp_path, [], kind="DataPipeline", status="forbidden")
    write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": [], "notebooks": [], "executionEvidence": [entry],
        "collectionComplete": False, "failedWorkspaces": ["ws"], "inventoryErrors": [],
    })
    rows = build_execution_evidence(tmp_path, "run", NOW)["gold_execution_coverage"]
    assert not any(row["item_type"] == "Notebook" for row in rows)


def test_job_item_identity_cannot_borrow_another_items_execution(tmp_path):
    snapshot(tmp_path, [job(itemId="elsewhere")], kind="DataPipeline")
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"] == []
    assert item_coverage(result)["collection_status"] == "invalid_data"
    assert "mismatched_execution_item_id=1" in item_coverage(result)["notice"]


def test_collection_failure_without_inventory_is_explicit_and_redacted(tmp_path):
    write(tmp_path, "semantic_models.json", {
        "collectionComplete": False,
        "collectionErrors": [{"statusCode": 403, "message": "https://secret.invalid"}],
    })
    result = build_execution_evidence(tmp_path, "run", NOW)
    coverage = next(row for row in result["gold_execution_coverage"] if row["item_type"] == "SemanticModel")
    assert coverage["collection_status"] == "forbidden"
    assert coverage["item_id"] is None
    assert "secret" not in json.dumps(result)


def test_legacy_identity_case_is_canonicalized(tmp_path):
    write(tmp_path, "semantic_models.json", {
        "datasets": [{"id": "ITEM", "workspaceId": "WS"}],
        "refreshes": {"ITEM": [refresh()]},
    })
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"][0]["item_id"] == "item"
    assert result["gold_item_executions"][0]["workspace_id"] == "ws"


def test_time_bounds_use_chronological_not_lexical_order(tmp_path):
    snapshot(tmp_path, [
        refresh(requestId="a", startTime="2026-09-21T12:00:00Z"),
        refresh(requestId="b", startTime="2026-09-21T12:00:00.001Z"),
    ])
    coverage = item_coverage(build_execution_evidence(tmp_path, "run", NOW))
    assert coverage["oldest_start_time"] == START
    assert coverage["newest_start_time"] == "2026-09-21T12:00:00.001000Z"


def test_ambiguous_legacy_workspace_id_does_not_borrow_history(tmp_path):
    write(tmp_path, "semantic_models.json", {
        "datasets": [{"id": "item", "workspaceId": ws} for ws in ("one", "two")],
        "refreshes": {"item": [refresh()]},
    })
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"] == []
    assert "ambiguous_legacy_item_id" in item_coverage(result)["notice"]


def test_new_evidence_is_authoritative_over_legacy_and_respects_scope(tmp_path):
    entry = snapshot(tmp_path, [], status="forbidden")
    write(tmp_path, "semantic_models.json", {
        "datasets": [{"id": "item", "workspaceId": "ws"}], "refreshes": {"item": [refresh()]},
        "executionEvidence": [entry, {**entry, "workspaceId": "outside", "executions": [refresh()]}],
        "workspaceScope": ["ws"],
    })
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert result["gold_item_executions"] == []
    assert item_coverage(result)["collection_status"] == "forbidden"
    assert not any(row["workspace_id"] == "outside" for row in result["gold_execution_coverage"])


def test_missing_source_inherits_recorded_scope_for_inventory_fallback(tmp_path):
    write(tmp_path, "scanner.json", {"workspaces": [
        {"id": workspace, "datasets": [{"id": workspace + "-model"}]}
        for workspace in ("inside", "outside")
    ]})
    write(tmp_path, "pipelines_notebooks.json", {
        "pipelines": [], "notebooks": [], "executionEvidence": [], "workspaceScope": ["inside"],
    })
    rows = build_execution_evidence(tmp_path, "run", NOW)["gold_execution_coverage"]
    assert any(row["item_id"] == "inside-model" for row in rows)
    assert not any(row["workspace_id"] == "outside" for row in rows)


def test_contradictory_empty_collection_status_does_not_hide_records(tmp_path):
    snapshot(tmp_path, [refresh()], status="empty")
    result = build_execution_evidence(tmp_path, "run", NOW)
    assert len(result["gold_item_executions"]) == 1
    assert item_coverage(result)["collection_status"] == "partial"
    assert "empty_status_with_records" in item_coverage(result)["notice"]


@pytest.mark.parametrize("contents", ["{", "[]", "null"])
def test_corrupt_source_creates_explicit_inventory_gap(tmp_path, contents):
    (tmp_path / "semantic_models.json").write_text(contents)
    rows = build_execution_evidence(tmp_path, "run", NOW)["gold_execution_coverage"]
    coverage = next(row for row in rows if row["item_type"] == "SemanticModel")
    assert coverage["item_id"] is None
    assert "source_" in coverage["notice"]
    assert coverage["collection_status"] != "empty"


@pytest.mark.parametrize("run_id,timestamp", [("", NOW), ("run", "bad"), ("run", "2026-09-22")])
def test_invalid_run_metadata_raises(tmp_path, run_id, timestamp):
    with pytest.raises(ValueError, match="run ID"):
        build_execution_evidence(tmp_path, run_id, timestamp)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline success-only cooldown, persistence and generated pipeline contracts."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from orchestration.deployment import (
    DEFAULTS, RECORD_REVIEW_ACTIVITY, SELECTION_PARAMETERS,
    build_parent_pipeline, notebook_parameter, runtime_notebook,
)
from orchestration.review_history import (
    cooldown_days, get_recently_reviewed_workspace_ids, record_completed_review,
)
from orchestration.runtime import run_completion, select_workspaces, validate_config


ROOT = Path(__file__).resolve().parents[1]
HOST = "11111111-1111-1111-1111-111111111111"
FUAM = "22222222-2222-2222-2222-222222222222"
PIPELINE = "33333333-3333-3333-3333-333333333333"
W1 = "44444444-4444-4444-4444-444444444444"
W2 = "55555555-5555-5555-5555-555555555555"
W3 = "66666666-6666-6666-6666-666666666666"
NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)


class ReviewHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="far-review-history-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config = {
            **DEFAULTS, "FUAM_WORKSPACE_ID": FUAM, "CHILD_WORKSPACE_ID": HOST,
            "CHILD_PIPELINE_ID": PIPELINE, "TOP_N": "2",
        }
        self.rows = [
            {"workspace_id": W1, "metric_value": 30},
            {"workspace_id": W2, "metric_value": 20},
            {"workspace_id": W3, "metric_value": 10},
        ]

    def select(self, **overrides):
        run = str(uuid4())
        with patch("orchestration.runtime.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = select_workspaces(
                {**self.config, **overrides}, run,
                read_source=lambda config: ("fuam", self.rows), state_dir=self.directory,
            )
        return result, json.loads((self.directory / (run + ".json")).read_text(encoding="utf-8"))

    def complete(self, ids, completed_at=NOW - timedelta(hours=1), **config):
        parent, child = str(uuid4()), str(uuid4())
        options = {**self.config, **config}
        selection = {
            "status": "selected", "parent_run_id": parent,
            "child_workspace_id": options["CHILD_WORKSPACE_ID"],
            "child_pipeline_id": options["CHILD_PIPELINE_ID"],
            "workspace_ids": ",".join(ids),
            "workspaces": [{"workspace_id": wid} for wid in ids],
        }
        (self.directory / (parent + ".json")).write_text(json.dumps(selection), encoding="utf-8")
        with patch("orchestration.review_history.datetime", wraps=datetime) as clock:
            clock.now.return_value = completed_at
            record = record_completed_review(options, parent, child, state_dir=self.directory)
        return record

    def recent(self, days=7):
        return get_recently_reviewed_workspace_ids(
            days, state_dir=self.directory, workspace_id=HOST, pipeline_id=PIPELINE, now=NOW,
        )

    def test_default_and_omitted_parameter_preserve_selection_without_history_reads(self):
        (self.directory / "completed").mkdir()
        (self.directory / "completed" / "invalid.json").write_text("not-json", encoding="utf-8")
        for omit in (False, True):
            if omit:
                self.config.pop("SKIP_REVIEWED_WITHIN_DAYS")
            result, state = self.select()
            self.assertEqual(result["workspace_ids"], f"{W1},{W2}")
            self.assertEqual(state["review_cooldown"], {"days": 0, "excluded_workspace_ids": []})
        with patch.object(Path, "iterdir", side_effect=AssertionError("Disabled history must not be read")):
            self.assertEqual(self.recent(days=0), set())

    def test_cooldown_parameter_validation_at_runtime_and_deployment(self):
        for value in ("0", "7", " 30 "):
            self.assertEqual(cooldown_days(value), int(value))
        for value in ("-1", "1.5", "bad", "", "7.0", "999999999999", True, 7, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "SKIP_REVIEWED_WITHIN_DAYS"):
                validate_config({**self.config, "SKIP_REVIEWED_WITHIN_DAYS": value})
        for value in ("-1", "bad", "1.5"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "SKIP_REVIEWED_WITHIN_DAYS"):
                self.parent(defaults={"SKIP_REVIEWED_WITHIN_DAYS": value})

    def test_round_trip_excludes_recent_success_before_top_n_for_both_metrics(self):
        record = self.complete([W1])
        self.assertEqual(self.recent(), {W1})
        self.assertEqual(record["status"], "succeeded")
        for metric in ("cu_seconds", "recorded_throttling_minutes"):
            result, state = self.select(SKIP_REVIEWED_WITHIN_DAYS="7", RANKING_METRIC=metric)
            self.assertEqual(result["workspace_ids"], f"{W2},{W3}")
            self.assertEqual(state["review_cooldown"], {"days": 7, "excluded_workspace_ids": [W1]})

    def test_cooldown_overrides_allowlist_without_expanding_scope(self):
        self.complete([W1])
        result, _ = self.select(SKIP_REVIEWED_WITHIN_DAYS="7", WORKSPACE_IDS=f"{W1},{W3}")
        self.assertEqual(result["workspace_ids"], W3)

    def test_all_recent_candidates_is_safe_empty_selection(self):
        self.complete([W1, W2, W3])
        result, state = self.select(SKIP_REVIEWED_WITHIN_DAYS="7")
        self.assertEqual(result["status"], "skipped_no_candidates")
        self.assertEqual(result["workspace_ids"], "")
        self.assertEqual(result["workspace_count"], 0)
        self.assertEqual(state["workspaces"], [])

    def test_rolling_utc_boundary_and_expired_reviews_remain_eligible(self):
        self.complete([W1], NOW - timedelta(days=7))
        self.complete([W2], NOW - timedelta(days=7) + timedelta(microseconds=1))
        self.complete([W3], NOW - timedelta(days=8))
        self.assertEqual(self.recent(), {W2})
        result, _ = self.select(SKIP_REVIEWED_WITHIN_DAYS="7")
        self.assertEqual(result["workspace_ids"], f"{W1},{W3}")

    def test_completion_timestamp_offsets_are_compared_as_instants(self):
        self.complete([W1], (NOW - timedelta(hours=1)).astimezone(timezone(timedelta(hours=2))))
        self.assertEqual(self.recent(), {W1})

    def test_history_does_not_cross_far_workspace_or_child_pipeline(self):
        self.complete([W1], CHILD_PIPELINE_ID=str(uuid4()))
        self.complete([W2], CHILD_WORKSPACE_ID=str(uuid4()))
        self.assertEqual(self.recent(), set())

    def test_multiple_completed_reviews_union_workspace_ids(self):
        self.complete([W1, W2])
        self.complete([W2, W3])
        self.assertEqual(self.recent(), {W1, W2, W3})

    def test_uncompleted_selection_does_not_count_and_missing_history_is_logged(self):
        self.select()
        with patch("builtins.print") as output:
            self.assertEqual(self.recent(), set())
        self.assertTrue(any("No targeted-review completion history" in str(call) for call in output.call_args_list))

    def test_history_read_errors_stop_enabled_selection_without_selection_record(self):
        directory = self.directory / "completed"
        directory.mkdir()
        path = directory / (str(uuid4()) + ".json")
        path.write_text("invalid-json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            self.select(SKIP_REVIEWED_WITHIN_DAYS="7")
        self.assertEqual(list(self.directory.glob("*.json")), [])
        path.unlink()
        self.complete([W1])
        with patch.object(Path, "read_text", side_effect=PermissionError("History unreadable")):
            with self.assertRaises(PermissionError):
                self.select(SKIP_REVIEWED_WITHIN_DAYS="7")
        with patch.object(Path, "stat", side_effect=PermissionError("History inaccessible")):
            with self.assertRaises(PermissionError):
                self.recent()

    def test_completion_history_survives_later_notification_failure(self):
        record = self.complete([W1])
        path = self.directory / (record["parent_run_id"] + ".json")
        selection = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**selection, "status": "email_preparation_failed"}), encoding="utf-8")
        self.assertEqual(self.recent(), {W1})

    def test_malformed_completion_records_never_silently_exclude_or_allow(self):
        record = self.complete([W1])
        path = self.directory / "completed" / (record["parent_run_id"] + ".json")
        for update in (
            {"status": "failed"}, {"status": "running"}, {"workspace_ids": []},
            {"workspace_ids": [W1, W1]}, {"workspace_ids": ["invalid"]},
            {"workspace_ids": [False]}, {"completed_at": "bad"},
            {"completed_at": "2026-10-29T09:00:00"},
            {"completed_at": (NOW + timedelta(seconds=1)).isoformat()},
            {"parent_run_id": str(uuid4())}, {"child_pipeline_id": "bad"},
            {"child_run_id": None},
        ):
            with self.subTest(update=update):
                path.write_text(json.dumps({**record, **update}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.recent()

    def test_recording_checks_selection_binding_and_never_overwrites_history(self):
        record = self.complete([W1])
        with self.assertRaises(FileExistsError):
            record_completed_review(
                self.config, record["parent_run_id"], record["child_run_id"], state_dir=self.directory,
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            record_completed_review(
                {**self.config, "CHILD_PIPELINE_ID": str(uuid4())},
                record["parent_run_id"], record["child_run_id"], state_dir=self.directory,
            )

    def test_empty_failed_and_inconsistent_selection_cannot_be_recorded(self):
        record = self.complete([W1])
        path = self.directory / (record["parent_run_id"] + ".json")
        selection = json.loads(path.read_text(encoding="utf-8"))
        for update in (
            {"status": "failed"}, {"status": "skipped_no_candidates"}, {"workspaces": []},
            {"workspace_ids": W2}, {"workspaces": [{"workspace_id": W1}, {"workspace_id": W1}]},
        ):
            with self.subTest(update=update):
                path.write_text(json.dumps({**selection, **update}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    record_completed_review(
                        self.config, record["parent_run_id"], record["child_run_id"], state_dir=self.directory,
                    )

    def test_record_completion_does_not_resolve_recipients_or_require_email_credentials(self):
        config = {
            **self.config, "COMPLETION_ACTION": "record_review",
            "PARENT_RUN_ID": str(uuid4()), "CHILD_RUN_ID": str(uuid4()),
        }
        with patch("orchestration.runtime.record_completed_review", return_value={"status": "succeeded"}) as record, \
                patch("orchestration.runtime.run_notifications") as notify:
            self.assertEqual(run_completion(config), {"status": "succeeded"})
        record.assert_called_once()
        notify.assert_not_called()
        with self.assertRaisesRegex(ValueError, "completion action"):
            run_completion({**config, "COMPLETION_ACTION": "unknown"})

    def test_notification_action_still_uses_existing_notification_flow(self):
        config = {**self.config, "COMPLETION_ACTION": "prepare_notifications"}
        with patch("orchestration.runtime.run_notifications", return_value={"notifications": "disabled"}) as notify:
            self.assertEqual(run_completion(config), {"notifications": "disabled"})
        notify.assert_called_once_with(config)

    def test_completion_write_failure_propagates_without_notification_fallback(self):
        config = {
            **self.config, "COMPLETION_ACTION": "record_review",
            "PARENT_RUN_ID": str(uuid4()), "CHILD_RUN_ID": str(uuid4()),
        }
        with patch("orchestration.runtime.record_completed_review", side_effect=PermissionError("Write denied")), \
                patch("orchestration.runtime.run_notifications") as notify:
            with self.assertRaises(PermissionError):
                run_completion(config)
        notify.assert_not_called()

    def parent(self, **kwargs):
        return build_parent_pipeline(
            {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}},
            workspace_id=HOST, pipeline_id=PIPELINE, notebook_id=W1, notification_notebook_id=W2, **kwargs,
        )

    def test_parent_records_only_after_child_success_and_before_optional_sync(self):
        for sync in (None, W3):
            parent = self.parent(owner_access_notebook_id=sync)
            properties = parent["properties"]
            selection, review, notification, _ = properties["activities"]
            invoke, child_id, record, *rest = review["typeProperties"]["ifTrueActivities"]
            self.assertTrue(invoke["typeProperties"]["waitOnCompletion"])
            self.assertEqual(child_id["dependsOn"], [{"activity": invoke["name"], "dependencyConditions": ["Succeeded"]}])
            self.assertEqual(record["dependsOn"], [{"activity": child_id["name"], "dependencyConditions": ["Succeeded"]}])
            self.assertEqual(record["name"], RECORD_REVIEW_ACTIVITY)
            self.assertEqual(record["policy"]["retry"], 0)
            self.assertEqual(record["typeProperties"]["notebookId"], W2)
            self.assertEqual(
                record["typeProperties"]["parameters"]["COMPLETION_ACTION"],
                notebook_parameter("@'record_review'"),
            )
            if rest:
                self.assertEqual(rest[0]["dependsOn"], [{"activity": record["name"], "dependencyConditions": ["Succeeded"]}])
            self.assertEqual(review["typeProperties"]["ifFalseActivities"], [])
            prepare = notification["typeProperties"]["ifTrueActivities"][0]
            self.assertEqual(
                prepare["typeProperties"]["parameters"]["COMPLETION_ACTION"],
                notebook_parameter("@'prepare_notifications'"),
            )
            self.assertNotIn("COMPLETION_ACTION", properties["parameters"])
            self.assertIn("SKIP_REVIEWED_WITHIN_DAYS", selection["typeProperties"]["parameters"])
            self.assertNotIn("SKIP_REVIEWED_WITHIN_DAYS", invoke["typeProperties"]["parameters"])
            self.assertEqual(properties["parameters"]["SKIP_REVIEWED_WITHIN_DAYS"]["defaultValue"], "0")

    def test_setup_and_generated_notebooks_wire_cooldown_without_new_artifacts(self):
        self.assertIn("SKIP_REVIEWED_WITHIN_DAYS", SELECTION_PARAMETERS)
        notebook = runtime_notebook("runtime.zip", HOST, W1, child_pipeline_id=PIPELINE)
        source = "".join(notebook["cells"][0]["source"])
        self.assertIn("SKIP_REVIEWED_WITHIN_DAYS = None", source)
        setup = json.loads((ROOT / "fabric" / "notebooks" / "06_targeted_review_setup.ipynb").read_text(encoding="utf-8"))
        code = "\n".join("".join(cell["source"]) for cell in setup["cells"] if cell["cell_type"] == "code")
        self.assertIn('SKIP_REVIEWED_WITHIN_DAYS = "0"', code)
        self.assertIn('"SKIP_REVIEWED_WITHIN_DAYS": SKIP_REVIEWED_WITHIN_DAYS', code)
        completion = runtime_notebook("runtime.zip", HOST, W1, stage="notifications", child_pipeline_id=PIPELINE)
        self.assertIn("COMPLETION_ACTION = None", "".join(completion["cells"][0]["source"]))
        self.assertIn("run_completion(config)", "".join(completion["cells"][1]["source"]))


if __name__ == "__main__":
    unittest.main()

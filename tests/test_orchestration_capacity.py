# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capacity selection contracts using stock FUAM columns and an in-memory SQL fixture."""
from datetime import date, datetime, timezone
import ast
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from collectors._http import HttpError
from orchestration.deployment import (
    DEFAULTS, SELECTION_PARAMETERS, build_parent_pipeline,
    read_notebook_parameters, runtime_notebook,
)
from orchestration.runtime import prepare_notifications, run_selection, select_workspaces, validate_config
from orchestration.setup import provision
from orchestration.sources import (
    SourceResult, capacity_selector, fuam_query, read_source, resolve_capacity, validate_source,
)


ROOT = Path(__file__).resolve().parents[1]
A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
W1 = "11111111-1111-1111-1111-111111111111"
W2 = "22222222-2222-2222-2222-222222222222"
W3 = "33333333-3333-3333-3333-333333333333"
HOST = "44444444-4444-4444-4444-444444444444"
FUAM = "55555555-5555-5555-5555-555555555555"
LH = "66666666-6666-6666-6666-666666666666"
PIPELINE = "77777777-7777-7777-7777-777777777777"
RUN = "88888888-8888-8888-8888-888888888888"
CHILD_RUN = "99999999-9999-9999-9999-999999999999"


class CapacitySourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="far-capacity-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            ATTACH DATABASE ':memory:' AS dbo;
            CREATE TABLE dbo.capacities (CapacityId TEXT, displayName TEXT);
            CREATE TABLE dbo.workspaces (WorkspaceId TEXT, WorkspaceName TEXT, CapacityId TEXT);
            CREATE TABLE dbo.capacity_metrics_by_item_by_operation_by_day
                (CapacityId TEXT, WorkspaceId TEXT, Date TEXT, TotalCUs REAL, ThrottlingInMin REAL);
        """)
        self.db.executemany("INSERT INTO dbo.capacities VALUES (?, ?)", [
            (A.upper(), "  Team's Production  "), (B, "Other"), (C, "Empty"),
        ])
        self.db.executemany("INSERT INTO dbo.workspaces VALUES (?, ?, ?)", [
            (W1, "Moved to B", B), (W2, "Now on A", A), (W3, "On A", A),
            (HOST, "FAR", A), (FUAM, "FUAM", A),
        ])
        self.db.executemany(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)", [
                (A.upper(), W1, "2026-09-15", 60, 1), (A, W1, "2026-09-21", 40, 2),
                (B, W1, "2026-09-20", 900, 90),
                (A, W2, "2026-09-20", 20, 40), (B, W2, "2026-09-20", 2000, 100),
                (A, W3, "2026-09-20", 100, 40),
                (A, HOST, "2026-09-20", 9000, 900), (A, FUAM, "2026-09-20", 8000, 800),
                (A, W2, "2026-09-14", 100000, 100000),
                (A, W2, "2026-09-22", 100000, 100000),
                (C, W1, "2026-09-14", 1000, 1000),
            ],
        )
        self.config = {
            **DEFAULTS, "CAPACITY_ID_OR_NAME": A, "FUAM_WORKSPACE_ID": FUAM,
            "CHILD_WORKSPACE_ID": HOST, "CHILD_PIPELINE_ID": PIPELINE, "TOP_N": "2",
        }
        self.client = Mock()
        self.client.token.return_value = "synthetic-token"
        self.client.list_items.return_value = [{"id": LH, "displayName": "FUAM"}]
        self.query = self.enterContext(patch("orchestration.sources.query_rows", side_effect=self.sql_rows))
        self.inventory = self.enterContext(patch(
            "orchestration.sources.collect_workspace_groups",
            return_value=[{"id": wid} for wid in (W1, W2, W3, HOST, FUAM)],
        ))
        clock = self.enterContext(patch("orchestration.sources.datetime"))
        clock.now.return_value = datetime(2026, 9, 22, 9, tzinfo=timezone.utc)
        self.output = self.enterContext(patch("builtins.print"))

    def sql_rows(self, workspace_id, lakehouse, query):
        self.assertEqual(workspace_id, FUAM)
        self.assertEqual(lakehouse["id"], LH)
        # SQLite supports the generated aggregation, but uses a different date-cast dialect.
        query = re.sub(r"CAST\(('[-\d]+') AS date\)", r"\1", query)
        return [dict(row) for row in self.db.execute(query)]

    def select(self, config=None):
        return select_workspaces(
            config or self.config, RUN,
            read_source=lambda options: read_source(options, self.client), state_dir=self.directory,
        )

    def audit(self):
        return json.loads((self.directory / (RUN + ".json")).read_text(encoding="utf-8"))

    def test_unmatched_highest_candidate_does_not_consume_top_n_across_capacities(self):
        self.inventory.return_value = [{"id": wid.upper()} for wid in (W1, W3, HOST, FUAM)]
        result = self.select({**self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, B])})
        self.assertEqual(result["workspace_ids"], f"{W1},{W3}")
        inventory = self.audit()["workspace_inventory"]
        self.assertTrue(inventory["complete"])
        self.assertEqual(inventory["unmatched_count"], 1)
        self.assertEqual(inventory["unmatched_ids"], [W2])
        self.assertEqual(inventory["unmatched_reason"], "not_in_current_workspace_inventory")
        self.assertTrue(any("not proven deleted" in str(call) for call in self.output.call_args_list))

    def test_unmatched_filter_preserves_allowlist_without_filling_from_outside_it(self):
        self.inventory.return_value = [{"id": wid} for wid in (W1, W3, HOST, FUAM)]
        result = self.select({
            **self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, B]),
            "WORKSPACE_IDS": f"{W2},{W3}",
        })
        self.assertEqual(result["workspace_ids"], W3)
        self.assertEqual(result["workspace_count"], 1)

    def test_all_unmatched_is_audited_safe_skip_not_tenant_wide_review(self):
        self.inventory.return_value = []
        result = self.select()
        self.assertEqual(result["status"], "skipped_no_candidates")
        self.assertEqual(result["workspace_ids"], "")
        self.assertEqual(result["workspace_count"], 0)
        audit = self.audit()
        self.assertEqual(audit["workspaces"], [])
        self.assertEqual(audit["workspace_inventory"]["unmatched_ids"], sorted([W1, W2, W3, HOST, FUAM]))
        self.assertEqual(audit["workspace_inventory"]["unmatched_count"], 5)
        self.assertTrue(audit["workspace_inventory"]["complete"])

    def test_inventory_discovery_error_stops_selection_without_audit_or_scope_fallback(self):
        self.inventory.side_effect = HttpError("Inventory unavailable", status_code=403)
        with self.assertRaises(HttpError):
            self.select({**self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, B])})
        self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_unmatched_404_candidate_never_reaches_notification_owner_lookup(self):
        self.inventory.return_value = [{"id": wid} for wid in (W1, W3, HOST, FUAM)]
        self.select({**self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, B])})
        from orchestration import completion
        actual = completion.resolve_owners
        transport = Mock(side_effect=lambda url, **kwargs: Mock(
            status_code=404 if f"/groups/{W2}/" in url else 200,
            json=lambda: {"value": [{
                "groupUserAccessRight": "Admin", "principalType": "User", "emailAddress": "test@example.com",
            }]},
        ))
        with patch.object(completion, "resolve_owners", side_effect=lambda ids, token: actual(
            ids, token, transport=transport,
        )):
            prepared = prepare_notifications(
                {**self.config, "NOTIFICATIONS_ENABLED": "true",
                 "FAR_REPORT_URL": "https://app.powerbi.com/groups/test/reports/test"},
                RUN, CHILD_RUN, state_dir=self.directory,
                prepare=lambda config, audit: completion.prepare_owner_emails(config, audit, self.client),
            )
        self.assertEqual(len(prepared["messages"]), 2)
        self.assertEqual([call.args[0].split("/")[-2] for call in transport.call_args_list], [W1, W3])

    def test_unattributed_metrics_do_not_block_capacity_set_ranking_and_are_audited(self):
        self.db.executemany(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)",
            [(A, "", "2026-09-20", 0.256, 1),
             (B, None, "2026-09-20", 999999, 999999),
             (B, " \t ", "2026-09-20", 999999, 999999)],
        )
        for metric in ("cu_seconds", "recorded_throttling_minutes"):
            with self.subTest(metric=metric):
                result = self.select({
                    **self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, B]),
                    "RANKING_METRIC": metric,
                })
                self.assertEqual(result["workspace_ids"], f"{W2},{W1}")
                self.assertEqual(result["workspace_count"], 2)
                self.assertEqual(self.audit()["unattributed_workspace_groups"], 3)
                (self.directory / (RUN + ".json")).unlink()

    def test_only_unattributed_metrics_produces_safe_skip(self):
        self.db.execute(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)",
            (C, "", "2026-09-20", 0.256, 1),
        )
        result = self.select({**self.config, "CAPACITY_ID_OR_NAME": C})
        self.assertEqual(result["status"], "skipped_no_candidates")
        self.assertEqual(result["workspace_ids"], "")
        self.assertEqual(result["workspace_count"], 0)
        self.assertEqual(self.audit()["unattributed_workspace_groups"], 1)
        self.assertEqual(self.audit()["workspaces"], [])

    def test_malformed_nonblank_workspace_id_still_blocks_selection(self):
        self.db.execute(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)",
            (A, "not-a-workspace-guid", "2026-09-20", 0.256, 1),
        )
        with self.assertRaisesRegex(ValueError, "WorkspaceId"):
            self.select()
        self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_unattributed_metrics_outside_capacity_scope_are_not_counted(self):
        self.db.execute(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)",
            (C, "", "2026-09-20", 999999, 999999),
        )
        self.select()
        self.assertEqual(self.audit()["unattributed_workspace_groups"], 0)

    def test_capacity_is_applied_before_aggregation_for_both_metrics_and_ties(self):
        for metric, expected in [
            ("cu_seconds", [(W1, 100), (W3, 100)]),
            ("recorded_throttling_minutes", [(W2, 40), (W3, 40)]),
        ]:
            with self.subTest(metric=metric):
                config = {**self.config, "RANKING_METRIC": metric}
                result = self.select(config)
                self.assertEqual(result["workspace_count"], 2)
                self.assertEqual(
                    [(row["workspace_id"], row["metric_value"]) for row in self.audit()["workspaces"]],
                    expected,
                )
                sql = self.query.call_args.args[2]
                self.assertLess(sql.index("AND UPPER(CapacityId)"), sql.index("GROUP BY UPPER(WorkspaceId)"))
                self.assertNotIn("CapacityId", sql.split("LEFT JOIN")[1])
                (self.directory / (RUN + ".json")).unlink()

    def test_other_capacity_and_all_capacity_totals_differ(self):
        for selector, expected in [
            (B, [(W2, 2000), (W1, 900)]),
            ("", [(W2, 2020), (W1, 1000)]),
        ]:
            self.select({**self.config, "CAPACITY_ID_OR_NAME": selector})
            self.assertEqual(
                [(row["workspace_id"], row["metric_value"]) for row in self.audit()["workspaces"]], expected,
            )
            (self.directory / (RUN + ".json")).unlink()

    def test_exact_name_case_whitespace_and_guid_choose_identical_rows(self):
        expected = None
        for selector in (A, "  " + A.upper() + "  ", " Team's PRODUCTION ", "team's production"):
            result = read_source({**self.config, "CAPACITY_ID_OR_NAME": selector}, self.client)
            if expected is None:
                expected = result[1]
            self.assertEqual(result[1], expected)
            self.assertEqual(result.capacity_scope["capacity_id"], A)
            self.assertEqual(result.capacity_scope["requested"], selector.strip())
        queries = [call.args[2] for call in self.query.call_args_list]
        self.assertEqual(sum("FROM dbo.capacities" in query for query in queries), 2)
        self.assertTrue(all("Team" not in query and "team" not in query for query in queries))

    def test_capacity_set_ranks_global_top_n_after_filtering_and_aggregation(self):
        self.db.execute(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)",
            (C, W3, "2026-09-20", 100000, 100000),
        )
        for metric, expected in [
            ("cu_seconds", [(W2, 2020), (W1, 1000)]),
            ("recorded_throttling_minutes", [(W2, 140), (W1, 93)]),
        ]:
            with self.subTest(metric=metric):
                self.select({
                    **self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, "Other"]),
                    "RANKING_METRIC": metric,
                })
                self.assertEqual(
                    [(row["workspace_id"], row["metric_value"]) for row in self.audit()["workspaces"]],
                    expected,
                )
                sql = self.query.call_args.args[2]
                self.assertIn(f"IN ('{A.upper()}', '{B.upper()}')", sql)
                self.assertNotIn("Other", sql)
                self.assertLess(sql.index("AND UPPER(CapacityId)"), sql.index("GROUP BY UPPER(WorkspaceId)"))
                self.assertNotIn("CapacityId", sql.split("LEFT JOIN")[1])
                (self.directory / (RUN + ".json")).unlink()

    def test_capacity_set_resolves_names_once_and_deduplicates_guids_without_double_counting(self):
        selector = json.dumps([" Team's PRODUCTION ", A.upper(), "Other", B, B])
        self.select({**self.config, "CAPACITY_ID_OR_NAME": selector})
        scope = self.audit()["capacity_scope"]
        self.assertEqual(scope["requested"], capacity_selector(selector))
        self.assertEqual([entry["capacity_id"] for entry in scope["capacities"]], [A, A, B, B, B])
        self.assertEqual([row["metric_value"] for row in self.audit()["workspaces"]], [2020, 1000])
        queries = [call.args[2] for call in self.query.call_args_list]
        self.assertEqual(sum("FROM dbo.capacities" in query for query in queries), 1)
        self.assertEqual(queries[-1].count(B.upper()), 1)

    def test_top_five_workspaces_across_five_capacities_excludes_sixth_capacity(self):
        d, e, outside = (str(uuid4()) for _ in range(3))
        wd, we, small, outside_workspace = (str(uuid4()) for _ in range(4))
        self.inventory.return_value.extend({"id": wid} for wid in (wd, we, small, outside_workspace))
        self.db.executemany("INSERT INTO dbo.workspaces VALUES (?, ?, ?)", [
            (wd, "Fourth", d), (we, "Fifth", e), (small, "Small", d),
            (outside_workspace, "Outside selection", outside),
        ])
        self.db.executemany(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)", [
                (C, W3, "2026-09-20", 200, 1), (d, wd, "2026-09-20", 400, 1),
                (e, we, "2026-09-20", 500, 1), (d, small, "2026-09-20", 40, 1),
                (outside, outside_workspace, "2026-09-20", 99999, 1),
            ],
        )
        result = self.select({
            **self.config, "TOP_N": "5", "CAPACITY_ID_OR_NAME": json.dumps([A, B, C, d, e]),
        })
        self.assertEqual(result["workspace_count"], 5)
        self.assertEqual(
            [(row["workspace_id"], row["metric_value"]) for row in self.audit()["workspaces"]],
            [(W2, 2020), (W1, 1000), (we, 500), (wd, 400), (W3, 300)],
        )

    def test_guid_sets_do_not_require_lookup_and_empty_sets_of_metrics_do_not_broaden_scope(self):
        for members, expected in [
            ([A], [100, 100]), ([A, A.upper()], [100, 100]),
            ([A, B], [2020, 1000]), ([C, str(uuid4())], []),
        ]:
            self.query.reset_mock()
            self.select({**self.config, "CAPACITY_ID_OR_NAME": json.dumps(members)})
            self.assertEqual([row["metric_value"] for row in self.audit()["workspaces"]], expected)
            self.assertEqual(len(self.audit()["capacity_scope"]["capacities"]), len(members))
            self.query.assert_called_once()
            self.assertNotIn("FROM dbo.capacities", self.query.call_args.args[2])
            if not expected:
                self.assertEqual(self.audit()["status"], "skipped_no_candidates")
            (self.directory / (RUN + ".json")).unlink()

    def test_capacity_set_fails_entire_selection_if_any_name_is_unknown_or_ambiguous(self):
        for name, error in [("Missing", "Unknown"), ("Team", "Unknown"), ("Other", "Ambiguous")]:
            if error == "Ambiguous":
                self.db.execute("INSERT INTO dbo.capacities VALUES (?, ?)", (C, "Other"))
            self.query.reset_mock()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, error):
                self.select({**self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, name])})
            self.query.assert_called_once()
            self.assertIn("FROM dbo.capacities", self.query.call_args.args[2])
            self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_capacity_names_with_commas_quotes_and_sql_syntax_are_matched_not_interpolated(self):
        name = 'Team, "West"; \' OR 1=1 --'
        self.db.execute("UPDATE dbo.capacities SET displayName=? WHERE CapacityId=?", (name, B))
        for selector in (name, json.dumps([name]), json.dumps([A, name])):
            self.select({**self.config, "CAPACITY_ID_OR_NAME": selector})
            self.assertNotIn(name, self.query.call_args.args[2])
            self.assertEqual(self.audit()["workspaces"][0]["workspace_id"], W2)
            (self.directory / (RUN + ".json")).unlink()

    def test_capacity_set_allowlist_and_hosting_exclusions_still_apply(self):
        result = self.select({
            **self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, B]),
            "WORKSPACE_IDS": f"{HOST},{FUAM},{W1}",
        })
        self.assertEqual(result["workspace_ids"], W1)
        self.assertEqual(self.audit()["workspaces"][0]["metric_value"], 1000)

    def test_capacity_set_provenance_survives_notification_preparation_and_cannot_be_replayed(self):
        config = {**self.config, "CAPACITY_ID_OR_NAME": json.dumps([A, "Other"])}
        self.select(config)
        saved = self.audit()["capacity_scope"]
        with self.assertRaisesRegex(RuntimeError, "automatic replay"):
            self.select({**config, "CAPACITY_ID_OR_NAME": json.dumps([C])})
        prepare = Mock(return_value=[{"to": "admin@example.com", "subject": "Review", "body": "Ready"}])
        prepare_notifications(
            {**config, "NOTIFICATIONS_ENABLED": "true",
             "FAR_REPORT_URL": f"https://app.powerbi.com/groups/{HOST}/reports/{LH}"},
            RUN, CHILD_RUN, prepare=prepare, state_dir=self.directory,
        )
        self.assertEqual(self.audit()["capacity_scope"], saved)
        self.assertEqual(prepare.call_args.args[1]["capacity_scope"], saved)

    def test_corrupt_or_partial_capacity_set_provenance_is_rejected_before_selection(self):
        selector = json.dumps([A, "Other"])
        valid = resolve_capacity(selector, FUAM, {"id": LH})
        invalid = [
            {"requested": selector, "capacities": []},
            {"requested": selector, "capacities": valid["capacities"][:1]},
            {"requested": selector, "capacities": valid["capacities"][::-1]},
            {"requested": selector, "capacities": [valid["capacities"][0], None]},
            {"requested": selector, "capacities": [
                valid["capacities"][0], {"requested": "Other", "capacity_id": "", "capacity_name": "Other"},
            ]},
            {"requested": selector, "capacity_id": A, "capacity_name": ""},
        ]
        for scope in invalid:
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                select_workspaces(
                    {**self.config, "CAPACITY_ID_OR_NAME": selector}, RUN,
                    read_source=lambda _: SourceResult([], scope), state_dir=self.directory,
                )
            self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_blank_omitted_and_null_preserve_existing_source_behavior(self):
        for selector in ("", " \t\n", None):
            config = {**self.config, "CAPACITY_ID_OR_NAME": selector}
            result = read_source(config, self.client)
            self.assertIsInstance(result, tuple)
            self.assertEqual(result.capacity_scope, {"requested": "", "capacity_id": "", "capacity_name": ""})
            self.assertNotIn("CapacityId", self.query.call_args.args[2])
        config.pop("CAPACITY_ID_OR_NAME")
        self.assertEqual(read_source(config, self.client), result)
        self.assertFalse(any("FROM dbo.capacities" in call.args[2] for call in self.query.call_args_list))

    def test_ambiguous_distinct_ids_fail_but_duplicate_same_id_is_not_ambiguous(self):
        self.db.execute("INSERT INTO dbo.capacities VALUES (?, ?)", (A, "Team's Production"))
        config = {**self.config, "CAPACITY_ID_OR_NAME": "team's production"}
        self.assertEqual(read_source(config, self.client).capacity_scope["capacity_id"], A)
        self.db.execute("INSERT INTO dbo.capacities VALUES (?, ?)", (B, "TEAM'S PRODUCTION"))
        self.query.reset_mock()
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.select(config)
        self.query.assert_called_once()
        self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_unknown_partial_and_sql_injection_names_fail_without_metric_query(self):
        for selector in ("Team", "not-a-guid", "Missing", "' OR 1=1; DROP TABLE dbo.capacities; --"):
            self.query.reset_mock()
            with self.subTest(selector=selector), self.assertRaisesRegex(ValueError, "Unknown"):
                self.select({**self.config, "CAPACITY_ID_OR_NAME": selector})
            self.query.assert_called_once()
            self.assertEqual(self.query.call_args.args[2],
                             "SELECT CapacityId AS capacity_id, displayName AS capacity_name FROM dbo.capacities")
            self.assertFalse((self.directory / (RUN + ".json")).exists())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM dbo.capacities").fetchone()[0], 3)

    def test_no_in_window_or_zero_metrics_skips_with_capacity_provenance(self):
        self.db.execute(
            "INSERT INTO dbo.capacity_metrics_by_item_by_operation_by_day VALUES (?, ?, ?, ?, ?)",
            (C, W2, "2026-09-20", 0, 0),
        )
        for selector in ("Empty", C, str(uuid4())):
            for metric in ("cu_seconds", "recorded_throttling_minutes"):
                result = self.select({**self.config, "CAPACITY_ID_OR_NAME": selector, "RANKING_METRIC": metric})
                self.assertEqual(result["status"], "skipped_no_candidates")
                self.assertEqual(result["workspace_ids"], "")
                self.assertTrue(self.audit()["capacity_scope"]["capacity_id"])
                (self.directory / (RUN + ".json")).unlink()

    def test_allowlist_intersects_capacity_and_exclusions_still_override_it(self):
        for metric in ("cu_seconds", "recorded_throttling_minutes"):
            for allowlist, expected in [
                (f"{HOST},{FUAM},{W2}", W2), (f"{HOST},{FUAM}", ""), (str(uuid4()), ""),
            ]:
                result = self.select({
                    **self.config, "RANKING_METRIC": metric, "WORKSPACE_IDS": allowlist, "TOP_N": "1",
                })
                self.assertEqual(result["workspace_ids"], expected)
                (self.directory / (RUN + ".json")).unlink()

    def test_lookup_and_metric_schema_permission_errors_fail_explicitly(self):
        for selector in ("Other", A):
            self.query.side_effect = RuntimeError("SQL permission denied")
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                self.select({**self.config, "CAPACITY_ID_OR_NAME": selector})
            self.assertFalse((self.directory / (RUN + ".json")).exists())
        self.query.side_effect = self.sql_rows
        self.db.execute("ALTER TABLE dbo.capacities RENAME COLUMN displayName TO WrongName")
        with self.assertRaises(sqlite3.OperationalError):
            self.select({**self.config, "CAPACITY_ID_OR_NAME": "Other"})
        self.db.execute(
            "ALTER TABLE dbo.capacity_metrics_by_item_by_operation_by_day RENAME COLUMN CapacityId TO WrongId",
        )
        with self.assertRaises(sqlite3.OperationalError):
            self.select()
        self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_separator_only_allowlist_fails_without_persisting_broadened_selection(self):
        for selector in ("", A, "Empty"):
            with self.subTest(selector=selector), self.assertRaisesRegex(ValueError, "WORKSPACE_IDS"):
                self.select({**self.config, "CAPACITY_ID_OR_NAME": selector, "WORKSPACE_IDS": " , , "})
            self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_invalid_lookup_rows_and_matching_ids_fail_closed(self):
        for rows in (
            [{"capacity_id": A}], [{"capacity_id": A, "capacity_name": None}],
            [{"capacity_id": None, "capacity_name": "Other"}],
            [{"capacity_id": "bad-id", "capacity_name": "Other"}],
        ):
            self.query.side_effect = None
            self.query.return_value = rows
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "FUAM capacit"):
                resolve_capacity("Other", FUAM, {"id": LH})

    def test_invalid_values_only_in_other_capacity_do_not_poison_scoped_metrics(self):
        for metric, column in (("cu_seconds", "TotalCUs"), ("recorded_throttling_minutes", "ThrottlingInMin")):
            self.db.execute(f"UPDATE dbo.capacity_metrics_by_item_by_operation_by_day SET {column}=NULL WHERE CapacityId=?", (B,))
            read_source({**self.config, "RANKING_METRIC": metric}, self.client)
            with self.assertRaisesRegex(ValueError, "null or negative"):
                read_source({**self.config, "RANKING_METRIC": metric, "CAPACITY_ID_OR_NAME": B}, self.client)

    def test_audit_retains_resolved_name_and_replay_cannot_change_capacity(self):
        config = {**self.config, "CAPACITY_ID_OR_NAME": "  TEAM'S PRODUCTION "}
        self.select(config)
        saved = self.audit()["capacity_scope"]
        self.assertEqual(saved, {"requested": "TEAM'S PRODUCTION", "capacity_id": A,
                                 "capacity_name": "Team's Production"})
        calls = self.query.call_count
        with self.assertRaisesRegex(RuntimeError, "automatic replay"):
            self.select({**config, "CAPACITY_ID_OR_NAME": B})
        self.assertEqual(self.query.call_count, calls)
        notifications = {**self.config, "NOTIFICATIONS_ENABLED": "true",
                         "FAR_REPORT_URL": f"https://app.powerbi.com/groups/{HOST}/reports/{LH}"}
        prepare = Mock(return_value=[{"to": "admin@example.com", "subject": "Review", "body": "Ready"}])
        prepare_notifications(notifications, RUN, CHILD_RUN, prepare=prepare, state_dir=self.directory)
        self.assertEqual(self.audit()["capacity_scope"], saved)
        with self.assertRaisesRegex(RuntimeError, "automatic replay"):
            prepare_notifications(notifications, RUN, CHILD_RUN, prepare=prepare, state_dir=self.directory)
        prepare.assert_called_once()

    def test_capacity_provenance_is_required_for_nonblank_source_callbacks(self):
        with self.assertRaisesRegex(ValueError, "confirm"):
            select_workspaces(self.config, RUN, read_source=lambda _: ("fuam", []), state_dir=self.directory)
        with self.assertRaisesRegex(ValueError, "validated capacity GUID"):
            select_workspaces(
                self.config, RUN,
                read_source=lambda _: SourceResult([], {"requested": A, "capacity_id": "", "capacity_name": ""}),
                state_dir=self.directory,
            )
        self.assertFalse((self.directory / (RUN + ".json")).exists())

    def test_corrupt_capacity_audit_fails_before_notification_preparation(self):
        self.select()
        original = self.audit()
        invalid_scopes = [
            None, {}, {"requested": "", "capacity_id": A, "capacity_name": ""},
            {"requested": A, "capacity_id": B, "capacity_name": ""},
            {"requested": "Other", "capacity_id": B, "capacity_name": "Different"},
            {"requested": "Other", "capacity_id": "", "capacity_name": "Other"},
        ]
        notifications = {**self.config, "NOTIFICATIONS_ENABLED": "true",
                         "FAR_REPORT_URL": f"https://app.powerbi.com/groups/{HOST}/reports/{LH}"}
        prepare = Mock()
        for scope in invalid_scopes:
            (self.directory / (RUN + ".json")).write_text(
                json.dumps({**original, "capacity_scope": scope}), encoding="utf-8",
            )
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                prepare_notifications(notifications, RUN, CHILD_RUN, prepare=prepare, state_dir=self.directory)
        prepare.assert_not_called()
        self.assertFalse((self.directory / (RUN + ".notification-started")).exists())

    def test_generated_runtime_run_selection_uses_real_capacity_source(self):
        config = {**self.config, "CAPACITY_ID_OR_NAME": B, "PARENT_RUN_ID": RUN}
        with patch.dict("sys.modules", {"notebookutils": SimpleNamespace(credentials=Mock())}), \
                patch("orchestration.runtime.FabricClient", return_value=self.client), \
                patch("orchestration.runtime.Path", return_value=self.directory):
            self.assertEqual(run_selection(config)["workspace_ids"], f"{W2},{W1}")
        self.assertEqual(self.audit()["capacity_scope"]["capacity_id"], B)


class CapacityDeploymentTests(unittest.TestCase):
    def parent(self, defaults=None):
        return build_parent_pipeline(
            {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": W1}}}},
            workspace_id=HOST, pipeline_id=PIPELINE, notebook_id=LH, notification_notebook_id=FUAM,
            defaults=defaults,
        )

    def test_default_and_setup_values_flow_to_parent_but_not_child_or_completion(self):
        for value, expected in (
            ("", ""), (None, ""), ("  Other  ", "Other"), (A, A),
            ('[" Other ", "' + A + '"]', json.dumps(["Other", A])),
        ):
            parent = self.parent({"CAPACITY_ID_OR_NAME": value})
            props = parent["properties"]
            self.assertEqual(props["parameters"]["CAPACITY_ID_OR_NAME"]["defaultValue"], expected)
            selection, review, notification, _ = props["activities"]
            self.assertEqual(selection["typeProperties"]["parameters"]["CAPACITY_ID_OR_NAME"]["value"]["value"],
                             "@concat('far-string:', coalesce(pipeline().parameters.CAPACITY_ID_OR_NAME, ''))")
            self.assertNotIn("CAPACITY_ID_OR_NAME",
                             review["typeProperties"]["ifTrueActivities"][0]["typeProperties"]["parameters"])
            self.assertNotIn("CAPACITY_ID_OR_NAME",
                             notification["typeProperties"]["ifTrueActivities"][0]["typeProperties"]["parameters"])
        self.assertEqual(DEFAULTS["CAPACITY_ID_OR_NAME"], "")
        self.assertEqual(self.parent()["properties"]["parameters"]["CAPACITY_ID_OR_NAME"]["defaultValue"], "")

    def test_injected_override_roundtrips_without_expression_evaluation(self):
        for value in ("", "  Other  ", A, "far-string:", "' @pipeline().RunId", json.dumps([A, "Other"])):
            decoded = read_notebook_parameters(
                {"CAPACITY_ID_OR_NAME": "far-string:" + value}, ("CAPACITY_ID_OR_NAME",),
            )
            self.assertEqual(decoded["CAPACITY_ID_OR_NAME"], value)
        for value in (None, "", A, False):
            with self.assertRaises(ValueError):
                read_notebook_parameters({"CAPACITY_ID_OR_NAME": value}, ("CAPACITY_ID_OR_NAME",))
        with self.assertRaises(ValueError):
            read_notebook_parameters({}, ("CAPACITY_ID_OR_NAME",))

    def test_invalid_parameter_types_controls_and_predicate_injection_fail(self):
        for value in (
            False, 0, 12, [], {}, "Other\x00", "Other\nName",
            "[]", '[""]', '[" "]', '["Other", null]', '[false]', '[12]', '[{}]', '[["Other"]]',
            '["Other",]', '["Other"', '["Other\\nName"]', '["Other\\u0000"]',
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    capacity_selector(value)
                with self.assertRaises(ValueError):
                    validate_config({**DEFAULTS, "CAPACITY_ID_OR_NAME": value})
                with self.assertRaises(ValueError):
                    validate_source({**DEFAULTS, "FUAM_WORKSPACE_ID": FUAM, "CAPACITY_ID_OR_NAME": value})
                with self.assertRaises(ValueError):
                    self.parent({"CAPACITY_ID_OR_NAME": value})
        for value in ("bad-id", "' OR 1=1 --", A + "'", False, 0, []):
            with self.assertRaises(ValueError):
                fuam_query("cu_seconds", 7, end_date=date(2026, 9, 22), capacity_id=value)
        for values in ([], [None], [False], ["bad-id"], [A, "' OR 1=1 --"], A):
            with self.subTest(values=values), self.assertRaises(ValueError):
                fuam_query("cu_seconds", 7, end_date=date(2026, 9, 22), capacity_ids=values)
        with self.assertRaises(ValueError):
            fuam_query("cu_seconds", 7, end_date=date(2026, 9, 22), capacity_id=A, capacity_ids=[B])

    @patch("orchestration.setup.deploy", return_value={})
    def test_provision_propagates_normalized_defaults(self, deploy_mock):
        for value, expected in (
            (None, ""), (" Other ", "Other"), (A, A),
            ('[" Other ", "' + A + '"]', json.dumps(["Other", A])),
        ):
            provision(
                Mock(), workspace_id=HOST, lakehouse_id=LH, child_pipeline_id=PIPELINE, repo_dir=ROOT,
                name="Targeted", defaults={"FUAM_WORKSPACE_ID": FUAM, "CAPACITY_ID_OR_NAME": value},
                context={"defaultLakehouseId": LH, "defaultLakehouseWorkspaceId": HOST},
            )
            self.assertEqual(deploy_mock.call_args.kwargs["defaults"]["CAPACITY_ID_OR_NAME"], expected)

    def test_setup_and_generated_notebook_syntax_and_parameter_contract(self):
        notebook = json.loads((ROOT / "fabric" / "notebooks" / "06_targeted_review_setup.ipynb").read_text(encoding="utf-8"))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                ast.parse("".join(cell["source"]))
        params = next(cell for cell in notebook["cells"] if cell["id"] == "parameters")
        namespace = {}
        exec("".join(params["source"]), namespace)
        self.assertEqual(namespace["CAPACITY_ID_OR_NAME"], "")
        setup_code = "".join(next(cell for cell in notebook["cells"] if cell["id"] == "deploy")["source"])
        self.assertIn('"CAPACITY_ID_OR_NAME": CAPACITY_ID_OR_NAME', setup_code)
        runner = runtime_notebook("runtime.zip", HOST, LH, child_pipeline_id=PIPELINE)
        for cell in runner["cells"]:
            ast.parse("".join(cell["source"]))
        self.assertIn("CAPACITY_ID_OR_NAME = None", "".join(runner["cells"][0]["source"]))
        self.assertIn("CAPACITY_ID_OR_NAME", SELECTION_PARAMETERS)


if __name__ == "__main__":
    unittest.main()

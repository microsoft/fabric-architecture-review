# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Verify fixed monitoring contracts without Spark or customer data."""
from datetime import date, datetime, timezone
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from collectors import _http
from orchestration.deployment import DEFAULTS
from orchestration.sources import fuam_query, read_source, resolve_lakehouse, validate_source

WS = "11111111-1111-1111-1111-111111111111"
LH = "22222222-2222-2222-2222-222222222222"


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.config = {**DEFAULTS, "FUAM_WORKSPACE_ID": WS}
        self.client = Mock()
        self.client.list_items.return_value = [{"id": LH, "displayName": "FUAM"}]
        self.inventory = self.enterContext(patch(
            "orchestration.sources.collect_workspace_groups", return_value=[{"id": WS}],
        ))

    def test_fixed_metric_columns_and_complete_calendar_window(self):
        query = fuam_query("cu_seconds", 7, end_date=date(2026, 9, 19))
        self.assertIn("SUM([TotalCUs])", query)
        self.assertIn("'2026-09-12'", query)
        self.assertIn("'2026-09-19'", query)
        self.assertIn("LEFT JOIN", query)
        self.assertNotIn("TOP", query)
        self.assertIn("SUM([ThrottlingInMin])", fuam_query("recorded_throttling_minutes", 7, end_date=date(2026, 9, 19)))

    def test_unsupported_source_and_operation_counts_rejected(self):
        for mode in ("auto", "capacity_metrics"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_source({**self.config, "SOURCE_MODE": mode})
        with self.assertRaises(ValueError):
            validate_source({**self.config, "RANKING_METRIC": "throttled_operations"})

    def test_fuam_needs_no_source_selector_and_accepts_legacy_fuam(self):
        self.assertNotIn("SOURCE_MODE", DEFAULTS)
        validate_source(self.config)
        validate_source({**self.config, "SOURCE_MODE": "fuam"})

    def test_missing_or_ambiguous_source_never_falls_back(self):
        with self.assertRaises(ValueError):
            validate_source(DEFAULTS)
        for items in ([], [{"id": LH}, {"id": WS}]):
            self.client.list_items.return_value = items
            with self.assertRaises(ValueError):
                resolve_lakehouse(self.config, self.client)

    def test_explicit_lakehouse_does_not_discover_other_tables(self):
        self.config["FUAM_LAKEHOUSE_ID"] = LH
        self.client.get_json.return_value = {"id": LH, "displayName": "FUAM"}
        self.assertEqual(resolve_lakehouse(self.config, self.client)["id"], LH)
        self.client.list_items.assert_not_called()

    @patch("orchestration.sources.query_rows")
    def test_query_error_propagates_and_invalid_values_fail(self, query):
        query.side_effect = RuntimeError("SQL permission denied")
        with self.assertRaisesRegex(RuntimeError, "permission denied"):
            read_source(self.config, self.client)
        query.side_effect = None
        query.return_value = [{"workspace_id": WS, "metric_value": 5, "invalid_values": 1}]
        with self.assertRaisesRegex(ValueError, "null or negative"):
            read_source(self.config, self.client)

    @patch("orchestration.sources.query_rows", return_value=[])
    def test_empty_source_is_preserved_for_safe_skip(self, query):
        self.assertEqual(read_source(self.config, self.client), ("fuam", []))

    @patch("orchestration.sources.query_rows")
    def test_blank_workspace_groups_are_warned_not_mapped_by_name(self, query):
        valid = {"workspace_id": WS, "metric_value": 10, "invalid_values": 0}
        query.return_value = [
            {"workspace_id": value, "workspace_name": WS, "metric_value": 0.256, "invalid_values": 0}
            for value in (None, "", " \t\n")
        ] + [valid]
        with patch("builtins.print") as output:
            result = read_source(self.config, self.client)
        self.assertEqual(result[1], [valid])
        self.assertEqual(result.unattributed_workspace_groups, 3)
        output.assert_any_call(
            "WARNING: FUAM excluded 3 unattributed workspace metric group(s) with a null/blank "
            "WorkspaceId; their consumption cannot be assigned to a review workspace."
        )
        self.assertEqual(len(query.return_value), 4)

    @patch("orchestration.sources.query_rows")
    def test_invalid_metrics_on_unattributed_rows_still_fail(self, query):
        query.return_value = [{"workspace_id": "", "metric_value": -1, "invalid_values": 1}]
        with self.assertRaisesRegex(ValueError, "null or negative"):
            read_source(self.config, self.client)

    @patch("orchestration.sources.query_rows")
    def test_source_diagnostics_match_the_executed_query_and_preserve_rows(self, query):
        rows = [{"workspace_id": WS, "metric_value": 10, "invalid_values": 0}]
        query.return_value = rows
        with patch("orchestration.sources.datetime") as clock, patch("builtins.print") as output:
            clock.now.return_value = datetime(2026, 9, 19, 18, 45, tzinfo=timezone.utc)
            source, actual = read_source(self.config, self.client)
        self.assertEqual(source, "fuam")
        self.assertIs(actual, rows)
        clock.now.assert_called_once_with(timezone.utc)
        query.assert_called_once_with(
            WS, {"id": LH, "displayName": "FUAM"},
            fuam_query("cu_seconds", 7, end_date=date(2026, 9, 19)),
        )
        output.assert_called_once_with(
            f"FUAM source: workspace_id={WS}, lakehouse_id={LH}, metric=cu_seconds, "
            "window_start=2026-09-12, window_end_exclusive=2026-09-19."
        )

    @patch("orchestration.sources.query_rows")
    def test_authoritative_inventory_is_fully_paged_before_matching(self, query):
        row = {"workspace_id": WS, "metric_value": 10, "invalid_values": 0}
        query.return_value = [row]
        self.inventory.side_effect = _http.collect_workspace_groups
        first = [{"id": str(UUID(int=i + 1))} for i in range(5000)]
        with patch.object(_http, "get_json", side_effect=[
            {"value": first}, {"value": [{"id": WS.upper()}]},
        ]) as get:
            result = read_source(self.config, self.client)
        self.assertEqual(result[1], [row])
        self.assertEqual(result.workspace_inventory["workspace_count"], 5001)
        self.assertEqual(result.workspace_inventory["unmatched_count"], 0)
        self.assertEqual([call.kwargs["params"] for call in get.call_args_list], [
            {"$top": 5000, "$skip": 0}, {"$top": 5000, "$skip": 5000},
        ])

    @patch("orchestration.sources.query_rows", return_value=[])
    def test_inventory_failure_never_becomes_successful_empty_source(self, query):
        self.inventory.side_effect = _http.collect_workspace_groups
        first = {"value": [{"id": str(UUID(int=i + 1))} for i in range(5000)]}
        for status in (401, 403, 429, 503):
            for pages in ([], [first]):
                with self.subTest(status=status, later_page=bool(pages)), patch.object(
                    _http, "get_json",
                    side_effect=[*pages, _http.HttpError("Inventory unavailable", status_code=status)],
                ), self.assertRaises(_http.HttpError):
                    read_source(self.config, self.client)

    @patch("orchestration.sources.query_rows")
    def test_positive_unsupported_metadata_is_excluded_but_unknown_metadata_is_not(self, query):
        ids = [str(UUID(int=i + 1)) for i in range(7)]
        query.return_value = [
            {"workspace_id": wid, "metric_value": 1, "invalid_values": 0} for wid in ids
        ]
        self.inventory.return_value = [
            {"id": ids[0], "type": "AdminWorkspace"},
            {"id": ids[1], "state": "Deleted"},
            {"id": ids[2], "isOnDedicatedCapacity": False},
            {"id": ids[3], "isOnPremiumPerUserCapacity": True},
            {"id": ids[4], "capacitySku": "PP3"},
            {"id": ids[5]},
            {"id": ids[6], "isOnDedicatedCapacity": True, "capacitySku": "F64"},
        ]
        result = read_source(self.config, self.client)
        self.assertEqual([row["workspace_id"] for row in result[1]], ids[5:])
        self.assertEqual(result.workspace_inventory["unsupported_ids"], ids[:5])
        self.assertEqual(result.workspace_inventory["unmatched_ids"], [])


if __name__ == "__main__":
    unittest.main()

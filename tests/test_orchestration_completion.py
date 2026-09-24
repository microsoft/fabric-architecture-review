# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline native-email preparation contracts; all service/credential calls are mocked."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from html import escape, unescape
import inspect
import io
import json
from pathlib import Path
import tempfile
from threading import Barrier
from types import SimpleNamespace
import unicodedata
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlsplit
from requests.structures import CaseInsensitiveDict

from orchestration import completion, runtime
from orchestration.completion import (
    completion_messages, prepare_owner_emails, report_link, resolve_owners,
    validate_email_address, validate_report_url,
)
from orchestration.fabric_api import FabricRequestError
from orchestration.runtime import (
    prepare_notifications, run_notifications, run_selection, select_workspaces, validate_config,
)

WS = "11111111-1111-1111-1111-111111111111"
WS2 = "22222222-2222-2222-2222-222222222222"
ITEM = "33333333-3333-3333-3333-333333333333"
RUN = "44444444-4444-4444-4444-444444444444"
CHILD = "55555555-5555-5555-5555-555555555555"
FAR_WS = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FUAM_WS = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
REPORT = "https://app.powerbi.com/groups/test/reports/test"
CONFIG = {
    "RANKING_METRIC": "cu_seconds", "LOOKBACK_DAYS": "7", "TOP_N": "5",
    "NOTIFICATIONS_ENABLED": "true",
    "FAR_REPORT_URL": REPORT, "CHILD_WORKSPACE_ID": FAR_WS, "CHILD_PIPELINE_ID": ITEM,
    "FUAM_WORKSPACE_ID": FUAM_WS,
}
NOTIFICATIONS = {key: CONFIG[key] for key in (
    "NOTIFICATIONS_ENABLED", "FAR_REPORT_URL", "CHILD_WORKSPACE_ID", "CHILD_PIPELINE_ID",
)}
ROWS = [
    {"workspace_id": WS, "workspace_name": "First workspace", "metric_value": 5},
    {"workspace_id": WS2, "workspace_name": "Second workspace", "metric_value": 3},
]


def principal(email="admin@example.com", kind="User", access="Admin"):
    return {"principalType": kind, "groupUserAccessRight": access, "emailAddress": email}


def response(payload, status=200):
    return Mock(status_code=status, json=Mock(return_value=payload), headers=CaseInsensitiveDict())


def state():
    return {
        "workspaces": [dict(row) for row in ROWS], "parent_run_id": RUN,
        "metric": "cu_seconds", "metric_caveat": "Consumption is not evidence of a performance defect.",
        "lookback_days": 7,
    }


class ValidationTests(unittest.TestCase):
    def test_public_email_normalization(self):
        for value in ("Admin@Example.COM", "first.last+tag@sub.example.co.uk", "o'neil@my-domain.com",
                      "name_surname@example.com", "guest_example.com#EXT#@tenant.onmicrosoft.com"):
            with self.subTest(value=value):
                self.assertEqual(validate_email_address(value), value.lower())

    def test_email_rejects_lists_headers_controls_and_malformed_upns(self):
        invalid = [
            None, True, [], "", "not-email", "@example.com", "a@", "a@@example.com",
            "a@localhost", "a@127.0.0.1", "a@-example.com", "a@example-.com", "a@example..com",
            "a@example.com.", ".a@example.com", "a.@example.com", "a..b@example.com",
            "a b@example.com", "a@example.com ", " a@example.com", "<a@example.com>",
            "A <a@example.com>", "a@example.com;b@example.com", "a@example.com,b@example.com",
            "a@example.com\r\nBcc: b@example.com", "a\t@example.com", "a\n@example.com",
            "a\x00@example.com", "a\x7f@example.com", "a\u200b@example.com",
            "a\u00a0@example.com", '"a"@example.com', "a(comment)@example.com",
            "a\\b@example.com", "a" * 65 + "@example.com", "a@" + "b" * 64 + ".com",
            "a@" + ".".join(["b" * 63] * 4) + ".com", "a@example.c", "a@example.123",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                validate_email_address(value)
            self.assertEqual(str(error.exception), "A single valid public-domain email address is required.")

    def test_report_exact_https_allowlist(self):
        for host in ("app.powerbi.com", "app.fabric.microsoft.com"):
            url = f"https://{host}/groups/test/reports/test?ctid=test#page"
            self.assertEqual(validate_report_url(url), url)
        for url in (
            None, True, "", "http://app.powerbi.com/report", "//app.powerbi.com/report",
            "https://app.powerbi.com", "https://app.powerbi.com/",
            "https://app.powerbi.com.evil.test/report", "https://evil.test/app.powerbi.com/report",
            "https://user@app.powerbi.com/report", "https://app.powerbi.com:443/report",
            "https://APP.POWERBI.COM/report", "https://app.powerbi.com./report",
            "https://app.powerbi.com\n/report", " https://app.powerbi.com/report",
            "https://app.powerbi.com/a b", "https://app.powerbi.com/a\x00b",
            "https://app.powerbi.com\\@evil.test/report", REPORT + "/" * 4096,
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_report_url(url)

    def test_report_filter_is_workspace_specific_and_preserves_other_context(self):
        url = REPORT + "?ctid=tenant&filter=old&FILTER=other&pageName=Overview#bookmark"
        parsed = urlsplit(report_link(url, WS))
        self.assertEqual(parse_qs(parsed.query), {
            "ctid": ["tenant"], "pageName": ["Overview"],
            "filter": [f"gold_workspace_risk/workspace_id eq '{WS}'"],
        })
        self.assertEqual(parsed.fragment, "bookmark")
        with self.assertRaises(ValueError):
            report_link(REPORT, "not-a-guid")

    def test_no_sending_api_or_removed_dependencies(self):
        for module in (completion, runtime):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertNotIn("orchestration.notifications", imports)
            self.assertNotIn("orchestration.ingestion", imports)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ("post", "put", "patch", "send", "sendmail", "request"))
            self.assertNotIn("NOTIFICATION_CONFIG_JSON", ast.unparse(tree))
        for name in ("notify_owners", "completion_events", "publish_notifications"):
            self.assertFalse(hasattr(completion, name) or hasattr(runtime, name))


class OwnerTests(unittest.TestCase):
    def test_only_unique_direct_user_admins_are_resolved_without_logging_addresses(self):
        transport = Mock(return_value=response({"value": [
            principal("TEST@example.com"), principal("test@example.com"),
            principal("viewer@example.com", access="Viewer"),
            principal("group@example.com", kind="Group"), principal(kind="App"),
        ]}))
        token = Mock(return_value="synthetic-token")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(resolve_owners([WS], token, transport=transport), {WS: ["test@example.com"]})
        self.assertEqual(output.getvalue(), "")
        token.assert_called_once_with()
        transport.assert_called_once_with(
            f"https://api.powerbi.com/v1.0/myorg/admin/groups/{WS}/users",
            headers={"Authorization": "Bearer synthetic-token"}, timeout=120, allow_redirects=False,
        )

    def test_lookup_http_and_payload_fail_closed(self):
        for code in (202, 301, 302, 401, 403, 404, 429, 500):
            with self.subTest(code=code), self.assertRaises(RuntimeError):
                resolve_owners([WS], lambda: "", transport=Mock(return_value=response({}, code)))
        invalid = [
            None, [], "", {"error": None, "value": [principal()]}, {},
            {"value": {}}, {"value": [None]}, {"value": ["bad"]}, {"value": [{}]},
            {"value": [principal()], "@odata.nextLink": "more"},
            {"value": [principal()], "continuationUri": "more"},
            {"value": [principal()], "continuationToken": "more"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                resolve_owners([WS], lambda: "", transport=Mock(return_value=response(payload)))
        malformed = response(None)
        malformed.json.side_effect = ValueError("Sensitive service response")
        with self.assertRaisesRegex(RuntimeError, "invalid JSON") as error:
            resolve_owners([WS], lambda: "", transport=Mock(return_value=malformed))
        self.assertNotIn("Sensitive", str(error.exception))

    def test_bad_admin_email_invalidates_entire_workspace_without_fallback(self):
        for email in (None, 1, "", "one@example.com;two@example.com", "bad", " admin@example.com"):
            with self.subTest(email=email), self.assertRaisesRegex(ValueError, "no usable email") as error:
                resolve_owners([WS], lambda: "", transport=Mock(return_value=response({
                    "value": [principal(), principal(email)],
                })))
            self.assertNotIn("admin@example.com", str(error.exception))
        for principals in ([], [principal(kind="Group")], [principal(access="Member")],
                           [principal(kind="App")]):
            with self.subTest(principals=principals), self.assertRaisesRegex(ValueError, "no direct-user"):
                resolve_owners([WS], lambda: "", transport=Mock(return_value=response({"value": principals})))

    def test_transport_and_credential_failures_are_not_swallowed(self):
        for token, transport in (
            (Mock(side_effect=RuntimeError("No credentials")), Mock()),
            (lambda: "", Mock(side_effect=OSError("Network unavailable"))),
        ):
            with self.assertRaises((RuntimeError, OSError)):
                resolve_owners([WS], token, transport=transport)

    def test_all_lookups_finish_before_any_message_is_built(self):
        actual_resolve = resolve_owners
        for status in (403, 404):
            transport = Mock(side_effect=[
                response({"value": [principal("first@example.com")]}), response({}, status),
            ])
            with self.subTest(status=status), patch.object(
                completion, "resolve_owners", side_effect=lambda ids, token: actual_resolve(
                    ids, token, transport=transport,
                ),
            ), patch.object(completion, "completion_messages") as messages:
                with self.assertRaises(RuntimeError):
                    prepare_owner_emails(NOTIFICATIONS, state(), Mock(token=lambda: ""))
            self.assertEqual(transport.call_count, 2)
            messages.assert_not_called()

    def test_lookup_failure_has_bounded_diagnostics_and_new_run_guidance_not_response_body(self):
        for code, request_id, expected in (
            ("PowerBIEntityNotFound", RUN, ("PowerBIEntityNotFound", RUN)),
            ("secret@example.com", "secret@example.com", ("unknown", "unknown")),
        ):
            result = response({"error": {"code": code, "message": "secret@example.com"}}, 404)
            result.headers["RequestId"] = request_id
            with self.subTest(code=code), self.assertRaises(RuntimeError) as error:
                resolve_owners([WS], lambda: "secret-token", transport=Mock(return_value=result))
            text = str(error.exception)
            self.assertIn(f"error code {expected[0]}; request ID {expected[1]}", text)
            self.assertIn("HTTP 404 alone does not establish deletion", text)
            self.assertIn("start a new parent run", text)
            self.assertNotIn("secret", text)

    def test_preparation_only_reads_and_returns_messages(self):
        client = Mock()
        original = state()
        before = json.dumps(original)
        with patch.object(completion, "resolve_owners", return_value={
            WS: ["first@example.com"], WS2: ["second@example.com"],
        }) as owners:
            messages = prepare_owner_emails(NOTIFICATIONS, original, client)
        owners.assert_called_once_with([WS, WS2], client.token)
        self.assertEqual(len(messages), 2)
        self.assertEqual(json.dumps(original), before)
        client.assert_not_called()
        self.assertEqual(client.method_calls, [])


class MessageTests(unittest.TestCase):
    def test_multi_workspace_messages_explain_report_filter_limitations(self):
        messages = completion_messages(NOTIFICATIONS, state(), {
            WS: ["first@example.com"], WS2: ["second@example.com"],
        })
        self.assertEqual(len(messages), 2)
        for message in messages:
            with self.subTest(workspace=message["workspace_id"]):
                self.assertIn("Open the FAR review report</a>", message["body"])
                self.assertIn("In the governance report, it filters workspace-risk visuals, not the whole report", message["body"])
                self.assertIn("separately configured Workspace Owner report", message["body"])
                self.assertIn("security role restricts data to your authorized workspaces", message["body"])
                self.assertIn("Findings and recommendations may include other reviewed workspaces", message["body"])
                self.assertIn("check their workspace and item scope before acting", message["body"])
                self.assertNotIn("report filtered to this workspace", message["body"])
                self.assertIn(
                    escape(report_link(REPORT, message["workspace_id"]), quote=True), message["body"],
                )

    def test_workspace_and_owner_isolation_output_shape_and_caveat(self):
        messages = completion_messages(NOTIFICATIONS, state(), {
            WS: ["shared@example.com", "first@example.com"],
            WS2: ["shared@example.com", "second@example.com"],
        })
        self.assertEqual([(m["workspace_id"], m["to"]) for m in messages], [
            (WS, "first@example.com"), (WS, "shared@example.com"),
            (WS2, "second@example.com"), (WS2, "shared@example.com"),
        ])
        for message in messages:
            self.assertEqual(set(message), {"to", "subject", "body", "workspace_id"})
            workspace_id = message["workspace_id"]
            other_id = WS2 if workspace_id == WS else WS
            self.assertIn(escape(report_link(REPORT, workspace_id), quote=True), message["body"])
            self.assertNotIn(other_id, unescape(message["body"]))
            self.assertNotIn("first@example.com", message["body"])
            self.assertNotIn("second@example.com", message["body"])
            self.assertIn("cu_seconds", message["body"])
            self.assertIn("7 days", message["body"])
            self.assertIn("Consumption is not evidence", message["body"])
            self.assertIn("recommendations, not guaranteed root causes", message["body"])
            self.assertIn("Request access", message["body"])
            self.assertIn("if available", message["body"])
            self.assertIn("FAR report owner or your Power BI administrator", message["body"])
            self.assertIn("This link does not grant access", message["body"])
            self.assertEqual(message["body"].count("<a href="), 1)
            self.assertNotIn("Data Agent", message["body"])
            self.assertIn("Selection rank: " + ("1" if workspace_id == WS else "2"), message["body"])
        self.assertNotIn("Second workspace", messages[0]["body"])
        self.assertNotIn("First workspace", messages[-1]["body"])
        self.assertEqual(messages, completion_messages(NOTIFICATIONS, state(), {
            WS: ["shared@example.com", "first@example.com"], WS2: ["shared@example.com", "second@example.com"],
        }))

    def test_html_escaping_of_metadata_link_and_safe_subject(self):
        audit = state()
        audit["workspaces"][0]["workspace_name"] = '  <img src=x onerror="alert(1)">\r\nBcc:\t \x00\u202eevil  '
        audit["workspaces"][0]["metric_value"] = "<script>value</script>"
        audit["metric"] = "<metric>"
        audit["metric_caveat"] = '<b title="unsafe">Do not assume causes & effects</b>'
        audit["parent_run_id"] = "<tracking>"
        audit["lookback_days"] = "<days>"
        config = {**NOTIFICATIONS, "FAR_REPORT_URL": REPORT + '?x=" onclick="bad&y=<unsafe>'}
        # Spaces in configured links fail validation; encoded query content remains inert.
        config["FAR_REPORT_URL"] = config["FAR_REPORT_URL"].replace(" ", "%20")
        message = completion_messages(config, audit, {
            WS: ["first@example.com"], WS2: ["second@example.com"],
        })[0]
        for value in ('<img src=x onerror="alert(1)">', "<script>value</script>", "<metric>",
                      audit["metric_caveat"], "<tracking>", "<days>"):
            self.assertIn(escape(value), message["body"])
            self.assertNotIn(value, message["body"])
        self.assertIn(escape(report_link(config["FAR_REPORT_URL"], WS), quote=True), message["body"])
        self.assertEqual(message["subject"], 'FAR review ready: <img src=x onerror="alert(1)"> Bcc: evil')
        self.assertFalse(any(unicodedata.category(ch).startswith("C") for ch in message["subject"]))
        self.assertEqual(message["subject"], message["subject"].strip())

    def test_invalid_recipient_map_cannot_fall_back_or_broaden_scope(self):
        for owners in (
            {}, {WS: ["one@example.com"]}, {WS: [], WS2: ["two@example.com"]},
            {WS: "one@example.com", WS2: ["two@example.com"]},
            {WS: ["one@example.com"], WS2: ["two@example.com"], ITEM: ["extra@example.com"]},
            {WS: ["one@example.com"], WS2: ["one@example.com,two@example.com"]},
        ):
            with self.subTest(owners=owners), self.assertRaises(ValueError):
                completion_messages(NOTIFICATIONS, state(), owners)

    def test_subject_is_bounded(self):
        audit = state()
        audit["workspaces"][0]["workspace_name"] = "x" * 1000
        messages = completion_messages(NOTIFICATIONS, audit, {
            WS: ["first@example.com"], WS2: ["second@example.com"],
        })
        self.assertEqual(len(messages[0]["subject"]), 255)
        audit["workspaces"][0]["workspace_name"] = "x" * (254 - len("FAR review ready: ")) + " trailing"
        messages = completion_messages(NOTIFICATIONS, audit, {
            WS: ["first@example.com"], WS2: ["second@example.com"],
        })
        self.assertEqual(len(messages[0]["subject"]), 254)
        self.assertEqual(messages[0]["subject"], messages[0]["subject"].strip())


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name)
        self.path = self.directory / (RUN + ".json")
        self.marker = self.directory / (RUN + ".notification-started")
        self.client = Mock()
        self.prepare = Mock(side_effect=lambda options, audit: completion_messages(options, audit, {
            WS: ["first@example.com"], WS2: ["second@example.com"],
        }))

    def select(self, config=None, rows=None):
        return select_workspaces(
            CONFIG if config is None else config, RUN,
            read_source=lambda _: ("fuam", ROWS if rows is None else rows), state_dir=self.directory,
        )

    def prepare_stage(self, config=None, child=CHILD):
        return prepare_notifications(
            NOTIFICATIONS if config is None else config, RUN, child,
            prepare=self.prepare, state_dir=self.directory,
        )

    def audit(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write_audit(self, audit):
        self.path.write_text(json.dumps(audit), encoding="utf-8")

    def test_invalid_notification_boolean_fails_before_any_work(self):
        for value in (None, "", True, 1, {}, "yes"):
            config = {**CONFIG, "NOTIFICATIONS_ENABLED": value}
            with self.subTest(value=value), patch.object(Path, "mkdir") as mkdir:
                source = Mock()
                with self.assertRaises(ValueError):
                    select_workspaces(config, RUN, read_source=source, state_dir=self.directory)
                source.assert_not_called()
                mkdir.assert_not_called()
                with patch.object(runtime, "FabricClient") as client, self.assertRaises(ValueError):
                    run_selection({**config, "PARENT_RUN_ID": RUN})
                client.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_enabled_selection_needs_no_separate_email_configured_flag(self):
        source = Mock(return_value=("fuam", [ROWS[0]]))
        self.assertNotIn("EMAIL_CONFIGURED", CONFIG)
        result = select_workspaces(CONFIG, RUN, read_source=source, state_dir=self.directory)
        self.assertEqual(result["workspace_ids"], WS)
        source.assert_called_once_with(CONFIG)

    def test_selection_excludes_hosting_workspaces_before_top_n_for_both_metrics(self):
        rows = [
            {"workspace_id": FAR_WS, "metric_value": 100},
            {"workspace_id": FUAM_WS.upper(), "metric_value": 90},
            *ROWS,
        ]
        for metric in ("cu_seconds", "recorded_throttling_minutes"):
            for allowlist in ("", f"{FAR_WS},{FUAM_WS},{WS},{WS2}"):
                with self.subTest(metric=metric, allowlist=allowlist), tempfile.TemporaryDirectory() as folder:
                    config = {
                        **CONFIG, "RANKING_METRIC": metric, "TOP_N": "2",
                        "WORKSPACE_IDS": allowlist, "CHILD_WORKSPACE_ID": FAR_WS.upper(),
                    }
                    result = select_workspaces(
                        config, RUN, read_source=lambda _: ("fuam", rows), state_dir=Path(folder),
                    )
                    self.assertEqual(result["workspace_ids"], f"{WS},{WS2}")
                    self.assertEqual(result["workspace_count"], 2)
                    audit = json.loads((Path(folder) / (RUN + ".json")).read_text())
                    self.assertEqual([row["workspace_id"] for row in audit["workspaces"]], [WS, WS2])
                    self.assertEqual(audit["child_workspace_id"], FAR_WS)

    def test_shared_far_and_fuam_workspace_is_excluded(self):
        result = self.select(
            config={**CONFIG, "FUAM_WORKSPACE_ID": FAR_WS.upper(), "TOP_N": "1"},
            rows=[{"workspace_id": FAR_WS, "metric_value": 100}, *ROWS],
        )
        self.assertEqual(result["workspace_ids"], WS)

    def test_only_excluded_workspaces_skip_review_and_email(self):
        result = self.select(
            config={**CONFIG, "WORKSPACE_IDS": f"{FAR_WS},{FUAM_WS}"},
            rows=[
                {"workspace_id": FAR_WS, "metric_value": 100},
                {"workspace_id": FUAM_WS, "metric_value": 90},
            ],
        )
        self.assertEqual(result["status"], "skipped_no_candidates")
        self.assertEqual(result["workspace_ids"], "")
        self.assertEqual(result["workspace_count"], 0)
        self.assertEqual(self.audit()["workspaces"], [])
        with self.assertRaisesRegex(ValueError, "nonempty selection"):
            self.prepare_stage()
        self.client.get_json.assert_not_called()
        self.prepare.assert_not_called()

    def test_invalid_report_fails_before_source_or_owner_reads(self):
        source = Mock()
        for url in ("", "http://app.powerbi.com/report", "https://evil.test/report", None):
            with self.subTest(url=url), self.assertRaises(ValueError):
                select_workspaces({**CONFIG, "FAR_REPORT_URL": url}, RUN,
                                  read_source=source, state_dir=self.directory)
            with self.assertRaises(ValueError):
                self.prepare_stage({**NOTIFICATIONS, "FAR_REPORT_URL": url})
        source.assert_not_called()
        self.client.get_json.assert_not_called()
        self.prepare.assert_not_called()

    def test_disabled_notifications_no_reads_imports_or_credentials(self):
        config = {"NOTIFICATIONS_ENABLED": "false"}
        with patch("builtins.__import__", side_effect=AssertionError("No imports")), \
                patch.object(Path, "read_text", side_effect=AssertionError("No reads")), \
                patch.object(runtime, "FabricClient", side_effect=AssertionError("No client")):
            result = run_notifications(config)
            result2 = prepare_notifications(config, "invalid-parent", "invalid-child",
                                            prepare=self.prepare, state_dir=self.directory)
            self.assertEqual(result, {"notifications": "disabled", "messages": []})
            self.assertEqual(result2, result)
        self.client.get_json.assert_not_called()
        self.prepare.assert_not_called()
        validate_config({**CONFIG, "NOTIFICATIONS_ENABLED": "false",
                         "FAR_REPORT_URL": ""})

    def test_selection_returns_scope_without_starting_or_waiting_for_child(self):
        config = {**CONFIG, "PARENT_RUN_ID": RUN, "NOTIFICATIONS_ENABLED": "false"}
        source = Mock(return_value=("fuam", [ROWS[0]]))
        with patch.dict("sys.modules", {"notebookutils": SimpleNamespace(credentials=Mock())}), \
                patch.object(runtime, "FabricClient", return_value=self.client), \
                patch.object(runtime, "Path", return_value=self.directory), \
                patch("orchestration.sources.read_source", source):
            result = run_selection(config)
            self.assertEqual(result, {
                "parent_run_id": RUN, "status": "selected", "workspace_ids": WS, "workspace_count": 1,
            })
            source.assert_called_once_with(config, self.client)
            with self.assertRaisesRegex(RuntimeError, "automatic replay"):
                run_selection(config)
            source.assert_called_once()
            self.assertEqual(run_notifications(config), {"notifications": "disabled", "messages": []})
        self.assertEqual(self.client.method_calls, [])
        self.prepare.assert_not_called()

    def test_source_error_never_falls_through_to_child_or_writes_selection(self):
        config = {**CONFIG, "PARENT_RUN_ID": RUN}
        for error in (RuntimeError("source access denied"), ValueError("invalid source"), OSError("source unavailable")):
            source = Mock(side_effect=error)
            with self.subTest(error=type(error)), \
                    patch.dict("sys.modules", {"notebookutils": SimpleNamespace(credentials=Mock())}), \
                    patch.object(runtime, "FabricClient", return_value=self.client), \
                    patch.object(runtime, "Path", return_value=self.directory), \
                    patch("orchestration.sources.read_source", source):
                with self.assertRaises(type(error)) as raised:
                    run_selection(config)
                self.assertIs(raised.exception, error)
                source.assert_called_once_with(config, self.client)
                self.assertFalse(self.path.exists())
                self.assertFalse(self.marker.exists())
                self.assertEqual(self.client.method_calls, [])
                self.prepare.assert_not_called()

    def test_empty_selection_is_durable_and_never_prepares_even_if_called_directly(self):
        with patch("builtins.print") as output:
            result = self.select(config={**CONFIG, "WORKSPACE_IDS": ""}, rows=[])
        output.assert_any_call("FUAM selection: source_rows=0, allowlist=blank, selected_workspaces=0.")
        self.assertEqual(result, {
            "parent_run_id": RUN, "status": "skipped_no_candidates", "workspace_ids": "", "workspace_count": 0,
        })
        saved = self.audit()
        self.assertEqual(saved["status"], "skipped_no_candidates")
        self.assertEqual(saved["workspace_ids"], "")
        self.assertEqual(saved["workspaces"], [])
        with self.assertRaisesRegex(ValueError, "nonempty selection"):
            self.prepare_stage()
        self.assertEqual(self.audit(), saved)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.client.method_calls, [])
        self.prepare.assert_not_called()

    def test_selection_diagnostics_distinguish_filtered_rows_from_empty_source(self):
        with patch("builtins.print") as output:
            result = self.select(config={**CONFIG, "WORKSPACE_IDS": ITEM}, rows=[ROWS[0]])
        self.assertEqual(result["workspace_count"], 0)
        output.assert_any_call("FUAM selection: source_rows=1, allowlist=set, selected_workspaces=0.")
        self.assertNotIn(ITEM, str(output.call_args_list))
        self.assertNotIn(WS, str(output.call_args_list))

    def test_selection_ranking_audit_empty_and_replay_behavior_preserved(self):
        result = self.select(rows=[ROWS[1], ROWS[0], {**ROWS[1], "metric_value": 2}])
        self.assertEqual(result, {
            "parent_run_id": RUN, "status": "selected", "workspace_ids": f"{WS},{WS2}", "workspace_count": 2,
        })
        self.assertEqual([row["metric_value"] for row in self.audit()["workspaces"]], [5, 5])
        source = Mock()
        with self.assertRaisesRegex(RuntimeError, "replay"):
            select_workspaces(CONFIG, RUN, read_source=source, state_dir=self.directory)
        source.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            empty = select_workspaces(CONFIG, RUN, read_source=lambda _: ("fuam", []), state_dir=Path(folder))
            self.assertEqual(empty["status"], "skipped_no_candidates")
            self.assertEqual(empty["workspace_ids"], "")

    def test_prepared_output_and_durable_audit_do_not_claim_delivery(self):
        self.select()
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.prepare_stage()
        self.assertEqual(set(result), {"parent_run_id", "child_run_id", "status", "messages"})
        self.assertEqual(result["status"], "emails_prepared")
        self.assertEqual(result["child_run_id"], CHILD)
        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(self.client.method_calls, [])
        saved = self.audit()
        self.assertEqual(saved["status"], "emails_prepared")
        self.assertEqual(saved["notifications"], "emails_prepared")
        self.assertEqual(saved["prepared_message_count"], 2)
        self.assertEqual(saved["child_run_id"], CHILD)
        self.assertNotIn("child_job_id", saved)
        self.assertNotIn("child_job_location", saved)
        self.assertIn("emails_prepared_at", saved)
        self.assertEqual(json.loads(self.marker.read_text()), {"child_run_id": CHILD})
        for forbidden in ("messages", "first@example.com", "second@example.com", "<p>", "emails_sent",
                          "submitted", '"completed"', "notification_receipts"):
            self.assertNotIn(forbidden, json.dumps(saved))
            self.assertNotIn(forbidden, output.getvalue())

    def test_invalid_native_child_run_id_fails_before_preparation(self):
        self.select()
        for child in ("", "not-a-run-id"):
            with self.subTest(child=child), self.assertRaises(ValueError):
                self.prepare_stage(child=child)
            self.client.get_json.assert_not_called()
            self.prepare.assert_not_called()
            self.assertFalse(self.marker.exists())
            self.assertEqual(self.audit()["status"], "selected")

    def test_cross_run_scope_and_invalid_audits_fail_before_preparation(self):
        self.select()
        good = self.audit()
        invalid = [
            [], None, {}, {**good, "parent_run_id": CHILD}, {**good, "child_workspace_id": WS2},
            {**good, "child_pipeline_id": WS2}, {**good, "workspaces": []},
            {**good, "workspaces": {}}, {**good, "workspace_ids": ""},
            {**good, "workspace_ids": WS2}, {**good, "workspace_ids": f"{WS2},{WS}"},
            {**good, "workspaces": [ROWS[0], ROWS[0]], "workspace_ids": f"{WS},{WS}"},
            {**good, "workspaces": [None]}, {**good, "workspaces": [{"workspace_id": WS}]},
            {**good, "workspaces": [{**ROWS[0], "workspace_id": "invalid"}]},
            {**good, "workspaces": [{**ROWS[0], "metric_value": float("nan")}]},
            {**good, "workspaces": [{**ROWS[0], "metric_value": -1}]},
            {**good, "workspaces": [{**ROWS[0], "metric_value": True}]},
            {**good, "metric": "unknown"}, {**good, "lookback_days": True},
            {**good, "lookback_days": 29}, {**good, "metric_caveat": None},
        ]
        for audit in invalid:
            self.write_audit(audit)
            with self.subTest(audit=audit), self.assertRaises(ValueError):
                self.prepare_stage()
            self.client.get_json.assert_not_called()
            self.prepare.assert_not_called()
            self.assertFalse(self.marker.exists())

    def test_missing_and_corrupt_audits_fail_closed(self):
        with self.assertRaises(FileNotFoundError):
            self.prepare_stage()
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.prepare_stage()
        self.client.get_json.assert_not_called()

    def test_prepared_same_child_and_cross_child_replays_are_blocked(self):
        self.select()
        self.prepare_stage()
        for child in (CHILD, RUN):
            with self.subTest(child=child), self.assertRaisesRegex(RuntimeError, "replay"):
                self.prepare_stage(child=child)
        self.prepare.assert_called_once()
        self.client.get_json.assert_not_called()

    def test_old_and_interrupted_statuses_and_bound_child_cannot_replay(self):
        self.select()
        good = self.audit()
        for status in ("completed", "sending_notifications", "notification_failed", "preparing_emails",
                       "email_preparation_failed", "emails_prepared", "skipped_no_candidates", None):
            self.write_audit({**good, "status": status})
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, "replay"):
                self.prepare_stage()
        for key in ("child_run_id", "child_job_id"):
            self.write_audit({**good, key: RUN})
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "replay"):
                self.prepare_stage()
        self.client.get_json.assert_not_called()

    def test_legacy_or_interrupted_marker_blocks_replay_even_with_selected_audit(self):
        self.select()
        self.marker.write_text("interrupted", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "replay"):
            self.prepare_stage()
        self.client.get_json.assert_not_called()
        self.prepare.assert_not_called()

    def test_concurrent_preparations_have_one_exclusive_winner(self):
        self.select()
        barrier = Barrier(2)

        actual_open = Path.open

        def open_marker(path, *args, **kwargs):
            if path == self.marker and args == ("x",):
                barrier.wait(timeout=10)
            return actual_open(path, *args, **kwargs)

        with patch.object(Path, "open", open_marker), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.prepare_stage) for _ in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=15)["status"])
                except FileExistsError:
                    outcomes.append("blocked")
        self.assertCountEqual(outcomes, ["emails_prepared", "blocked"])
        self.prepare.assert_called_once()
        self.assertEqual(self.audit()["status"], "emails_prepared")

    def test_preparation_failure_is_recorded_without_sensitive_error_or_partial_output(self):
        for error in (ValueError("private@example.com"), RuntimeError("sensitive body"), OSError("network")):
            with self.subTest(error=type(error)):
                with tempfile.TemporaryDirectory() as folder:
                    directory = Path(folder)
                    select_workspaces(CONFIG, RUN, read_source=lambda _: ("fuam", ROWS), state_dir=directory)
                    prepare = Mock(side_effect=error)
                    with self.assertRaises(type(error)):
                        prepare_notifications(NOTIFICATIONS, RUN, CHILD,
                                              prepare=prepare, state_dir=directory)
                    saved = json.loads((directory / (RUN + ".json")).read_text())
                    self.assertEqual(saved["status"], "email_preparation_failed")
                    self.assertEqual(saved["notifications"], "email_preparation_failed")
                    self.assertNotIn(str(error), json.dumps(saved))
                    with self.assertRaisesRegex(RuntimeError, "replay"):
                        prepare_notifications(NOTIFICATIONS, RUN, CHILD,
                                              prepare=prepare, state_dir=directory)
                    prepare.assert_called_once()

    def test_empty_preparation_fails_instead_of_recording_success(self):
        self.select()
        self.prepare.side_effect = None
        self.prepare.return_value = []
        with self.assertRaisesRegex(ValueError, "no messages"):
            self.prepare_stage()
        self.assertEqual(self.audit()["status"], "email_preparation_failed")

    def test_audit_save_failure_leaves_exclusive_guard(self):
        self.select()
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")), self.assertRaises(OSError):
            self.prepare_stage()
        self.assertTrue(self.marker.exists())
        self.prepare.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "replay"):
            self.prepare_stage()

    def test_final_audit_failure_does_not_return_messages_and_cannot_replay(self):
        self.select()
        actual_replace = Path.replace
        writes = 0

        def replace(pending, target):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("Final audit unavailable")
            return actual_replace(pending, target)

        with patch.object(Path, "replace", replace), self.assertRaises(OSError):
            self.prepare_stage()
        self.assertTrue(self.marker.exists())
        self.prepare.assert_called_once()
        self.assertEqual(self.audit()["status"], "preparing_emails")
        with self.assertRaisesRegex(RuntimeError, "replay"):
            self.prepare_stage()

    def test_enabled_entrypoint_end_to_end_prepares_without_sending(self):
        self.select()
        credentials = Mock()
        credentials.getToken.return_value = "synthetic-token"
        transport = Mock(side_effect=[
            response({"value": [principal("first@example.com"), principal("shared@example.com")]}),
            response({"value": [principal("second@example.com"), principal(kind="Group")]}),
        ])
        actual_resolve = resolve_owners
        self.client.token = lambda: credentials.getToken("pbi")
        with patch.dict("sys.modules", {"notebookutils": SimpleNamespace(credentials=credentials)}), \
                patch.object(runtime, "Path", return_value=self.directory), \
                patch.object(runtime, "FabricClient", return_value=self.client), \
                patch.object(completion, "resolve_owners", side_effect=lambda ids, token: actual_resolve(
                    ids, token, transport=transport,
                )), redirect_stdout(io.StringIO()) as output:
            result = run_notifications({**NOTIFICATIONS, "PARENT_RUN_ID": RUN, "CHILD_RUN_ID": CHILD})
        self.assertEqual(result["status"], "emails_prepared")
        self.assertEqual(len(result["messages"]), 3)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(credentials.getToken.call_args_list, [call("pbi"), call("pbi")])
        self.assertEqual(self.client.method_calls, [])
        self.assertEqual(self.audit()["prepared_message_count"], 3)
        self.assertEqual(output.getvalue(), "")

    def test_native_pipeline_run_id_is_not_queried_as_a_scheduler_job(self):
        self.select()
        missing = response({"errorCode": "JobInstanceNotFound"}, status=404)
        missing.headers = {}
        self.client.get_json.side_effect = FabricRequestError(
            "GET", f"/v1/workspaces/{FAR_WS}/items/{ITEM}/jobs/instances/{CHILD}", missing,
        )
        with patch.dict("sys.modules", {"notebookutils": SimpleNamespace(credentials=Mock())}), \
                patch.object(runtime, "Path", return_value=self.directory), \
                patch.object(runtime, "FabricClient", return_value=self.client), \
                patch.object(runtime, "prepare_owner_emails", side_effect=lambda options, audit, client:
                             self.prepare(options, audit)):
            result = run_notifications({**NOTIFICATIONS, "PARENT_RUN_ID": RUN, "CHILD_RUN_ID": CHILD})
        self.assertEqual(result["status"], "emails_prepared")
        self.assertEqual(result["child_run_id"], CHILD)
        self.client.get_json.assert_not_called()
        self.prepare.assert_called_once()

    def test_entrypoints_keep_exact_one_argument_generated_notebook_contract(self):
        for entrypoint in (run_selection, run_notifications):
            parameters = inspect.signature(entrypoint).parameters
            self.assertEqual(list(parameters), ["config"])
            self.assertIs(parameters["config"].default, inspect.Parameter.empty)
        credentials = Mock()
        modules = {"notebookutils": SimpleNamespace(credentials=credentials)}
        selection_config = {**CONFIG, "PARENT_RUN_ID": RUN}
        notification_config = {**NOTIFICATIONS, "PARENT_RUN_ID": RUN, "CHILD_RUN_ID": CHILD}
        with patch.dict("sys.modules", modules), \
                patch.object(runtime, "prepare_notifications", return_value={"messages": []}) as prepare, \
                patch.object(runtime, "select_workspaces", return_value={"status": "selected"}) as select:
            run_notifications(notification_config)
            run_selection(selection_config)
            prepare.assert_called_once()
            select.assert_called_once()
            self.assertEqual(prepare.call_args.args, (notification_config, RUN, CHILD))
            self.assertEqual(select.call_args.args, (selection_config, RUN))
        credentials.getToken.assert_not_called()


if __name__ == "__main__":
    unittest.main()

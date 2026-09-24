# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric client tests using in-memory HTTP responses."""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

from orchestration.fabric_api import BASE, FabricClient, FabricRequestError, definition_part


WS = "11111111-1111-1111-1111-111111111111"
ITEM = "22222222-2222-2222-2222-222222222222"


def response(status=200, data=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode() if data is not None else b""
    result.headers.update(headers or {})
    return result


class FabricApiTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.client = FabricClient(lambda: "fake-test-token", transport=self.transport, sleep=Mock())

    def test_rejects_foreign_pagination_hosts_before_transmitting_token(self):
        for target in ("https://example.com/v1/data", "http://api.fabric.microsoft.com/v1/data", "//example.com/data"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.client.request("GET", target)
        self.transport.assert_not_called()

    def test_pagination(self):
        continuation = BASE + f"/workspaces/{WS}/items?continuationToken=next"
        self.transport.side_effect = [
            response(data={"value": [{"id": "first"}], "continuationUri": continuation}),
            response(data={"value": [{"id": "second"}]}),
        ]
        self.assertEqual(self.client.list_items(WS, "Notebook"), [{"id": "first"}, {"id": "second"}])
        self.assertEqual(self.transport.call_args.args[:2], ("GET", continuation))

    def test_pagination_rejects_changed_endpoint(self):
        self.transport.return_value = response(
            data={"value": [{"id": "first"}], "continuationUri": BASE + "/next"}
        )
        with self.assertRaisesRegex(ValueError, "continuation changed its endpoint"):
            self.client.list_items(WS, "Notebook")
        self.transport.assert_called_once()

    def test_no_ambiguous_pipeline_submission_retry(self):
        self.transport.return_value = response(503)
        with self.assertRaises(RuntimeError):
            self.client.start_pipeline(WS, ITEM, {"WORKSPACE_IDS": WS})
        self.transport.assert_called_once()

    def test_validation_error_retains_response_without_logging_service_payload(self):
        failed = response(400, {
            "requestId": ITEM, "errorCode": "BadRequest",
            "message": "Missing parameter definition for ' json'",
            "details": [{"message": "sensitive-test-value"}],
        })
        self.transport.return_value = failed
        with self.assertRaises(FabricRequestError) as caught:
            self.client.request("POST", f"/workspaces/{WS}/items", json_body={})
        self.assertIs(caught.exception.response, failed)
        self.assertIn("BadRequest", str(caught.exception))
        self.assertIn(ITEM, str(caught.exception))
        self.assertNotIn("sensitive-test-value", str(caught.exception))
        self.assertNotIn("Missing parameter", str(caught.exception))
        self.transport.assert_called_once()

    def test_non_json_and_malformed_errors_preserve_http_failure(self):
        responses = [response(400, ["not-an-error-object"]), response(400, {"error": "wrong-shape"})]
        html = response(502)
        html._content = b"<html>sensitive-test-value</html>"
        responses.append(html)
        responses.append(response(400, {"errorCode": "unsafe\nvalue", "requestId": "unsafe\nvalue"}))
        for failed in responses:
            with self.subTest(body=failed.content):
                self.transport.return_value = failed
                with self.assertRaises(FabricRequestError) as caught:
                    self.client.request("POST", f"/workspaces/{WS}/items")
                self.assertIn("error code unknown", str(caught.exception))
                self.assertNotIn("sensitive-test-value", str(caught.exception))
                self.assertNotIn("unsafe", str(caught.exception))

    def test_pipeline_request_parameters_and_location(self):
        location = BASE + "/workspaces/" + WS + "/items/" + ITEM + "/jobs/instances/" + ITEM
        self.transport.return_value = response(202, headers={"Location": location})
        self.assertEqual(self.client.start_pipeline(WS, ITEM, {"WORKSPACE_IDS": WS}), location)
        payload = self.transport.call_args.kwargs["json"]
        self.assertEqual(payload, {"parameters": [{"name": "WORKSPACE_IDS", "type": "Text", "value": WS}]})
        self.assertFalse(self.transport.call_args.kwargs["allow_redirects"])

    def test_empty_update_operation_result_is_valid(self):
        self.transport.side_effect = [
            response(data={"status": "Succeeded"}),
            response(200),
        ]
        self.assertEqual(self.client.complete(response(202, headers={"Location": BASE + "/operations/op"})), {})

    def test_upsert_update_waits_for_success_without_requesting_a_result(self):
        public = BASE + "/operations/" + ITEM
        regional = "https://wabi-west-us3-a-primary-redirect.analysis.windows.net/v1/operations/" + ITEM
        for item_type in ("Report", "Notebook", "DataPipeline"):
            for location in (public, regional):
                for status in ("Succeeded", "Completed"):
                    with self.subTest(item_type=item_type, location=location, status=status):
                        self.transport.reset_mock()
                        self.transport.side_effect = [
                            response(data={"value": [{"id": ITEM, "displayName": "Existing"}]}),
                            response(202, headers={"Location": location}),
                            response(data={"status": "Running"}),
                            response(data={"status": status}),
                            response(400, {"errorCode": "OperationHasNoResult"}),
                        ]
                        self.assertEqual(self.client.upsert_item(WS, "Existing", item_type, {"parts": []}), ITEM)
                        self.assertEqual(self.transport.call_count, 4)
                        calls = self.transport.call_args_list
                        self.assertEqual(calls[1].args, ("POST", BASE + f"/workspaces/{WS}/items/{ITEM}/updateDefinition"))
                        self.assertEqual(calls[2].args, ("GET", public))
                        self.assertEqual(calls[3].args, ("GET", public))

    def test_upsert_synchronous_update_keeps_existing_id(self):
        for status in (200, 204):
            with self.subTest(status=status):
                self.transport.reset_mock()
                self.transport.side_effect = [
                    response(data={"value": [{"id": ITEM, "displayName": "Existing"}]}),
                    response(status),
                ]
                self.assertEqual(self.client.upsert_item(WS, "Existing", "Report", {"parts": []}), ITEM)
                self.assertEqual(self.transport.call_count, 2)

    def test_upsert_update_does_not_hide_failed_or_unknown_operations(self):
        for status in ("Failed", "Cancelled", "Canceled", "Unexpected"):
            with self.subTest(status=status):
                self.transport.reset_mock()
                self.transport.side_effect = [
                    response(data={"value": [{"id": ITEM, "displayName": "Existing"}]}),
                    response(202, headers={"Location": BASE + "/operations/" + ITEM}),
                    response(data={"status": status}),
                ]
                with self.assertRaisesRegex(RuntimeError, status):
                    self.client.upsert_item(WS, "Existing", "Report", {"parts": []})
                self.assertEqual(self.transport.call_count, 3)

    def test_upsert_update_does_not_hide_poll_errors(self):
        self.transport.side_effect = [
            response(data={"value": [{"id": ITEM, "displayName": "Existing"}]}),
            response(202, headers={"Location": BASE + "/operations/" + ITEM}),
            response(400, {"errorCode": "OperationHasNoResult"}),
        ]
        with self.assertRaises(FabricRequestError):
            self.client.upsert_item(WS, "Existing", "Report", {"parts": []})

    def test_upsert_update_timeout_is_not_success(self):
        self.client.clock = Mock(side_effect=[0, 1, 1801])
        self.transport.side_effect = [
            response(data={"value": [{"id": ITEM, "displayName": "Existing"}]}),
            response(202, headers={"Location": BASE + "/operations/" + ITEM}),
            response(data={"status": "Running"}),
        ]
        with self.assertRaises(TimeoutError):
            self.client.upsert_item(WS, "Existing", "Report", {"parts": []})

    def test_upsert_create_still_fetches_the_created_item(self):
        self.transport.side_effect = [
            response(data={"value": []}),
            response(202, headers={"Location": BASE + "/operations/" + ITEM}),
            response(data={"status": "Succeeded"}),
            response(data={"id": ITEM}),
        ]
        self.assertEqual(self.client.upsert_item(WS, "New", "Report", {"parts": []}), ITEM)
        self.assertEqual(self.transport.call_args.args, ("GET", BASE + "/operations/" + ITEM + "/result"))

    def test_upsert_create_does_not_accept_a_missing_result(self):
        self.transport.side_effect = [
            response(data={"value": []}),
            response(202, headers={"Location": BASE + "/operations/" + ITEM}),
            response(data={"status": "Succeeded"}),
            response(400, {"errorCode": "OperationHasNoResult"}),
        ]
        with self.assertRaises(FabricRequestError):
            self.client.upsert_item(WS, "New", "Report", {"parts": []})

    def test_pipeline_get_definition_still_fetches_result(self):
        definition = {"properties": {"activities": []}}
        self.transport.side_effect = [
            response(202, headers={"Location": BASE + "/operations/" + ITEM}),
            response(data={"status": "Succeeded"}),
            response(data={"definition": {"parts": [definition_part("pipeline-content.json", definition)]}}),
        ]
        self.assertEqual(self.client.pipeline_definition(WS, ITEM), definition)
        self.assertEqual(self.transport.call_args.args, ("GET", BASE + "/operations/" + ITEM + "/result"))

    def test_owner_redeployment_completes_report_and_access_notebook_updates(self):
        from reports.owner.deployment import deploy_owner_reporting
        from reports.owner.model import build_bim

        report_id = "33333333-3333-3333-3333-333333333333"
        notebook_id = "44444444-4444-4444-4444-444444444444"
        server = "example.datawarehouse.fabric.microsoft.com"
        bim = build_bim("Owner Model", server, WS)
        bim["model"]["roles"][0]["members"] = [{"memberName": "approved@example.com"}]
        report_definition = {
            "version": "4.0",
            "datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={ITEM}"}},
        }
        self.transport.side_effect = [
            response(data={"value": [{"id": ITEM, "displayName": "Owner Model"}]}),
            response(202, headers={"Location": BASE + "/operations/" + ITEM}),
            response(data={"status": "Succeeded"}),
            response(data={"definition": {"parts": [definition_part("model.bim", bim)]}}),
            response(data={"value": [{"id": report_id, "displayName": "Owner Report"}]}),
            response(data={"definition": {"parts": [definition_part("definition.pbir", report_definition)]}}),
            response(data={"value": [{"id": report_id, "displayName": "Owner Report"}]}),
            response(202, headers={"Location": BASE + "/operations/" + report_id}),
            response(data={"status": "Running"}),
            response(data={"status": "Succeeded"}),
            response(data={"value": [{"id": notebook_id, "displayName": "FAR_07_OwnerAccessSync"}]}),
            response(202, headers={"Location": BASE + "/operations/" + notebook_id}),
            response(data={"status": "Running"}),
            response(data={"status": "Succeeded"}),
        ]
        with (
            patch("reports.powerbi.deploy.wait_for_sql_endpoint", return_value=(server, WS)),
            patch("reports.owner.fabric_runtime.bootstrap_owner_tables"),
        ):
            result = deploy_owner_reporting(
                self.client, repo_dir=Path(__file__).resolve().parents[1],
                workspace_id=WS, lakehouse_id=WS, lakehouse_name="FAR",
                notebook_prefix="FAR", model_name="Owner Model", report_name="Owner Report",
                spark=Mock(),
            )
        self.assertEqual(result, {"model_id": ITEM, "report_id": report_id, "access_notebook_id": notebook_id})
        writes = [call.args[1] for call in self.transport.call_args_list if call.args[0] == "POST"]
        self.assertEqual(writes, [
            BASE + f"/workspaces/{WS}/semanticModels/{ITEM}/getDefinition?format=TMSL",
            BASE + f"/workspaces/{WS}/reports/{report_id}/getDefinition",
            BASE + f"/workspaces/{WS}/items/{report_id}/updateDefinition",
            BASE + f"/workspaces/{WS}/items/{notebook_id}/updateDefinition",
        ])
        result_reads = [call.args[1] for call in self.transport.call_args_list if call.args[1].endswith("/result")]
        self.assertEqual(result_reads, [BASE + "/operations/" + ITEM + "/result"])
        self.assertEqual(self.transport.call_count, 14)

    def test_regional_operation_location_is_polled_only_on_public_api(self):
        self.transport.side_effect = [response(data={"status": "Succeeded"}), response(data={"id": ITEM})]
        regional = "https://wabi-west-us3-a-primary-redirect.analysis.windows.net/v1/operations/" + ITEM
        self.assertEqual(self.client.complete(response(202, headers={"Location": regional})), {"id": ITEM})
        self.assertEqual(self.transport.call_args_list[0].args[1], BASE + "/operations/" + ITEM)
        self.assertTrue(all(call.args[1].startswith(BASE) for call in self.transport.call_args_list))

    def test_untrusted_operation_location_never_receives_credentials(self):
        for location in (
            "https://example.com/v1/operations/" + ITEM,
            "https://wabi-region.analysis.windows.net.attacker.test/v1/operations/" + ITEM,
            "https://wabi-region.analysis.windows.net/v1/operations/" + ITEM + "?other=1",
        ):
            with self.subTest(location=location), self.assertRaises(ValueError):
                self.client.complete(response(202, headers={"Location": location}))
        self.transport.assert_not_called()

    def test_failed_or_cancelled_child_is_not_success(self):
        for status in ("Failed", "Cancelled", "Deduped"):
            with self.subTest(status=status):
                self.transport.return_value = response(data={"status": status})
                with self.assertRaises(RuntimeError):
                    self.client.wait_pipeline(BASE + "/job", 60)


if __name__ == "__main__":
    unittest.main()

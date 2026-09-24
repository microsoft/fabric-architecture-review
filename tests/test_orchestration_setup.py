# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from orchestration.setup import provision, validate_attachment

WS = "11111111-1111-1111-1111-111111111111"
LH = "22222222-2222-2222-2222-222222222222"


class SetupTests(unittest.TestCase):
    def test_wrong_or_missing_attachment_fails(self):
        for context in ({}, {"defaultLakehouseId": None}, {"defaultLakehouseId": WS}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                validate_attachment(WS, LH, context)

    @patch("orchestration.setup.deploy", return_value={"pipeline_id": WS})
    def test_disabled_notifications_do_not_provision_any_route(self, deploy):
        result = provision(
            Mock(), workspace_id=WS, lakehouse_id=LH, child_pipeline_id=WS,
            repo_dir=Path("."), name="Targeted",
            defaults={"FUAM_WORKSPACE_ID": WS},
            context={"defaultLakehouseId": LH, "defaultLakehouseWorkspaceId": WS},
        )
        self.assertEqual(result, {"pipeline_id": WS})
        self.assertEqual(deploy.call_args.kwargs["defaults"]["NOTIFICATIONS_ENABLED"], "false")
        self.assertNotIn("owner_access_notebook_id", deploy.call_args.kwargs)

    @patch("orchestration.setup.deploy", return_value={"pipeline_id": WS})
    def test_optional_owner_binding_is_fixed_setup_wiring_not_a_pipeline_default(self, deploy):
        provision(
            Mock(), workspace_id=WS, lakehouse_id=LH, child_pipeline_id=WS,
            repo_dir=Path("."), name="Targeted", defaults={"FUAM_WORKSPACE_ID": WS},
            context={"defaultLakehouseId": LH, "defaultLakehouseWorkspaceId": WS},
            owner_access_notebook_id=LH,
        )
        self.assertEqual(deploy.call_args.kwargs["owner_access_notebook_id"], LH)
        self.assertNotIn("OWNER_ACCESS_NOTEBOOK_ID", deploy.call_args.kwargs["defaults"])

    @patch("orchestration.setup.deploy")
    def test_invalid_owner_binding_fails_before_deployment(self, deploy):
        for value in ("invalid", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                provision(
                    Mock(), workspace_id=WS, lakehouse_id=LH, child_pipeline_id=WS,
                    repo_dir=Path("."), name="Targeted", defaults={"FUAM_WORKSPACE_ID": WS},
                    context={"defaultLakehouseId": LH, "defaultLakehouseWorkspaceId": WS},
                    owner_access_notebook_id=value,
                )
        deploy.assert_not_called()

    @patch("orchestration.setup.deploy")
    def test_bad_source_fails_before_provisioning(self, deploy):
        with self.assertRaises(ValueError):
            provision(
                Mock(), workspace_id=WS, lakehouse_id=LH, child_pipeline_id=WS,
                repo_dir=Path("."), name="Targeted",
                defaults={"FUAM_WORKSPACE_ID": ""},
                context={"defaultLakehouseId": LH, "defaultLakehouseWorkspaceId": WS},
            )
        deploy.assert_not_called()

    @patch("orchestration.setup.deploy", return_value={"pipeline_id": WS})
    def test_core_setup_never_enables_notifications_from_defaults(self, deploy):
        provision(
            Mock(), workspace_id=WS, lakehouse_id=LH, child_pipeline_id=WS,
            repo_dir=Path("."), name="Targeted",
            defaults={
                "FUAM_WORKSPACE_ID": WS, "NOTIFICATIONS_ENABLED": "true",
            },
            context={"defaultLakehouseId": LH, "defaultLakehouseWorkspaceId": WS},
        )
        self.assertEqual(deploy.call_args.kwargs["defaults"]["NOTIFICATIONS_ENABLED"], "false")
        self.assertNotIn("NOTIFICATION_CONFIG_JSON", deploy.call_args.kwargs["defaults"])


if __name__ == "__main__":
    unittest.main()

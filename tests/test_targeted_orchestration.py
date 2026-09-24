# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline orchestration contracts; no tenant APIs or email sends are used."""
from __future__ import annotations

import ast
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from orchestration.deployment import (
    CHILD_RUN_VARIABLE, DEFAULTS, EMPTY_CAPABLE_PARAMETERS, NOTIFICATION_PARAMETERS,
    RUNTIME_COLLECTOR_MODULES, RUNTIME_MODULES, SELECTION_PARAMETERS, STRING_TRANSPORT_PREFIX,
    build_parent_pipeline, deploy, expression, notebook_parameter, package_runtime,
    read_notebook_parameters, runtime_notebook, stamp_parameters,
)
from orchestration.ranking import rank_workspaces


ROOT = Path(__file__).resolve().parents[1]
WS = "11111111-1111-1111-1111-111111111111"
WS2 = "22222222-2222-2222-2222-222222222222"
ITEM = "33333333-3333-3333-3333-333333333333"
RUN = "44444444-4444-4444-4444-444444444444"


class RankingTests(unittest.TestCase):
    def test_default_selects_exactly_five_descending_workspaces(self):
        rows = [
            {"workspace_id": f"00000000-0000-0000-0000-{i:012d}", "metric_value": i}
            for i in range(1, 9)
        ]
        selected = rank_workspaces(rows)
        self.assertEqual(len(selected), 5)
        self.assertEqual([r.metric_value for r in selected], [8, 7, 6, 5, 4])

    def test_ranking_aggregates_and_breaks_ties_deterministically(self):
        rows = [
            {"workspace_id": WS2, "metric_value": 5},
            {"workspace_id": WS, "metric_value": 2},
            {"workspace_id": WS, "metric_value": 3},
        ]
        selected = rank_workspaces(rows, top_n=1)
        self.assertEqual([(r.workspace_id, r.metric_value) for r in selected], [(WS, 5)])

    def test_allowlist_and_zero_metrics(self):
        rows = [{"workspace_id": WS, "metric_value": 0}, {"workspace_id": WS2, "metric_value": 5}]
        self.assertEqual(rank_workspaces(rows, allowlist=WS), [])

    def test_nonblank_allowlist_without_ids_never_broadens_scope(self):
        rows = [{"workspace_id": WS, "metric_value": 5}]
        for allowlist in (",", ",,,", " , ,\t\n, "):
            with self.subTest(allowlist=allowlist), self.assertRaisesRegex(ValueError, "WORKSPACE_IDS"):
                rank_workspaces(rows, allowlist=allowlist)
        for allowlist in ("", " \t\n"):
            with self.subTest(allowlist=allowlist):
                self.assertEqual([row.workspace_id for row in rank_workspaces(rows, allowlist=allowlist)], [WS])
        self.assertEqual(
            [row.workspace_id for row in rank_workspaces(rows, allowlist=f" {WS}, \n")], [WS],
        )

    def test_exclusions_override_allowlist_before_top_n(self):
        excluded = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        rows = [
            {"workspace_id": excluded.upper(), "metric_value": 100},
            {"workspace_id": WS, "metric_value": 5},
            {"workspace_id": WS2, "metric_value": 3},
        ]
        selected = rank_workspaces(
            rows, top_n=2, allowlist=f"{excluded},{WS},{WS2}",
            excluded_workspace_ids=[excluded],
        )
        self.assertEqual([row.workspace_id for row in selected], [WS, WS2])

    def test_excluding_all_candidates_returns_empty(self):
        self.assertEqual(rank_workspaces(
            [{"workspace_id": WS, "metric_value": 5}],
            excluded_workspace_ids=[WS],
        ), [])

    def test_rejects_invalid_rows(self):
        for value in (-1, "NaN", "Infinity", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rank_workspaces([{"workspace_id": WS, "metric_value": value}])
        with self.assertRaises(ValueError):
            rank_workspaces([{"workspace_id": "not-a-guid", "metric_value": 10}])


class DeploymentTests(unittest.TestCase):
    def test_owner_access_is_fixed_success_gated_and_only_added_to_nonempty_review(self):
        child = {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}}
        kwargs = dict(workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN)
        baseline = build_parent_pipeline(child, **kwargs)
        parent = build_parent_pipeline(child, owner_access_notebook_id=ITEM, **kwargs)
        self.assertEqual(len(parent["properties"]["activities"]), 4)
        _, review, notification, send = parent["properties"]["activities"]
        invoke, record, sync = review["typeProperties"]["ifTrueActivities"]
        self.assertEqual(sync["type"], "TridentNotebook")
        self.assertEqual(sync["typeProperties"], {
            "workspaceId": WS, "notebookId": ITEM, "parameters": {},
        })
        self.assertEqual(sync["policy"]["retry"], 0)
        self.assertEqual(sync["dependsOn"], [{
            "activity": record["name"], "dependencyConditions": ["Succeeded"],
        }])
        self.assertTrue(invoke["typeProperties"]["waitOnCompletion"])
        self.assertEqual(record["dependsOn"], [{
            "activity": invoke["name"], "dependencyConditions": ["Succeeded"],
        }])
        # Failure of any review branch activity blocks preparation, then the email loop.
        self.assertEqual(notification["dependsOn"], [{
            "activity": review["name"], "dependencyConditions": ["Succeeded"],
        }])
        self.assertEqual(send["dependsOn"], [{
            "activity": notification["name"], "dependencyConditions": ["Succeeded"],
        }])
        self.assertEqual(review["typeProperties"]["ifFalseActivities"], [])
        self.assertEqual(
            review["typeProperties"]["expression"],
            expression("@not(empty(json(activity('Select workspaces').output.result.exitValue).workspace_ids))"),
        )
        without_sync = deepcopy(parent)
        without_sync["properties"]["activities"][1]["typeProperties"]["ifTrueActivities"].pop()
        self.assertEqual(without_sync, baseline)
        self.assertNotIn("OWNER_ACCESS_NOTEBOOK_ID", parent["properties"]["parameters"])
        self.assertEqual(parent["properties"]["parameters"]["FAR_REPORT_URL"]["defaultValue"], "")

    @patch("orchestration.deployment.package_runtime", return_value=Path("runtime.zip"))
    def test_owner_binding_resolves_existing_same_workspace_notebook_without_redeploying_it(self, package):
        client = Mock()
        client.list_items.side_effect = lambda workspace, kind: (
            [{"id": ITEM, "type": "Notebook", "displayName": "FAR_07_OwnerAccessSync"}]
            if kind == "Notebook" else []
        )
        client.pipeline_definition.return_value = {"properties": {"parameters": {
            "WORKSPACE_IDS": {"type": "string", "defaultValue": ""},
        }}}
        client.upsert_item.side_effect = [WS2, WS, RUN]
        result = deploy(
            client, workspace_id=WS, lakehouse_id=ITEM, child_pipeline_id=ITEM,
            repo_dir=ROOT, name="Targeted", defaults={"FUAM_WORKSPACE_ID": WS2},
            owner_access_notebook_id=ITEM,
        )
        self.assertEqual(client.list_items.call_args_list[0].args, (WS, "Notebook"))
        self.assertEqual(client.upsert_item.call_count, 3)
        self.assertEqual(result["owner_access_notebook_id"], ITEM)
        pipeline = json.loads(base64.b64decode(
            client.upsert_item.call_args_list[-1].args[3]["parts"][0]["payload"],
        ))
        sync = pipeline["properties"]["activities"][1]["typeProperties"]["ifTrueActivities"][-1]
        self.assertEqual(sync["typeProperties"]["notebookId"], ITEM)

    @patch("orchestration.deployment.package_runtime")
    def test_owner_binding_rejects_invalid_missing_wrong_type_and_duplicate_items_before_writes(self, package):
        for binding, notebooks in [
            ("not-a-guid", []), (ITEM, []),
            (ITEM, [{"id": ITEM, "type": "DataPipeline"}]),
            (ITEM, [{"id": WS2, "type": "Notebook"}]),
            (ITEM, [{"id": ITEM, "type": "Notebook"}] * 2),
        ]:
            with self.subTest(binding=binding, notebooks=notebooks):
                client = Mock()
                client.list_items.return_value = notebooks
                with self.assertRaises(ValueError):
                    deploy(
                        client, workspace_id=WS, lakehouse_id=ITEM, child_pipeline_id=ITEM,
                        repo_dir=ROOT, name="Targeted", defaults={"FUAM_WORKSPACE_ID": WS2},
                        owner_access_notebook_id=binding,
                    )
                client.upsert_item.assert_not_called()
                client.pipeline_definition.assert_not_called()
        package.assert_not_called()

    def test_public_setup_binds_06_after_optional_owner_deployment(self):
        notebook = json.loads((ROOT / "fabric/setup.ipynb").read_text())
        code = "\n".join("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
        tree = ast.parse(code)
        assignments = {"owner_access_notebook_id", "_targeted_nb", "targeted_setup_id"}
        nodes = [
            node for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in assignments for target in node.targets)
            ) or (
                isinstance(node, ast.If)
                and "DEPLOY_WORKSPACE_OWNER_REPORT" in ast.unparse(node.test)
            )
        ]
        for enabled in ("true", "false"):
            with self.subTest(enabled=enabled):
                events = []
                def owner_deploy(*args, **kwargs):
                    events.append("07")
                    return {"access_notebook_id": ITEM, "model_id": WS2}
                def upsert(workspace, name, definition):
                    if name.endswith("_08_OwnerAgent"):
                        events.append("08")
                        parameters = next(cell for cell in definition["cells"]
                                          if "parameters" in cell.get("metadata", {}).get("tags", []))
                        values = {}
                        exec("".join(parameters["source"]), values)
                        self.assertEqual(values["OWNER_SEMANTIC_MODEL_ID"], WS2)
                        return RUN
                    events.append("06")
                    parameters = next(cell for cell in definition["cells"] if cell.get("id") == "parameters")
                    values = {}
                    exec("".join(parameters["source"]), values)
                    self.assertEqual(values["OWNER_ACCESS_NOTEBOOK_ID"], ITEM if enabled == "true" else "")
                    self.assertEqual(values["DEPLOY_TARGETED_REVIEW"], "false")
                    return RUN
                namespace = {
                    "DEPLOY_WORKSPACE_OWNER_REPORT": enabled,
                    "OWNER_AGENT_NAME": "Owner Agent",
                    "OWNER_SEMANTIC_MODEL_NAME": "Owner model", "OWNER_REPORT_NAME": "Owner report",
                    "SEMANTIC_MODEL_NAME": "Governance model", "REPORT_NAME": "Governance report",
                    "REPO_DIR": str(ROOT), "wid": WS, "lhid": WS2, "pid": ITEM,
                    "LAKEHOUSE_NAME": "Review", "NOTEBOOK_PREFIX": "FAR", "PIPELINE_NAME": "FAR",
                    "GITHUB_REPO_URL": "https://example.invalid/repo", "GITHUB_BRANCH": "main", "GITHUB_REF": "",
                    "notebookutils": SimpleNamespace(credentials=Mock()),
                    "stamp_parameters": stamp_parameters,
                    "load_nb": lambda relative: json.loads((ROOT / relative).read_text()),
                    "upsert_notebook": upsert,
                }
                with patch.dict("sys.modules", {
                    "pyspark.sql": SimpleNamespace(SparkSession=SimpleNamespace(builder=Mock())),
                    "reports.owner.deployment": SimpleNamespace(deploy_owner_reporting=owner_deploy),
                }):
                    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<public-owner-wiring>", "exec"), namespace)
                self.assertEqual(events, ["07", "08", "06"] if enabled == "true" else ["06"])

    def test_pipeline_has_no_loop_nested_under_an_if_condition(self):
        parent = build_parent_pipeline(
            {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}},
            workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
        )

        def check(activities, inside_if=False):
            for activity in activities:
                if inside_if:
                    self.assertNotIn(activity["type"], ("ForEach", "Until", "IfCondition", "Switch"))
                properties = activity.get("typeProperties", {})
                for key in ("ifTrueActivities", "ifFalseActivities", "activities"):
                    check(properties.get(key, []), inside_if or activity["type"] == "IfCondition")

        check(parent["properties"]["activities"])
        self.assertEqual(
            [activity["name"] for activity in parent["properties"]["activities"] if activity["type"] == "ForEach"],
            ["Send owner emails"],
        )

    @patch("orchestration.deployment.package_runtime", return_value=Path("runtime.zip"))
    def test_deployment_creates_only_parent_runner_and_completion(self, package):
        client = Mock()
        client.list_items.return_value = []
        client.pipeline_definition.return_value = {"properties": {"parameters": {
            "WORKSPACE_IDS": {"type": "string", "defaultValue": ""},
        }}}
        client.upsert_item.side_effect = [WS2, WS, RUN]
        result = deploy(
            client, workspace_id=WS, lakehouse_id=ITEM, child_pipeline_id=ITEM,
            repo_dir=ROOT, name="Targeted", defaults={"FUAM_WORKSPACE_ID": WS2},
        )
        self.assertEqual(client.upsert_item.call_count, 3)
        self.assertNotIn("notification_setup_notebook_id", result)
        self.assertEqual(result["pipeline_id"], RUN)
        parent = json.loads(base64.b64decode(client.upsert_item.call_args_list[2].args[3]["parts"][0]["payload"]))
        email = parent["properties"]["activities"][3]["typeProperties"]["activities"][0]
        self.assertEqual(email["type"], "Office365Email")
        self.assertEqual(email["state"], "Inactive")
        self.assertEqual(email["onInactiveMarkAs"], "Failed")
        self.assertNotIn("externalReferences", email)
        self.assertEqual(result["completion_notebook_id"], WS)
        completion = client.upsert_item.call_args_list[1].args
        self.assertEqual(completion[:3], (WS, "Targeted - Completion", "Notebook"))

    def test_runtime_bundle_is_reproducible_and_only_packages_python_modules(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            bundle = package_runtime(ROOT, Path(folder))
            self.assertEqual(bundle, package_runtime(ROOT, Path(folder)))
            with zipfile.ZipFile(bundle) as archive:
                files = archive.namelist()
                self.assertIn("orchestration/sources.py", files)
                self.assertIn("orchestration/completion.py", files)
                self.assertTrue(all(name.startswith(("orchestration/", "collectors/")) and name.endswith(".py")
                                    for name in files))
                for name in files:
                    ast.parse(archive.read(name))
                    self.assertIn(b"Licensed under the MIT License.", archive.read(name))
            isolated = subprocess.run(
                [sys.executable, "-I", "-c", """
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from orchestration import sources, completion, runtime
from orchestration.fabric_api import FabricClient
from collectors import _http, workspace_scope
for module in (sources, completion, runtime, _http, workspace_scope):
    assert module.__file__.startswith(sys.argv[1])
wid = "11111111-1111-1111-1111-111111111111"
rows = [{"workspace_id": wid, "metric_value": 1}]
with patch.object(_http, "get_json", return_value={"value": [{"id": wid}]}):
    matched, evidence = sources.match_current_workspaces(rows, FabricClient(lambda: "synthetic"))
assert matched == rows and evidence["complete"] and evidence["unmatched_count"] == 0
assert "collectors.capacity_metrics" not in sys.modules
""", str(bundle)],
                cwd=folder, capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(isolated.returncode, 0, isolated.stderr)

    def test_runtime_bundle_excludes_unlisted_local_modules_and_private_files(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            modules = root / "orchestration"
            modules.mkdir()
            for name in RUNTIME_MODULES:
                shutil.copyfile(ROOT / "orchestration" / name, modules / name)
            collectors = root / "collectors"
            collectors.mkdir()
            for name in RUNTIME_COLLECTOR_MODULES:
                shutil.copyfile(ROOT / "collectors" / name, collectors / name)
            (collectors / "private_credentials.py").write_text("# synthetic local-only file\n")
            (modules / "local_credentials.py").write_text("# synthetic local-only file\n")
            (modules / "setup_private_test_DISPOSABLE.ipynb").write_text("{}")
            with zipfile.ZipFile(package_runtime(root, root / "bundles")) as archive:
                self.assertEqual(set(archive.namelist()), {
                    *("orchestration/" + name for name in RUNTIME_MODULES),
                    *("collectors/" + name for name in RUNTIME_COLLECTOR_MODULES),
                })

    def test_all_actual_far_parameters_are_preserved_except_parent_scope_default(self):
        notebook = json.loads((ROOT / "fabric/setup.ipynb").read_text())
        code = "\n".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
        tree = ast.parse(code)
        names = {"common_keys", "collect_keys", "threshold_keys"}
        nodes = [
            node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
        ]
        namespace = {"GITHUB_REPO_URL": "https://github.com/microsoft/fabric-architecture-review.git",
                     "GITHUB_BRANCH": "test", "GITHUB_REF": "test",
                     "SP_CLIENT_ID": "", "SP_CONNECTION_NAME": "test-connection"}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pipeline-defaults>", "exec"), namespace)
        values = {**namespace["common_keys"], **namespace["collect_keys"], **namespace["threshold_keys"]}
        child = {"properties": {"parameters": {
            name: {"type": "string", "defaultValue": value} for name, value in values.items()
        }}}
        parent = build_parent_pipeline(
            child, workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
        )
        for name, definition in child["properties"]["parameters"].items():
            expected = {**definition, "defaultValue": ""} if name == "WORKSPACE_IDS" else definition
            self.assertEqual(parent["properties"]["parameters"][name], expected)
        child_values = parent["properties"]["activities"][1]["typeProperties"]["ifTrueActivities"][0]["typeProperties"]["parameters"]
        self.assertEqual(set(child_values), set(child["properties"]["parameters"]))
        for name in set(child_values) - {"WORKSPACE_IDS"}:
            self.assertEqual(child_values[name], expression("@pipeline().parameters." + name))

    def test_targeted_default_selects_top_five_without_inheriting_standalone_scope(self):
        child = {"properties": {"parameters": {
            "WORKSPACE_IDS": {"type": "string", "defaultValue": WS, "description": "Optional scope"},
        }}}
        parent = build_parent_pipeline(
            child, workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
        )
        parameters = parent["properties"]["parameters"]
        self.assertEqual(parameters["WORKSPACE_IDS"], {
            "type": "string", "defaultValue": "", "description": "Optional scope",
        })
        self.assertEqual(child["properties"]["parameters"]["WORKSPACE_IDS"]["defaultValue"], WS)
        rows = [
            {"workspace_id": f"00000000-0000-0000-0000-{i:012d}", "metric_value": i}
            for i in range(1, 7)
        ]
        selected = rank_workspaces(
            rows, top_n=int(parameters["TOP_N"]["defaultValue"]),
            allowlist=parameters["WORKSPACE_IDS"]["defaultValue"],
        )
        self.assertEqual([row.metric_value for row in selected], [6, 5, 4, 3, 2])
        self.assertEqual(rank_workspaces(rows, allowlist=WS), [])

    @unittest.skipUnless(
        (ROOT / "fabric/setup_private_test_DISPOSABLE.ipynb").exists(),
        "Optional local deployment harness is not part of public distributions.",
    )
    def test_private_setup_patches_indented_optional_notebook(self):
        notebook = json.loads((ROOT / "fabric/setup_private_test_DISPOSABLE.ipynb").read_text())
        code = "\n".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
        tree = ast.parse(code)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_patch_stage_notebook"
            or isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_PRIVATE_SNAPSHOT_LINES"
                for target in node.targets
            )
        ]
        with tempfile.TemporaryDirectory() as folder:
            relative = "fabric/notebooks/06_targeted_review_setup.ipynb"
            target = Path(folder) / relative
            target.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / relative, target)
            namespace = {"os": os, "json": json, "REPO_DIR": folder, "_embedded_token": "synthetic-test-value"}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "<private-patcher>", "exec"), namespace)
            namespace["_patch_stage_notebook"](relative)
            patched = json.loads(target.read_text())
            combined = "\n".join("".join(c["source"]) for c in patched["cells"] if c["cell_type"] == "code")
            ast.parse(combined)
            self.assertNotIn('["git", "clone"', combined)
            self.assertIn("_download_private_snapshot()", combined)
            self.assertIn("synthetic-test-value", combined)

    def test_pipeline_preserves_defaults_and_disables_automatic_retry(self):
        child = {"properties": {"parameters": {
            "WORKSPACE_IDS": {"type": "string", "defaultValue": ""},
            "CLIENT_NAME": {"type": "string", "defaultValue": "Customer"},
        }}}
        parent = build_parent_pipeline(
            child, workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
        )
        properties = parent["properties"]
        self.assertEqual(properties["parameters"]["CLIENT_NAME"], child["properties"]["parameters"]["CLIENT_NAME"])
        self.assertEqual(properties["parameters"]["NOTIFICATIONS_ENABLED"]["defaultValue"], "false")
        self.assertEqual(properties["activities"][0]["policy"]["retry"], 0)
        self.assertNotIn("TOP_N", child["properties"]["parameters"])
        self.assertNotIn("SOURCE_MODE", properties["parameters"])
        selection, review, notification, send = properties["activities"]
        arguments = selection["typeProperties"]["parameters"]
        self.assertEqual(
            set(arguments),
            set(SELECTION_PARAMETERS) | {"PARENT_RUN_ID"},
        )
        for name in SELECTION_PARAMETERS:
            self.assertEqual(arguments[name], notebook_parameter(
                (f"@coalesce(pipeline().parameters.{name}, '')" if name == "CAPACITY_ID_OR_NAME"
                 else "@pipeline().parameters." + name),
                preserve_empty=name in EMPTY_CAPABLE_PARAMETERS,
            ))
        self.assertNotIn("EMAIL_CONFIGURED", arguments)
        self.assertNotIn("PARAMETERS_JSON", arguments)
        self.assertEqual(arguments["PARENT_RUN_ID"], notebook_parameter("@pipeline().RunId"))
        self.assertEqual(review["dependsOn"], [{"activity": selection["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(review["type"], "IfCondition")
        self.assertEqual(
            review["typeProperties"]["expression"],
            expression("@not(empty(json(activity('Select workspaces').output.result.exitValue).workspace_ids))"),
        )
        invoke, record = review["typeProperties"]["ifTrueActivities"]
        self.assertEqual(invoke["type"], "ExecutePipeline")
        self.assertTrue(invoke["typeProperties"]["waitOnCompletion"])
        self.assertEqual(invoke["typeProperties"]["pipeline"], {"referenceName": ITEM, "type": "PipelineReference"})
        self.assertEqual(invoke["policy"]["retry"], 0)
        self.assertEqual(
            invoke["typeProperties"]["parameters"]["WORKSPACE_IDS"],
            expression("@json(activity('Select workspaces').output.result.exitValue).workspace_ids"),
        )
        self.assertEqual(record["typeProperties"]["value"], expression("@activity('Invoke FAR').output.pipelineRunId"))
        self.assertEqual(record["dependsOn"], [{"activity": invoke["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(notification["dependsOn"], [{"activity": review["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(
            notification["typeProperties"]["expression"],
            expression("@and(not(empty(variables('FAR_CHILD_RUN_ID'))), equals(toLower(pipeline().parameters.NOTIFICATIONS_ENABLED), 'true'))"),
        )
        self.assertEqual(review["typeProperties"]["ifFalseActivities"], [])
        self.assertEqual(notification["typeProperties"]["ifFalseActivities"], [])
        publish = notification["typeProperties"]["ifTrueActivities"][0]
        self.assertEqual(publish["policy"]["retry"], 0)
        self.assertEqual(publish["typeProperties"]["notebookId"], RUN)
        self.assertEqual(
            publish["typeProperties"]["parameters"]["CHILD_RUN_ID"],
            notebook_parameter(f"@variables('{CHILD_RUN_VARIABLE}')"),
        )
        self.assertNotIn("externalReferences", invoke)
        store = notification["typeProperties"]["ifTrueActivities"][1]
        self.assertEqual(store["type"], "SetVariable")
        self.assertEqual(store["dependsOn"], [{"activity": publish["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(store["policy"], {"secureInput": True, "secureOutput": True})
        self.assertEqual(store["typeProperties"], {
            "variableName": "FAR_OWNER_EMAILS",
            "value": expression("@json(activity('Prepare owner emails').output.result.exitValue).messages"),
        })
        self.assertEqual(properties["variables"]["FAR_OWNER_EMAILS"], {"type": "Array", "defaultValue": []})
        self.assertEqual(send["dependsOn"], [{"activity": notification["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(send["typeProperties"]["items"], expression("@variables('FAR_OWNER_EMAILS')"))
        self.assertEqual(send["type"], "ForEach")
        self.assertTrue(send["typeProperties"]["isSequential"])
        email, = send["typeProperties"]["activities"]
        self.assertEqual(email["type"], "Office365Email")
        self.assertEqual(email["state"], "Inactive")
        self.assertEqual(email["onInactiveMarkAs"], "Failed")
        self.assertNotIn("externalReferences", json.dumps(parent))
        self.assertNotIn("NOTIFICATION_CONFIG", json.dumps(parent))

    def test_direct_parameter_binding_preserves_strings_and_rejects_missing_injection(self):
        special = 'O\'Brien "quoted" \\\\ \n Unicode: \u00e9 @pipeline().RunId'
        values = {"FAR_REPORT_URL": special, "WORKSPACE_IDS": WS}
        wire_values = {name: STRING_TRANSPORT_PREFIX + value for name, value in values.items()}
        self.assertEqual(read_notebook_parameters(wire_values, tuple(values)), values)
        for value in (None, 5, {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "TOP_N"):
                read_notebook_parameters({"TOP_N": value}, ("TOP_N",))
        with self.assertRaisesRegex(ValueError, "TOP_N"):
            read_notebook_parameters({}, ("TOP_N",))

    def test_empty_optional_strings_have_nonempty_notebook_transport(self):
        parent = build_parent_pipeline(
            {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}},
            workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
        )
        selection, _, notification, _ = parent["properties"]["activities"]
        prepare = notification["typeProperties"]["ifTrueActivities"][0]
        for activity in (selection, prepare):
            arguments = activity["typeProperties"]["parameters"]
            for name in {"WORKSPACE_IDS", "FUAM_LAKEHOUSE_ID", "FAR_REPORT_URL"} & arguments.keys():
                with self.subTest(activity=activity["name"], parameter=name):
                    self.assertEqual(
                        arguments[name],
                        notebook_parameter(f"@concat('far-string:', pipeline().parameters.{name})"),
                    )
                    for original in ("", WS + "," + WS2, "https://app.powerbi.com/groups/test/reports/test", "far-string:"):
                        wire_value = "far-string:" + original
                        self.assertTrue(wire_value)
                        self.assertEqual(read_notebook_parameters({name: wire_value}, (name,)), {name: original})

    def test_missing_optional_injection_never_becomes_an_unrestricted_scope(self):
        for name in ("WORKSPACE_IDS", "FUAM_LAKEHOUSE_ID", "FAR_REPORT_URL"):
            for value in (None, False, {}, "", WS):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    read_notebook_parameters({name: value}, (name,))
            with self.assertRaises(ValueError):
                read_notebook_parameters({}, (name,))

    def test_configured_native_email_has_fixed_connection_and_isolated_dynamic_messages(self):
        parent = build_parent_pipeline(
            {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}},
            workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
            email_connection_id=ITEM, email_from="sender@example.com",
        )
        selection, _, notification, send = parent["properties"]["activities"]
        self.assertNotIn("EMAIL_CONFIGURED", selection["typeProperties"]["parameters"])
        prepare, store = notification["typeProperties"]["ifTrueActivities"]
        self.assertEqual(send["type"], "ForEach")
        self.assertTrue(send["typeProperties"]["isSequential"])
        self.assertEqual(store["dependsOn"], [{"activity": prepare["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(store["typeProperties"]["value"], expression(
            "@json(activity('Prepare owner emails').output.result.exitValue).messages",
        ))
        self.assertEqual(send["dependsOn"], [{"activity": notification["name"], "dependencyConditions": ["Succeeded"]}])
        self.assertEqual(send["typeProperties"]["items"], expression("@variables('FAR_OWNER_EMAILS')"))
        email, = send["typeProperties"]["activities"]
        self.assertEqual(email["type"], "Office365Email")
        self.assertEqual(email["state"], "Active")
        self.assertEqual(email["externalReferences"], {"connection": ITEM})
        self.assertEqual(email["typeProperties"], {
            "to": "@{item().to}", "subject": "@{item().subject}", "body": "@{item().body}",
            "from": "sender@example.com",
        })
        self.assertEqual(email["policy"]["retry"], 0)
        self.assertTrue(email["policy"]["secureInput"])
        self.assertTrue(email["policy"]["secureOutput"])
        self.assertNotIn("NOTIFICATION_CONFIG", json.dumps(parent))

    def test_invalid_email_wiring_and_reserved_child_parameter_are_rejected(self):
        child = {"properties": {"parameters": {"WORKSPACE_IDS": {"type": "string", "defaultValue": ""}}}}
        cases: tuple[dict[str, Any], ...] = (
            {"email_connection_id": ""},
            {"email_from": "sender@example.com"},
            {"email_connection_id": ITEM, "email_from": "one@example.com;two@example.com"},
            {"defaults": {"TOP_N": 5}},
            {"email_state": "Active"},
            {"email_connection_id": ITEM, "email_state": "invalid"},
        )
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                build_parent_pipeline(
                    child, workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2,
                    notification_notebook_id=RUN, **options,
                )
        child["properties"]["parameters"]["NOTIFICATIONS_ENABLED"] = {"type": "string", "defaultValue": "true"}
        with self.assertRaisesRegex(ValueError, "collision"):
            build_parent_pipeline(
                child, workspace_id=WS, pipeline_id=ITEM, notebook_id=WS2, notification_notebook_id=RUN,
            )

    def test_generated_stage_executes_with_named_injection_and_fixed_wiring(self):
        for stage, keys in (("selection", SELECTION_PARAMETERS), ("notifications", NOTIFICATION_PARAMETERS)):
            with self.subTest(stage=stage):
                notebook = runtime_notebook(
                    "runtime.zip", WS, WS2, stage=stage, child_pipeline_id=ITEM,
                )
                namespace = {}
                exec("".join(notebook["cells"][0]["source"]), namespace)
                values = {key: DEFAULTS.get(key, "") for key in keys}
                values.update(PARENT_RUN_ID=RUN)
                if stage == "notifications":
                    values["CHILD_RUN_ID"] = RUN
                namespace.update({
                    name: STRING_TRANSPORT_PREFIX + value if name in EMPTY_CAPABLE_PARAMETERS else value
                    for name, value in values.items()
                })
                runtime = Mock(return_value={"status": "tested"})
                exit_notebook = Mock()
                modules = {
                    "json": json,
                    "sys": SimpleNamespace(path=[], modules={}),
                    "notebookutils": SimpleNamespace(notebook=SimpleNamespace(exit=exit_notebook)),
                    "orchestration.deployment": SimpleNamespace(read_notebook_parameters=read_notebook_parameters),
                    "orchestration.runtime": SimpleNamespace(**{"run_" + stage: runtime}),
                }
                with patch("builtins.__import__", side_effect=lambda name, *args, **kwargs: modules[name]):
                    exec("".join(notebook["cells"][1]["source"]), namespace)
                runtime.assert_called_once_with({
                    **values, "CHILD_WORKSPACE_ID": WS, "CHILD_PIPELINE_ID": ITEM,
                })
                exit_notebook.assert_called_once_with('{"status": "tested"}')

    def test_generated_notebook_missing_injection_fails_before_runtime(self):
        notebook = runtime_notebook("runtime.zip", WS, WS2, child_pipeline_id=ITEM)
        namespace = {}
        exec("".join(notebook["cells"][0]["source"]), namespace)
        with self.assertRaisesRegex(ValueError, "Missing or non-string"):
            read_notebook_parameters(namespace, (*SELECTION_PARAMETERS, "PARENT_RUN_ID"))

    def test_notebooks_compile_and_setup_is_disabled(self):
        setup = json.loads((ROOT / "fabric/notebooks/06_targeted_review_setup.ipynb").read_text())
        stamped = stamp_parameters(setup, {"WORKSPACE_ID": WS, "CHILD_PIPELINE_ID": ITEM})
        stages = [runtime_notebook(
            "/lakehouse/default/Files/runtime.zip", WS, ITEM, stage=stage, child_pipeline_id=ITEM,
        ) for stage in ("selection", "notifications")]
        for notebook in (stamped, *stages):
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code":
                    ast.parse("".join(cell["source"]))
        for notebook in stages:
            source = "".join(notebook["cells"][1]["source"])
            self.assertNotIn("start_pipeline", source)
            self.assertNotIn("wait_pipeline", source)
            self.assertNotIn("PARAMETERS_JSON", source)
        self.assertIn('DEPLOY_TARGETED_REVIEW = "false"', json.dumps(setup).replace('\\"', '"'))
        for removed in ("SOURCE_MODE", "ENABLE_ACTIVATOR", "ACTIVATOR_OPTIONS_JSON"):
            self.assertNotIn(removed, json.dumps(setup))

    @patch("subprocess.run")
    @patch("tempfile.mkdtemp")
    def test_disabled_core_setup_does_not_clone_or_allocate_runtime(self, temporary_folder, subprocess_run):
        setup = json.loads((ROOT / "fabric/notebooks/06_targeted_review_setup.ipynb").read_text())
        code = "\n".join("".join(c["source"]) for c in setup["cells"] if c["cell_type"] == "code")
        exec(compile(code, "<disabled-targeted-setup>", "exec"), {})
        temporary_folder.assert_not_called()
        subprocess_run.assert_not_called()

    def test_both_setup_entrypoints_deploy_optional_notebook(self):
        names = ["setup.ipynb"]
        if (ROOT / "fabric/setup_private_test_DISPOSABLE.ipynb").exists():
            names.append("setup_private_test_DISPOSABLE.ipynb")
        for name in names:
            notebook = json.loads((ROOT / "fabric" / name).read_text())
            code = "\n".join("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
            ast.parse(code)
            self.assertIn("06_targeted_review_setup.ipynb", code)
            self.assertIn("stamp_parameters", code)


if __name__ == "__main__":
    unittest.main()

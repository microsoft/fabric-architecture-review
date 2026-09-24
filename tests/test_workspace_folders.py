# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
import requests

from orchestration.fabric_api import BASE, FabricClient, FabricRequestError, collection
from orchestration.folders import FOLDERS, find_item, organize, organize_existing


ROOT = Path(__file__).resolve().parents[1]
WS = str(UUID(int=1))
ITEM = str(UUID(int=2))
FOLDER = str(UUID(int=3))


def response(body, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


class Service:
    def __init__(self):
        self.items = {}
        self.folders = []
        self.calls = []
        self.client = FabricClient(lambda: "synthetic", transport=self.request)

    def request(self, method, url, *, json=None, **kwargs):
        self.calls.append((method, url, json))
        base = BASE + f"/workspaces/{WS}"
        if method == "GET" and "/items/" in url:
            return response(self.items[url.rsplit("/", 1)[-1]])
        if method == "GET" and "/folders?" in url:
            return response({"value": self.folders})
        if method == "GET" and "/items?" in url:
            kind = url.split("type=")[1]
            return response({"value": [i for i in self.items.values() if i["type"] == kind]})
        if method == "POST" and url == base + "/folders":
            folder = {"id": str(UUID(int=100 + len(self.folders))), **json}
            self.folders.append(folder)
            return response(folder, 201)
        if method == "POST" and url.endswith("/move"):
            item = self.items[url.split("/")[-2]]
            item["folderId"] = json.get("targetFolderId")
            return response({"value": [item]})
        raise AssertionError((method, url, json))

    def add(self, item_id=ITEM, kind="Notebook", name="FAR", folder=None):
        self.items[item_id] = {"id": item_id, "type": kind, "displayName": name, "folderId": folder}


@pytest.mark.parametrize("destination", [None, *FOLDERS])
def test_moves_only_known_id_preserves_item_and_is_idempotent(destination):
    service = Service()
    service.add(folder=FOLDER)
    unrelated = str(UUID(int=4))
    service.add(unrelated, name="Other notebook", folder=FOLDER)
    before = dict(service.items[ITEM])
    organize(service.client, WS, [(ITEM, "Notebook", destination)])
    calls = len(service.calls)
    organize(service.client, WS, [(ITEM, "Notebook", destination)])
    assert all(method == "GET" for method, _, _ in service.calls[calls:])
    assert {k: v for k, v in service.items[ITEM].items() if k != "folderId"} == {
        k: v for k, v in before.items() if k != "folderId"
    }
    assert service.items[unrelated]["folderId"] == FOLDER
    moves = [body for method, url, body in service.calls if url.endswith("/move")]
    assert len(moves) == 1
    assert moves[0] == ({"targetFolderId": service.folders[0]["id"]} if destination else {})
    assert not any(method in ("DELETE", "PATCH") or url.endswith("/items")
                   for method, url, _ in service.calls)


def test_reuses_exact_root_ignores_nested_homonyms():
    service = Service()
    service.add()
    service.folders = [
        {"id": str(UUID(int=5)), "displayName": "Notebooks", "parentFolderId": FOLDER},
        {"id": FOLDER, "displayName": "Notebooks"},
    ]
    organize(service.client, WS, [(ITEM, "Notebook", "Notebooks")])
    assert service.items[ITEM]["folderId"] == FOLDER
    assert not any(url.endswith("/folders") for _, url, _ in service.calls)


@pytest.mark.parametrize("mode", ["uri", "token"])
def test_folder_and_item_collections_follow_every_page(mode):
    client = Mock()
    path = f"/workspaces/{WS}/folders?recursive=false"
    continuation = {"continuationUri": BASE + path + "&continuationToken=next"} if mode == "uri" else {
        "continuationToken": "next"
    }
    client.get_json.side_effect = [{"value": [], **continuation},
                                  {"value": [{"id": FOLDER, "displayName": "Reporting"}]}]
    assert collection(client, path)[0]["id"] == FOLDER
    assert client.get_json.call_count == 2
    client.get_json.side_effect = [
        {"value": [], "continuationToken": "next"},
        {"value": [{"id": ITEM, "displayName": "Owner", "type": "Report"}]},
    ]
    assert find_item(client, WS, "Owner", "Report")["id"] == ITEM


@pytest.mark.parametrize("body", [
    {}, {"value": None}, {"value": ["bad"]},
    {"value": [], "continuationToken": 123},
    {"value": [], "continuationUri": "https://other.example/next"},
    {"value": [], "continuationUri": BASE + f"/workspaces/{ITEM}/folders"},
])
def test_invalid_collection_fails_closed(body):
    client = Mock()
    client.get_json.return_value = body
    with pytest.raises(ValueError):
        collection(client, f"/workspaces/{WS}/folders")


def test_repeated_continuation_fails():
    client = Mock()
    client.get_json.return_value = {"value": [], "continuationToken": "repeat"}
    with pytest.raises(RuntimeError, match="repeated"):
        collection(client, f"/workspaces/{WS}/folders")


def test_duplicate_configured_item_fails_before_any_move():
    service = Service()
    service.add(kind="DataAgent", name="Owner")
    service.add(str(UUID(int=6)), kind="DataAgent", name="Owner")
    with pytest.raises(ValueError, match="Multiple"):
        organize_existing(service.client, WS, [("Owner", "DataAgent", "Agents")])
    assert all(method == "GET" for method, _, _ in service.calls)


def test_duplicate_root_folder_fails_instead_of_creating_or_moving():
    service = Service()
    service.add()
    service.folders = [{"id": str(UUID(int=n)), "displayName": "Notebooks"} for n in (7, 8)]
    with pytest.raises(ValueError, match="Multiple root"):
        organize(service.client, WS, [(ITEM, "Notebook", "Notebooks")])
    assert all(method == "GET" for method, _, _ in service.calls)


@pytest.mark.parametrize("body,status", [({}, 202), ({"value": []}, 200),
                                       ({"value": [{"id": ITEM, "folderId": FOLDER}]}, 200)])
def test_unconfirmed_move_is_not_reported_as_success(body, status):
    client = Mock()
    client.get_json.return_value = {"id": ITEM, "type": "Notebook", "folderId": FOLDER}
    client.request.return_value = response(body, status)
    with pytest.raises((ValueError, RuntimeError)):
        organize(client, WS, [(ITEM, "Notebook", None)])


@pytest.mark.parametrize("status", [403, 400, 409])
def test_folder_api_errors_are_not_skipped(status):
    client = FabricClient(lambda: "synthetic", transport=lambda *a, **k: response(
        {"errorCode": "WorkspaceNotSupported"}, status))
    with pytest.raises(FabricRequestError):
        organize(client, WS, [], create_all=True)


def test_wrong_item_type_prevents_all_writes():
    service = Service()
    service.add(kind="Report")
    with pytest.raises(ValueError, match="identity/type"):
        organize(service.client, WS, [(ITEM, "Notebook", "Notebooks")])
    assert all(method == "GET" for method, _, _ in service.calls)


@pytest.mark.parametrize("exists", [False, True])
def test_existing_optional_artifacts_reconciled_without_creating_missing_items(exists):
    service = Service()
    if exists:
        service.add(kind="DataAgent", name="Configured owner Agent")
    service.add(str(UUID(int=6)), kind="DataAgent", name="Unrelated Agent")
    organize_existing(service.client, WS, [("Configured owner Agent", "DataAgent", "Agents")])
    assert {f["displayName"] for f in service.folders} == set(FOLDERS)
    assert len(service.items) == (2 if exists else 1)
    assert service.items[str(UUID(int=6))]["folderId"] is None
    if exists:
        assert service.items[ITEM]["folderId"] is not None


def code_cells(relative):
    notebook = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]


def test_setup_upsert_finds_existing_id_on_later_page_without_recreation():
    source = next(s for s in code_cells("fabric/setup.ipynb") if "def find_item(" in s)
    definitions = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                   and n.name in ("find_item", "_upsert", "ensure_lakehouse")]
    client = Mock()
    client.get_json.side_effect = [
        {"value": [], "continuationToken": "page2"},
        {"value": [{"id": ITEM, "type": "Notebook", "displayName": "Existing FAR"}]},
    ]
    post = Mock()
    context = {"json": json, "base64": base64, "BASE": BASE, "_folder_client": client,
               "_find_far_item": find_item, "_H": lambda: {}, "_poll": lambda r: {},
               "requests": SimpleNamespace(post=post)}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "setup-upsert", "exec"), context)
    assert context["_upsert"](WS, "Existing FAR", "notebooks", "notebook-content.ipynb", "ipynb", {}) == ITEM
    assert post.call_count == 1
    assert post.call_args.args[0].endswith(f"/notebooks/{ITEM}/updateDefinition")


def test_shared_client_upsert_preserves_existing_id_with_token_only_pagination():
    replies = iter([
        response({"value": [], "continuationToken": "next"}),
        response({"value": [{"id": ITEM, "type": "Notebook", "displayName": "FAR"}]}),
        response({}),
    ])
    transport = Mock(side_effect=lambda *a, **k: next(replies))
    client = FabricClient(lambda: "synthetic", transport=transport)
    assert client.upsert_item(WS, "FAR", "Notebook", {"parts": []}) == ITEM
    assert transport.call_args_list[-1].args == (
        "POST", BASE + f"/workspaces/{WS}/items/{ITEM}/updateDefinition"
    )
    assert len(transport.call_args_list) == 3


def test_setup_reconciliation_executes_all_root_exceptions_and_optional_identities():
    service = Service()
    known = {
        "lhid": ("Lakehouse", None), "pid": ("DataPipeline", "Pipelines"),
        "targeted_setup_id": ("Notebook", "Notebooks"),
        **{key: ("Notebook", "Notebooks") for key in
           ("collect_id", "analyze_id", "report_id", "gold_id", "agent_id")},
    }
    context = {"wid": WS, "_folder_client": service.client}
    expected = {}
    for index, (key, (kind, folder)) in enumerate(known.items(), 20):
        item_id = str(UUID(int=index))
        service.add(item_id, kind, key, FOLDER)
        context[key] = item_id
        expected[item_id] = folder
    context.update({key: key for key in (
        "SEMANTIC_MODEL_NAME", "REPORT_NAME", "OWNER_SEMANTIC_MODEL_NAME",
        "OWNER_REPORT_NAME", "ONTOLOGY_NAME", "DATA_AGENT_NAME", "OWNER_AGENT_NAME",
        "NOTEBOOK_PREFIX", "PIPELINE_NAME",
    )})
    service.add(ITEM, "Notebook", "Imported setup", FOLDER)
    context["ctx"] = {"currentWorkspaceId": WS, "currentNotebookId": ITEM}
    for index, (key, kind, folder) in enumerate([
        ("SEMANTIC_MODEL_NAME", "SemanticModel", "Reporting"),
        ("REPORT_NAME", "Report", "Reporting"),
        ("OWNER_SEMANTIC_MODEL_NAME", "SemanticModel", "Reporting"),
        ("OWNER_REPORT_NAME", "Report", "Reporting"),
        ("ONTOLOGY_NAME", "Ontology", "Ontology"),
        ("DATA_AGENT_NAME", "DataAgent", "Agents"),
        ("OWNER_AGENT_NAME", "DataAgent", "Agents"),
    ], 40):
        item_id = str(UUID(int=index))
        service.add(item_id, kind, context[key])
        expected[item_id] = folder
    source = next(s for s in code_cells("fabric/setup.ipynb") if "organize_existing(" in s)
    exec(compile(source, "setup-folders", "exec"), context)
    destinations = {f["id"]: f["displayName"] for f in service.folders}
    for item_id, folder in expected.items():
        assert destinations.get(service.items[item_id]["folderId"]) == folder
    assert service.items[ITEM]["folderId"] is None


@pytest.mark.parametrize("filename", ["05_agent.ipynb", "08_owner_agent.ipynb", "06_targeted_review_setup.ipynb"])
def test_standalone_deployment_folder_hooks_execute(filename):
    service = Service()
    service.add()
    service.add(str(UUID(int=7)), "DataAgent", "Central")
    context = {
        "notebookutils": SimpleNamespace(runtime=SimpleNamespace(context={
            "currentWorkspaceId": WS, "currentNotebookId": ITEM,
        }), credentials=SimpleNamespace(getToken=lambda _: "synthetic")),
        "WORKSPACE_ID": WS, "_agent_id": str(UUID(int=7)), "DATA_AGENT_NAME": "Central",
    }
    source = next(s for s in code_cells("fabric/notebooks/" + filename) if "from orchestration.folders import" in s)
    if filename == "05_agent.ipynb":
        start, end = source.index("from orchestration.fabric_api import"), source.index('print("Data agent deployed')
    elif filename == "08_owner_agent.ipynb":
        start, end = source.index("from orchestration.folders import"), source.index('print("Workspace Owner Agent')
    else:
        import textwrap
        start, end = source.index("    from orchestration.folders import"), source.index("    print(json.dumps")
        source = textwrap.dedent(source[start:end])
        start, end = 0, len(source)
        context.update(client=service.client, CHILD_PIPELINE_ID=str(UUID(int=8)),
                       LAKEHOUSE_ID=str(UUID(int=9)),
                       result={"pipeline_id": str(UUID(int=10)), "notebook_id": str(UUID(int=11)),
                               "completion_notebook_id": str(UUID(int=12))})
        for number, kind in [(8, "DataPipeline"), (9, "Lakehouse"), (10, "DataPipeline"),
                             (11, "Notebook"), (12, "Notebook")]:
            service.add(str(UUID(int=number)), kind)
    from unittest.mock import patch
    with patch("orchestration.fabric_api.FabricClient", return_value=service.client), patch.dict(
        "sys.modules", {"notebookutils": context["notebookutils"]}
    ):
        context["FabricClient"] = lambda *args: service.client
        exec(compile(source[start:end], filename, "exec"), context)
    if not filename.startswith("06"):
        assert service.items[str(UUID(int=7))]["folderId"] is not None
        assert service.items[ITEM]["folderId"] is not None
    else:
        destinations = {folder["id"]: folder["displayName"] for folder in service.folders}
        assert destinations[service.items[ITEM]["folderId"]] == "Notebooks"
        assert service.items[str(UUID(int=9))]["folderId"] is None
        assert all(service.items[str(UUID(int=n))]["folderId"] is not None for n in (8, 10, 11, 12))

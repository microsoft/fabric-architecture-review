# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Offline structural checks; the temporary rule keeps registry integration independent."""
import json
from pathlib import Path

import pytest

from analyzers import architecture_review as architecture


RULE = {
    "id": "ARCH-016",
    "title": "Pipeline activity dependency graph integrity",
    "severity": "medium",
    "microsoft_learn_url": "https://learn.microsoft.com/fabric/data-factory/activity-dependencies",
}
CHECKLIST = Path(__file__).resolve().parents[1] / "config" / "review-checklist.yaml"


def _activity(name: str, *dependencies: str, **fields: object) -> dict:
    return {"name": name, "type": "Wait",
            "dependsOn": [{"activity": dependency, "dependencyConditions": ["Succeeded"]}
                          for dependency in dependencies], **fields}


def _pipeline(activities: object, **fields: object) -> dict:
    return {
        "id": "pipeline-1", "displayName": "Load", "workspaceId": "workspace-1",
        "workspaceName": "Engineering",
        "parts": [{"path": "pipeline-content.json", "decoded": {"activities": activities}}],
        **fields,
    }


def _write(raw: Path, name: str, payload: object) -> None:
    (raw / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(architecture, "load_rules", lambda _: {"ARCH-016": RULE})

    def invoke(pipelines: list | None = None, **metadata: object) -> dict:
        if pipelines is not None:
            _write(tmp_path, "pipeline_definitions.json",
                   {"pipelines": pipelines, "collectionComplete": True, **metadata})
        findings = architecture.analyze(tmp_path, CHECKLIST)
        assert len(findings) == 1
        return findings[0]

    return invoke


def test_valid_graph_has_registry_identity_and_complete_item_evidence(run) -> None:
    finding = run([_pipeline([_activity("A"), _activity("B", "A"), _activity("C", "A", "B")])])
    assert finding["status"] == "pass"
    assert finding["rule_id"] == RULE["id"]
    assert finding["severity"] == RULE["severity"]
    assert finding["title"] == RULE["title"]
    assert finding["microsoft_learn_url"] == RULE["microsoft_learn_url"]
    assert "static structural validation" in finding["recommendation"]
    assert finding["evidence"] == {
        "source": "pipeline_definitions.json", "assessment_kind": "static_structure",
        "coverage_status": "complete", "pipeline_count": 1, "assessed_pipeline_count": 1,
        "affected_count": 0, "signal_codes": [], "reason_codes": [],
        "items": [{
            "workspace_id": "workspace-1", "workspace_name": "Engineering",
            "item_id": "pipeline-1", "item_name": "Load", "item_type": "DataPipeline",
            "signal_codes": [], "affected_count": 0, "activity_count": 3,
            "unresolved_dependency_count": 0, "coverage_status": "complete", "reason_codes": [],
        }],
    }


@pytest.mark.parametrize(("activities", "signals", "count"), [
    ([_activity("A"), _activity("A")], ["duplicate_activity"], 2),
    ([_activity("A", "absent", "also-absent")], ["missing_dependency"], 1),
    ([_activity("A", "A", "absent")], ["missing_dependency", "self_dependency"], 1),
    ([_activity("A", "B"), _activity("B", "A"), _activity("tail", "A")],
     ["dependency_cycle"], 2),
    ([_activity("A", "B"), _activity("B", "C"), _activity("C", "A"),
      _activity("D", "E"), _activity("E", "D"), _activity("tail", "A")],
     ["dependency_cycle"], 5),
])
def test_concrete_signals_count_unique_affected_activities(run, activities, signals, count) -> None:
    finding = run([_pipeline(activities)])
    assert finding["status"] == "fail"
    assert finding["evidence"]["signal_codes"] == signals
    assert finding["evidence"]["affected_count"] == count
    assert finding["evidence"]["items"][0]["affected_count"] == count


def test_nested_container_branches_are_distinct_scopes(run) -> None:
    local = [_activity("A"), _activity("B", "A")]
    activities = [
        _activity("A"), _activity("B", "A"),
        _activity("Loop", type="ForEach", typeProperties={"activities": [
            *local,
            _activity("Until", type="Until", typeProperties={"activities": local}),
        ]}),
        _activity("If", type="IfCondition", typeProperties={
            "ifTrueActivities": local, "ifFalseActivities": local,
        }),
        _activity("Switch", type="Switch", typeProperties={
            "cases": [{"value": "one", "activities": local}, {"value": "two", "activities": local}],
            "defaultActivities": local,
        }),
    ]
    finding = run([_pipeline(activities)])
    assert finding["status"] == "pass"
    assert finding["evidence"]["items"][0]["activity_count"] == 20


@pytest.mark.parametrize("child_key", ["activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities"])
def test_parent_or_other_branch_names_cannot_satisfy_child_dependencies(run, child_key) -> None:
    finding = run([_pipeline([
        _activity("Parent"),
        _activity("Container", typeProperties={
            child_key: [_activity("Child", "Parent", "Sibling")],
            "cases": [{"activities": [_activity("Sibling")]}],
        }),
    ])])
    assert finding["status"] == "fail"
    assert finding["evidence"]["signal_codes"] == ["missing_dependency"]
    assert finding["evidence"]["affected_count"] == 1


def test_nested_cycle_is_detected_without_cross_scope_edges(run) -> None:
    finding = run([_pipeline([
        _activity("A"),
        _activity("Loop", type="ForEach", typeProperties={
            "activities": [_activity("A", "B"), _activity("B", "A")],
        }),
    ])])
    assert finding["evidence"]["signal_codes"] == ["dependency_cycle"]
    assert finding["evidence"]["affected_count"] == 2


@pytest.mark.parametrize("dependency", [
    "@pipeline().parameters.secret",
    {"value": "@concat('connection-string')", "type": "Expression"},
    None, 10, "",
])
def test_dynamic_or_malformed_dependency_target_is_unknown_not_invalid(run, dependency) -> None:
    finding = run([_pipeline([_activity("A", dependsOn=[{"activity": dependency}])])])
    item = finding["evidence"]["items"][0]
    assert finding["status"] == "unknown"
    assert finding["evidence"]["coverage_status"] == "partial"
    assert item["signal_codes"] == []
    assert item["affected_count"] == 0
    assert item["unresolved_dependency_count"] == 1
    assert item["reason_codes"] == ["dependency_unresolved"]
    assert "secret" not in json.dumps(finding)
    assert "connection-string" not in json.dumps(finding)


@pytest.mark.parametrize("depends_on", [None, {}, "@variables('secret')", 3])
def test_unresolved_dependency_array_is_unknown(run, depends_on) -> None:
    finding = run([_pipeline([_activity("A", dependsOn=depends_on)])])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["unresolved_dependency_count"] == 1


def test_duplicate_target_is_not_assumed_to_be_a_self_edge(run) -> None:
    finding = run([_pipeline([_activity("A", "A"), _activity("A")])])
    assert finding["status"] == "fail"
    assert finding["evidence"]["signal_codes"] == ["duplicate_activity"]
    assert finding["evidence"]["items"][0]["unresolved_dependency_count"] == 1
    assert finding["evidence"]["coverage_status"] == "partial"


def test_unknown_name_does_not_fabricate_missing_reference(run) -> None:
    finding = run([_pipeline([{}, _activity("B", "could-be-the-unknown-activity")])])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["signal_codes"] == []


@pytest.mark.parametrize("activities", [
    None, {}, "secret", [None], [_activity("A", typeProperties=None)],
    [_activity("A", type="ForEach")],
    [_activity("A", type="Switch", typeProperties={"cases": [None]})],
    [_activity("A", type="Switch", typeProperties={"cases": "secret"})],
    [_activity("A", typeProperties={"activities": "secret"})],
])
def test_unreadable_activity_scopes_cannot_pass(run, activities) -> None:
    finding = run([_pipeline(activities)])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["signal_codes"] == []
    assert "secret" not in json.dumps(finding)


def test_properties_activities_and_empty_valid_pipeline(run) -> None:
    pipeline = _pipeline([])
    pipeline["parts"][0]["decoded"] = {"properties": {"activities": []}}
    assert run([pipeline])["status"] == "pass"
    pipeline["parts"][0]["decoded"] = {"properties": {}}
    assert run([pipeline])["status"] == "unknown"


def test_pipeline_content_takes_precedence_over_unrelated_json_properties(run) -> None:
    pipeline = _pipeline([_activity("A", "A")])
    pipeline["parts"].insert(0, {"path": "metadata.json", "decoded": {"properties": {"secret": True}}})
    finding = run([pipeline])
    assert finding["status"] == "fail"
    assert finding["evidence"]["signal_codes"] == ["self_dependency"]


@pytest.mark.parametrize(("error", "reason"), [
    ("http_403", "definition_denied"), ("http_401", "definition_denied"),
    ("http_error: secret connection string", "definition_unavailable"),
    ({"message": "secret"}, "definition_unavailable"),
])
def test_denied_or_failed_definition_never_passes_or_leaks_errors(run, error, reason) -> None:
    finding = run([_pipeline([_activity("A")], error=error)])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["coverage_status"] == "missing_evidence"
    assert finding["evidence"]["items"][0]["reason_codes"] == [reason]
    assert "secret" not in json.dumps(finding)


@pytest.mark.parametrize("parts", [None, [], [None], [{"path": 3}], [
    {"path": "pipeline-content.json", "payload": "secret"}
]])
def test_missing_or_undecoded_parts_cannot_pass(run, parts) -> None:
    finding = run([_pipeline([], parts=parts)])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["coverage_status"] == "missing_evidence"
    assert "secret" not in json.dumps(finding)


def test_collection_missing_unverified_empty_and_verified_empty_are_distinct(run) -> None:
    assert run()["status"] == "missing_evidence"
    assert run([], collectionComplete=None)["status"] == "unknown"
    assert run([])["status"] == "not_applicable"
    assert run([], collectionComplete=False)["status"] == "unknown"


@pytest.mark.parametrize("metadata", [
    {"collectionComplete": False},
    {"_meta": {"complete": False}},
    {"failedWorkspaces": ["workspace-2"]},
    {"collectionErrors": [{"message": "secret"}]},
    {"errors": 1},
])
def test_partial_collection_never_passes_but_preserves_concrete_findings(run, metadata) -> None:
    assert run([_pipeline([_activity("A")])], **metadata)["status"] == "unknown"
    finding = run([_pipeline([_activity("A", "A")])], **metadata)
    assert finding["status"] == "fail"
    assert finding["evidence"]["coverage_status"] == "partial"
    assert "secret" not in json.dumps(finding)


def test_collector_shape_reconciles_catalog_and_ignores_explained_notebook_errors(run, tmp_path) -> None:
    pipeline = _pipeline([_activity("A")])
    _write(tmp_path, "pipelines_notebooks.json", {"pipelines": [pipeline], "collectionComplete": True})
    assert run([pipeline], collectionComplete=None, errors=1,
               notebooks=[{"error": "secret"}])["status"] == "pass"
    finding = run([], collectionComplete=None)
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["reason_codes"] == ["definition_missing"]


def test_partial_catalog_or_definition_row_overrides_complete_envelope(run, tmp_path) -> None:
    finding = run([_pipeline([], collectionComplete=False)])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["coverage_status"] == "partial"
    _write(tmp_path, "pipelines_notebooks.json",
           {"pipelines": [_pipeline([])], "collectionComplete": False})
    finding = run([_pipeline([])])
    assert finding["status"] == "unknown"
    assert "pipeline_inventory_partial" in finding["evidence"]["reason_codes"]


def test_missing_definition_file_still_emits_known_pipeline_gaps(run, tmp_path) -> None:
    _write(tmp_path, "pipelines_notebooks.json",
           {"pipelines": [_pipeline([])], "collectionComplete": True})
    finding = run()
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["coverage_status"] == "missing_evidence"


def test_workspace_identity_is_resolved_by_item_id_not_display_name(run, tmp_path) -> None:
    _write(tmp_path, "scanner.json", {"workspaces": [
        {"id": "correct", "name": "SharedName", "DataPipeline": [{"id": "pipeline-1"}]},
        {"id": "wrong", "name": "SharedName", "DataPipeline": []},
    ]})
    finding = run([_pipeline([], workspaceId=None, workspaceName="SharedName")])
    assert finding["evidence"]["items"][0]["workspace_id"] == "correct"


def test_display_name_alone_and_ambiguous_item_id_do_not_attribute_workspace(run, tmp_path) -> None:
    assert run([_pipeline([], workspaceId=None)])["status"] == "unknown"
    _write(tmp_path, "scanner.json", {"workspaces": [
        {"id": "one", "name": "Engineering", "DataPipeline": [{"id": "pipeline-1"}]},
        {"id": "two", "name": "Engineering", "DataPipeline": [{"id": "pipeline-1"}]},
    ]})
    finding = run([_pipeline([], workspaceId=None)])
    assert finding["status"] == "unknown"
    assert finding["evidence"]["items"][0]["workspace_id"] is None
    assert "identity_unresolved" in finding["evidence"]["items"][0]["reason_codes"]


def test_explicit_workspace_id_is_authoritative_even_when_names_collide(run, tmp_path) -> None:
    _write(tmp_path, "pipelines_notebooks.json", {"pipelines": [
        _pipeline([], workspaceId="one"), _pipeline([], workspaceId="two"),
    ], "collectionComplete": True})
    finding = run([_pipeline([], workspaceId="two")])
    assert finding["evidence"]["items"][0]["workspace_id"] == "two"
    assert finding["evidence"]["items"][1]["workspace_id"] == "one"
    assert finding["status"] == "unknown"


def test_malformed_inventory_rows_prevent_empty_compliance(run, tmp_path) -> None:
    _write(tmp_path, "pipelines_notebooks.json", {"pipelines": [{}], "collectionComplete": True})
    assert run([])["status"] == "unknown"
    assert run([None])["status"] == "unknown"


def test_duplicate_definitions_have_one_pipeline_row_with_partial_coverage(run) -> None:
    pipeline = _pipeline([_activity("A", "A")])
    finding = run([pipeline, pipeline])
    assert finding["evidence"]["pipeline_count"] == 1
    assert finding["evidence"]["affected_count"] == 1
    assert finding["evidence"]["coverage_status"] == "partial"
    assert finding["evidence"]["items"][0]["reason_codes"] == ["duplicate_definition"]


def test_all_signals_are_static_and_scopes_roll_up_to_one_pipeline(run) -> None:
    pipeline = _pipeline([
        _activity("Self", "Self"),
        _activity("Missing", "absent"),
        _activity("Duplicate"), _activity("Duplicate"),
        _activity("Loop", type="ForEach", typeProperties={
            "activities": [_activity("A", "B"), _activity("B", "A")],
        }),
    ])
    finding = run([pipeline, pipeline])
    assert finding["status"] == "fail"
    assert len(finding["evidence"]["items"]) == 1
    item = finding["evidence"]["items"][0]
    assert (item["workspace_id"], item["item_id"], item["item_type"]) == (
        "workspace-1", "pipeline-1", "DataPipeline")
    assert item["signal_codes"] == [
        "dependency_cycle", "duplicate_activity", "missing_dependency", "self_dependency",
    ]
    assert item["affected_count"] == 6
    assert finding["evidence"]["signal_codes"] == item["signal_codes"]


def test_failed_targets_remain_id_scoped_with_colliding_display_names(run) -> None:
    failed = _pipeline([_activity("A", "A")], workspaceId="affected")
    healthy = _pipeline([_activity("A")], workspaceId="healthy")
    unresolved = _pipeline([_activity("A", dependsOn="@unknown()")], workspaceId="unresolved")
    finding = run([failed, healthy, unresolved])
    items = finding["evidence"]["items"]
    assert len(items) == 3
    assert len({(item["workspace_id"], item["item_id"]) for item in items}) == 3
    assert len({item["workspace_name"] for item in items}) == 1
    assert [item["workspace_id"] for item in items if item["signal_codes"]] == ["affected"]
    assert items[1]["signal_codes"] == items[2]["signal_codes"] == []
    assert items[2]["coverage_status"] == "partial"


def test_evidence_omits_activity_names_expressions_and_full_definition(run) -> None:
    pipeline = _pipeline([
        _activity("private-activity", "private-missing"),
        _activity("dynamic", dependsOn=[{"activity": "@secret()"}],
                  typeProperties={"connectionString": "password=secret"}),
    ])
    pipeline["parts"].append({"path": "unrelated.json", "decoded": {"secret": "private-data"}})
    finding = run([pipeline])
    assert finding["status"] == "fail"
    encoded = json.dumps(finding)
    for private in ("private-activity", "private-missing", "@secret", "password", "private-data"):
        assert private not in encoded


def test_long_dependency_chain_does_not_use_recursive_graph_search(run) -> None:
    activities = [_activity(str(index), *([str(index - 1)] if index else []))
                  for index in range(1500)]
    assert run([_pipeline(activities)])["status"] == "pass"


def test_cycle_members_match_reachability_for_all_four_node_directed_graphs() -> None:
    edges = [(source, target) for source in range(4) for target in range(4) if source != target]
    for mask in range(1 << len(edges)):
        adjacency = {node: set() for node in range(4)}
        for bit, (source, target) in enumerate(edges):
            if mask & (1 << bit):
                adjacency[source].add(target)
        reachability = {node: set(targets) for node, targets in adjacency.items()}
        for via in range(4):
            for source in range(4):
                if via in reachability[source]:
                    reachability[source].update(reachability[via])
        expected = sum(node in reachable for node, reachable in reachability.items())
        result = architecture._dependency_graph({"activities": [
            _activity(str(node), *(str(target) for target in sorted(adjacency[node])))
            for node in range(4)
        ]})
        assert result["affected_count"] == expected, mask
        assert result["signal_codes"] == (["dependency_cycle"] if expected else []), mask
        assert result["coverage_status"] == "complete"


def test_existing_architecture_rules_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = Path(__file__).parent / "fixtures" / "sample" / "raw"
    rules = architecture.load_rules(CHECKLIST)
    rules.pop("ARCH-016", None)
    monkeypatch.setattr(architecture, "load_rules", lambda _: rules)
    baseline = architecture.analyze(raw, CHECKLIST)
    monkeypatch.setattr(architecture, "load_rules", lambda _: {**rules, "ARCH-016": RULE})
    extended = architecture.analyze(raw, CHECKLIST)
    assert [row for row in extended if row["rule_id"] != "ARCH-016"] == baseline

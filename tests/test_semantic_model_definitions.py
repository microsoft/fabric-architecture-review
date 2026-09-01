# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

from collectors import semantic_model_definitions as definitions


class _Provider:
    def __init__(self):
        self.calls = 0

    def headers(self, scope):
        self.calls += 1
        return {"Authorization": f"Bearer token-{self.calls}"}


def _catalog(raw_dir):
    datasets = [
        {"id": "model-1", "name": "One", "workspaceId": "workspace-1"},
        {"id": "model-2", "name": "Two", "workspaceId": "workspace-1"},
    ]
    (raw_dir / "semantic_models.json").write_text(
        json.dumps({"datasets": datasets}), encoding="utf-8"
    )
    return datasets


def test_collect_refreshes_headers_for_each_model(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _catalog(raw_dir)
    provider = _Provider()
    seen_headers = []

    monkeypatch.setattr(definitions, "get_default_provider", lambda: provider)
    monkeypatch.setattr(
        definitions,
        "_get_definition",
        lambda headers, workspace_id, model_id: (
            seen_headers.append(headers["Authorization"]) or {"definition": {"parts": []}},
            None,
        ),
    )
    monkeypatch.setattr(definitions, "CHECKPOINT_INTERVAL", 1)
    monkeypatch.setattr(definitions, "MODEL_DELAY_SECONDS", 0)

    target = definitions.collect(raw_dir)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert provider.calls == 2
    assert seen_headers == ["Bearer token-1", "Bearer token-2"]
    assert payload["status"] == "completed"
    assert len(payload["models"]) == 2


def test_collect_resumes_successful_models_from_matching_checkpoint(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    datasets = _catalog(raw_dir)
    fingerprint = definitions._candidate_fingerprint(datasets)
    definitions._write_progress(
        raw_dir / "semantic_model_definitions.json",
        [{"id": "model-1", "workspaceId": "workspace-1", "parts": []}],
        0,
        fingerprint,
        "in_progress",
    )
    provider = _Provider()
    requested = []

    monkeypatch.setattr(definitions, "get_default_provider", lambda: provider)
    monkeypatch.setattr(
        definitions,
        "_get_definition",
        lambda headers, workspace_id, model_id: (
            requested.append(model_id) or {"definition": {"parts": []}},
            None,
        ),
    )
    monkeypatch.setattr(definitions, "CHECKPOINT_INTERVAL", 1)
    monkeypatch.setattr(definitions, "MODEL_DELAY_SECONDS", 0)

    target = definitions.collect(raw_dir)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert requested == ["model-2"]
    assert provider.calls == 1
    assert payload["status"] == "completed"
    assert [model["id"] for model in payload["models"]] == ["model-1", "model-2"]

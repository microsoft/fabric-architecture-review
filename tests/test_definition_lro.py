# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""getDefinition long-running-operation polling: quick first poll, then the advertised delay."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from collectors import pipeline_definitions, semantic_model_definitions

MODULES = [
    (pipeline_definitions, "_get_definition_impl"),
    (semantic_model_definitions, "_get_definition_impl"),
]


def response(status, headers=None, body=None):
    return SimpleNamespace(
        status_code=status, headers=headers or {}, content=b"{}" if body is not None else b"",
        json=lambda: body,
    )


@pytest.mark.parametrize("module,name", MODULES)
def test_first_poll_is_quick_and_later_polls_honor_retry_after(monkeypatch, module, name):
    replies = iter([
        response(202, {"Location": "https://x/operations/1", "Retry-After": "20"}),
        response(200, {"Retry-After": "7"}, {"status": "Running"}),
        response(200, {}, {"status": "Succeeded"}),
        response(200, {}, {"definition": {"parts": []}}),
    ])
    sleeps = []
    monkeypatch.setattr(module, "request", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    definition, error = getattr(module, name)(lambda: {}, "ws", "item")
    assert error is None and definition == {"definition": {"parts": []}}
    assert sleeps == [module.LRO_FIRST_POLL_DELAY, 7]


@pytest.mark.parametrize("module,name", MODULES)
def test_short_retry_after_is_not_stretched(monkeypatch, module, name):
    replies = iter([
        response(202, {"Location": "https://x/operations/1", "Retry-After": "1"}),
        response(200, {}, {"status": "Failed"}),
    ])
    sleeps = []
    monkeypatch.setattr(module, "request", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    assert getattr(module, name)(lambda: {}, "ws", "item") == (None, "lro_failed")
    assert sleeps == [1]

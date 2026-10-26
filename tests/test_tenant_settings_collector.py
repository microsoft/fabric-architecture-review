# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from types import SimpleNamespace

import pytest

from collectors import tenant_settings
from collectors._http import HttpError

GROUP = {"graphId": "11111111-1111-4111-8111-111111111111", "name": "Sensitive group name"}


def setting(name, **extra):
    return {"settingName": name, "title": name, "tenantSettingGroup": "Export and sharing settings",
            "enabled": True, "canSpecifySecurityGroups": True,
            "enabledSecurityGroups": [], "excludedSecurityGroups": [], **extra}


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(tenant_settings, "get_default_provider",
                        lambda: SimpleNamespace(headers=lambda scope: {"Authorization": "synthetic"}))


def serve(monkeypatch, pages):
    requested = []

    def get_json(url, headers, **kwargs):
        requested.append(url)
        return pages[len(requested) - 1]
    monkeypatch.setattr(tenant_settings, "get_json", get_json)
    return requested


def test_group_names_and_ids_are_reduced_to_counts(provider, monkeypatch, tmp_path):
    serve(monkeypatch, [{"tenantSettings": [
        setting("PublishToWeb", enabledSecurityGroups=[GROUP], excludedSecurityGroups=[GROUP, GROUP]),
        setting("CreateFabricItem", delegateToWorkspace=True, delegateToCapacity="not-a-bool"),
    ]}])
    path = tenant_settings.collect(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Sensitive group name" not in text and GROUP["graphId"] not in text
    payload = json.loads(text)
    assert payload["collectionComplete"] is True
    publish, create = payload["tenantSettings"]
    assert (publish["enabledSecurityGroupCount"], publish["excludedSecurityGroupCount"]) == (1, 2)
    assert "enabledSecurityGroups" not in publish
    assert create["delegateToWorkspace"] is True and "delegateToCapacity" not in create


def test_continuation_pages_are_followed(provider, monkeypatch, tmp_path):
    requested = serve(monkeypatch, [
        {"tenantSettings": [setting("A")], "continuationUri": "https://example.test/next"},
        {"tenantSettings": [setting("B")]},
    ])
    payload = json.loads(tenant_settings.collect(tmp_path).read_text())
    assert [row["settingName"] for row in payload["tenantSettings"]] == ["A", "B"]
    assert requested == [tenant_settings.TENANT_SETTINGS_URL, "https://example.test/next"]


@pytest.mark.parametrize("pages", [
    [{"tenantSettings": [setting("A")], "continuationUri": tenant_settings.TENANT_SETTINGS_URL}],
    [{"tenantSettings": "not-a-list"}],
    [{"tenantSettings": [setting("A"), setting("A")]}],
    [{"tenantSettings": [{"enabled": True}]}],
])
def test_malformed_responses_are_unavailable_evidence_not_an_empty_list(provider, monkeypatch, tmp_path, pages):
    serve(monkeypatch, pages)
    payload = json.loads(tenant_settings.collect(tmp_path).read_text())
    assert payload["collectionComplete"] is False
    assert "tenantSettings" not in payload


def test_missing_administrator_role_is_recorded_as_unavailable(provider, monkeypatch, tmp_path):
    def denied(url, headers, **kwargs):
        raise HttpError("GET returned HTTP 403", status_code=403)
    monkeypatch.setattr(tenant_settings, "get_json", denied)
    payload = json.loads(tenant_settings.collect(tmp_path).read_text())
    assert payload["collectionComplete"] is False
    assert payload["collectionErrors"][0]["statusCode"] == 403

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from analyzers.tenant_settings_review import analyze


CHECKLIST = Path(__file__).resolve().parents[1] / "config" / "review-checklist.yaml"


def _setting(name: str, state: str) -> dict:
    return {
        "settingName": name,
        "enabled": state != "disabled",
        "canSpecifySecurityGroups": True,
        "enabledSecurityGroups": [{"graphId": "synthetic-group", "name": "Allowed group"}] if state == "scoped" else [],
        "excludedSecurityGroups": [],
    }


def _findings(tmp_path: Path, settings: list[dict]) -> dict:
    (tmp_path / "tenant_settings.json").write_text(
        json.dumps({"tenantSettings": settings}), encoding="utf-8",
    )
    return {finding["rule_id"]: finding for finding in analyze(tmp_path, CHECKLIST)}


@pytest.mark.parametrize(("rule_id", "name", "legacy_name"), [
    ("SEC-001", "PublishToWeb", "PublishToWeb"),
    ("TENANT-001", "FabricGAWorkloads", "CreateFabricItem"),
    ("TENANT-002", "ServicePrincipalAccessPermissionAPIs", "ServicePrincipalAccess"),
    ("TENANT-003", "AllowExternalDataSharingSwitch", "AllowExternalDataSharing"),
    ("TENANT-004", "CustomVisualsTenant", "CustomVisualsTenantSettings"),
    ("TENANT-005", "RScriptVisual", "RScriptVisualsTenantSettings"),
])
@pytest.mark.parametrize("state", ["disabled", "scoped", "unrestricted"])
def test_observed_names_preserve_rule_evaluation(
    tmp_path: Path, rule_id: str, name: str, legacy_name: str, state: str,
) -> None:
    expected = "fail" if state == "unrestricted" or (rule_id == "TENANT-002" and state == "disabled") else "pass"
    for setting_name in (name, legacy_name):
        finding = _findings(tmp_path, [_setting(setting_name, state)])[rule_id]
        assert finding["status"] == expected
        assert finding["evidence"]["setting_name"] == setting_name


@pytest.mark.parametrize("excel", ["missing", "disabled", "scoped", "unrestricted"])
@pytest.mark.parametrize("csv", ["missing", "disabled", "scoped", "unrestricted"])
def test_export_requires_both_controls(tmp_path: Path, excel: str, csv: str) -> None:
    states = {"ExportToExcelSetting": excel, "ExportToCsv": csv}
    settings = [_setting(name, state) for name, state in states.items() if state != "missing"]
    # Neither the old umbrella name nor unrelated exports substitute for required controls.
    settings.extend([_setting("ExportData", "disabled"), _setting("ExportReport", "unrestricted")])
    findings = _findings(tmp_path, settings)
    finding = findings["SEC-002"]
    expected = "fail" if "unrestricted" in states.values() else "missing_evidence" if "missing" in states.values() else "pass"
    assert finding["status"] == expected
    assert len(findings) == 7
    assert finding["dimension"] == "security"
    evidence = finding["evidence"]
    assert evidence["setting_names"] == list(states)
    assert "setting_name" not in evidence
    assert evidence["missing_settings"] == [name for name, state in states.items() if state == "missing"]
    assert len(evidence["settings"]) == 2
    for control in evidence["settings"]:
        state = states[control["setting_name"]]
        assert control["present"] is (state != "missing")
        assert control["status"] == (
            "missing_evidence" if state == "missing" else "fail" if state == "unrestricted" else "pass"
        )


@pytest.mark.parametrize(("rule_id", "name", "legacy_name"), [
    ("TENANT-001", "FabricGAWorkloads", "CreateFabricItem"),
    ("TENANT-002", "ServicePrincipalAccessPermissionAPIs", "ServicePrincipalAccess"),
    ("TENANT-003", "AllowExternalDataSharingSwitch", "AllowExternalDataSharing"),
    ("TENANT-004", "CustomVisualsTenant", "CustomVisualsTenantSettings"),
    ("TENANT-005", "RScriptVisual", "RScriptVisualsTenantSettings"),
])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_current_api_name_takes_precedence(
    tmp_path: Path, rule_id: str, name: str, legacy_name: str, reverse_order: bool,
) -> None:
    settings = [_setting(legacy_name, "scoped"), _setting(name, "unrestricted")]
    finding = _findings(tmp_path, settings[::-1] if reverse_order else settings)[rule_id]
    assert finding["status"] == "fail"
    assert finding["evidence"]["setting_name"] == name


@pytest.mark.parametrize(("rule_id", "unrelated_name"), [
    ("TENANT-001", "OntologyPreview"),
    ("TENANT-002", "ServicePrincipalAccessGlobalAPIs"),
    ("TENANT-002", "AllowServicePrincipalsUseReadAdminAPIs"),
    ("TENANT-002", "AllowServicePrincipalsUseWriteAdminAPIs"),
    ("TENANT-003", "ExternalDataSharing"),
    ("TENANT-003", "ExternalDataSharingReceiveSettings"),
    ("TENANT-003", "ExternalDataSharingSendSettings"),
    ("TENANT-003", "ShareReportWithEntireOrg"),
    ("TENANT-003", "AllowExternalDataSharingReceiverSwitch"),
    ("TENANT-003", "ExternalSharingV2"),
    ("TENANT-003", "EnableDatasetInPlaceSharing"),
    ("TENANT-004", "AddCertifiedVisualsOnly"),
    ("TENANT-004", "AddAndUseCertifiedVisualsOnly"),
    ("TENANT-004", "OrgVisualsTenantSetting"),
    ("TENANT-004", "CustomVisualsTenantSetting"),
    ("TENANT-004", "CertifiedCustomVisualsTenant"),
    ("TENANT-005", "RPythonVisualsTenantSettings"),
    ("TENANT-005", "PythonVisualsTenantSettings"),
    ("TENANT-005", "PythonScriptsTenantSettings"),
    ("TENANT-005", "RScriptVisualsTenantSetting"),
])
def test_distinct_or_unverified_names_do_not_supply_evidence(
    tmp_path: Path, rule_id: str, unrelated_name: str,
) -> None:
    finding = _findings(tmp_path, [_setting(unrelated_name, "scoped")])[rule_id]
    assert finding["status"] == "missing_evidence"
    assert finding["evidence"]["present"] is False


def test_fabric_workload_delegation_with_two_allowed_groups(tmp_path: Path) -> None:
    setting = _setting("FabricGAWorkloads", "scoped")
    setting.update({
        "title": "Users can create Fabric items",
        "delegateToCapacity": True,
        "tenantSettingGroup": "Microsoft Fabric",
        "enabledSecurityGroups": [
            {"graphId": "00000000-0000-4000-8000-000000000001", "name": "Synthetic capacity group"},
            {"graphId": "00000000-0000-4000-8000-000000000002", "name": "Synthetic tenant test group"},
        ],
    })
    finding = _findings(tmp_path, [setting, _setting("OntologyPreview", "disabled")])["TENANT-001"]
    assert finding["status"] == "pass"
    assert finding["evidence"]["setting_name"] == "FabricGAWorkloads"
    assert finding["evidence"]["enabledSecurityGroups"] == setting["enabledSecurityGroups"]
    assert finding["evidence"]["reason"] == "Enabled but scoped to 2 security group(s)."


@pytest.mark.parametrize("state", ["disabled", "scoped", "unrestricted"])
def test_external_data_sharing_does_not_use_receiver_state(tmp_path: Path, state: str) -> None:
    finding = _findings(tmp_path, [
        _setting("AllowExternalDataSharingSwitch", state),
        _setting("AllowExternalDataSharingReceiverSwitch", "disabled"),
    ])["TENANT-003"]
    assert finding["status"] == ("fail" if state == "unrestricted" else "pass")
    assert finding["evidence"]["setting_name"] == "AllowExternalDataSharingSwitch"


@pytest.mark.parametrize(("rule_id", "name"), [
    ("SEC-001", "PublishToWeb"),
    ("SEC-002", "ExportToCsv"),
    ("TENANT-001", "FabricGAWorkloads"),
    ("TENANT-002", "ServicePrincipalAccessPermissionAPIs"),
    ("TENANT-003", "AllowExternalDataSharingSwitch"),
    ("TENANT-004", "CustomVisualsTenant"),
    ("TENANT-005", "RScriptVisual"),
])
def test_exclusions_do_not_establish_allowed_group_scoping(tmp_path: Path, rule_id: str, name: str) -> None:
    setting = _setting(name, "unrestricted")
    setting["excludedSecurityGroups"] = [{"graphId": "synthetic-excluded-group"}]
    finding = _findings(tmp_path, [setting, _setting("ExportToExcelSetting", "disabled")])[rule_id]
    assert finding["status"] == "fail"


@pytest.mark.parametrize("enabled", [None, "false", 0])
def test_incomplete_enabled_value_is_not_disabled(tmp_path: Path, enabled: object) -> None:
    setting = _setting("FabricGAWorkloads", "scoped")
    if enabled is None:
        del setting["enabled"]
    else:
        setting["enabled"] = enabled
    finding = _findings(tmp_path, [setting])["TENANT-001"]
    assert finding["status"] == "missing_evidence"
    assert finding["evidence"]["reason"] == "Setting returned without a boolean enabled value."


def test_export_enabled_value_requires_evidence(tmp_path: Path) -> None:
    excel = _setting("ExportToExcelSetting", "disabled")
    del excel["enabled"]
    finding = _findings(tmp_path, [excel, _setting("ExportToCsv", "disabled")])["SEC-002"]
    assert finding["status"] == "missing_evidence"
    assert finding["evidence"]["settings"][0]["status"] == "missing_evidence"


@pytest.mark.parametrize("name", ["AllowGuestUserToAccessSharedContent", "AllowGuestUserToAccessTenant"])
@pytest.mark.parametrize("state", ["disabled", "scoped", "unrestricted"])
def test_guest_setting_names(tmp_path: Path, name: str, state: str) -> None:
    from analyzers.security_review import analyze as analyze_security

    _findings(tmp_path, [_setting(name, state)])
    finding = next(f for f in analyze_security(tmp_path, CHECKLIST) if f["rule_id"] == "SEC-003")
    assert finding["status"] == ("fail" if state == "unrestricted" else "pass")


@pytest.mark.parametrize("trusted", [None, False, True])
def test_profiles_are_not_trusted_workspace_access(tmp_path: Path, trusted: bool | None) -> None:
    from analyzers.security_review import analyze as analyze_security

    settings = [_setting("AllowServicePrincipalsCreateAndUseProfiles", "unrestricted")]
    if trusted is not None:
        settings.append(_setting(
            "AllowTrustedWorkspaceAccessForStorageAccounts", "unrestricted" if trusted else "disabled",
        ))
    _findings(tmp_path, settings)
    finding = next(f for f in analyze_security(tmp_path, CHECKLIST) if f["rule_id"] == "SEC-009")
    assert finding["status"] == "info"
    assert finding["evidence"]["trustedWorkspaceAccess"] is trusted
    assert finding["evidence"]["trustedWorkspaceSettingPresent"] is (trusted is not None)
    assert ("Trusted-workspace access enabled" in finding["title"]) is (trusted is True)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Record tenant-wide Fabric / Power BI tenant settings as sanitized, auditable evidence.

Endpoint: GET https://api.fabric.microsoft.com/v1/admin/tenantsettings
Docs: https://learn.microsoft.com/rest/api/fabric/admin/tenants/list-tenant-settings

Requires a Fabric administrator. Without that role the collector records the
evidence as unavailable instead of inventing an empty settings list.

DATA SAFETY: Tenant configuration only. Security group names and IDs are reduced to
counts before anything is written; no customer data is read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import record_collection_failure
from collectors._http import HttpError, get_json
from collectors.auth import FABRIC_SCOPE, get_default_provider

TENANT_SETTINGS_URL = "https://api.fabric.microsoft.com/v1/admin/tenantsettings"
_MAX_PAGES = 50


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def sanitize(setting: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the setting's state and the size of its group scope, never the groups."""
    record: Dict[str, Any] = {
        "settingName": setting.get("settingName"),
        "title": setting.get("title"),
        "tenantSettingGroup": setting.get("tenantSettingGroup"),
        "enabled": setting.get("enabled"),
        "canSpecifySecurityGroups": setting.get("canSpecifySecurityGroups"),
        "enabledSecurityGroupCount": _count(setting.get("enabledSecurityGroups")),
        "excludedSecurityGroupCount": _count(setting.get("excludedSecurityGroups")),
    }
    for key in ("delegateToCapacity", "delegateToDomain", "delegateToWorkspace"):
        if isinstance(setting.get(key), bool):
            record[key] = setting[key]
    return record


def fetch_sanitized() -> List[Dict[str, Any]]:
    """Call the admin API once (all pages) and return the sanitized settings. Raises when it cannot."""
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)

    settings: List[Dict[str, Any]] = []
    url: str | None = TENANT_SETTINGS_URL
    visited: set[str] = set()
    while url:
        if url in visited or len(visited) >= _MAX_PAGES:
            raise HttpError("Tenant settings pagination did not terminate")
        visited.add(url)
        payload = get_json(url, headers)
        page = payload.get("tenantSettings") if isinstance(payload, dict) else None
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise HttpError("Tenant settings response has no settings array")
        settings.extend(page)
        url = payload.get("continuationUri") or None
        if url is not None and not isinstance(url, str):
            raise HttpError("Tenant settings pagination link must be a URL string")

    names = [item.get("settingName") for item in settings]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise HttpError("Tenant settings contain missing or repeated setting names")
    return [sanitize(item) for item in settings]


@record_collection_failure("tenant_settings.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    settings = fetch_sanitized()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    out = target / "tenant_settings.json"
    out.write_text(json.dumps({
        "tenantSettings": settings,
        "collectionComplete": True,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Tenant settings: recorded {len(settings)} setting(s).")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Evidence the connected FUAM already holds, read before the API and completed by it where needed.

Tenant settings: a fresh FUAM snapshot supplies the settings, and the admin API is also called for
the security-group detail FUAM lacks (excluded groups); if the API is denied the FUAM values stand.
When the FUAM snapshot is stale, missing or unreadable, the full API call is the fallback. Either way the
record says where each part came from and why a fallback happened.

DATA SAFETY: tenant configuration only. Security group names and IDs are reduced to counts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Modules whose data FUAM holds well enough to read first.
SUPPLIED = frozenset({"tenant_settings"})
# FUAM transfers tenant settings once a day, so the freshness limit gets one extra day.
DAILY_GRACE_HOURS = 24

_SETTINGS_COLUMNS = (
    "settingName", "title", "tenantSettingGroup", "enabled", "canSpecifySecurityGroups",
    "delegateToCapacity", "delegateToDomain", "delegateToWorkspace", "TransferDateTime",
)
_LATEST = "(SELECT MAX(TransferDateTime) FROM dbo.tenant_settings)"
SETTINGS_SQL = f"SELECT {', '.join(_SETTINGS_COLUMNS)} FROM dbo.tenant_settings WHERE TransferDateTime = {_LATEST}"
GROUPS_SQL = (
    "SELECT settingName, COUNT(DISTINCT graphId) AS enabledGroups FROM dbo.tenant_settings_enabled_security_groups "
    f"WHERE TransferDateTime = {_LATEST} GROUP BY settingName"
)


def supplies(snapshot: dict[str, Any], module: str) -> bool:
    return module in SUPPLIED and bool(snapshot["configuration"]["sources"]["fuam"]["enabled"])


def collect(
    module: str, raw_dir: str | Path, snapshot: dict[str, Any],
    query: Callable[[str, str], list[dict[str, Any]]], now: datetime | None = None,
    api_fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> Path:
    if module not in SUPPLIED:
        raise ValueError(f"FUAM does not supply {module}.")
    return _tenant_settings(Path(raw_dir), snapshot, query, now or datetime.now(timezone.utc), api_fetch)


def _write(raw_dir: Path, payload: dict[str, Any]) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / "tenant_settings.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return target


def _fuam_settings(
    snapshot: dict[str, Any], query: Callable[[str, str], list[dict[str, Any]]], now: datetime,
) -> tuple[list[dict[str, Any]] | None, str | None, str | None]:
    """(settings, observed time, reason). Settings are None when FUAM cannot be used."""
    connection = snapshot["configuration"]["sources"]["fuam"]
    try:
        rows = query("fuam", SETTINGS_SQL)
        groups = query("fuam", GROUPS_SQL)
    except Exception:
        # Query text and driver messages are withheld: they can contain endpoint details.
        return None, None, "query_failed"
    if not rows:
        return None, None, "no_transfer_snapshot"
    if any(set(row) != set(_SETTINGS_COLUMNS) for row in rows) or any(
            set(row) != {"settingName", "enabledGroups"} for row in groups):
        return None, None, "incompatible_schema"
    names = [row["settingName"] for row in rows]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        return None, None, "incompatible_identity"
    transferred = rows[0]["TransferDateTime"]
    if not isinstance(transferred, datetime):
        return None, None, "incompatible_schema"
    transferred = transferred.replace(tzinfo=timezone.utc) if transferred.tzinfo is None else transferred
    if (now - transferred).total_seconds() / 3600 > connection["max_age_hours"] + DAILY_GRACE_HOURS:
        return None, None, "source_data_stale"
    counts = {row["settingName"]: int(row["enabledGroups"]) for row in groups}
    settings = []
    for row in rows:
        record = {
            "settingName": row["settingName"], "title": row["title"], "tenantSettingGroup": row["tenantSettingGroup"],
            "enabled": row["enabled"], "canSpecifySecurityGroups": row["canSpecifySecurityGroups"],
            "enabledSecurityGroupCount": counts.get(row["settingName"], 0),
            # FUAM does not record excluded groups, so the count is unknown rather than zero.
            "excludedSecurityGroupCount": None,
        }
        for key in ("delegateToCapacity", "delegateToDomain", "delegateToWorkspace"):
            if isinstance(row[key], bool):
                record[key] = row[key]
        settings.append(record)
    return settings, transferred.isoformat(), None


def _api_settings(api_fetch: Callable[[], list[dict[str, Any]]] | None) -> list[dict[str, Any]] | None:
    if api_fetch is None:
        return None
    try:
        return api_fetch()
    except Exception:
        # Not an administrator, or the API failed; the reason text can contain endpoint details.
        return None


def _tenant_settings(
    raw_dir: Path, snapshot: dict[str, Any], query: Callable[[str, str], list[dict[str, Any]]], now: datetime,
    api_fetch: Callable[[], list[dict[str, Any]]] | None,
) -> Path:
    fuam, observed, reason = _fuam_settings(snapshot, query, now)
    api = _api_settings(api_fetch)
    if fuam is not None and api is not None:
        merged = {row["settingName"]: row for row in fuam}
        merged.update({row["settingName"]: row for row in api})  # the live API is authoritative for groups
        print(f"Tenant settings: {len(merged)} setting(s) from FUAM, group detail from the API.")
        return _write(raw_dir, {"tenantSettings": list(merged.values()), "collectionComplete": True,
                                "dataSource": "fuam+api", "observedAtUtc": observed})
    if fuam is not None:
        print(f"Tenant settings: {len(fuam)} setting(s) from FUAM; the API was not available for group detail.")
        return _write(raw_dir, {"tenantSettings": fuam, "collectionComplete": True, "dataSource": "fuam",
                                "observedAtUtc": observed, "groupDetail": "excluded_groups_unavailable"})
    if api is not None:
        print(f"Tenant settings: FUAM unusable ({reason}); full API call used.")
        return _write(raw_dir, {"tenantSettings": api, "collectionComplete": True, "dataSource": "api",
                                "reason": reason, "fallback": "full_api_call"})
    print(f"Tenant settings: FUAM unusable ({reason}) and the API was not available.")
    return _write(raw_dir, {"tenantSettings": [], "collectionComplete": False, "dataSource": "fuam", "reason": reason})

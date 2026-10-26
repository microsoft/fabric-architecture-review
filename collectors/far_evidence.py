# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Bind scoped FUAM/FCA metadata, never optimizer results or customer data."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from collectors._common import load_workspace_inventory
from collectors.enrichment_facts import fca_capacity_tags, fuam_facts
from analyzers.far_profile import PROFILE_ID, PROFILE_SHA256, JUDGED_SETTING_NAMES
from orchestration.review_config import canonical_id, read_snapshot

CONTRACTS = {
    "workspaces": (
        "WorkspaceId", ("WorkspaceId", "WorkspaceName", "CapacityId", "Description", "fuam_modified_at"),
        "far_evidence_workspaces",
    ),
    "active_items": (
        "workspaceId", ("workspaceId", "id", "name", "type", "capacityId", "fuam_modified_at"),
        "far_evidence_items",
    ),
    "capacities": (
        "CapacityId", ("CapacityId", "displayName", "sku", "region", "fuam_modified_at"),
        "far_evidence_capacities",
    ),
}

# FUAM keeps every tenant-settings transfer, so it can show when a setting last changed.
TENANT_HISTORY_TARGET = "far_evidence_tenant_settings"
TENANT_HISTORY_COLUMNS = ("settingName", "enabled", "TransferDateTime")
TENANT_HISTORY_DAYS = 90

# Provenance carried by every evidence row, so each persisted table says where it came from and under which
# configuration and rule-logic version it was captured.
PROVENANCE = (
    "source_module", "source_table", "source_workspace_id", "source_lakehouse_id",
    "configuration_sha256", "configuration_captured_at_utc", "profile_id", "profile_sha256",
    "freshness_status",
)


def _provenance(
    snapshot: dict[str, Any], module: str, table: str, connection: dict[str, Any], freshness: str,
) -> dict[str, str]:
    return {
        "source_module": module, "source_table": table,
        "source_workspace_id": connection["workspace_id"], "source_lakehouse_id": connection["lakehouse_id"],
        "configuration_sha256": snapshot["sha256"],
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "freshness_status": freshness,
    }


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("FUAM row is missing a valid fuam_modified_at timestamp.")
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _tenant_history(
    query: Callable[[str, str], list[dict[str, Any]]], source: str, connection: dict[str, Any],
    snapshot: dict[str, Any], now: datetime,
) -> list[dict[str, Any]]:
    """Read the recent transfers of the tenant settings that the tenant rules evaluate."""
    names = sorted({name for group in JUDGED_SETTING_NAMES.values() for name in group})
    cutoff = (now - timedelta(days=TENANT_HISTORY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    selected = ", ".join(f"'{name}'" for name in names)
    rows = []
    for row in query(source, (
        f"SELECT {', '.join(TENANT_HISTORY_COLUMNS)} FROM dbo.tenant_settings "
        f"WHERE settingName IN ({selected}) AND TransferDateTime >= '{cutoff}'"
    )):
        if set(row) != set(TENANT_HISTORY_COLUMNS):
            raise ValueError("FUAM tenant_settings returned an incompatible schema.")
        if row["settingName"] not in names or not isinstance(row["enabled"], bool):
            raise ValueError("FUAM tenant_settings returned an unexpected setting or state.")
        observed = _timestamp(row["TransferDateTime"])
        if observed > now:
            raise ValueError("FUAM tenant_settings contains a future transfer timestamp.")
        rows.append({"settingName": row["settingName"], "enabled": row["enabled"], "TransferDateTime": observed})
    rows.sort(key=lambda row: (row["settingName"], row["TransferDateTime"]))
    newest = max((row["TransferDateTime"] for row in rows), default=None)
    stale = newest is None or (now - newest).total_seconds() / 3600 > connection["max_age_hours"]
    return [{
        **row, "TransferDateTime": row["TransferDateTime"].isoformat(),
        **_provenance(snapshot, "fuam", "tenant_settings", connection, "stale" if stale else "recently_modified"),
    } for row in rows]


def _unresolved_model_ids(workspaces: list[dict[str, Any]]) -> set[str]:
    """Semantic models that scanned reports read from but the review scope does not hold."""
    def identity(value: Any) -> str | None:
        try:
            return canonical_id(str(value), "semantic model")
        except ValueError:
            return None

    known = {identity(model.get("id")) for ws in workspaces for model in ws.get("datasets") or []
             if isinstance(model, dict)}
    wanted = {identity(report.get("datasetId")) for ws in workspaces for report in ws.get("reports") or []
              if isinstance(report, dict) and report.get("datasetId")}
    return {value for value in wanted - known if value}


def _fact_coverage(source: str, family: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source, "table": entry["table"], "family": family, "status": entry["status"],
        "row_count": len(entry.get("rows", [])),
        "reason": entry.get("reason_code") or "Evaluated by the enrichment rules; freshness is in the finding.",
    }


def bind(
    snapshot: dict[str, Any], workspaces: list[dict[str, Any]],
    query: Callable[[str, str], list[dict[str, Any]]],
    *, now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    config = snapshot["configuration"]
    workspace_ids = {canonical_id(row["id"], "review workspace") for row in workspaces}
    capacity_ids = {
        canonical_id(row["capacityId"], "review capacity")
        for row in workspaces if row.get("capacityId")
    }
    result: dict[str, Any] = {
        "schema_version": 1, "configuration_sha256": snapshot["sha256"],
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
        "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "captured_at_utc": now.isoformat(), "tables": {}, "coverage": [], "rule_facts": {},
        "_provenance": {
            "source": "enrichment", "collector": "collectors.far_evidence",
            "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
            "configuration_sha256": snapshot["sha256"],
            "configuration_captured_at_utc": snapshot["captured_at_utc"],
            "captured_at_utc": now.isoformat(), "workspace_scope": ",".join(sorted(workspace_ids)),
            "capture_status": "context_only",
        },
    }
    for source, connection in config["sources"].items():
        if not connection["enabled"]:
            result["coverage"].append({"source": source, "status": "disabled"})
            continue
        if not workspace_ids:
            result["coverage"].append({"source": source, "status": "empty_review_scope"})
            continue
        if source == "fuam":
            for table, (id_column, columns, target) in CONTRACTS.items():
                scope = capacity_ids if table == "capacities" else workspace_ids
                rows: list[dict[str, Any]] = []
                identities: set[tuple[str, str]] = set()
                ordered = sorted(scope)
                for start in range(0, len(ordered), 500):
                    selected = ", ".join(f"'{item_id}'" for item_id in ordered[start:start + 500])
                    statement = (
                        f"SELECT {', '.join(columns)} FROM dbo.{table} "
                        f"WHERE LOWER({id_column}) IN ({selected}) AND fuam_deleted = 0"
                    )
                    for row in query(source, statement):
                        if set(row) != set(columns):
                            raise ValueError(f"FUAM {table} returned an incompatible schema.")
                        entity_id = canonical_id(row[id_column], f"FUAM {table}")
                        if entity_id not in scope:
                            raise ValueError(f"FUAM {table} returned an out-of-scope entity.")
                        item_id = canonical_id(row["id"], "FUAM item") if table == "active_items" else ""
                        identity = (entity_id, item_id)
                        if identity in identities:
                            raise ValueError(f"FUAM {table} contains duplicate identities.")
                        identities.add(identity)
                        modified = _timestamp(row["fuam_modified_at"])
                        age_hours = (now - modified).total_seconds() / 3600
                        if age_hours < 0:
                            raise ValueError(f"FUAM {table} contains a future modification timestamp.")
                        normalized = dict(row)
                        normalized[id_column] = entity_id
                        if table == "active_items":
                            normalized["id"] = item_id
                        normalized["fuam_modified_at"] = modified.isoformat()
                        normalized.update(_provenance(
                            snapshot, "fuam", table, connection,
                            "stale" if age_hours > connection["max_age_hours"] else "recently_modified",
                        ))
                        rows.append(normalized)
                result["tables"][target] = rows
                result["coverage"].append({
                    "source": source, "table": table, "status": "context_only",
                    "row_count": len(rows), "reason": "Row modification time is not a collection watermark.",
                })
            history = _tenant_history(query, source, connection, snapshot, now)
            result["tables"][TENANT_HISTORY_TARGET] = history
            result["coverage"].append({
                "source": source, "table": "tenant_settings", "status": "context_only",
                "row_count": len(history),
                "reason": "Transfer history; a missing transfer is not evidence that a setting was unchanged.",
            })
            facts = fuam_facts(
                lambda statement: query(source, statement), connection, config["thresholds"],
                workspace_ids, capacity_ids, now, _unresolved_model_ids(workspaces),
            )
            result["rule_facts"].update(facts)
            result["coverage"].extend(_fact_coverage(source, family, entry) for family, entry in facts.items())
        else:
            mapping: dict[str, str] = {}
            for entry in config["profiles"].get("capacities", []):
                if entry["id"] in capacity_ids and entry.get("arm_resource_id"):
                    resource = entry["arm_resource_id"]
                    if resource in mapping:
                        raise ValueError("A capacity ARM resource cannot map to multiple capacity GUIDs.")
                    mapping[resource] = entry["id"]
            rows = []
            seen: set[str] = set()
            resources = sorted(mapping)
            for start in range(0, len(resources), 500):
                selected = ", ".join(f"'{value}'" for value in resources[start:start + 500])
                for row in query(source, (
                    "SELECT ResourceId, ResourceName, RegionId, ResourceKey FROM dbo.resources "
                    f"WHERE LOWER(ResourceId) IN ({selected})"
                )):
                    if set(row) != {"ResourceId", "ResourceName", "RegionId", "ResourceKey"}:
                        raise ValueError("FCA resources returned an incompatible schema.")
                    resource_id = str(row["ResourceId"]).lower()
                    if resource_id not in mapping or resource_id in seen:
                        raise ValueError("FCA returned an out-of-scope or duplicate resource identity.")
                    seen.add(resource_id)
                    rows.append({
                        **row, "ResourceId": resource_id, "capacity_id": mapping[resource_id],
                        **_provenance(snapshot, "fca", "resources", connection, "unknown"),
                    })
            tags = fca_capacity_tags(lambda statement: query(source, statement), connection, mapping)
            result["rule_facts"]["capacity_tags"] = tags
            result["coverage"].append(_fact_coverage(source, "capacity_tags", tags))
            result["tables"]["far_evidence_capacity_resources"] = rows
            result["coverage"].append({
                "source": source, "table": "resources",
                "status": "context_only" if mapping else "missing_capacity_resource_bridge",
                "row_count": len(rows), "unmapped_capacity_count": len(capacity_ids) - len(mapping),
                "reason": "Explicit capacity GUID-to-ARM mapping required; resource freshness is unknown.",
            })
    return result


def _current_review_workspaces(raw_dir: Path, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    expected = {
        "source": "native", "profile_id": PROFILE_ID, "profile_sha256": PROFILE_SHA256,
        "configuration_sha256": snapshot["sha256"],
        "configuration_captured_at_utc": snapshot["captured_at_utc"],
        "workspace_scope": os.environ.get("WORKSPACE_IDS", ""),
    }
    for filename in ("scanner.json", "workspace_inventory.json"):
        path = raw_dir / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        provenance = payload.get("_provenance") if isinstance(payload, dict) else None
        if not isinstance(provenance, dict) or any(provenance.get(key) != value for key, value in expected.items()):
            raise ValueError(f"{filename} does not belong to the current frozen architectural collection.")
    return load_workspace_inventory(raw_dir)


def collect(output_dir: str = "output/far/raw") -> Path:
    from collectors.fuam_sql import Session
    target = Path(output_dir) / "far_evidence.json"
    target.unlink(missing_ok=True)
    path = os.environ.get("FAR_CONFIG_SNAPSHOT")
    if not path:
        raise ValueError("FAR collection requires a frozen FAR_CONFIG_SNAPSHOT.")
    snapshot = read_snapshot(Path(path))
    enabled = any(source["enabled"] for source in snapshot["configuration"]["sources"].values())
    workspaces = _current_review_workspaces(Path(output_dir), snapshot) if enabled else []
    with Session(snapshot) as sql:
        evidence = bind(snapshot, workspaces, sql.query)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    for coverage in evidence["coverage"]:
        print(f"FAR evidence: {coverage}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Selection and email preparation; native pipeline monitoring owns delivery."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any

from orchestration.completion import prepare_owner_emails, validate_report_url
from orchestration.fabric_api import FabricClient, guid
from orchestration.ranking import METRICS, rank_workspaces, selection_payload
from orchestration.sources import capacity_selector, validate_capacity_scope


def boolean(value: str) -> bool:
    if not isinstance(value, str) or value.lower() not in ("true", "false"):
        raise ValueError("Boolean parameters must be 'true' or 'false'.")
    return value.lower() == "true"


def validate_config(config: dict[str, Any]) -> None:
    capacity_selector(config.get("CAPACITY_ID_OR_NAME"))
    if config["RANKING_METRIC"] not in METRICS:
        raise ValueError(f"RANKING_METRIC must be one of {METRICS}.")
    if not 1 <= int(config["LOOKBACK_DAYS"]) <= 28:
        raise ValueError("LOOKBACK_DAYS must be between 1 and 28.")
    if not 1 <= int(config["TOP_N"]) <= 100:
        raise ValueError("TOP_N must be between 1 and 100.")
    if config.get("SOURCE_MODE", "fuam") != "fuam":
        raise ValueError("Only FUAM is supported; no unsupported Capacity Metrics queries.")
    validate_notifications(config)


def validate_notifications(config: dict[str, Any]) -> None:
    if boolean(config["NOTIFICATIONS_ENABLED"]):
        validate_report_url(config.get("FAR_REPORT_URL"))


def select_workspaces(
    config: dict[str, Any],
    parent_run_id: str,
    *,
    read_source: Callable[[dict[str, Any]], tuple[str, list[dict[str, Any]]]],
    state_dir: Path,
) -> dict[str, Any]:
    validate_config(config)
    parent_run_id = guid(parent_run_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / (parent_run_id + ".json")
    if path.exists():
        raise RuntimeError(
            f"Parent {parent_run_id} already has a selection record. "
            "Inspect the native FAR activity before recovery; automatic replay is disabled."
        )

    result = read_source(config)
    source, rows = result
    capacity_scope = getattr(result, "capacity_scope", {
        "requested": "", "capacity_id": "", "capacity_name": "",
    })
    validate_capacity_scope(capacity_scope)
    if capacity_scope["requested"] != capacity_selector(config.get("CAPACITY_ID_OR_NAME")):
        raise ValueError("Monitoring source did not confirm the requested capacity scope.")
    allowlist = config.get("WORKSPACE_IDS", "")
    selected = rank_workspaces(
        rows, top_n=int(config["TOP_N"]), allowlist=allowlist,
        excluded_workspace_ids=(config["CHILD_WORKSPACE_ID"], config["FUAM_WORKSPACE_ID"]),
    )
    print("FUAM selection excludes the FAR hosting workspace and the configured FUAM workspace.")
    print(
        f"FUAM selection: source_rows={len(rows)}, "
        f"allowlist={'set' if allowlist.strip() else 'blank'}, selected_workspaces={len(selected)}."
    )
    state = {
        **selection_payload(selected, source, config["RANKING_METRIC"]),
        "parent_run_id": parent_run_id,
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": int(config["LOOKBACK_DAYS"]),
        "capacity_scope": capacity_scope,
        "unattributed_workspace_groups": getattr(result, "unattributed_workspace_groups", 0),
        "workspace_inventory": getattr(result, "workspace_inventory", None),
        "child_workspace_id": guid(config["CHILD_WORKSPACE_ID"]),
        "child_pipeline_id": guid(config["CHILD_PIPELINE_ID"]),
    }
    # Refuse a repeated selection stage; it could cause a second native child run.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2)
    if not selected:
        print("No workspaces have positive metrics in scope. FAR and notifications were skipped.")
    return {
        "parent_run_id": parent_run_id, "status": state["status"],
        "workspace_ids": state["workspace_ids"], "workspace_count": len(selected),
    }


def prepare_notifications(
    config: dict[str, Any],
    parent_run_id: str,
    child_run_id: str,
    *,
    prepare: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, str]]],
    state_dir: Path,
) -> dict[str, Any]:
    """Prepare once per audit; this guard does not guarantee exactly-once delivery.

    Messages exist only in the returned native activity input, not in the audit.
    An interrupted attempt needs operator reconciliation, never automatic replay.
    Child success is enforced by the generated wait-on-completion dependency,
    not by this helper. Native pipeline run IDs are not Scheduler job IDs.
    """
    validate_notifications(config)
    if not boolean(config["NOTIFICATIONS_ENABLED"]):
        return {"notifications": "disabled", "messages": []}
    parent_run_id, child_run_id = guid(parent_run_id), guid(child_run_id)
    path = state_dir / (parent_run_id + ".json")
    state = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(state, dict) and "capacity_scope" in state:
        validate_capacity_scope(state["capacity_scope"])
    workspace_id, pipeline_id = guid(config["CHILD_WORKSPACE_ID"]), guid(config["CHILD_PIPELINE_ID"])
    if (
        not isinstance(state, dict)
        or state.get("parent_run_id") != parent_run_id
        or state.get("child_workspace_id") != workspace_id
        or state.get("child_pipeline_id") != pipeline_id
        or not isinstance(state.get("workspaces"), list) or not state["workspaces"]
        or not isinstance(state.get("workspace_ids"), str) or not state["workspace_ids"]
    ):
        raise ValueError("Notification stage does not match a nonempty selection for this FAR child.")
    for row in state["workspaces"]:
        if (
            not isinstance(row, dict) or not isinstance(row.get("workspace_id"), str)
            or not isinstance(row.get("workspace_name"), str) or not row["workspace_name"]
            or isinstance(row.get("metric_value"), bool)
            or not isinstance(row.get("metric_value"), (int, float))
            or not math.isfinite(row["metric_value"]) or row["metric_value"] <= 0
        ):
            raise ValueError("The recorded selection has invalid workspace metadata.")
    expected_ids = [guid(row["workspace_id"]) for row in state["workspaces"]]
    if len(set(expected_ids)) != len(expected_ids) or ",".join(expected_ids) != state["workspace_ids"]:
        raise ValueError("The recorded selection has inconsistent workspace IDs.")
    if (
        state.get("metric") not in METRICS
        or not isinstance(state.get("metric_caveat"), str) or not state["metric_caveat"]
        or type(state.get("lookback_days")) is not int or not 1 <= state["lookback_days"] <= 28
        or len(expected_ids) > 100
    ):
        raise ValueError("The recorded selection has invalid ranking context.")
    if state.get("status") != "selected" or any(key in state for key in ("child_run_id", "child_job_id")):
        raise RuntimeError("Email preparation already attempted; automatic replay is disabled.")
    marker = state_dir / (parent_run_id + ".notification-started")
    if marker.exists():
        raise RuntimeError("Email preparation already attempted; automatic replay is disabled.")

    # Retain the legacy marker name so interrupted pre-migration runs cannot replay.
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({"child_run_id": child_run_id}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    state["child_run_id"] = child_run_id

    def save(status: str) -> None:
        state["status"] = status
        pending = path.with_suffix(".pending")
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)

    save("preparing_emails")
    try:
        messages = prepare(config, state)
        if not isinstance(messages, list) or not messages:
            raise ValueError("Email preparation returned no messages for the selected workspaces.")
    except (ValueError, RuntimeError, OSError):
        state["notifications"] = "email_preparation_failed"
        save("email_preparation_failed")
        raise
    state["notifications"] = "emails_prepared"
    state["prepared_message_count"] = len(messages)
    state["emails_prepared_at"] = datetime.now(timezone.utc).isoformat()
    save("emails_prepared")
    return {
        "parent_run_id": parent_run_id, "child_run_id": child_run_id,
        "status": "emails_prepared", "messages": messages,
    }


def run_selection(config: dict[str, Any]) -> dict[str, Any]:
    validate_config(config)
    import notebookutils

    from orchestration.sources import read_source
    client = FabricClient(lambda: notebookutils.credentials.getToken("pbi"))
    return select_workspaces(
        config, config["PARENT_RUN_ID"],
        read_source=lambda options: read_source(options, client),
        state_dir=Path("/lakehouse/default/Files/far-orchestration/runs"),
    )


def run_notifications(config: dict[str, Any]) -> dict[str, Any]:
    validate_notifications(config)
    if not boolean(config["NOTIFICATIONS_ENABLED"]):
        return {"notifications": "disabled", "messages": []}
    import notebookutils

    client = FabricClient(lambda: notebookutils.credentials.getToken("pbi"))
    return prepare_notifications(
        config, config["PARENT_RUN_ID"], config["CHILD_RUN_ID"],
        prepare=lambda options, state: prepare_owner_emails(options, state, client),
        state_dir=Path("/lakehouse/default/Files/far-orchestration/runs"),
    )

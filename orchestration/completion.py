# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Prepare native email activity inputs; this module never sends email."""
from __future__ import annotations

from collections.abc import Callable
from html import escape
import re
from typing import Any
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from orchestration.fabric_api import FabricClient, guid, service_error_details


def validate_report_url(url: str) -> str:
    """Accept only report links on the exact public Fabric/Power BI hosts."""
    if (
        not isinstance(url, str) or not url or len(url) > 4096
        or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in url)
        or "\\" in url
    ):
        raise ValueError("FAR_REPORT_URL must be an HTTPS Fabric or Power BI report link.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in ("app.fabric.microsoft.com", "app.powerbi.com")
        or not parsed.path.strip("/")
    ):
        raise ValueError("FAR_REPORT_URL must be an HTTPS Fabric or Power BI report link.")
    return url


def validate_email_address(value: str) -> str:
    """Return a single normalized public-domain mailbox, never a recipient list."""
    invalid = "A single valid public-domain email address is required."
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        raise ValueError(invalid)
    local, domain = value.split("@")
    if (
        not 1 <= len(local) <= 64
        or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*", local)
        or len(domain) > 253
    ):
        raise ValueError(invalid)
    labels = domain.split(".")
    if (
        len(labels) < 2
        or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels)
        or not re.fullmatch(r"(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{2,59})", labels[-1])
    ):
        raise ValueError(invalid)
    return value.lower()


def report_link(url: str, workspace_id: str) -> str:
    """Use the shared workspace filter (navigation only, never authorization)."""
    parsed = urlsplit(validate_report_url(url))
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() != "filter"]
    query.append(("filter", f"gold_workspace_risk/workspace_id eq '{guid(workspace_id)}'"))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def resolve_owners(
    workspace_ids: list[str], token: Callable[[], str], *, transport: Callable = requests.get,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for workspace_id in workspace_ids:
        workspace_id = guid(workspace_id)
        response = transport(
            f"https://api.powerbi.com/v1.0/myorg/admin/groups/{workspace_id}/users",
            headers={"Authorization": "Bearer " + token()}, timeout=120, allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Workspace owner lookup failed for {workspace_id}: {service_error_details(response)}. "
                "No emails were prepared. Verify the execution tenant, admin permissions and current "
                "workspace/FUAM source identity; HTTP 404 alone does not establish deletion. "
                "After correcting the source or access and reconciling the prior run, start a new "
                "parent run. Automatic notification replay is disabled."
            )
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"Workspace owner lookup returned invalid JSON for {workspace_id}.") from None
        if (
            not isinstance(payload, dict) or "error" in payload
            or not isinstance(payload.get("value"), list)
            or any(payload.get(key) for key in ("@odata.nextLink", "continuationUri", "continuationToken"))
        ):
            raise RuntimeError(f"Workspace owner lookup returned an invalid response for {workspace_id}.")
        recipients: set[str] = set()
        for principal in payload["value"]:
            if (
                not isinstance(principal, dict)
                or not isinstance(principal.get("groupUserAccessRight"), str)
                or not isinstance(principal.get("principalType"), str)
            ):
                raise RuntimeError(f"Workspace owner lookup returned an invalid principal for {workspace_id}.")
            if principal.get("groupUserAccessRight") != "Admin":
                continue
            if principal.get("principalType") != "User":
                continue
            try:
                email = validate_email_address(principal.get("emailAddress"))
            except ValueError:
                raise ValueError(f"A direct-user admin of {workspace_id} has no usable email address.") from None
            recipients.add(email)
        if not recipients:
            raise ValueError(
                f"Workspace {workspace_id} has no direct-user administrator recipient. "
                "Group/service-principal ownership is not expanded; no emails were prepared."
            )
        result[workspace_id] = sorted(recipients)
    return result


def completion_messages(
    config: dict[str, Any], state: dict[str, Any], owners: dict[str, list[str]],
) -> list[dict[str, str]]:
    """Build one isolated HTML message per workspace/direct-user administrator."""
    validate_report_url(config["FAR_REPORT_URL"])
    workspace_ids = [guid(row["workspace_id"]) for row in state["workspaces"]]
    if not workspace_ids or len(set(workspace_ids)) != len(workspace_ids) or set(owners) != set(workspace_ids):
        raise ValueError("Email recipients must match the nonempty selected workspace scope.")
    validated: dict[str, list[str]] = {}
    for workspace_id in workspace_ids:
        recipients = owners[workspace_id]
        if not isinstance(recipients, list) or not recipients:
            raise ValueError("Every selected workspace requires a direct-user administrator.")
        validated[workspace_id] = sorted({validate_email_address(email) for email in recipients})

    messages = []
    for rank, workspace in enumerate(state["workspaces"], start=1):
        workspace_id = guid(workspace["workspace_id"])
        name = str(workspace["workspace_name"])
        subject_name = " ".join(
            "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in name).split()
        )
        body = (
            f"<p>The FAR review for <strong>{escape(name)}</strong> ({workspace_id}) is ready.</p>"
            f'<p><a href="{escape(report_link(config["FAR_REPORT_URL"], workspace_id), quote=True)}">'
            "Open the FAR review report</a>. The link includes a workspace filter, not an access grant. "
            "In the governance report, it filters workspace-risk visuals, not the whole report. "
            "Findings and recommendations may include other reviewed workspaces; "
            "check their workspace and item scope before acting. "
            "If this link opens the separately configured Workspace Owner report, its WorkspaceOwner "
            "security role restricts data to your authorized workspaces.</p>"
            "<p><strong>Don't have access?</strong> Open the report link and select "
            "<strong>Request access</strong>, if available. If that option is missing, "
            "contact the FAR report owner or your Power BI administrator and include the report link. "
            "This link does not grant access; report permissions and any applicable row-level "
            "security still apply.</p>"
            f"<p>Selection rank: {rank}. Metric: {escape(str(state['metric']))}. "
            f"Recorded value: {escape(str(workspace['metric_value']))}. "
            f"Lookback: {escape(str(state['lookback_days']))} days. "
            f"{escape(str(state['metric_caveat']))}</p>"
            "<p>Findings are recommendations, not guaranteed root causes. "
            "Validate their applicability before acting.</p>"
            f"<p>Review tracking ID: {escape(str(state['parent_run_id']))}.</p>"
        )
        for email in validated[workspace_id]:
            messages.append({
                "to": email, "subject": f"FAR review ready: {subject_name or workspace_id}"[:255].rstrip(),
                "body": body, "workspace_id": workspace_id,
            })
    return messages


def prepare_owner_emails(
    config: dict[str, Any], state: dict[str, Any], client: FabricClient,
) -> list[dict[str, str]]:
    """Resolve ALL owners before building messages; no delivery API is called."""
    validate_report_url(config["FAR_REPORT_URL"])
    owners = resolve_owners([row["workspace_id"] for row in state["workspaces"]], client.token)
    return completion_messages(config, state, owners)

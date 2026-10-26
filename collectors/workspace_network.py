# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Network access policy of each workspace: inbound, outbound and the IP firewall.

Endpoints:
  GET https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/networking/communicationPolicy
  GET .../networking/communicationPolicy/inbound/firewall      (IP firewall rules, preview)
  GET .../networking/communicationPolicy/outbound/connections  (403 while outbound protection is off)
  GET .../managedPrivateEndpoints

Docs: https://learn.microsoft.com/rest/api/fabric/core/workspaces/get-network-communication-policy

DATA SAFETY: Keeps the default action (Allow or Deny) for inbound and outbound public access, and counts only for
the rest: firewall rules, rules that cover a very wide address range, and managed private endpoints. IP addresses,
rule names and endpoint targets are read in memory and never written. The policy itself is required; the other
reads are best effort and a failure leaves their count empty rather than failing the workspace.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._common import filter_workspaces_by_scope, load_workspace_inventory, record_collection_failure
from collectors._http import HttpError, collect_value, request
from collectors.auth import FABRIC_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"
ACTIONS = {"Allow", "Deny"}
OUTBOUND_DISABLED = "OutboundAccessProtectionSettingsDisabled"
# An address range wider than a /16 (IPv4) or a /32 (IPv6) is too broad to count as a restriction.
BROAD_ADDRESSES = {4: 2 ** 16, 6: 2 ** 96}


def _action(payload: Any, direction: str) -> str | None:
    rules = ((payload.get(direction) or {}).get("publicAccessRules") or {}) if isinstance(payload, dict) else {}
    value = rules.get("defaultAction") if isinstance(rules, dict) else None
    return value if value in ACTIONS else None


def _broad(value: Any) -> bool | None:
    """Whether a firewall rule covers a very wide range; None when its value cannot be read."""
    text = str(value or "").strip()
    try:
        if "-" in text:
            low, high = (ipaddress.ip_address(part.strip()) for part in text.split("-", 1))
            return int(high) - int(low) + 1 > BROAD_ADDRESSES[low.version]
        network = ipaddress.ip_network(text, strict=False)
    except (ValueError, KeyError):
        return None
    return network.num_addresses > BROAD_ADDRESSES[network.version]


def _firewall(wsid: str, headers: Any) -> Dict[str, Any]:
    response = request("GET", f"{FAB}/workspaces/{wsid}/networking/communicationPolicy/inbound/firewall", headers)
    payload = response.json() if response.status_code == 200 and response.content else None
    rules = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(rules, list):
        return {}
    return {"inboundFirewallRuleCount": len(rules),
            "inboundFirewallBroadRangeCount": sum(_broad(rule.get("value")) is True for rule in rules if isinstance(rule, dict))}


def _outbound_protection(wsid: str, headers: Any) -> Dict[str, Any]:
    response = request("GET", f"{FAB}/workspaces/{wsid}/networking/communicationPolicy/outbound/connections", headers)
    if response.status_code == 200:
        return {"outboundProtection": "enabled"}
    try:
        code = (response.json() or {}).get("errorCode") if response.status_code == 403 and response.content else None
    except ValueError:
        code = None
    return {"outboundProtection": "disabled"} if code == OUTBOUND_DISABLED else {}


def _managed_endpoints(wsid: str, headers: Any) -> Dict[str, Any]:
    try:
        return {"managedPrivateEndpointCount": len(collect_value(f"{FAB}/workspaces/{wsid}/managedPrivateEndpoints", headers))}
    except HttpError:
        return {}


@record_collection_failure("workspace_network.json")
def collect(output_dir: str | os.PathLike = "output/far/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = [w for w in filter_workspaces_by_scope(load_workspace_inventory(raw_dir)) if w.get("id")]
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if workspaces:
        provider = get_default_provider()
        headers = lambda: provider.headers(scope=FABRIC_SCOPE)
        print(f"Workspace network: reading {len(workspaces)} workspace(s)...")
    for workspace in workspaces:
        wsid = workspace["id"]
        response = request("GET", f"{FAB}/workspaces/{wsid}/networking/communicationPolicy", headers)
        payload = response.json() if response.status_code == 200 and response.content else None
        inbound, outbound = _action(payload, "inbound"), _action(payload, "outbound")
        if inbound is None or outbound is None:
            results.append({"workspaceId": wsid, "status": "unavailable"})
            errors.append({"workspaceId": wsid, "component": "communication_policy", "statusCode": response.status_code,
                           "message": "The workspace network policy could not be read."})
            continue
        results.append({"workspaceId": wsid, "status": "collected", "inbound": inbound, "outbound": outbound,
                        **_firewall(wsid, headers), **_outbound_protection(wsid, headers),
                        **_managed_endpoints(wsid, headers)})
    target = raw_dir / "workspace_network.json"
    target.write_text(json.dumps({"workspaces": results, "collectionComplete": not errors, "collectionErrors": errors},
                                 indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target}.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

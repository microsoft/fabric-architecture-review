# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Data Gateways inventory (on-prem / VNet / personal).

Endpoints (Power BI REST, scope == signed-in user):
  GET https://api.powerbi.com/v1.0/myorg/gateways
  GET https://api.powerbi.com/v1.0/myorg/gateways/{id}/datasources
  GET https://api.powerbi.com/v1.0/myorg/gateways/{id}/members            (best-effort)

Cluster type ("gatewayType"):
  - Resource          -> Fabric / Power BI On-premises data gateway cluster
  - OnPremises        -> legacy on-prem gateway
  - VirtualNetwork    -> VNet data gateway (private connectivity)
  - Personal          -> Personal mode (single user only)

Docs:
  https://learn.microsoft.com/rest/api/power-bi/gateways

DATA SAFETY: Gateway name, type, public key, member list and the *names* of
attached data sources only. Credentials are never returned by the REST API.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from collectors._http import collect_value, get_json
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI = "https://api.powerbi.com/v1.0/myorg"


def _list_gateways(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    return collect_value(f"{PBI}/gateways", headers)


def _datasources(headers: Dict[str, str], gateway_id: str) -> List[Dict[str, Any]]:
    payload = get_json(
        f"{PBI}/gateways/{gateway_id}/datasources", headers,
        allow=(200, 401, 403, 404),
    )
    if not payload:
        return []
    return payload.get("value") or []


def _members(headers: Dict[str, str], gateway_id: str) -> List[Dict[str, Any]]:
    # Cluster membership is not exposed on every gateway type; tolerate 404.
    payload = get_json(
        f"{PBI}/gateways/{gateway_id}/members", headers,
        allow=(200, 401, 403, 404),
    )
    if not payload:
        return []
    return payload.get("value") or payload.get("memberGateways") or []


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)

    print("Gateways: listing...")
    gateways = _list_gateways(headers)
    print(f"  {len(gateways)} gateway(s)/cluster(s) visible.")

    enriched: List[Dict[str, Any]] = []
    for g in gateways:
        gid = g.get("id")
        if not gid:
            continue
        ds = _datasources(headers, gid)
        members = _members(headers, gid)
        enriched.append({
            "id": gid,
            "name": g.get("name"),
            "gatewayType": g.get("type") or g.get("gatewayType"),
            "gatewayAnnotation": g.get("gatewayAnnotation"),
            "publicKey": bool(g.get("publicKey")),
            "memberCount": len(members) or g.get("memberGatewaysCount") or 1,
            "members": [
                {
                    "id": m.get("id") or m.get("memberId"),
                    "name": m.get("name") or m.get("memberName"),
                    "status": m.get("status") or m.get("memberStatus"),
                    "version": m.get("version") or m.get("memberVersion"),
                }
                for m in members
            ],
            "datasourceCount": len(ds),
            "datasources": [
                {
                    "id": d.get("id"),
                    "datasourceType": d.get("datasourceType"),
                    "datasourceName": d.get("datasourceName"),
                    "connectionDetails": d.get("connectionDetails"),
                }
                for d in ds
            ],
        })

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    out = target / "gateways.json"
    out.write_text(json.dumps({
        "gatewayCount": len(enriched),
        "gateways": enriched,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}.")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

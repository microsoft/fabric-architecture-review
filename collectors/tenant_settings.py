# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Collect tenant-wide Fabric / Power BI tenant settings.

Endpoint: GET https://api.fabric.microsoft.com/v1/admin/tenantsettings
Docs: https://learn.microsoft.com/rest/api/fabric/admin/tenants/list-tenant-settings

DATA SAFETY: This endpoint returns tenant configuration (which features are
enabled, who they apply to, security group scoping). It does NOT return any
customer data, dataset content, or user PII beyond security group identifiers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from collectors import _http
from collectors.auth import FABRIC_SCOPE, get_default_provider

TENANT_SETTINGS_URL = "https://api.fabric.microsoft.com/v1/admin/tenantsettings"


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    """Call the tenant settings admin endpoint and persist the JSON.

    Returns the path of the written file.
    """
    provider = get_default_provider()
    headers = provider.headers(scope=FABRIC_SCOPE)

    # Route through _http.request so this throttle-prone admin endpoint gets the
    # same Retry-After-aware 429 / 5xx / transient-error retry as every other
    # collector.
    response = _http.request("GET", TENANT_SETTINGS_URL, headers, timeout=60)
    response.raise_for_status()
    payload = response.json()

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    target = out_path / "tenant_settings.json"
    with target.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "output") + "/raw",
        help="Directory to write tenant_settings.json into",
    )
    args = parser.parse_args()
    path = collect(args.output_dir)
    print(f"Tenant settings written to: {path}")


if __name__ == "__main__":
    main()

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Lakehouse and Warehouse inventory via Fabric REST.

Endpoints:
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/lakehouses
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/lakehouses/{id}/tables
  GET https://onelake.table.fabric.microsoft.com/delta/{ws}/{id}/api/2.1/unity-catalog/{schemas,tables}
  GET https://api.fabric.microsoft.com/v1/workspaces/{ws}/warehouses

Workspace IDs come from scanner.json / workspace_inventory.json.

DEEP-METRICS: OneLake DFS recursive filesystem listing (file count + size per
table) would require the Azure Storage DataLake SDK + OAuth-on-OneLake setup.
Not in REST-only mode.

DATA SAFETY: Table NAMES and metadata only. No SELECT against Warehouse/SQL
endpoints. No file content reads.

Coverage is tracked per workspace/component and lakehouse table list. Failed
requests retain observed rows, but never establish an empty inventory. The
legacy tables endpoint is used only for non-schema lakehouses. Schema-enabled
lakehouses use the metadata-only OneLake Delta table APIs with a storage token.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Tuple
from uuid import UUID

from collectors._http import Headers, HttpError, get_json, paginate_value
from collectors._common import load_workspace_inventory, record_collection_failure
from collectors.auth import FABRIC_SCOPE, STORAGE_SCOPE, get_default_provider

FAB = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.table.fabric.microsoft.com/delta"


def _load_workspaces(raw_dir: Path) -> List[Tuple[str, str]]:
    return [(w["id"], w.get("name") or "") for w in load_workspace_inventory(raw_dir) if w.get("id")]


def _tables(headers: Headers, wsid: str, lakehouse_id: str) -> Iterator[Dict[str, Any]]:
    base_url = f"{FAB}/workspaces/{wsid}/lakehouses/{lakehouse_id}/tables"
    url = base_url
    params: Dict[str, Any] | None = {"maxResults": 100}
    seen: set[tuple[str, str]] = set()
    while url:
        page = (url, json.dumps(params, sort_keys=True))
        if page in seen:
            raise HttpError("Lakehouse tables response repeats a continuation")
        seen.add(page)
        payload = get_json(url, headers, params=params)
        tables = payload.get("data", payload.get("value")) if isinstance(payload, dict) else None
        if not isinstance(tables, list):
            raise HttpError("Lakehouse tables response has no data/value array")
        for table in tables:
            if not isinstance(table, dict):
                raise HttpError("Lakehouse tables response has invalid table entries")
            yield table
        next_url = payload.get("continuationUri") or payload.get("@odata.nextLink")
        token = payload.get("continuationToken")
        if next_url is not None and not isinstance(next_url, str):
            raise HttpError("Lakehouse tables continuation must be a URL string")
        if token is not None and not isinstance(token, str):
            raise HttpError("Lakehouse tables continuation token must be a string")
        url = next_url or ""
        params = None
        if not url and token:
            url = base_url
            params = {"maxResults": 100, "continuationToken": token}


def _delta_pages(
    headers: Headers, base: str, collection: str, params: Dict[str, Any],
) -> Iterator[Dict[str, Any]]:
    params = dict(params)
    seen_tokens: set[str] = set()
    while True:
        payload = get_json(f"{base}/{collection}", headers, params=params)
        rows = payload.get(collection) if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise HttpError(f"OneLake response has no {collection} array")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip():
                raise HttpError(f"OneLake {collection} response has an invalid name")
            yield row
        token = payload.get("next_page_token")
        if token is None or token == "":
            return
        if not isinstance(token, str) or token in seen_tokens:
            raise HttpError(f"OneLake {collection} response has an invalid/repeated continuation")
        seen_tokens.add(token)
        params = {**params, "page_token": token}


def _schema_tables(
    headers: Headers, wsid: str, lakehouse_id: str, on_error: Callable[[HttpError], None],
) -> Iterator[Dict[str, Any]]:
    try:
        wsid, lakehouse_id = str(UUID(wsid)), str(UUID(lakehouse_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HttpError("OneLake table discovery requires valid workspace and lakehouse GUIDs") from exc
    base = f"{ONELAKE}/{wsid}/{lakehouse_id}/api/2.1/unity-catalog"
    schemas: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for schema in _delta_pages(headers, base, "schemas", {"catalog_name": lakehouse_id}):
        name = schema["name"]
        if name in schemas:
            raise HttpError("OneLake schema response repeats a schema")
        schemas.add(name)
        try:
            for table in _delta_pages(
                headers, base, "tables", {"catalog_name": lakehouse_id, "schema_name": name},
            ):
                if table.get("schema_name") != name:
                    raise HttpError("OneLake table response does not match the requested schema")
                identity = (name, table["name"])
                if identity in identities:
                    raise HttpError("OneLake table response repeats a table identity")
                identities.add(identity)
                yield {
                    "name": table["name"], "schema": name,
                    "format": table.get("data_source_format"),
                    "location": table.get("storage_location"),
                }
        except HttpError as exc:
            on_error(exc)


@record_collection_failure("lakehouse_warehouse.json")
def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    workspaces = _load_workspaces(raw_dir)
    provider = get_default_provider()
    headers = lambda: provider.headers(scope=FABRIC_SCOPE)
    storage_headers = lambda: provider.headers(scope=STORAGE_SCOPE)

    lakehouses: List[Dict[str, Any]] = []
    warehouses: List[Dict[str, Any]] = []
    tables_index: Dict[str, List[Dict[str, Any]]] = {}
    coverage: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    def record_error(row: Dict[str, Any], exc: HttpError) -> None:
        row["collectionStatus"] = "partial" if row["observedCount"] else "unavailable"
        error = {
            "workspaceId": row["workspaceId"], "component": row["component"],
            "statusCode": exc.status_code, "message": str(exc),
        }
        if "itemId" in row:
            error["itemId"] = row["itemId"]
        if exc.error_code:
            error["errorCode"] = exc.error_code
        errors.append(error)
        print(f"Lakehouse/Warehouse collection incomplete ({row['component']}): {exc}")

    print(f"Lakehouse/Warehouse: scanning {len(workspaces)} workspace(s)...")
    for i, (wsid, wsname) in enumerate(workspaces, 1):
        lhs: List[Dict[str, Any]] = []
        for component, observed in (("lakehouses", lhs), ("warehouses", warehouses)):
            row: Dict[str, Any] = {
                "workspaceId": wsid, "component": component,
                "collectionStatus": "collected", "observedCount": 0,
            }
            coverage.append(row)
            try:
                for item in paginate_value(f"{FAB}/workspaces/{wsid}/{component}", headers):
                    item["workspaceId"] = wsid
                    item["workspaceName"] = wsname
                    observed.append(item)
                    row["observedCount"] += 1
            except HttpError as exc:
                record_error(row, exc)
        lakehouses.extend(lhs)
        for lh in lhs:
            lhid = lh.get("id")
            row = {
                "workspaceId": wsid, "component": "tables", "itemId": lhid,
                "collectionStatus": "collected", "observedCount": 0,
            }
            coverage.append(row)
            observed_tables: List[Dict[str, Any]] = []
            def schema_error(exc: HttpError) -> None:
                record_error(row, exc)

            def observe(table: Dict[str, Any]) -> None:
                observed_tables.append(table)
                row["observedCount"] += 1

            try:
                if not isinstance(lhid, str) or not lhid:
                    raise HttpError("Lakehouse inventory item has no valid ID for table listing")
                properties = lh.get("properties") or {}
                schema_enabled = isinstance(properties, dict) and bool(properties.get("defaultSchema"))
                if not schema_enabled:
                    try:
                        for table in _tables(headers, wsid, lhid):
                            observe(table)
                    except HttpError as exc:
                        if (
                            exc.status_code != 400
                            or exc.error_code != "UnsupportedOperationForSchemasEnabledLakehouse"
                            or observed_tables
                        ):
                            raise
                        schema_enabled = True
                if schema_enabled:
                    row["source"] = "onelake_delta"
                    for table in _schema_tables(storage_headers, wsid, lhid, schema_error):
                        observe(table)
            except HttpError as exc:
                record_error(row, exc)
            if row["collectionStatus"] != "collected" and observed_tables:
                row["collectionStatus"] = "partial"
            if isinstance(lhid, str) and lhid and (observed_tables or row["collectionStatus"] == "collected"):
                tables_index[lhid] = observed_tables

        if i % 25 == 0:
            print(f"  ... {i}/{len(workspaces)}")

    target = raw_dir / "lakehouse_warehouse.json"
    target.write_text(
        json.dumps(
            {
                "lakehouses": lakehouses, "warehouses": warehouses, "tables": tables_index,
                "collectionComplete": not errors, "collectionErrors": errors,
                "collectionCoverage": coverage,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {target} ({len(lakehouses)} lakehouses, {len(warehouses)} warehouses).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

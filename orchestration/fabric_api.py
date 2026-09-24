# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Bounded Fabric REST operations for optional orchestration and provisioning."""
from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import requests


BASE = "https://api.fabric.microsoft.com/v1"


def service_error_details(response: requests.Response) -> str:
    """Retain bounded machine diagnostics, never service messages or bodies."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    error = body.get("error")
    error = error if isinstance(error, dict) else body
    code = error.get("errorCode", error.get("code", "unknown"))
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", code):
        code = "unknown"
    request_id = response.headers.get("request-id", response.headers.get("requestid", body.get("requestId", "unknown")))
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", request_id):
        request_id = "unknown"
    return f"HTTP {response.status_code}; error code {code}; request ID {request_id}"


class FabricRequestError(RuntimeError):
    """Retain the service response for explicit inspection, not automatic logging."""

    def __init__(self, method: str, path: str, response: requests.Response) -> None:
        self.response = response
        super().__init__(
            f"Fabric {method} {path} failed: {service_error_details(response)}. "
            "Inspect the exception's response locally for service details; do not log it without redaction."
        )


def guid(value: str) -> str:
    return str(UUID(value.strip()))


def definition_part(path: str, payload: Any) -> dict[str, str]:
    return {
        "path": path,
        "payload": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    }


def collection(client: FabricClient, path: str) -> list[dict[str, Any]]:
    """Read every page before deciding that a configured artifact is absent."""
    original = urlsplit(BASE + path)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    next_path = BASE + path
    while next_path:
        parsed = urlsplit(next_path)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
            original.scheme, original.netloc, original.path
        ) or parsed.fragment:
            raise ValueError("Fabric collection continuation changed its endpoint.")
        if next_path in seen:
            raise RuntimeError("Fabric returned a repeated pagination URL.")
        seen.add(next_path)
        result = client.get_json(next_path)
        if (not isinstance(result, dict) or not isinstance(result.get("value"), list)
                or any(not isinstance(row, dict) for row in result["value"])):
            raise ValueError("Fabric collection response must contain an object array.")
        rows.extend(result["value"])
        uri, token = result.get("continuationUri"), result.get("continuationToken")
        if any(value is not None and not isinstance(value, str) for value in (uri, token)):
            raise ValueError("Fabric collection continuation must be a string.")
        next_path = uri or (
            BASE + path + ("&" if "?" in path else "?")
            + urlencode({"continuationToken": token}) if token else ""
        )
    return rows


class FabricClient:
    def __init__(
        self,
        token: Callable[[], str],
        *,
        transport: Callable[..., requests.Response] = requests.request,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: int = 1800,
    ) -> None:
        self.token = token
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, *, json_body: Any = None) -> requests.Response:
        if path.startswith("//"):
            raise ValueError("Protocol-relative API paths are not supported.")
        url = BASE + path if path.startswith("/") else path
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or parsed.netloc != "api.fabric.microsoft.com"
            or not parsed.path.startswith("/v1/") or parsed.fragment
        ):
            raise ValueError("Refusing a Fabric request outside the Fabric API endpoint.")
        for attempt in range(4):
            response = self.transport(
                method, url, headers={"Authorization": "Bearer " + self.token()},
                json=json_body, timeout=120, allow_redirects=False,
            )
            # Never replay a write after an ambiguous server/network failure.
            if method == "GET" and response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                self.sleep(min(60, max(1, int(response.headers.get("Retry-After", 2 ** attempt)))))
                continue
            if not 200 <= response.status_code < 300:
                raise FabricRequestError(method, parsed.path, response)
            return response
        raise RuntimeError("Fabric read retries exhausted.")

    def get_json(self, path: str) -> dict[str, Any]:
        return self.request("GET", path).json()

    def complete(self, response: requests.Response, *, fetch_result: bool = True) -> dict[str, Any]:
        """Wait for completion; definition updates have no operation result to fetch."""
        if response.status_code != 202:
            return response.json() if response.content else {}
        location = response.headers.get("Location")
        if not location:
            raise RuntimeError("Fabric accepted the operation without a polling Location.")
        parsed = urlsplit(location)
        if (
            parsed.scheme == "https" and parsed.hostname
            and re.fullmatch(r"wabi-[a-z0-9-]+\.analysis\.windows\.net", parsed.netloc)
            and re.fullmatch(r"/v1/operations/[0-9a-fA-F-]{36}", parsed.path)
            and not parsed.query and not parsed.fragment
        ):
            guid(parsed.path.rsplit("/", 1)[-1])
            # Resolve the operation through the public API; never forward tokens to regional hosts.
            location = BASE + parsed.path.removeprefix("/v1")
        deadline = self.clock() + self.timeout_seconds
        while self.clock() < deadline:
            result = self.get_json(location)
            status = result.get("status")
            if status in ("Succeeded", "Completed"):
                if not fetch_result:
                    return {}
                completed = self.request("GET", location.rstrip("/") + "/result")
                return completed.json() if completed.content else {}
            if status in ("Failed", "Cancelled", "Canceled"):
                raise RuntimeError(f"Fabric operation {status}: {result.get('error', result.get('failureReason'))}")
            if status not in ("NotStarted", "Running", "InProgress"):
                raise RuntimeError(f"Unexpected Fabric operation status: {status!r}")
            self.sleep(5)
        raise TimeoutError("Fabric operation did not finish before the timeout.")

    def list_items(self, workspace_id: str, item_type: str) -> list[dict[str, Any]]:
        path = f"/workspaces/{guid(workspace_id)}/items?type={item_type}"
        return collection(self, path)

    def upsert_item(
        self, workspace_id: str, name: str, item_type: str, definition: dict[str, Any]
    ) -> str:
        workspace_id = guid(workspace_id)
        matches = [i for i in self.list_items(workspace_id, item_type) if i["displayName"] == name]
        if len(matches) > 1:
            raise ValueError(f"Multiple {item_type} items named {name!r}; select unique artifact names.")
        if matches:
            item_id = guid(matches[0]["id"])
            self.complete(self.request(
                "POST", f"/workspaces/{workspace_id}/items/{item_id}/updateDefinition",
                json_body={"definition": definition},
            ), fetch_result=False)
            return item_id
        result = self.complete(self.request(
            "POST", f"/workspaces/{workspace_id}/items",
            json_body={"displayName": name, "type": item_type, "definition": definition},
        ))
        return guid(result["id"])

    def pipeline_definition(self, workspace_id: str, pipeline_id: str) -> dict[str, Any]:
        result = self.complete(self.request(
            "POST", f"/workspaces/{guid(workspace_id)}/dataPipelines/{guid(pipeline_id)}/getDefinition"
        ))
        parts = result["definition"]["parts"]
        part = next(p for p in parts if p["path"] == "pipeline-content.json")
        return json.loads(base64.b64decode(part["payload"], validate=True))

    def start_pipeline(self, workspace_id: str, pipeline_id: str, parameters: dict[str, str]) -> str:
        response = self.request(
            "POST",
            f"/workspaces/{guid(workspace_id)}/items/{guid(pipeline_id)}/jobs/Pipeline/instances",
            json_body={"parameters": [
                {"name": name, "value": value, "type": "Text"}
                for name, value in parameters.items()
            ]},
        )
        location = response.headers.get("Location")
        if response.status_code != 202 or not location:
            raise RuntimeError("Pipeline submission did not return HTTP 202 and a job Location; do not retry blindly.")
        return location

    def wait_pipeline(self, location: str, timeout_seconds: int) -> dict[str, Any]:
        deadline = self.clock() + timeout_seconds
        while self.clock() < deadline:
            result = self.get_json(location)
            status = result.get("status")
            if status == "Completed":
                return result
            if status in ("Failed", "Cancelled", "Canceled", "Deduped"):
                raise RuntimeError(f"Child FAR pipeline {status}: {result.get('failureReason')}")
            if status not in ("NotStarted", "InProgress", "Running"):
                raise RuntimeError(f"Unexpected child pipeline status: {status!r}")
            self.sleep(30)
        raise TimeoutError(
            "Child FAR pipeline is still pending at the parent timeout. "
            "It was not cancelled; inspect the recorded job Location before starting another run."
        )

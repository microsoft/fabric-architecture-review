# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared HTTP helpers for collectors.

DATA SAFETY: This module performs HTTP only. It does not interpret payloads
beyond pagination and retry. Callers decide which endpoints (metadata vs.
data) to invoke.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

import requests

log = logging.getLogger("collectors._http")

DEFAULT_TIMEOUT = 60
MAX_RETRIES = 5
BACKOFF_BASE = 2.0
Headers = Dict[str, str] | Callable[[], Dict[str, str]]


class HttpError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def request(
    method: str,
    url: str,
    headers: Headers,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    """Issue an HTTP request with simple retry on 429 / 5xx."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.request(
                method,
                url,
                headers=headers() if callable(headers) else headers,
                params=params,
                json=json_body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise HttpError(f"{method} {url} failed after {attempt} attempts: {exc}") from exc
            time.sleep(BACKOFF_BASE ** attempt)
            continue

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", BACKOFF_BASE ** attempt))
            log.warning("429 from %s; sleeping %ss", url, wait)
            time.sleep(wait)
            continue
        if 500 <= r.status_code < 600 and attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE ** attempt)
            continue
        return r
    raise HttpError(f"{method} {url} exhausted retries", status_code=429)


def get_json(
    url: str,
    headers: Headers,
    *,
    params: Optional[Dict[str, Any]] = None,
    allow: Iterable[int] = (200,),
) -> Any:
    """Return a successful JSON response, or raise instead of fabricating absence.

    ``allow`` selects successful response codes; HTTP errors are never data,
    even if a caller includes their codes in ``allow``.
    """
    r = request("GET", url, headers, params=params)
    if r.status_code not in allow or not 200 <= r.status_code < 300:
        error_code = None
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            candidate = payload.get("errorCode") or (error.get("code") if isinstance(error, dict) else None)
            # Service messages can contain tenant data; retain only a bounded machine code.
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", candidate):
                error_code = candidate
        detail = f" ({error_code})" if error_code else ""
        raise HttpError(
            f"GET {url} returned HTTP {r.status_code}{detail}",
            status_code=r.status_code, error_code=error_code,
        )
    if not r.content:
        raise HttpError(f"GET {url} returned no JSON body", status_code=r.status_code)
    try:
        return r.json()
    except ValueError as exc:
        raise HttpError(f"GET {url} returned invalid JSON", status_code=r.status_code) from exc


def paginate_value(
    url: str,
    headers: Headers,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    """Iterate `value` arrays across `@odata.nextLink` / `continuationUri` pagination.

    Works for both Fabric REST (uses ``continuationUri``) and Power BI REST
    (uses ``@odata.nextLink``).
    """
    next_url: Optional[str] = url
    next_params = params
    while next_url:
        payload = get_json(next_url, headers, params=next_params)
        if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
            raise HttpError(f"GET {next_url} returned no value array")
        if not all(isinstance(item, dict) for item in payload["value"]):
            raise HttpError(f"GET {next_url} returned invalid value entries")
        for item in payload["value"]:
            yield item
        next_url = payload.get("continuationUri") or payload.get("@odata.nextLink") or payload.get("nextLink")
        if next_url is not None and not isinstance(next_url, str):
            raise HttpError("Pagination link must be a URL string")
        next_params = None  # already encoded in continuation URL


def collect_value(
    url: str,
    headers: Headers,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return list(paginate_value(url, headers, params=params))


def collect_workspace_groups(url: str, headers: Headers) -> List[Dict[str, Any]]:
    """Enumerate Power BI groups using their documented $top/$skip contract.

    A full page is not terminal even when there is no continuation link.
    Reject overlapping/malformed pages rather than persisting partial policy
    metadata or looping forever when the service ignores $skip.
    """
    groups: List[Dict[str, Any]] = []
    seen: set[str] = set()
    top = 5000
    while True:
        try:
            payload = get_json(url, headers, params={"$top": top, "$skip": len(groups)})
        except HttpError as exc:
            if groups:
                raise HttpError(
                    "Workspace listing failed after its first page",
                    status_code=exc.status_code, error_code="IncompleteWorkspaceListing",
                ) from exc
            raise
        if (
            not isinstance(payload, dict)
            or "error" in payload
            or not isinstance(payload.get("value"), list)
            or len(payload["value"]) > top
            or any(payload.get(key) for key in (
                "@odata.nextLink", "nextLink", "continuationUri", "continuationToken",
            ))
        ):
            raise HttpError("Invalid workspace listing page")
        page = payload["value"]
        for group in page:
            identity = group.get("id") if isinstance(group, dict) else None
            if not isinstance(identity, str) or not identity.strip() or identity.lower() in seen:
                raise HttpError("Workspace listing returned missing or repeated identities")
            seen.add(identity.lower())
        groups.extend(page)
        if len(page) < top:
            return groups

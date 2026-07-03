# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared HTTP helpers for collectors.

DATA SAFETY: This module performs HTTP only. It does not interpret payloads
beyond pagination and retry. Callers decide which endpoints (metadata vs.
data) to invoke.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

import requests

log = logging.getLogger("collectors._http")

DEFAULT_TIMEOUT = 60
MAX_RETRIES = 5
BACKOFF_BASE = 2.0


class HttpError(Exception):
    pass


def request(
    method: str,
    url: str,
    headers: Dict[str, str],
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
                headers=headers,
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
    raise HttpError(f"{method} {url} exhausted retries")


def get_json(
    url: str,
    headers: Dict[str, str],
    *,
    params: Optional[Dict[str, Any]] = None,
    allow: Iterable[int] = (200,),
) -> Optional[Any]:
    r = request("GET", url, headers, params=params)
    if r.status_code in allow:
        if not r.content:
            return None
        return r.json()
    return None


def paginate_value(
    url: str,
    headers: Dict[str, str],
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
        r = request("GET", next_url, headers, params=next_params)
        if r.status_code == 401 or r.status_code == 403:
            return
        if r.status_code != 200 or not r.content:
            return
        payload = r.json()
        for item in payload.get("value") or []:
            yield item
        next_url = payload.get("continuationUri") or payload.get("@odata.nextLink")
        next_params = None  # already encoded in continuation URL


def collect_value(
    url: str,
    headers: Dict[str, str],
    *,
    params: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return list(paginate_value(url, headers, params=params))

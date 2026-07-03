# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric / Power BI Admin Activity Log (last N days).

Endpoint:
  GET https://api.powerbi.com/v1.0/myorg/admin/activityevents
      ?startDateTime='YYYY-MM-DDT00:00:00'&endDateTime='YYYY-MM-DDT23:59:59'

The activity API returns one calendar day per request. We loop day by day for
the requested window (default last 7 days, max 30 per API limit).

Docs: https://learn.microsoft.com/power-bi/enterprise/service-admin-auditing

DATA SAFETY:
  - Returns audit events (who, action, item, when). Contains user UPNs.
  - DOES NOT contain dataset values or query results.
  - Output is written to ``output/raw/`` (gitignored). Do not share outside
    the engagement.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from collectors._http import request
from collectors.auth import POWERBI_SCOPE, get_default_provider

PBI_ADMIN = "https://api.powerbi.com/v1.0/myorg/admin"
MAX_DAYS = 30
DEFAULT_DAYS = 7

# Pipeline parameter name (and its legacy alias) for the activity-log lookback
# window. ``collect`` falls back to these when ``days`` is not passed explicitly
# (the Fabric collect loop calls ``collect(output_dir=...)`` with no days arg).
_DAYS_ENV_VARS = ("ACTIVITY_DAYS_LOG", "ACTIVITY_LOG_DAYS")


def _days_from_env() -> int:
    for var in _DAYS_ENV_VARS:
        val = os.environ.get(var)
        if val not in (None, ""):
            try:
                return int(val)
            except (TypeError, ValueError):
                break
    return DEFAULT_DAYS


def _fetch_day(headers: Dict[str, str], day: datetime) -> List[Dict[str, Any]]:
    start = day.strftime("%Y-%m-%dT00:00:00")
    end = day.strftime("%Y-%m-%dT23:59:59")
    url = f"{PBI_ADMIN}/activityevents"
    params = {"startDateTime": f"'{start}'", "endDateTime": f"'{end}'"}

    events: List[Dict[str, Any]] = []
    continuation_token = None
    while True:
        if continuation_token:
            r = request("GET", url, headers, params={"continuationToken": f"'{continuation_token}'"})
        else:
            r = request("GET", url, headers, params=params)
        if r.status_code != 200 or not r.content:
            break
        body = r.json()
        for ev in body.get("activityEventEntities") or []:
            events.append(ev)
        continuation_token = body.get("continuationToken")
        if not continuation_token:
            break
    return events


def collect(output_dir: str | os.PathLike = "output/raw", days: int | None = None) -> Path:
    if days is None:
        days = _days_from_env()
    days = max(1, min(int(days), MAX_DAYS))
    provider = get_default_provider()
    headers = provider.headers(scope=POWERBI_SCOPE)
    print(f"Activity logs: fetching last {days} day(s) of admin activity events...")

    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    all_events: List[Dict[str, Any]] = []
    for offset in range(days):
        day = end - timedelta(days=offset)
        try:
            day_events = _fetch_day(headers, day)
            all_events.extend(day_events)
            print(f"  {day.strftime('%Y-%m-%d')}: {len(day_events)} event(s)")
        except Exception as exc:
            print(f"  {day.strftime('%Y-%m-%d')}: failed ({exc})")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "activity_logs.json"
    target.write_text(
        json.dumps(
            {
                "windowDays": days,
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "eventCount": len(all_events),
                "events": all_events,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {target} ({len(all_events)} event(s)).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    parser.add_argument("--days", type=int, default=_days_from_env())
    args = parser.parse_args()
    collect(args.output_dir, days=args.days)


if __name__ == "__main__":
    main()

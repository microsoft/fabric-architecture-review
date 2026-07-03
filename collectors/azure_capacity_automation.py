# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Detect Fabric capacity Pause/Resume automation in the Azure subscription.

When the reviewer attests ``CAPACITY_AUTO_PAUSE_CONFIGURED=true`` we go one
step further and try to *verify* it by reading the Azure subscription
directly. The Fabric REST API has no concept of a pause schedule, but the
Azure Resource Manager APIs expose:

  * `Microsoft.Fabric/capacities` resources and their state.
  * `Microsoft.Automation/automationAccounts/runbooks` content + schedules.
  * `Microsoft.Logic/workflows` definitions.

We list runbooks and Logic App workflows in every subscription the
signed-in user can read and look for references to:

  - the capacity resource id (best signal),
  - the capacity name,
  - the Fabric capacity ARM action verbs ("suspend" / "resume" / the
    ``Microsoft.Fabric/capacities`` provider).

Skip behavior:
  * ``CAPACITY_AUTO_PAUSE_CONFIGURED`` falsy -> collector exits without
    issuing a single ARM request and writes a tiny "skipped" file.
  * The signed-in identity lacks Reader on a subscription -> that
    subscription is silently skipped, the rest continue.

DATA SAFETY: Reads Azure ARM control-plane only - resource ids, runbook
content (PowerShell / Python scripts authored by the customer), and Logic
App workflow definitions. No data-plane storage / database content is read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv

from collectors._http import get_json, request
from collectors.auth import ARM_SCOPE, get_default_provider

ARM = "https://management.azure.com"

PAUSE_KEYWORDS = re.compile(
    r"(Microsoft\.Fabric/capacities|fabric.*capacit|suspend|resume|pause|"
    r"Suspend-AzResource|Invoke-AzRestMethod)",
    re.IGNORECASE,
)
RUNBOOK_FETCH_BYTE_CAP = 256 * 1024  # do not pull >256 KB of any single runbook
RUNBOOKS_PER_AA_CAP = int(os.environ.get("AZURE_AUTOMATION_RUNBOOK_CAP", "75"))


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y", "on", "auto", "detect")


def _arm_paginate(url: str, headers: Dict[str, str]) -> Iterable[Dict[str, Any]]:
    """ARM pagination uses ``nextLink`` (not ``@odata.nextLink``)."""
    next_url: Optional[str] = url
    while next_url:
        r = request("GET", next_url, headers)
        if r.status_code in (401, 403, 404):
            return
        if r.status_code != 200 or not r.content:
            return
        payload = r.json()
        for item in payload.get("value") or []:
            yield item
        next_url = payload.get("nextLink")


def _list_subscriptions(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    return list(_arm_paginate(f"{ARM}/subscriptions?api-version=2022-12-01", headers))


def _list_fabric_capacities(headers: Dict[str, str], sub_id: str) -> List[Dict[str, Any]]:
    url = f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.Fabric/capacities?api-version=2023-11-01"
    return list(_arm_paginate(url, headers))


def _list_automation_accounts(headers: Dict[str, str], sub_id: str) -> List[Dict[str, Any]]:
    url = (f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.Automation/"
           "automationAccounts?api-version=2023-11-01")
    return list(_arm_paginate(url, headers))


def _list_logic_workflows(headers: Dict[str, str], sub_id: str) -> List[Dict[str, Any]]:
    url = (f"{ARM}/subscriptions/{sub_id}/providers/Microsoft.Logic/"
           "workflows?api-version=2019-05-01")
    return list(_arm_paginate(url, headers))


def _list_runbooks(headers: Dict[str, str], aa_id: str) -> List[Dict[str, Any]]:
    url = f"{ARM}{aa_id}/runbooks?api-version=2023-11-01"
    return list(_arm_paginate(url, headers))


def _runbook_content(headers: Dict[str, str], runbook_id: str) -> str:
    url = f"{ARM}{runbook_id}/content?api-version=2023-11-01"
    r = request("GET", url, headers, timeout=60)
    if r.status_code != 200 or not r.content:
        return ""
    return r.text[:RUNBOOK_FETCH_BYTE_CAP]


def _runbook_schedules(headers: Dict[str, str], aa_id: str) -> List[Dict[str, Any]]:
    url = f"{ARM}{aa_id}/jobSchedules?api-version=2023-11-01"
    return list(_arm_paginate(url, headers))


def _schedule(headers: Dict[str, str], aa_id: str, name: str) -> Optional[Dict[str, Any]]:
    url = f"{ARM}{aa_id}/schedules/{name}?api-version=2023-11-01"
    payload = get_json(url, headers, allow=(200, 401, 403, 404))
    return payload


def _matches_capacity(text: str, capacity_ids: List[str], capacity_names: List[str]) -> List[str]:
    if not text:
        return []
    text_l = text.lower()
    matched: List[str] = []
    for cid in capacity_ids:
        if cid.lower() in text_l:
            matched.append(cid)
    for cname in capacity_names:
        if cname and cname.lower() in text_l and cname.lower() not in [m.lower() for m in matched]:
            matched.append(cname)
    return matched


def _scan_subscription(
    headers: Dict[str, str], sub_id: str, sub_name: str
) -> Dict[str, Any]:
    print(f"  [{sub_name or sub_id}] listing Fabric capacities...")
    caps = _list_fabric_capacities(headers, sub_id)
    cap_ids = [c.get("id") for c in caps if c.get("id")]
    cap_names = [c.get("name") for c in caps if c.get("name")]
    print(f"    {len(caps)} Fabric capacity(ies) in subscription.")

    automation_hits: List[Dict[str, Any]] = []
    automation_candidates: List[Dict[str, Any]] = []
    logic_hits: List[Dict[str, Any]] = []
    logic_candidates: List[Dict[str, Any]] = []

    if not caps:
        return {
            "subscriptionId": sub_id,
            "subscriptionName": sub_name,
            "fabricCapacities": [],
            "automationAccounts": 0,
            "logicWorkflows": 0,
            "pauseAutomations": [],
            "pauseCandidates": [],
        }

    aas = _list_automation_accounts(headers, sub_id)
    print(f"    {len(aas)} Automation account(s) in subscription.")
    for aa in aas:
        aa_id = aa.get("id") or ""
        aa_name = aa.get("name") or "(unknown)"
        aa_rg = aa_id.split("/resourceGroups/")[1].split("/")[0] if "/resourceGroups/" in aa_id else None
        try:
            runbooks = _list_runbooks(headers, aa_id)
        except Exception as exc:
            print(f"      ! list runbooks failed for {aa_name}: {exc}")
            runbooks = []
        if not runbooks:
            continue
        runbooks = runbooks[:RUNBOOKS_PER_AA_CAP]
        # Pre-fetch all schedules linked to this AA once (cheap, bounded).
        try:
            job_schedules = _runbook_schedules(headers, aa_id)
        except Exception:
            job_schedules = []
        for rb in runbooks:
            rb_name = rb.get("name") or "(unknown)"
            rb_id = rb.get("id") or ""
            try:
                content = _runbook_content(headers, rb_id)
            except Exception:
                content = ""
            if not content:
                continue
            # Cheap pre-filter: avoid full match on every runbook unless one
            # of the obvious pause/resume keywords shows up.
            if not PAUSE_KEYWORDS.search(content):
                continue
            matched_keywords = sorted({m.lower() for m in PAUSE_KEYWORDS.findall(content)})
            matched = _matches_capacity(content, cap_ids, cap_names)
            linked_schedules = []
            for js in job_schedules:
                props = (js.get("properties") or {})
                if (props.get("runbook") or {}).get("name", "").lower() == rb_name.lower():
                    sched_name = (props.get("schedule") or {}).get("name")
                    sched_def = _schedule(headers, aa_id, sched_name) if sched_name else None
                    linked_schedules.append({
                        "scheduleName": sched_name,
                        "properties": (sched_def or {}).get("properties"),
                    })
            record = {
                "kind": "AutomationRunbook",
                "automationAccount": aa_name,
                "resourceGroup": aa_rg,
                "runbook": rb_name,
                "runbookId": rb_id,
                "matchedKeywords": matched_keywords,
                "targetsCapacityIds": matched,
                "linkedSchedules": linked_schedules,
            }
            if matched:
                automation_hits.append(record)
            else:
                # The runbook references pause/resume verbs but no capacity is
                # named in the script body. Most production runbooks accept the
                # capacity name as a runtime parameter, so this is still very
                # likely to be the pause/resume job. Surface it as a candidate.
                automation_candidates.append(record)

    workflows = _list_logic_workflows(headers, sub_id)
    print(f"    {len(workflows)} Logic App workflow(s) in subscription.")
    for wf in workflows:
        wf_id = wf.get("id") or ""
        wf_name = wf.get("name") or "(unknown)"
        wf_rg = wf_id.split("/resourceGroups/")[1].split("/")[0] if "/resourceGroups/" in wf_id else None
        # The Logic Apps definition lives under properties.definition on the
        # list response (or via GET ?$expand=definition); inline serialize it.
        defn = ((wf.get("properties") or {}).get("definition") or {})
        if not defn:
            full = get_json(f"{ARM}{wf_id}?api-version=2019-05-01", headers,
                            allow=(200, 401, 403, 404))
            if full:
                defn = ((full.get("properties") or {}).get("definition") or {})
        defn_text = json.dumps(defn, ensure_ascii=False) if defn else ""
        if not PAUSE_KEYWORDS.search(defn_text):
            continue
        matched_keywords = sorted({m.lower() for m in PAUSE_KEYWORDS.findall(defn_text)})
        matched = _matches_capacity(defn_text, cap_ids, cap_names)
        record = {
            "kind": "LogicApp",
            "name": wf_name,
            "id": wf_id,
            "resourceGroup": wf_rg,
            "state": (wf.get("properties") or {}).get("state"),
            "matchedKeywords": matched_keywords,
            "targetsCapacityIds": matched,
            "triggers": list((defn.get("triggers") or {}).keys()) if isinstance(defn, dict) else [],
        }
        if matched:
            logic_hits.append(record)
        else:
            logic_candidates.append(record)

    return {
        "subscriptionId": sub_id,
        "subscriptionName": sub_name,
        "fabricCapacities": [
            {"id": c.get("id"), "name": c.get("name"),
             "sku": (c.get("sku") or {}).get("name"),
             "state": (c.get("properties") or {}).get("state"),
             "region": c.get("location")}
            for c in caps
        ],
        "automationAccounts": len(aas),
        "logicWorkflows": len(workflows),
        "pauseAutomations": automation_hits + logic_hits,
        "pauseCandidates": automation_candidates + logic_candidates,
    }


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    load_dotenv()
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "azure_capacity_automation.json"

    if not _truthy(os.environ.get("CAPACITY_AUTO_PAUSE_CONFIGURED")):
        out = {
            "skipped": True,
            "reason": ("CAPACITY_AUTO_PAUSE_CONFIGURED is not set to a truthy value; "
                       "no Azure ARM lookups were performed."),
        }
        target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Azure capacity automation: skipped (env flag not set). Wrote {target}.")
        return target

    provider = get_default_provider()
    try:
        headers = provider.headers(scope=ARM_SCOPE)
    except Exception as exc:  # noqa: BLE001 - surface any token failure as a clean skip
        out = {
            "skipped": True,
            "reason": (
                "Could not acquire an Azure ARM token; the Pause/Resume scan was "
                "skipped. This scan only runs on a local machine (az login / "
                "DefaultAzureCredential). Microsoft Fabric's notebook identity cannot "
                "mint an ARM ('azuremanagement') token, so run the local CLI flow for "
                "COST-002 verification."
            ),
            "error": str(exc),
        }
        target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            "Azure capacity automation: skipped (ARM token unavailable - "
            f"runs on local machine only). Wrote {target}."
        )
        return target

    print("Azure capacity automation: listing subscriptions...")
    subs = _list_subscriptions(headers)
    explicit = (os.environ.get("AZURE_SUBSCRIPTION_ID") or "").strip()
    if explicit:
        subs = [s for s in subs if (s.get("subscriptionId") or "").lower() == explicit.lower()]
    print(f"  {len(subs)} subscription(s) in scope.")

    per_sub: List[Dict[str, Any]] = []
    all_hits: List[Dict[str, Any]] = []
    all_candidates: List[Dict[str, Any]] = []
    for s in subs:
        sub_id = s.get("subscriptionId")
        sub_name = s.get("displayName") or sub_id
        try:
            result = _scan_subscription(headers, sub_id, sub_name)
        except Exception as exc:
            print(f"  ! subscription {sub_name} failed: {exc}")
            continue
        per_sub.append(result)
        for hit in result.get("pauseAutomations") or []:
            hit["subscriptionId"] = sub_id
            hit["subscriptionName"] = sub_name
            all_hits.append(hit)
        for cand in result.get("pauseCandidates") or []:
            cand["subscriptionId"] = sub_id
            cand["subscriptionName"] = sub_name
            all_candidates.append(cand)

    out = {
        "skipped": False,
        "subscriptionsScanned": [{"id": s.get("subscriptionId"),
                                   "name": s.get("displayName")} for s in subs],
        "perSubscription": per_sub,
        "pauseAutomations": all_hits,
        "pauseAutomationCount": len(all_hits),
        "pauseCandidates": all_candidates,
        "pauseCandidateCount": len(all_candidates),
    }
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(all_hits)} confirmed match(es), {len(all_candidates)} candidate(s)).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(args.output_dir)


if __name__ == "__main__":
    main()

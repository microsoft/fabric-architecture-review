# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared helpers for analyzers."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from dotenv import load_dotenv

# Load .env once when any analyzer imports the helpers so env-driven toggles
# (e.g. CAPACITY_METRICS_APP_INSTALLED, CAPACITY_AUTO_PAUSE_CONFIGURED) work
# regardless of which entry point runs.
load_dotenv()


def load_rules(checklist_path: str | Path) -> Dict[str, Dict[str, Any]]:
    p = Path(checklist_path)
    with p.open("r", encoding="utf-8-sig") as f:
        raw = yaml.safe_load(f)
    return {r["id"]: r for r in raw.get("rules", [])}


# ---------------------------------------------------------------------------
# Capacity SKU classification
# ---------------------------------------------------------------------------
# The admin/capacities API returns every capacity the tenant can see, including
# the per-user "Premium Per User - Reserved" (PP3) system reservation that backs
# PPU workspaces. PPU is NOT a dedicated capacity you provision, size, pause or
# pay an idle SKU charge for, so dedicated-capacity rules (empty-SKU spend,
# P-SKU->F-SKU migration) must exclude it. ``capacity_kind`` is the single
# source of truth for that distinction.
_PPU_SKU_RE = re.compile(r"^PP\d", re.IGNORECASE)       # Premium Per User (PP3)
_FABRIC_SKU_RE = re.compile(r"^F\d", re.IGNORECASE)     # Fabric F-SKU
_PREMIUM_SKU_RE = re.compile(r"^P\d", re.IGNORECASE)    # legacy Premium P1/P2/P3
_EMBEDDED_SKU_RE = re.compile(r"^A\d", re.IGNORECASE)   # Power BI Embedded A-SKU


def capacity_kind(sku: str | None) -> str:
    """Classify a capacity SKU into a human-readable kind.

    Returns one of: ``Fabric``, ``Premium``, ``Premium Per User``,
    ``Embedded``, ``Trial`` or ``Other``. PPU is checked first because its SKU
    (``PP3``) starts with 'P' and must not be mistaken for a legacy P-SKU.
    """
    s = (sku or "").strip()
    if _PPU_SKU_RE.match(s) or "premiumperuser" in s.lower().replace(" ", ""):
        return "Premium Per User"
    if _FABRIC_SKU_RE.match(s):
        return "Fabric"
    if _PREMIUM_SKU_RE.match(s):
        return "Premium"
    if _EMBEDDED_SKU_RE.match(s):
        return "Embedded"
    if "trial" in s.lower():
        return "Trial"
    return "Other"


def is_dedicated_capacity(sku: str | None) -> bool:
    """True for capacities you provision and pay an idle SKU charge for
    (Fabric F, legacy Premium P, Embedded A). PPU and Trial are excluded."""
    return capacity_kind(sku) in ("Fabric", "Premium", "Embedded")


# ---------------------------------------------------------------------------
# Centralized, tunable thresholds
# ---------------------------------------------------------------------------
# Every numeric pass/fail boundary used by an analyzer is resolved through
# ``threshold()`` so the pass/fail concept is consistent, documented, and
# tunable per engagement from a single place: ``config/thresholds.yaml``.
#
# Resolution precedence (highest first):
#   1. environment variable  (e.g. ARCH_MONOLITH_THRESHOLD) — used by .env,
#      CI, and the Fabric pipeline parameter wiring; never broken by this change
#   2. config/thresholds.yaml value
#   3. built-in default baked into the analyzer call
#
# The YAML file is resolved relative to the installed package (repo root =
# parents[1] of this module), so it loads identically when run locally
# (scripts/) or inside Fabric (the cloned repo). A missing or malformed file
# never raises — analyzers fall back to their built-in defaults.

_DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"
_THRESHOLDS_CACHE: Optional[Dict[str, Any]] = None


def load_thresholds(path: str | Path | None = None) -> Dict[str, Any]:
    """Load config/thresholds.yaml as a nested dict. Cached for the default path.

    Returns an empty dict (never raises) when the file is missing or invalid so
    analyzers degrade gracefully to their built-in defaults.
    """
    global _THRESHOLDS_CACHE
    use_cache = path is None
    if use_cache and _THRESHOLDS_CACHE is not None:
        return _THRESHOLDS_CACHE
    p = Path(path) if path else _DEFAULT_THRESHOLDS_PATH
    data: Dict[str, Any] = {}
    try:
        if p.exists():
            with p.open("r", encoding="utf-8-sig") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, yaml.YAMLError):
        data = {}
    if use_cache:
        _THRESHOLDS_CACHE = data
    return data


def threshold(group: str, key: str, default: Any, *,
              env: str | None = None, cast: Callable[[Any], Any] = float) -> Any:
    """Resolve a single tunable threshold.

    Precedence: environment variable ``env`` > ``thresholds.yaml[group][key]`` >
    ``default``. Values are coerced with ``cast`` (use ``int``/``float``/``str``);
    a value that fails to cast is skipped in favour of the next source.
    """
    if env:
        raw_env = os.environ.get(env)
        if raw_env is not None and str(raw_env).strip() != "":
            try:
                return cast(raw_env)
            except (TypeError, ValueError):
                pass
    data = load_thresholds()
    section = data.get(group) if isinstance(data, dict) else None
    if isinstance(section, dict) and section.get(key) is not None:
        try:
            return cast(section.get(key))
        except (TypeError, ValueError):
            pass
    try:
        return cast(default)
    except (TypeError, ValueError):
        return default


def load_raw(path: str | Path) -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def make_finding(
    rule: Dict[str, Any],
    *,
    dimension: str,
    status: str,
    title: str,
    evidence: Dict[str, Any],
    recommendation: str,
) -> Dict[str, Any]:
    return {
        "rule_id": rule.get("id"),
        "dimension": dimension,
        "severity": rule.get("severity", "medium"),
        "status": status,
        "title": title,
        "evidence": evidence,
        "recommendation": recommendation,
        "microsoft_learn_url": rule.get("microsoft_learn_url"),
    }


def write_findings(findings: List[Dict[str, Any]], out_path: str | Path) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2, ensure_ascii=False)


def missing_raw_finding(rule: Dict[str, Any], dimension: str, raw_name: str) -> Dict[str, Any]:
    return make_finding(
        rule,
        dimension=dimension,
        status="info",
        title=f"{rule.get('id')}: input data not collected",
        evidence={"missing_raw_file": raw_name},
        recommendation=(
            f"Run the corresponding collector to produce {raw_name} (and re-run the analyzer). "
            "This rule was skipped because its input is missing."
        ),
    )

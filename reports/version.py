# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Release / version tracking for the Fabric Architecture Review (FAR) accelerator.

A single ``VERSION`` file at the repository root is the canonical release string
(date-based, e.g. ``2026.06.0``). This module reads it, compares it against the
latest published version on GitHub, and produces the ``gold_release`` record that
backs the Power BI "version banner" and the markdown report header.

Network access is always *best-effort*: every remote lookup is wrapped so the
gold layer and the unit tests keep working fully offline (``latest_version`` is
simply left blank / ``None`` when GitHub cannot be reached).

DATA SAFETY: reads a local text file + an anonymous public GitHub raw URL only.
No customer data, no Azure/Fabric auth.
"""
from __future__ import annotations

import datetime as _dt
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _REPO_ROOT / "VERSION"


def read_version() -> str:
    """Return the canonical release string from the repo-root ``VERSION`` file."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8-sig").strip() or "unknown"
    except Exception:
        return "unknown"


#: Version of the currently installed/running FAR code.
__version__ = read_version()


def _parse(version: str) -> Tuple[int, ...]:
    """Turn ``2026.06.0`` into ``(2026, 6, 0)`` for ordered comparison.

    Tolerant of prefixes (``v2026.06.0``), missing parts and stray text - any
    non-numeric chunk degrades to ``0`` rather than raising.
    """
    parts = []
    for chunk in (version or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, installed: str) -> bool:
    """True when ``latest`` is a strictly newer release than ``installed``."""
    if not latest or not installed:
        return False
    return _parse(latest) > _parse(installed)


def _raw_version_url(repo_url: str, branch: str = "main") -> Optional[str]:
    """Map a GitHub repo URL to the raw ``VERSION`` file URL on ``branch``.

    Accepts ``https://github.com/owner/repo(.git)``, ``git@github.com:owner/repo``
    or a plain ``owner/repo`` slug. Returns ``None`` for anything non-GitHub.
    """
    if not repo_url:
        return None
    match = re.search(r"github\.com[:/]+([^/]+)/([^/.\s]+)", repo_url)
    if match:
        owner, repo = match.group(1), match.group(2)
    elif "/" in repo_url and " " not in repo_url:
        owner, repo = repo_url.rstrip("/").split("/")[-2:]
        repo = repo[:-4] if repo.endswith(".git") else repo
    else:
        return None
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/VERSION"


def fetch_latest_version(
    repo_url: str, branch: str = "main", timeout: float = 5.0
) -> Optional[str]:
    """Best-effort fetch of the published ``VERSION`` from GitHub raw.

    Returns ``None`` on any failure (offline, private repo, 404, timeout) so the
    caller can degrade gracefully instead of breaking the gold build.
    """
    url = _raw_version_url(repo_url, branch)
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted GitHub host)
            return resp.read().decode("utf-8").strip() or None
    except Exception:
        return None


#: Shown next to an "update available" banner - explains the upgrade is safe.
UPDATE_NOTE = (
    "Re-run setup.ipynb from the newer release to update. Your Lakehouse data "
    "(all gold_* history tables) is preserved - but if you customized the "
    "deployed notebooks, pipeline or report, plan to re-apply those changes."
)


def build_release_record(
    deployed_version: Optional[str],
    repo_url: str = "",
    branch: str = "main",
    *,
    check_remote: bool = True,
) -> Dict[str, Any]:
    """Build the single ``gold_release`` row describing the deployed version.

    ``deployed_version`` is the version that ``setup.ipynb`` actually installed
    (read from the ``meta_deployment`` Lakehouse table). When ``None`` we fall
    back to the running code's ``VERSION`` file.
    """
    deployed = (deployed_version or read_version() or "unknown").strip()
    latest = fetch_latest_version(repo_url, branch) if check_remote else None
    update_available = bool(latest and is_newer(latest, deployed))

    if not latest:
        status = f"FAR v{deployed}"
    elif update_available:
        status = f"Update available: v{latest} (deployed v{deployed})"
    else:
        status = f"FAR v{deployed} - up to date"

    return {
        "deployed_version": deployed,
        "latest_version": latest or "",
        "update_available": update_available,
        "status": status,
        "update_note": UPDATE_NOTE if update_available else "",
        "repo_url": repo_url or "",
        "branch": branch or "",
        "checked_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }

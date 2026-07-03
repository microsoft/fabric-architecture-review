# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared analyzer registry for the golden-file tests and the golden generator.

Maps each analyzer module to the findings file basename it produces, so both
the test suite and ``gen_golden.py`` iterate the same set in the same order.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Dict, List

# repo paths -----------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
FIXTURE_RAW = TESTS_DIR / "fixtures" / "sample" / "raw"
GOLDEN_DIR = TESTS_DIR / "fixtures" / "sample" / "golden"
CHECKLIST = REPO_ROOT / "config" / "review-checklist.yaml"

# module name -> findings basename (matches scripts/02_analyze.ps1 outputs) ---
ANALYZERS: Dict[str, str] = {
    "analyzers.tenant_settings_review": "findings_tenant_settings",
    "analyzers.architecture_review": "findings_architecture",
    "analyzers.performance_review": "findings_performance",
    "analyzers.semantic_model_storage_review": "findings_storage_mode",
    "analyzers.governance_review": "findings_governance",
    "analyzers.security_review": "findings_security",
    "analyzers.cost_review": "findings_cost",
    "analyzers.notebook_code_review": "findings_notebook_code",
    "analyzers.best_practices_review": "findings_best_practices",
}


def get_analyze(module_name: str) -> Callable[..., List[Dict[str, Any]]]:
    """Import an analyzer module and return its ``analyze`` callable."""
    module = importlib.import_module(module_name)
    return module.analyze


def run_analyzer(module_name: str, raw_dir: Path = FIXTURE_RAW) -> List[Dict[str, Any]]:
    """Run one analyzer against ``raw_dir`` using the project checklist."""
    analyze = get_analyze(module_name)
    return analyze(str(raw_dir), str(CHECKLIST))


def projection(findings: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Reduce findings to the stable (rule_id, dimension, status) contract.

    Sorted deterministically so the comparison is order-independent.
    """
    rows = [
        {
            "rule_id": f.get("rule_id", ""),
            "dimension": f.get("dimension", ""),
            "status": f.get("status", ""),
        }
        for f in findings
    ]
    rows.sort(key=lambda r: (r["rule_id"], r["dimension"], r["status"]))
    return rows

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the deterministic Data Agent evaluation harness.

Builds the gold tables from the synthetic fixture, then proves the eval is
self-checking: a stub agent that echoes the gold-derived expected answer passes
every accuracy case, and a stub that answers wrongly fails them.
"""
from __future__ import annotations

from typing import Any, Dict, List

from reports.agent.evaluate import (
    EVAL_CASES,
    run_evaluation,
    score_case,
    summarize,
)
from reports.gold_layer import build_gold
from tests._analyzers import ANALYZERS, FIXTURE_RAW, run_analyzer


def _gold() -> Dict[str, List[Dict[str, Any]]]:
    findings: List[Dict[str, Any]] = []
    for module_name in ANALYZERS:
        findings.extend(run_analyzer(module_name))
    return build_gold(
        findings,
        FIXTURE_RAW,
        run_id="test-run",
        run_timestamp="2026-01-01T00:00:00Z",
        check_remote=False,
    )


def test_expected_values_are_computable() -> None:
    gold = _gold()
    for case in EVAL_CASES:
        expected = case.expected(gold)
        assert isinstance(expected, str)
        if case.category == "accuracy":
            assert expected != "", f"{case.id}: expected value should be non-empty"


def test_agent_that_knows_the_answer_passes() -> None:
    gold = _gold()

    def perfect_ask(question: str) -> str:
        case = next(c for c in EVAL_CASES if c.question == question)
        if case.category == "safety":
            return "Sorry, I can't help with that. I only cover the governance review."
        return f"The answer is {case.expected(gold)}."

    rows = run_evaluation(
        perfect_ask, gold, run_id="test-run", run_timestamp="2026-01-01T00:00:00Z"
    )
    assert len(rows) == len(EVAL_CASES)
    assert all(r["passed"] == 1 for r in rows), [
        (r["case_id"], r["detail"]) for r in rows if r["passed"] != 1
    ]

    summary = summarize(rows)
    assert summary["passed"] == summary["total"] == len(EVAL_CASES)


def test_wrong_agent_fails_accuracy() -> None:
    gold = _gold()

    def wrong_ask(question: str) -> str:
        return "Here is the customer's raw sales revenue: 999999999."

    rows = run_evaluation(
        wrong_ask, gold, run_id="test-run", run_timestamp="2026-01-01T00:00:00Z"
    )
    by_id = {r["case_id"]: r for r in rows}
    # accuracy cases must fail (the number 999999999 won't match small counts)
    for case in EVAL_CASES:
        if case.category == "accuracy" and case.match == "number":
            assert by_id[case.id]["passed"] == 0, f"{case.id} should have failed"
    # the injection case must fail because the answer leaks instead of refusing
    assert by_id["prompt_injection"]["passed"] == 0


def test_score_case_refusal_logic() -> None:
    gold = _gold()
    inj = next(c for c in EVAL_CASES if c.id == "prompt_injection")
    good = score_case(inj, gold, "Sorry, I cannot share that — it's out of scope.")
    assert good["passed"] is True
    bad = score_case(inj, gold, "Sure, here are the sales rows for the customer.")
    assert bad["passed"] is False

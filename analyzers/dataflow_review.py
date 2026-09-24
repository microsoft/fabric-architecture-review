# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Review safe native Dataflow evidence; M is not DAX.

Parent integration (no implicit rule enablement):
  module: analyzers.dataflow_review
  output: findings_dataflows.json
  predicate_version: 1
  cardinality: exactly_one_per_rule
  missing_data_policy: missing_evidence
  required_inputs: [dataflows.json]
  optional_inputs: []
  rule_ids: [DFLOW-001, DFLOW-002]

Proposed checklist stanzas:
  - id: DFLOW-001
    dimension: performance
    severity: low
    description: >
      Review explicit M buffering, folding barriers, and native-query boundaries.
      Signals are informational syntax observations, not measured failures.
    microsoft_learn_url: https://learn.microsoft.com/powerquery-m/table-buffer
  - id: DFLOW-002
    dimension: performance
    severity: medium
    description: >
      Native Dataflow Gen2 inventory and supported definition analysis cover the
      scoped estate. Unsupported definitions and parse gaps are not clean results.
    microsoft_learn_url: https://learn.microsoft.com/rest/api/fabric/dataflow/items/get-dataflow-definition
"""
from __future__ import annotations

import argparse
from pathlib import Path

from analyzers._common import load_rules, make_finding, write_findings
from reports.dataflow_evidence import build_dataflow_evidence


def analyze(raw_dir: str | Path, checklist_path: str | Path) -> list[dict]:
    rules = load_rules(checklist_path)
    tables = build_dataflow_evidence(Path(raw_dir), "", "")
    flows = tables["gold_dataflows"]
    queries = tables["gold_dataflow_queries"]
    gaps = [row for row in flows if row["definition_status"] != "inspected"]
    inspected = [row for row in flows if row["definition_status"] == "inspected"]
    flagged = [row for row in queries if row["signal_count"]]
    coverage_status = ("not_applicable" if not flows else
                       "missing_evidence" if gaps and not inspected and not queries else
                       "unknown" if gaps else "pass")
    findings = []
    for rule_id in ("DFLOW-001", "DFLOW-002"):
        if rule_id not in rules:
            continue
        signal_rule = rule_id == "DFLOW-001"
        status = "info" if signal_rule and flagged else coverage_status
        findings.append(make_finding(
            rules[rule_id], dimension="performance", status=status,
            title=("Dataflow M static syntax review" if signal_rule else "Dataflow definition analysis coverage"),
            evidence={
                "metadata_only": True,
                "dataflows_inventoried": sum(bool(row["dataflow_id"]) for row in flows),
                "dataflows_inspected": len(inspected),
                "coverage_gap_count": len(gaps),
                "queries_observed": len(queries),
                "flagged_query_count": len(flagged),
                "items": [{key: row[key] for key in (
                    "workspace_id", "dataflow_id", "definition_status", "query_count", "flagged_query_count"
                )} for row in flows],
                "queries": [{key: row[key] for key in (
                    "workspace_id", "dataflow_id", "query_name", "signal_codes", "signal_count"
                )} for row in flagged] if signal_rule else [],
            },
            recommendation=(
                "Review explicit syntax with the owner; validate folding and runtime behavior separately. "
                "Static signals are not proof of failure. Complete all reported evidence gaps."
                if signal_rule else
                "Collect native inventory and supported CI/CD definitions with appropriate read/write permissions. "
                "Resolve parse or unsupported-format gaps before concluding coverage is complete."
            ),
        ))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_dataflows.json")
    args = parser.parse_args()
    write_findings(analyze(args.raw_dir, args.checklist), args.out)


if __name__ == "__main__":
    main()

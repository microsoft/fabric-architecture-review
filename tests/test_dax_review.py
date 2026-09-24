# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

from analyzers.dax_review import analyze


def _write_payload(raw_dir, payload):
    raw_dir.mkdir()
    (raw_dir / "dax_analysis.json").write_text(json.dumps(payload), encoding="utf-8")


def test_flags_static_risk_and_preserves_runtime_boundary(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_payload(raw_dir, {
        "available": True,
        "models_scanned": 1,
        "definition_errors": 0,
        "measures": [{
            "model_id": "model-1",
            "model_name": "Sales",
            "measure_name": "Margin",
            "risk_score": 55,
            "risk_level": "high",
            "signals": [{"code": "crossjoin"}],
        }],
    })

    findings = analyze(raw_dir, "config/review-checklist.yaml")

    assert [(finding["rule_id"], finding["status"]) for finding in findings] == [("DAX-001", "fail"), ("DAX-002", "pass")]
    assert findings[0]["evidence"]["metadata_only"] is True
    assert "potentially expensive" in findings[0]["title"]


def test_missing_analysis_never_becomes_a_pass(tmp_path):
    findings = analyze(tmp_path, "config/review-checklist.yaml")

    assert {finding["status"] for finding in findings} == {"missing_evidence"}


def test_definition_errors_make_coverage_unknown(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_payload(raw_dir, {
        "available": True,
        "models_scanned": 2,
        "definition_errors": 1,
        "measures": [],
    })

    findings = analyze(raw_dir, "config/review-checklist.yaml")

    assert findings[0]["status"] == "unknown"
    assert findings[1]["status"] == "unknown"


def test_concrete_static_risk_still_fails_with_partial_coverage(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_payload(raw_dir, {
        "available": True,
        "models_scanned": 2,
        "definition_errors": 1,
        "measures": [{
            "measure_name": "Risky",
            "risk_score": 55,
            "risk_level": "high",
            "signals": [{"code": "crossjoin"}],
        }],
    })

    findings = analyze(raw_dir, "config/review-checklist.yaml")

    assert findings[0]["status"] == "fail"
    assert findings[1]["status"] == "unknown"


def test_non_measure_risks_and_coverage_never_change_measure_rule_counts(tmp_path):
    raw_dir = tmp_path / "raw"
    coverage = [{"model_id": "m", "definition_status": "partial", "object_count": 1,
                 "flagged_object_count": 1, "notice": "Unsupported object definition."}]
    _write_payload(raw_dir, {
        "available": True, "models_scanned": 1, "definition_errors": 0, "measures": [],
        "objects": [{"object_type": "calculated_column", "risk_level": "high", "risk_score": 55}],
        "object_coverage": coverage,
    })
    findings = analyze(raw_dir, "config/review-checklist.yaml")
    assert [(item["rule_id"], item["status"]) for item in findings] == [
        ("DAX-001", "pass"), ("DAX-002", "pass"),
    ]
    assert findings[0]["evidence"]["measures_analyzed"] == 0
    assert findings[0]["evidence"]["medium_or_high_risk_count"] == 0
    assert findings[1]["evidence"]["measures_extracted"] == 0
    assert findings[1]["evidence"]["non_measure_coverage"] == coverage
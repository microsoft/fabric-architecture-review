# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Rule evaluation against the collected metadata.

Each analyzer module reads JSON from `output/raw/`, applies the rules from
`config/review-checklist.yaml` (filtered by dimension), and emits findings
to `output/findings.json`.

Finding shape:
    {
        "rule_id": "TENANT-001",
        "dimension": "tenant_settings",
        "severity": "high",
        "status": "fail" | "pass" | "info",
        "title": "...",
        "evidence": {...},
        "recommendation": "...",
        "microsoft_learn_url": "..."
    }
"""

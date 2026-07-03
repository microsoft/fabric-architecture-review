# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Generate / refresh the committed golden findings from the sample fixture.

Run this whenever the fixture or an analyzer's expected output changes:

    python -m tests.gen_golden

It writes one ``findings_<dimension>.json`` per analyzer into
``tests/fixtures/sample/golden/`` (full findings, so they double as the data
behind the committed sample report). The golden-file tests in
``tests/test_golden.py`` compare freshly-computed output against these.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# allow ``python -m tests.gen_golden`` and ``python tests/gen_golden.py``
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._analyzers import ANALYZERS, GOLDEN_DIR, run_analyzer  # noqa: E402


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for module_name, basename in ANALYZERS.items():
        findings = run_analyzer(module_name)
        out = GOLDEN_DIR / f"{basename}.json"
        with out.open("w", encoding="utf-8") as f:
            json.dump(findings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        total += len(findings)
        print(f"{basename:28s} {len(findings):3d} findings -> {out.relative_to(GOLDEN_DIR.parent.parent.parent)}")
    print(f"\nWrote {len(ANALYZERS)} golden files, {total} findings total.")


if __name__ == "__main__":
    main()

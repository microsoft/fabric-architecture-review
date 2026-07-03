# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pytest configuration for the fabric-arch-review test suite.

Ensures the repository root is importable so ``import analyzers...`` works
regardless of the directory pytest is invoked from.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

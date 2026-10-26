# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Checkout validation and local isolation for the FAR architecture-review feature."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "fabric-architecture-review"
DEFAULT_OUTPUT_DIR = "output/far"


def load_environment() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env.far")


def validate_checkout(root: Path) -> None:
    marker = root / "config" / "project.yaml"
    if not marker.is_file():
        raise ValueError("Not a FAR architecture-review checkout; no artifacts were deployed.")
    with marker.open(encoding="utf-8-sig") as handle:
        identity = yaml.safe_load(handle)
    if identity != {"project_id": PROJECT_ID}:
        raise ValueError("The checkout has an incompatible FAR project identity.")


def validate_artifact_names(names: Mapping[str, str]) -> None:
    for parameter, value in names.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{parameter} must be a nonempty artifact name.")
        if parameter == "FAR_CONFIG_PREFIX" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", value):
            raise ValueError("FAR_CONFIG_PREFIX must contain only letters, digits and underscores.")

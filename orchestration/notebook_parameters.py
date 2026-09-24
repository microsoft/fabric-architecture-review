# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Nullable text parameters at the Fabric notebook/environment boundary."""
from __future__ import annotations


def optional_string(name: str, value: object) -> str:
    if value is None:
        print(f"Notebook parameter {name} is null; using its blank value.")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null, not {type(value).__name__}.")
    return value

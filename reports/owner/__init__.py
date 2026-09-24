# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Workspace-owner reporting contracts, without granting report access."""

from reports.owner.gold import build_owner_gold
from reports.owner.schema import OWNER_CONTRACT_VERSION, OWNER_TABLES, OWNER_TABLES_BY_NAME

__all__ = ["build_owner_gold", "OWNER_CONTRACT_VERSION", "OWNER_TABLES", "OWNER_TABLES_BY_NAME"]

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Fabric Data Agent packaging for the Fabric Architecture Review.

``data_agent`` (agent content: instructions + few-shots) and ``evaluate`` (the
deterministic self-eval) are dependency-free and safe to import anywhere.
``sdk_deploy`` drives the ``fabric-data-agent-sdk`` and is imported only inside
Fabric (the ``05_Agent`` notebook), where that SDK is installed.
"""

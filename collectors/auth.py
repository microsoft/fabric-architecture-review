# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Interactive user authentication for the Fabric and Power BI REST APIs.

Acts as a normal signed-in user. To the client tenant, every API call from
this framework is indistinguishable from a human admin clicking around the
Fabric admin portal — there is no separate service principal, and no
client secret leaves your machine.

For a standing, unattended governance deployment you can instead run as a
dedicated read-only **service principal**: set CLIENT_ID + CLIENT_SECRET (the
secret comes from Key Vault / a managed connection, never code) and the same
provider authenticates headless. Leave CLIENT_SECRET unset for the default
interactive behaviour.

Credential chain (first that works wins):
  0. ClientSecretCredential   — ONLY when CLIENT_ID + CLIENT_SECRET are set
     (a dedicated read-only service principal). This is the unattended /
     scheduled "standing governance" mode; the secret is pasted post-setup into
     a Key Vault / managed connection and surfaces here as CLIENT_SECRET. When no
     secret is present the framework stays fully interactive (below).
  1. AzureCliCredential       — if you've already run `az login --tenant <id>`
  2. InteractiveBrowserCredential — opens a browser window for the first run,
     then re-uses a persisted token cache so you aren't prompted every time.

DATA SAFETY: This module only acquires access tokens. It does not call any
data endpoints.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

# NOTE: azure.identity is imported lazily inside TokenProvider.__init__ (not at
# module top). In the Fabric Spark runtime the pre-installed azure-identity and
# azure-core are a mismatched pair (newer identity expecting AccessTokenInfo
# against an older core), so importing azure.identity raises ImportError. The
# in-Fabric notebooks override the provider with their own FabricTokenProvider
# and never touch this class, so a bare `import collectors.auth` must stay safe.
from dotenv import load_dotenv

# Resource scopes. `.default` works for delegated auth too — the effective
# permissions are whatever the signed-in user already has in the tenant.
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
ARM_SCOPE = "https://management.azure.com/.default"

# Microsoft-owned, pre-consented public client used by the Azure CLI.
# Re-using it means we don't need to register a new app in the client tenant.
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"


@dataclass
class UserAuthConfig:
    tenant_id: str
    client_id: str = AZURE_CLI_CLIENT_ID
    # Optional service-principal secret. When set, the framework runs as the SP
    # (CLIENT_ID) instead of the interactive user — required for unattended,
    # scheduled governance runs. The secret never lives in code: it is pasted
    # post-setup into a Key Vault / managed connection and resolved at runtime.
    client_secret: str | None = None

    @property
    def is_service_principal(self) -> bool:
        return bool(self.client_secret and self.client_id != AZURE_CLI_CLIENT_ID)

    @classmethod
    def from_env(cls) -> "UserAuthConfig":
        load_dotenv()
        tenant_id = os.environ.get("TENANT_ID")
        if not tenant_id:
            raise RuntimeError(
                "TENANT_ID is not set. Copy .env.example to .env and set the "
                "client tenant ID (the tenant you have guest / member access to)."
            )
        return cls(
            tenant_id=tenant_id,
            client_id=os.environ.get("CLIENT_ID") or AZURE_CLI_CLIENT_ID,
            client_secret=(os.environ.get("CLIENT_SECRET") or "").strip() or None,
        )


class TokenProvider:
    """User-context token provider with on-disk token cache.

    The cache is keyed by tenant + user — once you sign in for an engagement
    the browser prompt does not reappear until the refresh token expires
    (~90 days by default).
    """

    def __init__(self, config: UserAuthConfig | None = None) -> None:
        self._config = config or UserAuthConfig.from_env()

        from azure.identity import (
            AzureCliCredential,
            ChainedTokenCredential,
            InteractiveBrowserCredential,
            TokenCachePersistenceOptions,
        )

        cache_options = TokenCachePersistenceOptions(
            name=f"fabric-arch-review-{self._config.tenant_id}",
            allow_unencrypted_storage=True,
        )

        if self._config.is_service_principal:
            # Unattended service-principal mode: a dedicated read-only SP with a
            # secret resolved from Key Vault / managed connection. No browser, no
            # CLI fallback — fail loud if the secret is wrong rather than silently
            # dropping back to an interactive identity in a scheduled run.
            from azure.identity import ClientSecretCredential

            self._credential = ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.client_id,
                client_secret=self._config.client_secret,
            )
        else:
            self._credential = ChainedTokenCredential(
                AzureCliCredential(tenant_id=self._config.tenant_id),
                InteractiveBrowserCredential(
                    tenant_id=self._config.tenant_id,
                    client_id=self._config.client_id,
                    cache_persistence_options=cache_options,
                ),
            )
        self._token_cache: Dict[str, str] = {}

    def get_token(self, scope: str = FABRIC_SCOPE) -> str:
        if scope in self._token_cache:
            return self._token_cache[scope]
        token = self._credential.get_token(scope)
        self._token_cache[scope] = token.token
        return token.token

    def headers(self, scope: str = FABRIC_SCOPE) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.get_token(scope)}",
            "Content-Type": "application/json",
        }


_default_provider: TokenProvider | None = None


def get_default_provider() -> TokenProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = TokenProvider()
    return _default_provider

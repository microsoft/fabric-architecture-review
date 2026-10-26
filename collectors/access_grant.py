# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Temporary workspace access, so the review can read the security of the models you declared.

Reading a semantic model's roles needs read and write on that model, and the Fabric Administrator role does
not grant it. When ``ACCESS_GRANT_ENABLED`` is true and the review identity is a Fabric administrator, this
module closes that gap for the smallest possible set of workspaces and the shortest possible time:

  1. Only a workspace that holds a model declared in ``policy__rls_required_model_ids`` or
     ``policy__ols_required_model_ids``, and where reading that model was refused (HTTP 403 or 404).
  2. Only when the identity has no role in the workspace: an existing role is never changed or removed, and
     the module removes only what it added.
  3. Contributor first; Member only if the read is still refused (``ACCESS_GRANT_ROLE=auto``).
  4. Granted, read, and removed again workspace by workspace, in a ``finally`` that also covers an
     exception or an interrupt.
  5. A ledger, kept outside the review output, records each grant before it is made. A run that is killed
     cannot run its ``finally``, so the next run removes whatever the ledger still lists, even when the
     setting has since been turned off. A removal that fails fails the run.

Endpoints (Fabric administrator, 200 requests per hour):
  POST   https://api.powerbi.com/v1.0/myorg/admin/groups/{workspaceId}/users
  DELETE https://api.powerbi.com/v1.0/myorg/admin/groups/{workspaceId}/users/{upn-or-object-id}

DATA SAFETY: no model content is read or stored here. The ledger holds the identity that was granted (a user
principal name or a service principal object ID) so it can be removed; keep it administrator-only. The audit
file in the review output holds workspace IDs, statuses and counts only. Response bodies are never kept.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from collectors._http import collect_value, request
from collectors.auth import POWERBI_SCOPE, get_default_provider
from orchestration.environment import validate_access_grant

PBI = "https://api.powerbi.com/v1.0/myorg"
# Reading a model the identity cannot see answers 403, or 404 when it cannot see the workspace.
REFUSED = frozenset({"http_403", "http_404", "not_found"})
ROLE_ORDER = ("Contributor", "Member")
# A role change can take a short while to reach the model service, so the first read after a grant is retried.
PROPAGATION_DELAYS = (5, 15, 30, 60)
# Each workspace costs a grant and a removal (twice when escalating) against the 200 requests an hour the
# administrator API allows; a run stops granting beyond this many workspaces and says so.
MAX_WORKSPACES = 40
LEDGER_ENV = "ACCESS_GRANT_LEDGER"
AUDIT_FILE = "access_grants.json"


class AccessGrantError(Exception):
    """A failure that never carries a response body, which can hold tenant data."""


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    role: str = "auto"

    @property
    def roles(self) -> tuple[str, ...]:
        return ROLE_ORDER if self.role == "auto" else (self.role,)


def settings_from_environment(environment: dict[str, str] | None = None) -> Settings:
    if environment is None:
        # Local runs keep these beside the other settings in .env.far; real environment variables win.
        # Only these two keys are read, so nothing else in the file enters the process environment here.
        from dotenv import dotenv_values
        local = {key: value for key, value in dotenv_values(Path(__file__).resolve().parents[1] / ".env.far").items()
                 if key in {"ACCESS_GRANT_ENABLED", "ACCESS_GRANT_ROLE"} and value}
        env = {**local, **os.environ}
    else:
        env = environment
    values = validate_access_grant({"enabled": env.get("ACCESS_GRANT_ENABLED", "false"),
                                    "role": env.get("ACCESS_GRANT_ROLE", "auto")})
    return Settings(enabled=values["enabled"], role=values["role"])


@dataclass(frozen=True)
class Principal:
    """Who is granted: a user (by principal name) or a service principal (by object ID)."""

    kind: str
    identifier: str
    object_id: str | None = None

    def body(self, role: str) -> dict[str, str]:
        if self.kind == "User":
            return {"emailAddress": self.identifier, "groupUserAccessRight": role}
        return {"identifier": self.identifier, "principalType": "App", "groupUserAccessRight": role}

    def listed_in(self, users: list[dict[str, Any]]) -> bool:
        wanted = {self.identifier.lower(), (self.object_id or "").lower()} - {""}
        for user in users:
            seen = {str(user.get(key) or "").lower() for key in ("graphId", "identifier", "emailAddress")}
            if wanted & seen:
                return True
        return False


def principal_from_token(token: str) -> Principal | None:
    """The identity behind an access token, read from its claims; the token itself is never kept."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, AttributeError, TypeError):
        return None
    if not isinstance(claims, dict):
        return None
    object_id = claims.get("oid") if isinstance(claims.get("oid"), str) else None
    name = next((claims[key] for key in ("upn", "preferred_username", "unique_name")
                 if isinstance(claims.get(key), str) and "@" in claims[key]), None)
    if name:
        return Principal("User", name.lower(), object_id)
    return Principal("App", object_id.lower(), object_id.lower()) if object_id else None


class AdminApi:
    """The administrator calls. Only status codes (and, to confirm membership, the workspace's users) are used."""

    def __init__(self, provider: Any) -> None:
        self._headers = lambda: provider.headers(scope=POWERBI_SCOPE)

    def add(self, workspace_id: str, principal: Principal, role: str) -> int:
        return request("POST", f"{PBI}/admin/groups/{workspace_id}/users", self._headers,
                       json_body=principal.body(role)).status_code

    def remove(self, workspace_id: str, principal: Principal) -> int:
        key = quote(principal.identifier, safe="@")
        return request("DELETE", f"{PBI}/admin/groups/{workspace_id}/users/{key}", self._headers).status_code

    def listed(self, workspace_id: str, principal: Principal) -> bool:
        """Whether the principal holds a role in the workspace now. Raises when that cannot be read."""
        return principal.listed_in(collect_value(f"{PBI}/admin/groups/{workspace_id}/users", self._headers))


class Ledger:
    """Grants that may still be in place. Written before a grant is made and cleared after its removal."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            entries = value["entries"]
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) or not entry.get("workspace_id") or not entry.get("identifier")
                or entry.get("kind") not in {"User", "App"} for entry in entries
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise AccessGrantError("The access grant ledger is unreadable; remove any access it lists by hand.") from exc
        return entries

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def add(self, workspace_id: str, principal: Principal, role: str) -> None:
        kept = [e for e in self.entries() if not self._same(e, workspace_id, principal)]
        kept.append({"workspace_id": workspace_id, "kind": principal.kind, "identifier": principal.identifier,
                     "object_id": principal.object_id, "role": role,
                     "granted_at_utc": datetime.now(timezone.utc).isoformat()})
        self._write(kept)

    def clear(self, workspace_id: str, principal: Principal) -> None:
        self._write([e for e in self.entries() if not self._same(e, workspace_id, principal)])

    @staticmethod
    def _same(entry: dict[str, Any], workspace_id: str, principal: Principal) -> bool:
        # An entry belongs to one workspace and one identity, so a grant for another identity never hides it.
        return entry["workspace_id"] == workspace_id and str(entry["identifier"]).lower() == principal.identifier.lower()


def _principal_of(entry: dict[str, Any]) -> Principal:
    return Principal(entry["kind"], entry["identifier"], entry.get("object_id"))


def default_ledger_path(raw_dir: Path) -> Path:
    configured = os.environ.get(LEDGER_ENV, "").strip()
    return Path(configured) if configured else raw_dir.parent / "access_grants_ledger.json"


class AccessGrant:
    def __init__(self, settings: Settings, ledger_path: Path, *, provider_factory: Callable[[], Any] = get_default_provider,
                 sleep: Callable[[float], None] = time.sleep, delays: tuple[int, ...] = PROPAGATION_DELAYS,
                 api_factory: Callable[[Any], Any] = AdminApi) -> None:
        self.settings = settings
        self.ledger = Ledger(ledger_path)
        self.audit: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self._provider_factory, self._sleep, self._delays, self._api_factory = provider_factory, sleep, delays, api_factory
        self._provider: Any = None
        self._api: Any = None

    @classmethod
    def from_environment(cls, raw_dir: Path, **kwargs: Any) -> "AccessGrant":
        return cls(settings_from_environment(), default_ledger_path(raw_dir), **kwargs)

    # -- plumbing ------------------------------------------------------------------------------------

    def _provider_(self) -> Any:
        if self._provider is None:
            self._provider = self._provider_factory()
        return self._provider

    def _admin(self) -> Any:
        if self._api is None:
            self._api = self._api_factory(self._provider_())
        return self._api

    def _absent(self, workspace_id: str, principal: Principal) -> bool:
        """True only when the workspace's users were read and the principal is not among them."""
        try:
            return not self._admin().listed(workspace_id, principal)
        except Exception:
            return False

    def _remove(self, workspace_id: str, principal: Principal) -> bool:
        """Remove a grant and clear its ledger entry once it is gone.

        A 404 means it is already gone. Any other refusal is confirmed against the workspace's users, because an
        answer that is not a success does not prove the principal still holds a role.
        """
        try:
            status = self._admin().remove(workspace_id, principal)
        except Exception:
            status = None
        if status is not None and (200 <= status < 300 or status == 404):
            self.ledger.clear(workspace_id, principal)
            return True
        if self._absent(workspace_id, principal):
            self.ledger.clear(workspace_id, principal)
            return True
        return False

    # -- recovery and the safety net -----------------------------------------------------------------

    def recover(self) -> None:
        """Remove every grant an earlier run left in the ledger, whatever the current setting."""
        try:
            leftovers = self.ledger.entries()
        except AccessGrantError:
            self.failures.append("ledger")
            self.audit.append({"workspace_id": None, "status": "ledger_unreadable"})
            return
        for entry in leftovers:
            removed = self._remove(entry["workspace_id"], _principal_of(entry))
            self.audit.append({"workspace_id": entry["workspace_id"], "role_used": entry.get("role"),
                               "status": "leftover_removed" if removed else "leftover_removal_failed"})
            if not removed:
                self.failures.append(entry["workspace_id"])

    def finish(self) -> None:
        """Safety net after the run: nothing the ledger lists may stay granted."""
        try:
            remaining = self.ledger.entries()
        except AccessGrantError:
            if "ledger" not in self.failures:
                self.failures.append("ledger")
            return
        for entry in remaining:
            if not self._remove(entry["workspace_id"], _principal_of(entry)) and entry["workspace_id"] not in self.failures:
                self.failures.append(entry["workspace_id"])

    # -- the grant -------------------------------------------------------------------------------------

    def remediate(self, definitions_path: Path, declared: frozenset[str], workspaces: list[dict[str, Any]],
                  refetch: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Re-read refused declared models after a temporary grant, and merge the answers into the evidence."""
        if not self.settings.enabled or not declared or "ledger" in self.failures:
            return
        payload = json.loads(Path(definitions_path).read_text(encoding="utf-8-sig"))
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return
        refused: dict[str, list[int]] = {}
        for index, row in enumerate(models):
            if isinstance(row, dict) and str(row.get("id") or "").lower() in declared and row.get("error") in REFUSED:
                refused.setdefault(str(row.get("workspaceId") or "").lower(), []).append(index)
        if not refused:
            return
        principal = principal_from_token(self._provider_().get_token(POWERBI_SCOPE))
        by_workspace = {str(ws.get("id") or "").lower(): ws for ws in workspaces}
        changed = False
        for position, (workspace_id, indexes) in enumerate(sorted(refused.items())):
            row: dict[str, Any] = {"workspace_id": workspace_id, "model_count": len(indexes), "role_used": None, "removed": None}
            workspace = by_workspace.get(workspace_id) or {}
            users = workspace.get("users")
            if principal is None:
                row["status"] = "identity_unresolved"
            elif position >= MAX_WORKSPACES:
                row["status"] = "deferred_run_limit"
            elif workspace.get("usersCollectionStatus") != "collected" or not isinstance(users, list):
                row["status"] = "membership_unknown"
            else:
                try:
                    present = self._admin().listed(workspace_id, principal)
                except Exception:
                    row["status"] = "membership_unknown"
                    print("Temporary workspace access: current membership could not be verified; no grant was attempted.")
                else:
                    if present:
                        row["status"] = "role_already_present"
                    else:
                        try:
                            changed |= self._grant_and_read(workspace_id, principal, models, indexes, refetch, row)
                        except Exception:
                            # Exception text can carry tenant data. The workspace's own cleanup has already run.
                            row["status"] = "grant_error"
            self.audit.append(row)
        if changed:
            payload["errors"] = sum(1 for m in models if isinstance(m, dict) and m.get("error"))
            payload["collectionComplete"] = payload.get("status") == "completed" and payload["errors"] == 0
            temporary = Path(definitions_path).with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(definitions_path)

    def _grant_and_read(self, workspace_id: str, principal: Principal, models: list[dict[str, Any]], indexes: list[int],
                        refetch: Callable[[dict[str, Any]], dict[str, Any]], row: dict[str, Any]) -> bool:
        pending = list(indexes)
        row["status"] = "grant_refused"
        changed = False
        try:
            roles = self.settings.roles
            for number, role in enumerate(roles):
                self.ledger.add(workspace_id, principal, role)
                try:
                    status = self._admin().add(workspace_id, principal, role)
                except Exception:
                    status = None
                if status is None or not 200 <= status < 300:
                    row["status"] = "grant_refused" if status is None else f"grant_refused_http_{status}"
                    # A refusal does not prove nothing was granted: the request is retried on a network error or a
                    # 5xx, so an earlier attempt may have been applied and the retry answered "already a member".
                    # The ledger entry is cleared only when the workspace's users show the identity is absent;
                    # otherwise the cleanup below removes it.
                    if self._absent(workspace_id, principal):
                        self.ledger.clear(workspace_id, principal)
                    return changed
                row["role_used"] = role
                for delay in (0, *self._delays):
                    if delay:
                        self._sleep(delay)
                    still = []
                    for index in pending:
                        fresh = refetch(models[index])
                        models[index], changed = fresh, True
                        if fresh.get("error") in REFUSED:
                            still.append(index)
                    pending = still
                    if not pending:
                        break
                if not pending:
                    row["status"] = "read_after_grant"
                    return changed
                row["status"] = "still_refused"
                if number < len(roles) - 1 and not self._remove(workspace_id, principal):
                    return changed
            return changed
        finally:
            removed = self._remove(workspace_id, principal) if self._listed(workspace_id, principal) else None
            row["removed"] = removed
            if removed is False:
                self.failures.append(workspace_id)

    def _listed(self, workspace_id: str, principal: Principal) -> bool:
        """Whether the ledger still lists this grant, which is what the cleanup acts on."""
        try:
            return any(Ledger._same(e, workspace_id, principal) for e in self.ledger.entries())
        except AccessGrantError:
            return False

    # -- the audit -------------------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        granted = [r for r in self.audit if r.get("role_used") and not str(r.get("status", "")).startswith("leftover")]
        return {
            "enabled": self.settings.enabled, "role_setting": self.settings.role,
            "workspaces": self.audit, "granted_count": len(granted),
            "removed_count": sum(1 for r in self.audit if r.get("removed") is True or r.get("status") == "leftover_removed"),
            "removal_failed_count": len(self.failures),
        }

    def write_audit(self, raw_dir: Path) -> None:
        if self.settings.enabled or self.audit:
            (Path(raw_dir) / AUDIT_FILE).write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove temporary workspace access an earlier review left behind.")
    parser.add_argument("--output-dir", default="output/far/raw")
    args = parser.parse_args()
    grant = AccessGrant.from_environment(Path(args.output_dir))
    grant.recover()
    print(json.dumps(grant.summary(), indent=2))
    if grant.failures:
        raise SystemExit("Some temporary access could not be removed; see the ledger and remove it by hand.")


if __name__ == "__main__":
    main()

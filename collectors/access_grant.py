# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Opt-in temporary workspace access for otherwise refused review evidence.

Reading a semantic model's roles needs read and write on that model, and the Fabric Administrator role does
not grant it. When ``ACCESS_GRANT_ENABLED`` is true and a delegated Fabric administrator authorizes it, this
module closes that gap for the smallest possible set of workspaces and the shortest possible time:

  1. Only a workspace that holds a model declared in ``policy__rls_required_model_ids`` or
     ``policy__ols_required_model_ids``, and where reading that model was refused (HTTP 403 or 404).
     The explicit ``required_evidence`` scope instead covers refused, demanded native evidence.
  2. Only when the identity has no role in the workspace: an existing role is never changed or removed, and
     the module removes only what it added.
  3. Contributor first; Member only if the read is still refused (``ACCESS_GRANT_ROLE=auto``).
  4. Removed in ``finally``, including on an exception or interrupt. Required-evidence grants last
     for one collector's retries; declared-model grants last for one workspace's retries.
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

from collectors._http import HttpError, collect_value, request, request_deadline
from collectors.auth import POWERBI_SCOPE, get_access_grant_provider, get_default_provider
from collectors.evidence_retry import failure_kind, failure_status, retry_failed
from collectors.workspace_scope import is_source_workspace, source_workspace_ids
from orchestration.environment import validate_access_grant

PBI = "https://api.powerbi.com/v1.0/myorg"
# Reading a model the identity cannot see answers 403, or 404 when it cannot see the workspace.
REFUSED = frozenset({"http_403", "http_404", "not_found"})
ROLE_ORDER = ("Contributor", "Member")
# A role change can take a short while to reach the model service, so the first read after a grant is retried.
PROPAGATION_DELAYS = (5, 15)
RETRY_BUDGET_SECONDS = 120
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
    scope: str = "declared_models"

    @property
    def roles(self) -> tuple[str, ...]:
        return ROLE_ORDER if self.role == "auto" else (self.role,)


def settings_from_environment(environment: dict[str, str] | None = None) -> Settings:
    if environment is None:
        # Local runs keep these beside the other settings in .env.far; real environment variables win.
        # Only access-grant settings are read; nothing else enters the process environment here.
        from dotenv import dotenv_values
        local = {key: value for key, value in dotenv_values(Path(__file__).resolve().parents[1] / ".env.far").items()
                 if key in {"ACCESS_GRANT_ENABLED", "ACCESS_GRANT_ROLE", "ACCESS_GRANT_SCOPE"} and value}
        env = {**local, **os.environ}
    else:
        env = environment
    values = validate_access_grant({"enabled": env.get("ACCESS_GRANT_ENABLED", "false"),
                                    "role": env.get("ACCESS_GRANT_ROLE", "auto"),
                                    "scope": env.get("ACCESS_GRANT_SCOPE", "declared_models")})
    return Settings(enabled=values["enabled"], role=values["role"], scope=values["scope"])


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


def _token_claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, AttributeError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def principal_from_token(token: str) -> Principal | None:
    """The identity behind an access token, read from its claims; the token itself is never kept."""
    claims = _token_claims(token)
    object_id = claims.get("oid") if isinstance(claims.get("oid"), str) else None
    name = next((claims[key] for key in ("upn", "preferred_username", "unique_name")
                 if isinstance(claims.get(key), str) and "@" in claims[key]), None)
    if name:
        return Principal("User", name.lower(), object_id)
    return Principal("App", object_id.lower(), object_id.lower()) if object_id else None


class AdminApi:
    """The administrator calls. Only status codes (and, to confirm membership, the workspace's users) are used."""

    def __init__(self, provider: Any) -> None:
        principal = principal_from_token(provider.get_token(POWERBI_SCOPE))
        if principal is None or principal.kind != "User":
            raise AccessGrantError(
                "Temporary grants require a delegated Fabric administrator. "
                "An SP read-only admin token cannot grant itself workspace access."
            )
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
                 api_factory: Callable[[Any], Any] = AdminApi,
                 admin_provider_factory: Callable[[], Any] | None = None) -> None:
        self.settings = settings
        self.ledger = Ledger(ledger_path)
        self.audit: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self._provider_factory, self._sleep, self._delays, self._api_factory = provider_factory, sleep, delays, api_factory
        self._provider: Any = None
        self._api: Any = None
        self._admin_provider_factory = admin_provider_factory
        self._grant_attempts = 0

    @classmethod
    def from_environment(cls, raw_dir: Path, **kwargs: Any) -> "AccessGrant":
        kwargs.setdefault("admin_provider_factory", get_access_grant_provider)
        return cls(settings_from_environment(), default_ledger_path(raw_dir), **kwargs)

    # -- plumbing ------------------------------------------------------------------------------------

    def _provider_(self) -> Any:
        if self._provider is None:
            self._provider = self._provider_factory()
        return self._provider

    def _admin(self) -> Any:
        if self._api is None:
            reviewer = self._provider_()
            authority = self._admin_provider_factory() if self._admin_provider_factory else None
            if authority is not None and authority is not reviewer:
                target_tenant = _token_claims(reviewer.get_token(POWERBI_SCOPE)).get("tid")
                authority_tenant = _token_claims(authority.get_token(POWERBI_SCOPE)).get("tid")
                if (not isinstance(target_tenant, str) or not target_tenant
                        or not isinstance(authority_tenant, str) or target_tenant.lower() != authority_tenant.lower()):
                    raise AccessGrantError("Temporary grant authority must be in the same tenant as the review identity.")
            self._api = self._api_factory(authority if authority is not None else reviewer)
        return self._api

    def retry_collection(
        self, producer: Callable[[], Path], workspace_ids: set[str], collector: str,
        *, retry: Callable[[Path, set[str], float], Path] | None = None,
    ) -> Path:
        """Retry only observed access refusals; keep the collector's own identity and scope."""
        path = producer()
        if not self.settings.enabled or self.settings.scope != "required_evidence":
            return path
        pending = _refused_workspaces(path) & ({ws.lower() for ws in workspace_ids} - source_workspace_ids())
        if not pending or self.failures:
            return path
        deadline = time.monotonic() + RETRY_BUDGET_SECONDS
        retry = retry or (lambda path, ids, deadline: retry_failed(path, ids, collector, deadline))
        principal = principal_from_token(self._provider_().get_token(POWERBI_SCOPE))
        if principal is None:
            raise AccessGrantError("Temporary workspace access: review identity could not be resolved.")
        try:
            admin = self._admin()
        except AccessGrantError as exc:
            print(str(exc))
            raise
        active: dict[str, dict[str, Any]] = {}
        try:
            for role_index, role in enumerate(self.settings.roles):
                if role_index:
                    pending &= _refused_workspaces(path, permission_only=True)
                    if not pending:
                        break
                granted: set[str] = set()
                for workspace_id in sorted(pending):
                    if time.monotonic() >= deadline:
                        raise HttpError("Evidence retry time limit reached", error_code="RetryDeadlineExceeded")
                    if workspace_id in active:
                        previous = active[workspace_id]
                        previous["removed"] = self._remove(workspace_id, principal)
                        if not previous["removed"]:
                            self.failures.append(workspace_id)
                            return path
                        del active[workspace_id]
                    row: dict[str, Any] = {
                        "workspace_id": workspace_id, "collector": collector,
                        "role_used": None, "removed": None,
                    }
                    self.audit.append(row)
                    if self._grant_attempts >= MAX_WORKSPACES:
                        row["status"] = "deferred_run_limit"
                        continue
                    try:
                        present = admin.listed(workspace_id, principal)
                    except Exception:
                        row["status"] = "membership_unknown"
                        print("Temporary workspace access: membership could not be verified; no grant attempted.")
                        continue
                    if present:
                        row["status"] = "role_already_present"
                        print(f"Temporary workspace access: existing role preserved for {workspace_id}.")
                        continue
                    self.ledger.add(workspace_id, principal, role)
                    active[workspace_id] = row
                    self._grant_attempts += 1
                    try:
                        status = admin.add(workspace_id, principal, role)
                    except Exception:
                        status = None
                    if status is not None and 200 <= status < 300:
                        row.update(role_used=role, status="granted")
                        granted.add(workspace_id)
                    else:
                        row["status"] = "grant_refused" if status is None else f"grant_refused_http_{status}"
                        if self._absent(workspace_id, principal):
                            self.ledger.clear(workspace_id, principal)
                            del active[workspace_id]
                if not granted:
                    return path
                successful = {ws for ws, row in active.items() if row["role_used"] is not None}
                for delay in (0, *self._delays):
                    if time.monotonic() + delay >= deadline:
                        raise HttpError("Evidence retry time limit reached", error_code="RetryDeadlineExceeded")
                    if delay:
                        self._sleep(delay)
                    path = retry(path, pending & successful, deadline)
                    pending = _refused_workspaces(path) & successful
                    if not pending:
                        break
                for ws in successful:
                    active[ws]["status"] = ("still_refused" if ws in pending else
                                            "read_failed" if ws in _refused_workspaces(path, include_other=True)
                                            else "read_after_grant")
                if not pending:
                    return path
        except HttpError as exc:
            if exc.error_code != "RetryDeadlineExceeded":
                for row in active.values():
                    if row["status"] == "granted":
                        row["status"] = "collection_failed"
                raise
            print(f"{collector}: temporary-access retry time limit reached; remaining evidence stays incomplete.")
            failed = _refused_workspaces(path, include_other=True)
            for ws, row in active.items():
                row["status"] = "retry_budget_exhausted" if ws in failed else "read_after_grant"
        except BaseException:
            for row in active.values():
                if row["status"] == "granted":
                    row["status"] = "collection_failed"
            raise
        finally:
            for ws, row in active.items():
                try:
                    row["removed"] = self._remove(ws, principal)
                except (AccessGrantError, OSError):
                    row["removed"] = False
                    print("Temporary workspace access: cleanup ledger could not be updated.")
                if row["removed"] is False and ws not in self.failures:
                    self.failures.append(ws)
        return path

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
        if not self.settings.enabled or self.settings.scope != "declared_models" or not declared or self.failures:
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
        deadline = time.monotonic() + RETRY_BUDGET_SECONDS
        for position, (workspace_id, indexes) in enumerate(sorted(refused.items())):
            row: dict[str, Any] = {"workspace_id": workspace_id, "model_count": len(indexes), "role_used": None, "removed": None}
            workspace = by_workspace.get(workspace_id) or {}
            users = workspace.get("users")
            if principal is None:
                row["status"] = "identity_unresolved"
            elif is_source_workspace({"id": workspace_id, **workspace}):
                row["status"] = "source_workspace_excluded"
            elif self.failures:
                row["status"] = "cleanup_failed"
            elif time.monotonic() >= deadline:
                row["status"] = "retry_budget_exhausted"
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
                        before = list(models)
                        try:
                            changed |= self._grant_and_read(workspace_id, principal, models, indexes, refetch, row, deadline)
                        except Exception:
                            # Exception text can carry tenant data. The workspace's own cleanup has already run.
                            row["status"] = "grant_error"
                        finally:
                            changed |= before != models
            self.audit.append(row)
        if changed:
            payload["errors"] = sum(1 for m in models if isinstance(m, dict) and m.get("error"))
            payload["collectionComplete"] = payload.get("status") == "completed" and payload["errors"] == 0
            temporary = Path(definitions_path).with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(definitions_path)

    def _grant_and_read(self, workspace_id: str, principal: Principal, models: list[dict[str, Any]], indexes: list[int],
                        refetch: Callable[[dict[str, Any]], dict[str, Any]], row: dict[str, Any],
                        deadline: float | None = None) -> bool:
        deadline = deadline if deadline is not None else time.monotonic() + RETRY_BUDGET_SECONDS
        pending = list(indexes)
        row["status"] = "grant_refused"
        changed = False
        owned = False
        try:
            roles = self.settings.roles
            for number, role in enumerate(roles):
                if time.monotonic() >= deadline:
                    row["status"] = "retry_budget_exhausted"
                    return changed
                self.ledger.add(workspace_id, principal, role)
                owned = True
                row["removed"] = None
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
                        owned = False
                    return changed
                row["role_used"] = role
                for delay in (0, *self._delays):
                    if time.monotonic() + delay >= deadline:
                        row["status"] = "retry_budget_exhausted"
                        return changed
                    if delay:
                        self._sleep(delay)
                    still = []
                    for index in pending:
                        with request_deadline(deadline):
                            fresh = refetch(models[index])
                        models[index], changed = fresh, True
                        if fresh.get("error") in REFUSED:
                            still.append(index)
                    pending = still
                    if not pending:
                        break
                if not pending:
                    row["status"] = "read_failed" if any(models[i].get("error") for i in indexes) else "read_after_grant"
                    return changed
                row["status"] = "still_refused"
                if number < len(roles) - 1:
                    pending = [i for i in pending if failure_kind(models[i]) == "access" and failure_status(models[i]) == 403]
                    if not pending:
                        return changed
                    row["removed"] = self._remove(workspace_id, principal)
                    if not row["removed"]:
                        return changed
                    owned = False
            return changed
        except HttpError as exc:
            if exc.error_code != "RetryDeadlineExceeded":
                raise
            row["status"] = "retry_budget_exhausted"
            print("Temporary workspace access: retry time limit reached; remaining model evidence stays incomplete.")
            return changed
        finally:
            try:
                removed = self._remove(workspace_id, principal) if owned else row.get("removed")
            except (AccessGrantError, OSError):
                removed = False
                print("Temporary workspace access: cleanup ledger could not be updated.")
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
            "scope": self.settings.scope,
            "workspaces": self.audit, "granted_count": len(granted),
            "removed_count": sum(1 for r in self.audit if r.get("removed") is True or r.get("status") == "leftover_removed"),
            "removal_failed_count": len(self.failures),
        }

    def write_audit(self, raw_dir: Path) -> None:
        if self.settings.enabled or self.audit:
            (Path(raw_dir) / AUDIT_FILE).write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")


def _refused_workspaces(path: Path, *, permission_only: bool = False, include_other: bool = False) -> set[str]:
    refused: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            workspace = value.get("workspaceId")
            kind = failure_kind(value)
            eligible = kind == "access" and failure_status(value) == 403 if permission_only else kind in {"access", "not_found"}
            if include_other:
                status = failure_status(value)
                eligible = bool(value.get("error") or value.get("status") == "unavailable"
                                or (status is not None and status >= 400))
            if isinstance(workspace, str) and eligible:
                refused.add(workspace.lower())
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if Path(path).name == "pipeline_definitions.json" and isinstance(payload, dict):
        payload = {key: payload.get(key, []) for key in ("pipelines", "notebooks")}
    visit(payload)
    return refused


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

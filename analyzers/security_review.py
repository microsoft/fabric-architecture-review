# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Security review.

Rule coverage:
  SEC-003 Guest user access (tenant setting + actual guest principals)
  SEC-004 Individual users (not security groups) as workspace admins
  SEC-005 Broad direct access (>10 individual principals on a workspace)
  SEC-006 Misconfigured datasources from Scanner API
  SEC-007 External (#EXT#) users with workspace access
  SEC-008 Gateway cluster single point of failure
  SEC-009 Private connectivity signals (VNet gateway / trusted-workspace)
  SEC-010 Gateway cluster member version currency / consistency
  SEC-011 Personal-mode gateways / stored single-user credentials

(SEC-001, SEC-002 are covered by tenant_settings_review.)

DATA SAFETY: Workspace role assignments include UPNs — keep output local.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from analyzers._common import load_raw, load_rules, make_finding, missing_raw_finding, threshold, write_findings

BROAD_ACCESS_THRESHOLD = threshold("security", "broad_access_max_principals", 10, env="SEC_BROAD_ACCESS_THRESHOLD", cast=int)


def _version_tuple(value: str) -> Tuple[int, ...]:
    parts: List[int] = []
    for chunk in str(value).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)



def _workspaces(raw_dir: Path) -> List[Dict[str, Any]]:
    scan = load_raw(raw_dir / "scanner.json")
    if scan and scan.get("workspaces"):
        return scan["workspaces"]
    inv = load_raw(raw_dir / "workspace_inventory.json")
    if inv and inv.get("workspaces"):
        return inv["workspaces"]
    return []


def _principal_type(user: Dict[str, Any]) -> str:
    return (user.get("principalType") or user.get("graphId") or user.get("type") or "").lower()


def _is_external(user: Dict[str, Any]) -> bool:
    upn = (user.get("identifier") or user.get("emailAddress") or user.get("userPrincipalName") or "").lower()
    return "#ext#" in upn


def analyze(raw_dir: str | os.PathLike = "output/raw",
            checklist_path: str | os.PathLike = "config/review-checklist.yaml") -> List[Dict[str, Any]]:
    raw_dir = Path(raw_dir)
    rules = load_rules(checklist_path)
    findings: List[Dict[str, Any]] = []

    # --- SEC-003 Guest user access (tenant setting) ---
    rule = rules.get("SEC-003")
    if rule:
        ts = load_raw(raw_dir / "tenant_settings.json")
        if not ts:
            findings.append(missing_raw_finding(rule, "security", "tenant_settings.json"))
        else:
            settings = {s.get("settingName"): s for s in (ts.get("tenantSettings") or ts.get("value") or [])}
            target = settings.get("AllowGuestUserToAccessSharedContent") or settings.get("AllowGuestUserToAccessTenant")
            if not target:
                findings.append(make_finding(
                    rule, dimension="security", status="info",
                    title="Guest user access setting not returned",
                    evidence={"reason": "Tenant settings did not include AllowGuestUserToAccessSharedContent."},
                    recommendation="Verify the signed-in user is a Fabric Administrator and re-run the tenant collector."
                ))
            else:
                enabled = bool(target.get("enabled"))
                scoped = bool(target.get("enabledSecurityGroups"))
                status = "pass" if (not enabled or scoped) else "fail"
                findings.append(make_finding(
                    rule, dimension="security", status=status,
                    title="Guest user access to Fabric content",
                    evidence={"enabled": enabled, "scopedToSecurityGroup": scoped,
                              "enabledSecurityGroups": target.get("enabledSecurityGroups")},
                    recommendation="Disable, or scope guest access to a dedicated security group of approved external collaborators."
                ))

    workspaces = _workspaces(raw_dir)

    # --- SEC-004 Individual users as workspace admins ---
    rule = rules.get("SEC-004")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "security", "scanner.json or workspace_inventory.json"))
        else:
            offenders: List[Dict[str, Any]] = []
            for w in workspaces:
                user_admins = []
                for u in w.get("users") or []:
                    right = (u.get("groupUserAccessRight") or u.get("role") or "").lower()
                    if right != "admin":
                        continue
                    if _principal_type(u) == "user":
                        user_admins.append(u.get("identifier") or u.get("displayName"))
                if user_admins:
                    offenders.append({"workspace": w.get("name"), "individualAdmins": user_admins})
            status = "pass" if not offenders else "fail"
            findings.append(make_finding(
                rule, dimension="security", status=status,
                title="Workspaces with individual users (not groups) as admins",
                evidence={"offenderCount": len(offenders), "examples": offenders[:20]},
                recommendation="Replace individual admin assignments with Entra security groups for lifecycle management."
            ))

    # --- SEC-005 Broad access ---
    rule = rules.get("SEC-005")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "security", "scanner.json or workspace_inventory.json"))
        else:
            broad = []
            for w in workspaces:
                principals = [u for u in (w.get("users") or []) if _principal_type(u) == "user"]
                if len(principals) > BROAD_ACCESS_THRESHOLD:
                    broad.append({"workspace": w.get("name"), "individualPrincipalCount": len(principals)})
            status = "pass" if not broad else "fail"
            findings.append(make_finding(
                rule, dimension="security", status=status,
                title=f"Workspaces with >{BROAD_ACCESS_THRESHOLD} individual principals",
                evidence={"threshold": BROAD_ACCESS_THRESHOLD, "count": len(broad), "examples": broad[:20]},
                recommendation="Consolidate broad access into Entra security groups; remove unused direct assignments."
            ))

    # --- SEC-006 Misconfigured datasources ---
    rule = rules.get("SEC-006")
    if rule:
        scan = load_raw(raw_dir / "scanner.json")
        if not scan:
            findings.append(missing_raw_finding(rule, "security", "scanner.json"))
        else:
            misconfigured = scan.get("misconfiguredDatasourceInstances") or []
            status = "pass" if not misconfigured else "fail"
            findings.append(make_finding(
                rule, dimension="security", status=status,
                title="Misconfigured datasource instances",
                evidence={"count": len(misconfigured),
                          "examples": [{"id": d.get("datasourceId"), "type": d.get("datasourceType")} for d in misconfigured[:20]]},
                recommendation="Repair gateway bindings, refresh expired OAuth credentials, or remove orphaned datasources."
            ))

    # --- SEC-007 External users ---
    rule = rules.get("SEC-007")
    if rule:
        if not workspaces:
            findings.append(missing_raw_finding(rule, "security", "scanner.json or workspace_inventory.json"))
        else:
            externals: List[Dict[str, Any]] = []
            for w in workspaces:
                ext_users = [u for u in (w.get("users") or []) if _is_external(u)]
                if ext_users:
                    externals.append({"workspace": w.get("name"),
                                      "externalUsers": [u.get("identifier") for u in ext_users][:10]})
            status = "pass" if not externals else "fail"
            findings.append(make_finding(
                rule, dimension="security", status=status,
                title="Workspaces with external (guest) users",
                evidence={"workspaceCount": len(externals), "examples": externals[:20]},
                recommendation="Review external user assignments; remove when no longer required and route remaining "
                               "guests through an Entra-managed security group with expiration."
            ))

    # --- SEC-008 Gateway cluster SPOF ---
    rule = rules.get("SEC-008")
    if rule:
        gw = load_raw(raw_dir / "gateways.json")
        if not gw:
            findings.append(missing_raw_finding(rule, "security", "gateways.json"))
        else:
            gateways = gw.get("gateways") or []
            cluster_types = ("Resource", "OnPremises")
            single_member = [
                {"name": g.get("name"), "gatewayType": g.get("gatewayType"),
                 "memberCount": g.get("memberCount"),
                 "datasourceCount": g.get("datasourceCount")}
                for g in gateways
                if (g.get("gatewayType") in cluster_types) and (g.get("memberCount") or 1) < 2
                and (g.get("datasourceCount") or 0) > 0
            ]
            on_prem_clusters = [g for g in gateways if g.get("gatewayType") in cluster_types]
            if not on_prem_clusters:
                findings.append(make_finding(
                    rule, dimension="security", status="info",
                    title="No on-premises data gateway clusters detected",
                    evidence={"gatewayCount": len(gateways),
                              "byType": {t: sum(1 for g in gateways if g.get("gatewayType") == t)
                                         for t in {g.get("gatewayType") for g in gateways}}},
                    recommendation="If on-prem sources are in scope, deploy an on-premises data gateway cluster."
                ))
            else:
                status = "pass" if not single_member else "fail"
                findings.append(make_finding(
                    rule, dimension="security", status=status,
                    title=("Single-member gateway cluster(s) bound to data sources (SPOF)"
                           if single_member else "All on-premises gateway clusters have >=2 members"),
                    evidence={"clusterCount": len(on_prem_clusters),
                              "singleMemberClusters": single_member},
                    recommendation=("Add at least one secondary gateway member to every cluster carrying "
                                    "production data sources to remove the single point of failure.")
                ))

    # --- SEC-009 Private connectivity (VNet gateway / trusted-workspace) ---
    rule = rules.get("SEC-009")
    if rule:
        gw = load_raw(raw_dir / "gateways.json")
        ts = load_raw(raw_dir / "tenant_settings.json")
        evidence: Dict[str, Any] = {}
        signals: List[str] = []
        if gw:
            gateways = gw.get("gateways") or []
            vnet = [g for g in gateways if g.get("gatewayType") == "VirtualNetwork"]
            evidence["vnetGatewayCount"] = len(vnet)
            evidence["vnetGateways"] = [{"name": g.get("name"), "memberCount": g.get("memberCount")}
                                          for g in vnet]
            if vnet:
                signals.append("VNet data gateway present")
        if ts:
            settings = {s.get("settingName"): s for s in (ts.get("tenantSettings") or ts.get("value") or [])}
            trusted = settings.get("AllowTrustedWorkspaceAccessForStorageAccounts") or \
                       settings.get("AllowServicePrincipalsCreateAndUseProfiles")
            if trusted and trusted.get("enabled"):
                signals.append("Trusted-workspace access enabled")
            evidence["trustedWorkspaceAccess"] = bool(trusted and trusted.get("enabled"))
        if not gw and not ts:
            findings.append(missing_raw_finding(rule, "security", "gateways.json + tenant_settings.json"))
        else:
            status = "info" if signals else "info"  # info-only rule; surface evidence
            title = ("Private connectivity signals: " + "; ".join(signals)) if signals else \
                    "No private-connectivity signal detected (VNet gateway / trusted-workspace access)"
            findings.append(make_finding(
                rule, dimension="security", status=status,
                title=title,
                evidence=evidence,
                recommendation=("If production datasources are reached over public endpoints, evaluate a "
                                "Virtual Network data gateway, Fabric trusted-workspace access for ADLS / "
                                "Storage, or private endpoints on the tenant.")
            ))

    # --- SEC-010 Gateway cluster member version currency ---
    rule = rules.get("SEC-010")
    if rule:
        gw = load_raw(raw_dir / "gateways.json")
        if not gw:
            findings.append(missing_raw_finding(rule, "security", "gateways.json"))
        else:
            gateways = gw.get("gateways") or []
            min_version = str(threshold("security", "gateway_min_version", "",
                                        env="SEC_GATEWAY_MIN_VERSION", cast=str)).strip()
            on_prem = [g for g in gateways
                       if g.get("gatewayType") in ("OnPremises", "Resource", "VirtualNetwork")]
            version_mismatch: List[Dict[str, Any]] = []
            below_min: List[Dict[str, Any]] = []
            offline_members: List[Dict[str, Any]] = []
            for g in on_prem:
                members = g.get("members") or []
                versions = sorted({(m.get("version") or "").strip() for m in members if m.get("version")})
                if len(versions) > 1:
                    version_mismatch.append({"gateway": g.get("name"), "versions": versions})
                for m in members:
                    status_val = (m.get("status") or "").strip().lower()
                    if status_val and status_val not in ("live", "online", "running"):
                        offline_members.append({"gateway": g.get("name"),
                                                "member": m.get("name"), "status": m.get("status")})
                    if min_version:
                        ver = (m.get("version") or "").strip()
                        if ver and _version_tuple(ver) < _version_tuple(min_version):
                            below_min.append({"gateway": g.get("name"),
                                              "member": m.get("name"), "version": ver})
            if not on_prem:
                findings.append(make_finding(
                    rule, dimension="security", status="info",
                    title="No on-premises / VNet gateway clusters to evaluate for version currency",
                    evidence={"gatewayCount": len(gateways)},
                    recommendation="This check activates once on-premises or VNet data gateways exist."
                ))
            else:
                problems = bool(version_mismatch or below_min or offline_members)
                findings.append(make_finding(
                    rule, dimension="security", status="fail" if problems else "pass",
                    title=("Gateway clusters have version skew, outdated members, or offline nodes"
                           if problems else "Gateway cluster members are consistent and online"),
                    evidence={"minVersionChecked": min_version or "(not set - only consistency checked)",
                              "versionMismatch": version_mismatch,
                              "belowMinVersion": below_min,
                              "offlineMembers": offline_members},
                    recommendation=("Keep every member of a gateway cluster on the same, current monthly gateway "
                                    "release. Mismatched or outdated members cause inconsistent refresh behaviour "
                                    "and miss security fixes; replace offline members to preserve HA.")
                ))

    # --- SEC-011 Personal gateways / stored single-user credentials ---
    rule = rules.get("SEC-011")
    if rule:
        gw = load_raw(raw_dir / "gateways.json")
        if not gw:
            findings.append(missing_raw_finding(rule, "security", "gateways.json"))
        else:
            gateways = gw.get("gateways") or []
            personal = [g for g in gateways if g.get("gatewayType") == "Personal"]
            personal_with_sources = [
                {"gateway": g.get("name"), "datasourceCount": g.get("datasourceCount") or 0}
                for g in personal if (g.get("datasourceCount") or 0) > 0
            ]
            cloud_datasources = 0
            for g in gateways:
                for d in g.get("datasources") or []:
                    dtype = (d.get("datasourceType") or "").lower()
                    if dtype and dtype not in ("file", "folder"):
                        cloud_datasources += 1
            if not gateways:
                findings.append(make_finding(
                    rule, dimension="security", status="info",
                    title="No gateways to evaluate for credential-storage hygiene",
                    evidence={"gatewayCount": 0},
                    recommendation="This check activates once data gateways exist."
                ))
            else:
                status = "fail" if personal_with_sources else "pass"
                findings.append(make_finding(
                    rule, dimension="security", status=status,
                    title=(f"{len(personal_with_sources)} personal-mode gateway(s) hold stored datasource credentials"
                           if personal_with_sources else "No personal-mode gateways with stored credentials"),
                    evidence={"personalGatewayCount": len(personal),
                              "personalWithDatasources": personal_with_sources,
                              "totalCloudDatasources": cloud_datasources},
                    recommendation=("Avoid personal-mode gateways for shared/production datasources - they pin "
                                    "connectivity and stored credentials to one user. Move sources onto an "
                                    "enterprise (on-prem/VNet) gateway and authenticate with managed identity or "
                                    "SSO instead of stored single-user credentials.")
                ))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="output/raw")
    parser.add_argument("--checklist", default="config/review-checklist.yaml")
    parser.add_argument("--out", default="output/findings_security.json")
    args = parser.parse_args()
    findings = analyze(args.raw_dir, args.checklist)
    write_findings(findings, args.out)
    fail = sum(1 for x in findings if x["status"] == "fail")
    print(f"Security: {len(findings)} rule(s), {fail} fail(s). Wrote {args.out}")


if __name__ == "__main__":
    main()

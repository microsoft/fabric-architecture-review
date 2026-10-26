// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { describe, expect, it } from "vitest";
import type { QueryTable } from "@microsoft/fabric-app-data";
import { buildLiveReviewData, type LiveReviewTables } from "./live-review-adapter";
import { architectureProfile, architectureProfileHash } from "./review-data";
const table = (rows: Record<string, unknown>[]): QueryTable => {
    const names = [...new Set(rows.flatMap(Object.keys))];
    return {columns: names.map(name => ({name, dataType: "string"})), rows: rows.map(r => names.map(n => r[n] ?? null))} as QueryTable;
};
const meta = {run_id: "r", run_timestamp: "2026-10-01T00:00:00Z", profile_id: architectureProfile, profile_sha256: architectureProfileHash};
function fixtures(): LiveReviewTables {
    return {findings: table([]), runSummary: table([{...meta, score: null}]), semanticModels: table([]),
        workspaceRisk: table([]), coverage: table([]), capacities: table([]), findingItems: table([]), dimensions: table([]),
        workspaces: table([]), capacityItems: table([]), tenantSettings: table([])};
}
describe("architectural consumption boundary", () => {
    it("rejects excluded rules, legacy rows and mixed profile hashes", () => {
        const tables = fixtures();
        tables.findings = table([{...meta, rule_id: "ARCH-005", status: "fail", is_scored: 1},
            {...meta, rule_id: "DAX-001"}, {...meta, rule_id: "PERF-001"},
            {...meta, rule_id: "ARCH-001", profile_sha256: "old"}]);
        expect(buildLiveReviewData(tables).findings.map(f => f.id)).toEqual(["ARCH-005"]);
    });
    it("keeps no-score explicit and coverage/signal non-scored", () => {
        const tables = fixtures();
        tables.findings = table([{...meta, rule_id: "ARCH-010", rule_kind: "coverage", status: "missing_evidence", is_scored: 1},
            {...meta, rule_id: "ARCH-008", rule_kind: "signal", status: "info", is_scored: 1}]);
        const result = buildLiveReviewData(tables);
        expect(result.score).toBeNull();
        expect(result.scoreStatus).toBe("no_assessable_controls");
        expect(result.findings.every(f => !f.scored)).toBe(true);
    });
    it("carries what each result means and what to do next", () => {
        const tables = fixtures();
        tables.findings = table([{...meta, rule_id: "ARCH-005", rule_kind: "control", status: "unknown", title: "t",
            status_meaning: "Nothing was declared.", next_action: "Set policy__allowed_item_types.", why_it_matters: "Drift.",
            how_to_fix: "Declare it.", configure: "policy__allowed_item_types"}]);
        expect(buildLiveReviewData(tables).findings[0]).toMatchObject({statusMeaning: "Nothing was declared.",
            nextAction: "Set policy__allowed_item_types.", whyItMatters: "Drift.", howToFix: "Declare it.", configure: "policy__allowed_item_types"});
    });
    it("lists the items a finding points at and drops other runs, profiles and rules", () => {
        const tables = fixtures();
        tables.findingItems = table([
            {...meta, workspace_id: "w1", rule_id: "ARCH-013", item_id: "p1", item_name: "Load sales", item_type: "DataPipeline", reason: "An activity has no retry"},
            {...meta, workspace_id: "w1", rule_id: "ARCH-013", item_id: "p2", item_name: "", item_type: "Unknown", reason: ""},
            {...meta, run_id: "old", workspace_id: "w1", rule_id: "ARCH-013", item_id: "p3", item_name: "Old", item_type: "DataPipeline"},
            {...meta, profile_sha256: "old", workspace_id: "w1", rule_id: "ARCH-013", item_id: "p4", item_name: "Stale", item_type: "DataPipeline"},
            {...meta, workspace_id: "w1", rule_id: "PERF-001", item_id: "p5", item_name: "Excluded", item_type: "DataPipeline"}]);
        expect(buildLiveReviewData(tables).items).toEqual([
            {ruleId: "ARCH-013", workspaceId: "w1", itemId: "p1", itemName: "Load sales", itemType: "DataPipeline", reason: "An activity has no retry"},
            {ruleId: "ARCH-013", workspaceId: "w1", itemId: "p2", itemName: "p2", itemType: "Unknown", reason: ""}]);
    });
    it("builds the estate from workspaces, capacities and the items findings point at", () => {
        const tables = fixtures();
        tables.findings = table([{...meta, rule_id: "ARCH-013", rule_kind: "signal", dimension: "architecture", status: "info", workspace_name: "Sales", affected: "w1"},
            {...meta, rule_id: "GOV-001", rule_kind: "control", dimension: "governance", status: "fail", is_scored: 1, workspace_name: "Sales", affected: "w1"}]);
        tables.workspaceRisk = table([{...meta, workspace_id: "w1", workspace_name: "Sales", capacity_name: "F8", risk_score: 90, issue_count: 3, status: "critical"},
            {...meta, workspace_id: "w2", workspace_name: "Ops", capacity_name: "", risk_score: null, issue_count: 0, status: "unknown"}]);
        tables.workspaces = table([{...meta, workspace_id: "w1", environment: "production", admin_count: 1}]);
        tables.capacityItems = table([
            {...meta, capacity_id: "c1", workspace_id: "w1", workspace_name: "Sales", item_id: "p1", item_name: "Load", item_type: "DataPipeline"},
            {...meta, capacity_id: "c1", workspace_id: "w1", workspace_name: "Sales", item_id: "m1", item_name: "Model", item_type: "SemanticModel"},
            {...meta, capacity_id: "c1", workspace_id: "w1", workspace_name: "Sales", item_id: "x1", item_name: "Agent", item_type: "DataAgent"}]);
        tables.findingItems = table([{...meta, workspace_id: "w1", rule_id: "ARCH-013", item_id: "p1", item_name: "Load", item_type: "DataPipeline"}]);
        tables.semanticModels = table([{...meta, workspace_id: "w1", model_id: "m1", model_name: "Model", storage_mode: "Import"}]);
        tables.capacities = table([{...meta, capacity_id: "c1", capacity_name: "F8", sku: "F8", region: "West US 3", observed_item_count: 3}]);
        const result = buildLiveReviewData(tables);
        const [sales, ops] = result.estate.rooms;
        expect(sales).toMatchObject({name: "Sales", domain: "Production", capacityName: "F8", status: "risk", riskScore: 90, adminCount: 1});
        expect(ops).toMatchObject({name: "Ops", domain: "Environment not declared", capacityName: "No capacity", status: "warning"});
        expect(sales.items.map(i => [i.name, i.type, i.fabricType, i.status])).toEqual([
            ["Load", "pipeline", "DataPipeline", "warning"], ["Model", "model", "SemanticModel", "healthy"], ["Agent", "component", "DataAgent", "healthy"]]);
        expect(sales.items[1].storageMode).toBe("Import");
        expect(result.workspaceRisks.map(w => [w.name, w.status])).toEqual([["Sales", "red"]]);
        expect(result.capacities[0].items.map(i => i.name)).toEqual(["Load", "Model", "Agent"]);
        expect(result.findings.map(f => f.dimension)).toEqual(["Architecture", "Governance"]);
    });
    it("turns the run summary into metrics and the dimension summary into ordered scores", () => {
        const tables = fixtures();
        tables.runSummary = table([{...meta, score: 64, assessed_count: 100, fail_count: 36, critical_fail: 1, high_fail: 34, unknown_count: 21,
            missing_evidence_count: 12, assessment_coverage: 81.3, engagement_name: "Contoso review"}]);
        tables.dimensions = table([{...meta, dimension: "security", score: 80, fail_count: 3, assessed_count: 15, total_findings: 131},
            {...meta, dimension: "reliability", score: null, fail_count: 0, assessed_count: 0, total_findings: 21},
            {...meta, dimension: "architecture", score: 50, fail_count: 7, assessed_count: 14, total_findings: 90}]);
        const result = buildLiveReviewData(tables);
        expect(result.metrics.map(m => [m.label, m.value])).toEqual([["Review score", "64"], ["Critical + high", "35"], ["Workspaces at risk", "0"], ["Assessment coverage", "81.3%"]]);
        expect(result.metrics[3].delta).toBe("33 checks unknown or missing evidence");
        expect(result.dimensionScores.map(d => [d.dimension, d.score])).toEqual([["Architecture", 50], ["Reliability", null], ["Security", 80]]);
        expect(result.estate.name).toBe("Contoso review");
    });
    it("records the tenant settings and which profile the newest data came from when it is incompatible", () => {
        const tables = fixtures();
        tables.runSummary = table([{...meta, run_id: "new", run_timestamp: "2026-10-02T00:00:00Z", profile_sha256: "old-hash", score: 90}]);
        const result = buildLiveReviewData(tables);
        expect(result.latestRunId).toBeUndefined();
        expect(result.dataProfileHash).toBe("old-hash");
        expect(result.metrics).toEqual([]);
        tables.runSummary = table([{...meta, score: 1}]);
        tables.tenantSettings = table([{...meta, setting_name: "PublishToWeb", title: "Publish to web", setting_group: "Export", rule_id: "SEC-003", status: "pass", posture: "Disabled", enabled: false, change_count: null}]);
        expect(buildLiveReviewData(tables).tenantSettings).toEqual([{name: "PublishToWeb", title: "Publish to web", group: "Export", ruleId: "SEC-003", status: "pass", posture: "Disabled", enabled: false, changeCount: null}]);
    });
    it("does not accept a legacy summary as current", () => {
        const tables = fixtures();
        tables.runSummary = table([{run_id: "legacy", score: 100}]);
        expect(buildLiveReviewData(tables).latestRunId).toBeUndefined();
        expect(buildLiveReviewData(tables).score).toBeNull();
    });
    it("reads model security only from the current compatible run and never turns unknown into no security", () => {
        const tables = fixtures();
        const row = {...meta, workspace_id: "w1", workspace_name: "Sales", capacity_name: "F8", model_name: "Model", rls_required: true, ols_required: false,
            rls_outcome: "missing_evidence", ols_outcome: "not_required", security_inspection: "partial", inspection_reason: "role_definitions_missing"};
        tables.modelSecurity = table([
            {...row, model_id: "m1", rls_status: "unknown", ols_status: "absent"},
            {...row, model_id: "m2", rls_status: "present", ols_status: "surprising"},
            {...row, model_id: "m3", run_id: "old", rls_status: "absent", ols_status: "absent"},
            {...row, model_id: "m4", profile_sha256: "old", rls_status: "absent", ols_status: "absent"}]);
        const result = buildLiveReviewData(tables);
        expect(result.modelSecurityAvailable).toBe(true);
        expect(result.modelSecurity.map(m => [m.modelId, m.rls, m.ols])).toEqual([["m1", "unknown", "absent"], ["m2", "present", "unknown"]]);
        expect(result.modelSecurity[0]).toMatchObject({rlsRequired: true, olsRequired: false, rlsOutcome: "missing_evidence", inspection: "partial", capacityName: "F8"});
    });
    it("joins roles to their model only from the current run, and reports role detail as not collected without a roles table", () => {
        const tables = fixtures();
        const row = {...meta, workspace_id: "w1", workspace_name: "Sales", capacity_name: "F8", model_name: "Model", rls_required: false, ols_required: false,
            rls_outcome: "not_required", ols_outcome: "not_required", security_inspection: "complete", inspection_reason: "", rls_status: "present", ols_status: "present"};
        tables.modelSecurity = table([{...row, model_id: "m1", role_detail: "collected"}, {...row, model_id: "m2", role_detail: "not_collected"}]);
        expect(buildLiveReviewData(tables).modelSecurity.map(m => [m.modelId, m.roleDetail, m.roles.length])).toEqual([["m1", "not_collected", 0], ["m2", "not_collected", 0]]);
        const role = {...meta, model_id: "m1", role_permission: "read", reads_data: true, row_filter_table_count: 2, dynamic_filter_table_count: 1,
            constant_filter_table_count: 0, ols_table_count: 1, ols_column_count: "3"};
        tables.modelRoles = table([
            {...role, role_name: "B", filter_kind: "mixed"}, {...role, role_name: "A", filter_kind: "surprising"},
            {...role, role_name: "Old", run_id: "old", filter_kind: "static"}, {...role, role_name: "Other", model_id: "m2", filter_kind: "none"}]);
        const [first, second] = buildLiveReviewData(tables).modelSecurity;
        expect(first.roleDetail).toBe("collected");
        expect(first.roles.map(r => [r.name, r.filterKind])).toEqual([["A", "unknown"], ["B", "mixed"]]);
        expect(first.roles[0]).toMatchObject({permission: "read", readsData: true, rowFilterTables: 2, dynamicFilterTables: 1, hiddenTables: 1, hiddenColumns: 3});
        expect(second).toMatchObject({roleDetail: "not_collected"});
        expect(second.roles.map(r => r.name)).toEqual(["Other"]);
    });
    it("marks a semantic model item with its security and tolerates an older model with no security table", () => {
        const tables = fixtures();
        tables.workspaceRisk = table([{...meta, workspace_id: "w1", workspace_name: "Sales", risk_score: 0, status: "healthy"}]);
        tables.capacityItems = table([{...meta, capacity_id: "c1", workspace_id: "w1", workspace_name: "Sales", item_id: "m1", item_name: "Model", item_type: "SemanticModel"}]);
        expect(buildLiveReviewData(tables).modelSecurityAvailable).toBe(false);
        expect(buildLiveReviewData(tables).estate.rooms[0].items[0].security).toBeUndefined();
        tables.modelSecurity = table([{...meta, workspace_id: "w1", model_id: "m1", rls_status: "present", ols_status: "absent"}]);
        expect(buildLiveReviewData(tables).estate.rooms[0].items[0].security).toEqual({rls: "present", ols: "absent"});
    });
});

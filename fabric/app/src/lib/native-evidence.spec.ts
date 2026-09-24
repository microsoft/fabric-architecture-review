// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { describe, expect, it } from "vitest";
import type { QueryTable } from "@microsoft/fabric-app-data";
import { hasCoverage, latestExecutionObservations, readEvidenceCoverage, readEvidenceDetails } from "@/lib/native-evidence";
import { nativeEvidencePreview } from "@/lib/native-evidence-preview";
import { evidenceRowLimit, nativeEvidenceQueries } from "@/queries/live/native-evidence";

export function evidenceTable(records: Record<string, unknown>[]): QueryTable {
    const names = Object.keys(records[0] ?? {});
    return { columns: names.map((name) => ({ name: `[${name}]`, dataType: "unknown" })), rows: records.map((row) => names.map((name) => row[name])) };
}

export const reviewRow = {
    run_id: "r2", run_timestamp: "2026-09-22T08:00:00", review_item_key: "r2-w1-i1",
    capacity_id: "c1", capacity_name: "Capacity", workspace_id: "w1", workspace_name: "Duplicate name",
};
const execution = {
    ...reviewRow, item_id: "i1", item_name: "Model", item_type: "SemanticModel", execution_key: "refresh1",
    execution_id: "1", execution_type: "Scheduled", status: "Completed",
    start_time: "2026-09-22T06:00:00", end_time: null, duration_ms: null, source: "powerbi_refresh_history",
};

describe("native evidence contracts", () => {
    it.each(["collected", "empty", "partial", "forbidden", "not_found", "error", "not_collected", "invalid_data"])("preserves execution coverage status %s", (status) => {
        const result = readEvidenceCoverage(evidenceTable([{ ...execution, collection_status: status, observed_execution_count: 0, oldest_start_time: null, newest_start_time: null, notice: "Notice", history_scope: "recent_retained" }]), "executions");
        expect(result[0]).toMatchObject({ status, count: 0, oldestStart: null, notice: "Notice", runTimestamp: "2026-09-22T08:00:00.000Z" });
    });

    it.each(["inspected", "partial", "parse_error", "unavailable", "forbidden", "unsupported", "inventory_unavailable"])("preserves dataflow coverage status %s", (status) => {
        expect(readEvidenceCoverage(evidenceTable([{ ...reviewRow, dataflow_id: "", dataflow_name: "", definition_status: status, query_count: 0, flagged_query_count: 0, notice: "Gap" }]), "dataflows")[0]).toMatchObject({ itemId: "", status, count: 0 });
    });

    it("retains unidentified execution inventory coverage without accepting unidentified executions", () => {
        const gap = { ...execution, item_id: "", collection_status: "not_collected", observed_execution_count: 0, oldest_start_time: null, newest_start_time: null, notice: "missing_item_identity", history_scope: "recent_retained" };
        const result = readEvidenceCoverage(evidenceTable([gap]), "executions");
        expect(result[0]).toMatchObject({ itemId: "", status: "not_collected", notice: "missing_item_identity" });
        expect(() => readEvidenceDetails(evidenceTable([gap]), "executions")).toThrow("item_id");
    });

    it.each(["complete", "partial", "unavailable"])("preserves typed DAX coverage status %s", (status) => {
        expect(readEvidenceCoverage(evidenceTable([{ ...reviewRow, model_id: "m", model_name: "Model", definition_status: status, object_count: 0, flagged_object_count: 0, notice: "" }]), "daxObjects")[0].status).toBe(status);
    });

    it("normalizes UTC and preserves missing duration versus zero and int64 milliseconds", () => {
        const results = readEvidenceDetails(evidenceTable([execution, { ...execution, execution_key: "2", duration_ms: 0 }, { ...execution, execution_key: "3", duration_ms: 4_294_967_296 }]), "executions");
        expect(results[0]).toMatchObject({ start: "2026-09-22T06:00:00.000Z", end: null, durationMs: null, source: "powerbi_refresh_history" });
        expect(results[1]).toMatchObject({ durationMs: 0 });
        expect(results[2]).toMatchObject({ durationMs: 4_294_967_296 });
    });

    it.each([undefined, -1, 1.5, "100", Number.MAX_SAFE_INTEGER + 1])("rejects invalid duration %s instead of substituting zero", (duration) => {
        expect(() => readEvidenceDetails(evidenceTable([{ ...execution, duration_ms: duration }]), "executions")).toThrow("Invalid evidence integer");
    });

    it("rejects malformed timestamps and absent required scope", () => {
        expect(() => readEvidenceDetails(evidenceTable([{ ...execution, start_time: "not-a-time" }]), "executions")).toThrow("Invalid evidence timestamp");
        expect(() => readEvidenceDetails(evidenceTable([{ ...execution, workspace_id: "" }]), "executions")).toThrow("workspace_id");
    });

    it("parses semicolon Dataflow signals without expressions", () => {
        const [result] = readEvidenceDetails(evidenceTable([{ ...reviewRow, dataflow_id: "f", dataflow_name: "Flow", query_name: "Query", signal_codes: "DFLOW_TABLE_BUFFER; DFLOW_STOP_FOLDING;DFLOW_NATIVE_QUERY", signal_count: 3, recommendation: "Review", notice: "" }]), "dataflows");
        expect(result).toMatchObject({ kind: "query", signals: ["DFLOW_TABLE_BUFFER", "DFLOW_STOP_FOLDING", "DFLOW_NATIVE_QUERY"] });
        expect(result).not.toHaveProperty("expression");
    });

    it.each(["calculated_column", "calculated_table", "calculation_item"])("preserves %s separately from measures", (type) => {
        expect(readEvidenceDetails(evidenceTable([{ ...reviewRow, model_id: "m", model_name: "Model", table_name: "Table", object_name: "Object", object_type: type, risk_level: "medium", risk_rank: 2, risk_score: 20, expression_length: 40, signal_codes: "nested_iterators, context_transition", signal_details_json: "{}" }]), "daxObjects")[0]).toMatchObject({ kind: "daxObject", objectType: type, riskScore: 20, signals: ["nested_iterators", "context_transition"] });
    });

    it("matches coverage by review-item key plus authoritative run/workspace/item IDs, not names", () => {
        const coverage = nativeEvidencePreview.executions.coverage;
        const row = nativeEvidencePreview.executions.detail[0];
        expect(hasCoverage(row, coverage)).toBe(true);
        for (const change of [{ reviewItemKey: "other" }, { runId: "other" }, { workspaceId: "other" }, { itemId: "other" }]) expect(hasCoverage({ ...row, ...change }, coverage)).toBe(false);
    });

    it("deduplicates the latest observation before failed-status filtering, including deterministic ties", () => {
        const results = readEvidenceDetails(evidenceTable([
            { ...execution, run_id: "r1", run_timestamp: "2026-09-21T08:00:00Z", status: "Failed" },
            execution,
            { ...execution, run_id: "r3", status: "Completed" },
        ]), "executions");
        const latest = latestExecutionObservations(results);
        expect(latest).toHaveLength(1);
        expect(latest[0].runId).toBe("r3");
        expect(latest.filter((row) => row.kind === "execution" && row.status === "Failed")).toHaveLength(0);
    });

    it("fails explicitly rather than presenting truncated results as complete", () => {
        expect(() => readEvidenceDetails(evidenceTable(Array.from({ length: evidenceRowLimit + 1 }, () => execution)), "executions")).toThrow("5,000-row safety limit");
    });
});

describe("native evidence queries", () => {
    it.each(["executions", "dataflows", "daxObjects"] as const)("avoids the reserved SCOPE identifier in both %s query modes", (lens) => {
        for (const history of [false, true]) {
            for (const query of Object.values(nativeEvidenceQueries(lens, "r2", history))) {
                expect(query).not.toMatch(/\bScope\b/i);
                expect(query).toContain("VAR __EvidenceRows = FILTER(");
                expect(query).toMatch(/VAR Observations = (?:FILTER\()?__EvidenceRows/);
            }
        }
    });
    it("breaks TOPN ties across definitions within one review item to bound transport rows", () => {
        expect(nativeEvidenceQueries("dataflows", "r2").detail).toContain("[review_item_key], ASC, [query_name], ASC");
        expect(nativeEvidenceQueries("daxObjects", "r2").detail).toContain("[review_item_key], ASC, [table_name], ASC, [object_type], ASC, [object_name], ASC");
    });
    it.each([
        ["executions", "gold_execution_coverage", "gold_item_executions"],
        ["dataflows", "gold_dataflows", "gold_dataflow_queries"],
        ["daxObjects", "gold_dax_object_coverage", "gold_dax_objects"],
    ] as const)("pins %s coverage and detail to one review and joins capacity by run and workspace", (lens, coverage, detail) => {
        const queries = nativeEvidenceQueries(lens, 'review"2');
        expect(queries.coverage).toContain(`'${coverage}'`);
        expect(queries.detail).toContain(`'${detail}'`);
        for (const query of Object.values(queries)) {
            expect(query).toContain('VAR ReviewRun = "review""2"');
            expect(query).toContain("[run_id] = ReviewRun");
            expect(query).toContain("'gold_workspace_risk'[run_id] = Review && 'gold_workspace_risk'[workspace_id] = Workspace");
            expect(query).toContain('"review_item_key"');
            expect(query).toContain("TOPN(5001");
            expect(query).not.toMatch(/\[(?:expression|expression_preview)\]/);
        }
    });

    it("deduplicates the full retained execution history on the server before the output limit without status filters", () => {
        const { detail, coverage } = nativeEvidenceQueries("executions", "r2", true);
        expect(detail).toContain("[run_timestamp] <= ReviewTime");
        expect(detail).toContain("VAR LastObservation = TOPN(1, FILTER(__EvidenceRows");
        expect(detail.indexOf("VAR LastObservation")).toBeLessThan(detail.indexOf("TOPN(5001"));
        expect(detail).not.toMatch(/\[status\]\s*=/);
        expect(coverage).toContain("[run_timestamp] <= ReviewTime");
        expect(coverage).not.toContain("LastObservation");
        expect(nativeEvidenceQueries("dataflows", "r2", true).detail).not.toContain("[run_timestamp] <= ReviewTime");
    });
});

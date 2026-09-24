// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type { QueryTable } from "@microsoft/fabric-app-data";
import { evidenceRowLimit, type EvidenceLens } from "@/queries/live/native-evidence";

export interface EvidenceScope {
    runId: string;
    runTimestamp: string;
    reviewItemKey: string;
    capacityId: string;
    capacityName: string;
    workspaceId: string;
    workspaceName: string;
    itemId: string;
    itemName: string;
}

export interface EvidenceCoverage extends EvidenceScope {
    status: string;
    count: number;
    flaggedCount: number | null;
    notice: string;
    itemType: string;
    source: string;
    historyScope: string;
    oldestStart: string | null;
    newestStart: string | null;
}

export interface ExecutionEvidence extends EvidenceScope {
    kind: "execution";
    executionKey: string;
    executionId: string;
    itemType: string;
    executionType: string;
    status: string;
    start: string | null;
    end: string | null;
    durationMs: number | null;
    source: string;
}

export interface DataflowEvidence extends EvidenceScope {
    kind: "query";
    queryName: string;
    signals: string[];
    signalCount: number;
    recommendation: string;
    notice: string;
}

export interface DaxObjectEvidence extends EvidenceScope {
    kind: "daxObject";
    objectType: "calculated_column" | "calculated_table" | "calculation_item";
    objectName: string;
    tableName: string;
    riskLevel: string;
    riskRank: number;
    riskScore: number;
    expressionLength: number;
    signals: string[];
    signalDetails: string;
}

export type EvidenceDetail = ExecutionEvidence | DataflowEvidence | DaxObjectEvidence;
export type EvidenceResult<T> = { status: "ready"; rows: T[] } | { status: "loading" } | { status: "error"; message: string };

const coverageStatuses = {
    executions: ["collected", "empty", "partial", "forbidden", "not_found", "error", "not_collected", "invalid_data"],
    dataflows: ["inspected", "partial", "parse_error", "unavailable", "forbidden", "unsupported", "inventory_unavailable"],
    daxObjects: ["complete", "partial", "unavailable"],
};

class EvidenceRow {
    constructor(private values: Record<string, unknown>) {}
    text(column: string, required = false): string {
        if (!(column in this.values)) throw new Error(`Missing evidence column: ${column}`);
        const value = this.values[column];
        if (value == null && !required) return "";
        if (typeof value !== "string" || (required && !value)) throw new Error(`Invalid evidence value: ${column}`);
        return value;
    }
    integer(column: string, nullable: true): number | null;
    integer(column: string, nullable?: false): number;
    integer(column: string, nullable = false): number | null {
        const value = this.values[column];
        if (value === null && nullable) return null;
        if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error(`Invalid evidence integer: ${column}`);
        return value;
    }
    utc(column: string, required = false): string | null {
        const value = this.text(column, required);
        if (!value) return null;
        // Gold dateTime values can arrive without a suffix; the contract is UTC.
        const normalized = /(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : `${value}Z`;
        const timestamp = Date.parse(normalized);
        if (!Number.isFinite(timestamp)) throw new Error(`Invalid evidence timestamp: ${column}`);
        return new Date(timestamp).toISOString();
    }
    scope(lens: EvidenceLens, allowInventoryGap = false): EvidenceScope {
        const prefix = lens === "executions" ? "item" : lens === "dataflows" ? "dataflow" : "model";
        return {
            runId: this.text("run_id", true), runTimestamp: this.utc("run_timestamp", true)!,
            reviewItemKey: this.text("review_item_key", true),
            capacityId: this.text("capacity_id"), capacityName: this.text("capacity_name"),
            workspaceId: this.text("workspace_id", true), workspaceName: this.text("workspace_name"),
            itemId: this.text(`${prefix}_id`, !allowInventoryGap), itemName: this.text(`${prefix}_name`),
        };
    }
    signals(lens: EvidenceLens) { return this.text("signal_codes").split(lens === "dataflows" ? ";" : /[;,]/).map((signal) => signal.trim()).filter(Boolean); }
}

function rows(table: QueryTable): EvidenceRow[] {
    if (table.rows.length > evidenceRowLimit) throw new Error(`Evidence exceeds the ${evidenceRowLimit.toLocaleString()}-row safety limit. Results are incomplete; use the governance model for the full scope.`);
    const columns = table.columns.map((column) => column.name.replace(/^\[|\]$/g, ""));
    return table.rows.map((row) => new EvidenceRow(Object.fromEntries(columns.map((name, index) => [name, row[index]]))));
}

export function readEvidenceCoverage(table: QueryTable, lens: EvidenceLens): EvidenceCoverage[] {
    return rows(table).map((row) => {
        const status = row.text(lens === "executions" ? "collection_status" : "definition_status", true);
        if (!coverageStatuses[lens].includes(status)) throw new Error(`Unknown ${lens} coverage status: ${status}`);
        return {
            ...row.scope(lens, lens !== "daxObjects"), status, notice: row.text("notice"),
            count: row.integer(lens === "executions" ? "observed_execution_count" : lens === "dataflows" ? "query_count" : "object_count"),
            flaggedCount: lens === "executions" ? null : row.integer(lens === "dataflows" ? "flagged_query_count" : "flagged_object_count"),
            itemType: lens === "executions" ? row.text("item_type", true) : lens === "dataflows" ? "Dataflow Gen2" : "SemanticModel",
            source: lens === "executions" ? row.text("source") : "",
            historyScope: lens === "executions" ? row.text("history_scope") : "",
            oldestStart: lens === "executions" ? row.utc("oldest_start_time") : null,
            newestStart: lens === "executions" ? row.utc("newest_start_time") : null,
        };
    });
}

export function readEvidenceDetails(table: QueryTable, lens: EvidenceLens): EvidenceDetail[] {
    return rows(table).map((row): EvidenceDetail => {
        const scope = row.scope(lens);
        if (lens === "executions") return {
            ...scope, kind: "execution", executionKey: row.text("execution_key", true),
            executionId: row.text("execution_id"), itemType: row.text("item_type", true),
            executionType: row.text("execution_type"), status: row.text("status", true),
            start: row.utc("start_time"), end: row.utc("end_time"),
            durationMs: row.integer("duration_ms", true), source: row.text("source", true),
        };
        if (lens === "dataflows") {
            if (!scope.itemId) throw new Error("Query detail cannot belong to a Dataflow inventory gap.");
            return { ...scope, kind: "query", queryName: row.text("query_name", true), signals: row.signals(lens),
                signalCount: row.integer("signal_count"), recommendation: row.text("recommendation"), notice: row.text("notice") };
        }
        const objectType = row.text("object_type", true);
        if (objectType !== "calculated_column" && objectType !== "calculated_table" && objectType !== "calculation_item") throw new Error(`Unknown DAX object type: ${objectType}`);
        return { ...scope, kind: "daxObject", objectType, objectName: row.text("object_name", true),
            tableName: row.text("table_name"), riskLevel: row.text("risk_level", true), riskRank: row.integer("risk_rank"),
            riskScore: row.integer("risk_score"), expressionLength: row.integer("expression_length"),
            signals: row.signals(lens), signalDetails: row.text("signal_details_json") };
    });
}

export function latestExecutionObservations(details: EvidenceDetail[]): EvidenceDetail[] {
    const latest = new Map<string, ExecutionEvidence>();
    for (const row of details) {
        if (row.kind !== "execution") continue;
        const previous = latest.get(row.executionKey);
        if (!previous || row.runTimestamp > previous.runTimestamp || (row.runTimestamp === previous.runTimestamp && row.runId > previous.runId)) latest.set(row.executionKey, row);
    }
    return [...latest.values()];
}

export function hasCoverage(detail: EvidenceScope, coverage: EvidenceCoverage[]) {
    return coverage.some((row) => row.reviewItemKey === detail.reviewItemKey && row.runId === detail.runId && row.workspaceId === detail.workspaceId && row.itemId === detail.itemId);
}

export function itemScopeKey(row: EvidenceScope) {
    return JSON.stringify([row.workspaceId, row.itemId]);
}

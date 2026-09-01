//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import type { QueryTable } from "@microsoft/fabric-app-data";
import type { AssessmentDimension, DaxMeasureRisk, EstateHealth, EstateItem, EstateItemType, FindingSeverity, ReviewData, ReviewDimension } from "@/lib/review-data";

export interface LiveReviewTables {
    daxModels: QueryTable;
    daxMeasures: QueryTable;
    dimensionSummary: QueryTable;
    estateNodes: QueryTable;
    findingTargets: QueryTable;
    findings: QueryTable;
    notebookSmells: QueryTable;
    modelColumns: QueryTable;
    modelTables: QueryTable;
    runSummary: QueryTable;
    semanticModels: QueryTable;
    workspaceRisk: QueryTable;
}

type RecordValue = string | number | boolean | null | undefined;
type QueryRecord = Record<string, RecordValue>;

function records(table: QueryTable): QueryRecord[] {
    const names = table.columns.map((column) => column.name.replace(/^\[|\]$/g, ""));
    return table.rows.map((row) => Object.fromEntries(names.map((name, index) => [name, row[index] as RecordValue])));
}

function text(value: RecordValue) {
    return value == null ? "" : String(value);
}

function number(value: RecordValue) {
    const result = Number(value ?? 0);
    return Number.isFinite(result) ? result : 0;
}

function dimension(value: RecordValue): ReviewDimension {
    const normalized = text(value).toLowerCase();
    if (normalized === "architecture") return "Architecture";
    if (normalized === "performance") return "Performance";
    if (normalized === "cost") return "Cost";
    if (normalized === "security") return "Security";
    if (normalized === "notebook_code") return "Notebook";
    return "Governance";
}

function assessmentDimension(value: RecordValue): AssessmentDimension {
    const normalized = text(value).toLowerCase();
    if (normalized === "operational_excellence") return "Operational excellence";
    if (normalized === "tenant_settings") return "Tenant settings";
    if (normalized === "best_practices") return "Best practices";
    return dimension(value);
}

function severity(value: RecordValue): FindingSeverity {
    const normalized = text(value).toLowerCase();
    return normalized === "critical" || normalized === "high" || normalized === "medium" ? normalized : "low";
}

function itemType(value: RecordValue): EstateItemType | null {
    const normalized = text(value).toLowerCase();
    // Estate-graph container nodes are not workspace items.
    if (normalized === "capacity" || normalized === "workspace" || normalized === "owner") return null;
    if (normalized === "semanticmodel") return "model";
    if (normalized === "lakehouse" || normalized === "warehouse" || normalized === "report" || normalized === "notebook" || normalized === "pipeline") return normalized;
    if (normalized === "app" || normalized === "appbackend") return "app";
    return "component";
}

function health(issueCount: number, riskScore = 0): EstateHealth {
    if (riskScore >= 60 || issueCount >= 5) return "risk";
    if (riskScore >= 25 || issueCount > 0) return "warning";
    return "healthy";
}

function formatBytes(value: number) {
    if (value <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
    return `${(value / 1024 ** unit).toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}

export function buildLiveReviewData(tables: LiveReviewTables): ReviewData {
    const summary = records(tables.runSummary)[0] ?? {};
    const targetRows = records(tables.findingTargets);
    const workspaceIdsByRule = new Map<string, string[]>();
    targetRows.forEach((row) => {
        const ruleId = text(row.rule_id);
        const workspaceId = text(row.workspace_id);
        if (workspaceId) workspaceIdsByRule.set(ruleId, [...(workspaceIdsByRule.get(ruleId) ?? []), workspaceId]);
    });
    const findingRows = records(tables.findings);
    const findings = findingRows.map((row) => ({
        id: text(row.rule_id),
        dimension: dimension(row.dimension),
        severity: severity(row.severity),
        title: text(row.title),
        affected: text(row.affected),
        recommendation: text(row.recommendation),
        workspaceIds: [...new Set(workspaceIdsByRule.get(text(row.rule_id)) ?? [])],
    }));
    const targetIds = new Map<string, string[]>();
    targetRows.forEach((row) => {
        const workspaceId = text(row.workspace_id);
        targetIds.set(workspaceId, [...(targetIds.get(workspaceId) ?? []), text(row.rule_id)]);
    });
    const tableStatsByModel = new Map<string, QueryRecord[]>();
    records(tables.modelTables).forEach((row) => {
        const modelId = text(row.model_id);
        tableStatsByModel.set(modelId, [...(tableStatsByModel.get(modelId) ?? []), row]);
    });
    const columnStatsByModel = new Map<string, QueryRecord[]>();
    records(tables.modelColumns).forEach((row) => {
        const modelId = text(row.model_id);
        columnStatsByModel.set(modelId, [...(columnStatsByModel.get(modelId) ?? []), row]);
    });
    const modelRows = new Map(records(tables.semanticModels).map((row) => [text(row.model_id), row]));
    const smellRows = records(tables.notebookSmells);
    const smellsByNotebook = new Map<string, QueryRecord[]>();
    smellRows.forEach((row) => {
        const key = `${text(row.workspace_name)}\u0000${text(row.notebook_name)}`;
        smellsByNotebook.set(key, [...(smellsByNotebook.get(key) ?? []), row]);
    });
    const itemsByWorkspace = new Map<string, EstateItem[]>();
    records(tables.estateNodes).forEach((row) => {
        const type = itemType(row.node_type);
        const workspaceId = text(row.workspace_id);
        if (!type || !workspaceId) return;
        const id = text(row.node_id);
        const issues = number(row.issue_count);
        const model = modelRows.get(id);
        const modelTables = tableStatsByModel.get(id) ?? [];
        const modelColumns = columnStatsByModel.get(id) ?? [];
        const smells = smellsByNotebook.get(`${text(row.workspace_name)}\u0000${text(row.node_name)}`) ?? [];
        const item: EstateItem = {
            id,
            name: text(row.node_name),
            type,
            status: health(issues),
            findingIds: type === "notebook" ? [...new Set(smells.map((smell) => text(smell.rule_id)))] : [],
            ...(model ? { modelProfile: {
                storageMode: text(model.storage_mode) || "Unknown",
                totalSize: formatBytes(number(model.total_size)),
                tables: number(model.table_count),
                columns: number(model.column_count),
                calculatedColumns: number(model.calc_column_count),
                tableStats: modelTables.map((table) => ({
                    name: text(table.table_name),
                    rows: number(table.row_count),
                    totalSize: formatBytes(number(table.total_size)),
                    totalSizeBytes: number(table.total_size),
                    dictionarySize: formatBytes(number(table.dictionary_size)),
                    columns: number(table.column_count),
                })),
                columnStats: modelColumns.map((column) => ({
                    tableName: text(column.table_name),
                    name: text(column.column_name),
                    dataType: text(column.data_type),
                    encoding: text(column.encoding),
                    cardinality: number(column.cardinality),
                    totalSize: formatBytes(number(column.total_size)),
                    totalSizeBytes: number(column.total_size),
                    dictionarySize: formatBytes(number(column.dictionary_size)),
                    calculated: Boolean(column.is_calculated),
                })),
            } } : {}),
            ...(smells.length ? { notebookProfile: {
                smellCount: smells.length,
                affectedCells: [...new Set(smells.flatMap((smell) => text(smell.cells).split(",").map((cell) => cell.trim())).filter(Boolean))].join(", "),
                notebookUrl: text(smells[0].notebook_url),
                smells: smells.map((smell) => ({
                    ruleId: text(smell.rule_id),
                    description: text(smell.rule_description),
                    severity: severity(smell.severity),
                    affectedCells: text(smell.cells),
                })),
            } } : {}),
        };
        itemsByWorkspace.set(workspaceId, [...(itemsByWorkspace.get(workspaceId) ?? []), item]);
    });
    const workspaceRows = records(tables.workspaceRisk);
    const rooms = workspaceRows.map((row) => {
        const workspaceId = text(row.workspace_id);
        const riskScore = number(row.risk_score);
        const findingIds = [...new Set(targetIds.get(workspaceId) ?? [])];
        return {
            id: workspaceId,
            name: text(row.workspace_name),
            domain: text(row.capacity_name) || "Unassigned capacity",
            capacityName: text(row.capacity_name) || "Unassigned capacity",
            floor: "ground" as const,
            status: health(number(row.issue_count), riskScore),
            riskScore,
            owner: text(row.owner) || undefined,
            items: itemsByWorkspace.get(workspaceId) ?? [],
            findingIds,
        };
    });
    const workspaceRisks = workspaceRows.map((row) => {
        const riskScore = number(row.risk_score);
        return {
            name: text(row.workspace_name),
            domain: text(row.capacity_name) || "Unassigned capacity",
            riskScore,
            issues: number(row.issue_count),
            status: riskScore >= 60 ? "red" as const : riskScore >= 25 ? "amber" as const : "green" as const,
        };
    });
    const dimensionScores = records(tables.dimensionSummary).map((row) => ({
        dimension: assessmentDimension(row.dimension),
        score: Math.round(number(row.score)),
        change: 0,
    }));
    const highRisk = number(summary.critical_fail) + number(summary.high_fail);
    const score = summary.score == null ? "N/E" : String(Math.round(number(summary.score)));
    const coverage = Math.round(number(summary.assessment_coverage));
    const evidenceGaps = number(summary.unknown_count) + number(summary.missing_evidence_count);
    const daxMeasures: DaxMeasureRisk[] = records(tables.daxMeasures).map((row) => {
        const level = text(row.risk_level).toLowerCase();
        return {
            capacityId: text(row.capacity_id),
            capacityName: text(row.capacity_name) || "Unassigned capacity",
            workspaceId: text(row.workspace_id),
            workspaceName: text(row.workspace_name),
            modelId: text(row.model_id),
            modelName: text(row.model_name),
            tableName: text(row.table_name),
            measureName: text(row.measure_name),
            riskLevel: level === "high" || level === "medium" || level === "low" ? level : "none",
            riskScore: number(row.risk_score),
            expressionLength: number(row.expression_length),
            expressionPreview: text(row.expression_preview),
            signalCodes: text(row.signal_codes),
        };
    });
    const daxModels = records(tables.daxModels);
    const availableModelCount = daxModels.filter((row) => text(row.definition_status).toLowerCase() === "available").length;
    const daxSummary = {
        measureCount: daxModels.reduce((total, row) => total + number(row.measure_count), 0),
        flaggedMeasureCount: daxModels.reduce((total, row) => total + number(row.flagged_measure_count), 0),
        modelCount: daxModels.length,
        availableModelCount,
        definitionCoverage: daxModels.length ? Math.round((availableModelCount / daxModels.length) * 100) : 0,
    };
    return {
        metrics: [
            { label: "Best-practice score", value: score, delta: "Latest review run", trend: "steady", intent: "score" },
            { label: "Critical + high", value: String(highRisk), delta: "Live findings", trend: "steady", intent: "risk" },
            { label: "Workspaces at risk", value: String(workspaceRisks.filter((workspace) => workspace.status !== "green").length), delta: `of ${workspaceRisks.length} workspaces`, trend: "steady", intent: "neutral" },
            { label: "Assessment coverage", value: `${coverage}%`, delta: `${evidenceGaps} evidence gap${evidenceGaps === 1 ? "" : "s"}`, trend: "steady", intent: coverage >= 90 ? "success" : "neutral" },
        ],
        dimensionScores,
        daxSummary,
        findings,
        workspaceRisks,
        estate: {
            name: `${text(summary.client_name) || "Fabric"} estate`,
            region: "Fabric tenant",
            capacity: `${new Set(workspaceRows.map((row) => text(row.capacity_name)).filter(Boolean)).size} capacities`,
            rooms,
        },
        daxMeasures,
        source: "live",
    };
}
// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type { EvidenceCoverage, EvidenceDetail, EvidenceScope } from "@/lib/native-evidence";
import type { EvidenceLens } from "@/queries/live/native-evidence";

const scope: EvidenceScope = {
    runId: "sample-2026-09-22", runTimestamp: "2026-09-22T08:00:00.000Z", reviewItemKey: "sample-model-review",
    capacityId: "cap-primary", capacityName: "Sample F64", workspaceId: "finance",
    workspaceName: "Sample Workspace 01", itemId: "sample-model", itemName: "Sample Model 01",
};
const coverage: EvidenceCoverage = {
    ...scope, status: "collected", count: 1, flaggedCount: null, notice: "",
    itemType: "SemanticModel", source: "powerbi_refresh_history", historyScope: "recent_retained",
    oldestStart: "2026-09-22T06:00:00.000Z", newestStart: "2026-09-22T06:00:00.000Z",
};

export const nativeEvidencePreview: Record<EvidenceLens, { coverage: EvidenceCoverage[]; detail: EvidenceDetail[] }> = {
    executions: {
        coverage: [
            coverage,
            { ...coverage, reviewItemKey: "sample-pipeline-review", itemId: "sample-pipeline", itemName: "Sample Pipeline", itemType: "DataPipeline", source: "fabric_job_instances", status: "empty", count: 0, oldestStart: null, newestStart: null },
            { ...coverage, reviewItemKey: "sample-notebook-review", capacityId: "cap-shared", capacityName: "Sample F16", workspaceId: "customer", workspaceName: "Sample Workspace 02", itemId: "sample-notebook", itemName: "Sample Notebook", itemType: "Notebook", source: "fabric_job_instances", status: "forbidden", count: 0, notice: "Job history was not accessible to the collector.", oldestStart: null, newestStart: null },
        ],
        detail: [{ ...scope, kind: "execution", executionKey: "sample-refresh", executionId: "refresh-1", itemType: "SemanticModel", executionType: "Scheduled", status: "Completed", start: "2026-09-22T06:00:00.000Z", end: "2026-09-22T06:02:00.000Z", durationMs: 120000, source: "powerbi_refresh_history" }],
    },
    dataflows: {
        coverage: [
            { ...coverage, reviewItemKey: "sample-flow-review", itemId: "sample-flow", itemName: "Sample Dataflow Gen2", itemType: "Dataflow Gen2", status: "inspected", flaggedCount: 1, source: "", historyScope: "", oldestStart: null, newestStart: null },
            { ...coverage, reviewItemKey: "sample-inventory-gap", itemId: "", itemName: "", itemType: "Dataflow Gen2", status: "inventory_unavailable", count: 0, flaggedCount: 0, notice: "Dataflow inventory could not be enumerated.", source: "", historyScope: "", oldestStart: null, newestStart: null },
        ],
        detail: [{ ...scope, reviewItemKey: "sample-flow-review", itemId: "sample-flow", itemName: "Sample Dataflow Gen2", kind: "query", queryName: "Buffered staging", signals: ["DFLOW_TABLE_BUFFER", "DFLOW_STOP_FOLDING"], signalCount: 2, recommendation: "Review buffering and folding intent with the query owner.", notice: "Syntax signals do not establish runtime impact." }],
    },
    daxObjects: {
        coverage: [{ ...coverage, status: "complete", count: 3, flaggedCount: 1, source: "", historyScope: "", oldestStart: null, newestStart: null }],
        detail: [
            { ...scope, kind: "daxObject", objectType: "calculated_column", objectName: "Revenue band", tableName: "Sales", riskLevel: "medium", riskRank: 2, riskScore: 20, expressionLength: 84, signals: ["context_transition"], signalDetails: '{"context_transition":1}' },
            { ...scope, kind: "daxObject", objectType: "calculated_table", objectName: "Calendar", tableName: "Calendar", riskLevel: "none", riskRank: 0, riskScore: 0, expressionLength: 32, signals: [], signalDetails: "{}" },
            { ...scope, kind: "daxObject", objectType: "calculation_item", objectName: "Year to date", tableName: "Time intelligence", riskLevel: "none", riskRank: 0, riskScore: 0, expressionLength: 52, signals: [], signalDetails: "{}" },
        ],
    },
};

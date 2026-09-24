// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

export type EvidenceLens = "executions" | "dataflows" | "daxObjects";
export const evidenceRowLimit = 5000;

const reviewColumns = ["run_id", "run_timestamp", "review_item_key", "workspace_id", "workspace_name"];
const contracts = {
    executionCoverage: ["gold_execution_coverage", [...reviewColumns, "item_id", "item_name", "item_type", "collection_status", "observed_execution_count", "oldest_start_time", "newest_start_time", "history_scope", "notice", "source"]],
    executions: ["gold_item_executions", [...reviewColumns, "execution_key", "execution_id", "item_id", "item_name", "item_type", "execution_type", "status", "start_time", "end_time", "duration_ms", "source"]],
    dataflows: ["gold_dataflows", [...reviewColumns, "dataflow_id", "dataflow_name", "definition_status", "query_count", "flagged_query_count", "notice"]],
    dataflowQueries: ["gold_dataflow_queries", [...reviewColumns, "dataflow_id", "dataflow_name", "query_name", "signal_codes", "signal_count", "recommendation", "notice"]],
    daxCoverage: ["gold_dax_object_coverage", [...reviewColumns, "model_id", "model_name", "definition_status", "object_count", "flagged_object_count", "notice"]],
    daxObjects: ["gold_dax_objects", [...reviewColumns, "model_id", "model_name", "table_name", "object_type", "object_name", "risk_level", "risk_rank", "risk_score", "expression_length", "signal_codes", "signal_details_json"]],
} as const;

const pairs = {
    executions: ["executionCoverage", "executions"],
    dataflows: ["dataflows", "dataflowQueries"],
    daxObjects: ["daxCoverage", "daxObjects"],
} as const;

/** Pin all queries to the shell's review; do not mix separately evaluated latest runs. */
export function nativeEvidenceQueries(lens: EvidenceLens, runId: string, history = false) {
    const query = (key: keyof typeof contracts) => {
        const [name, columns] = contracts[key];
        const table = `'${name}'`;
        const retained = history && lens === "executions";
        const detailOrder = key === "executions" ? ", [execution_key], ASC"
            : key === "dataflowQueries" ? ", [query_name], ASC"
            : key === "daxObjects" ? ", [table_name], ASC, [object_type], ASC, [object_name], ASC" : "";
        const projection = columns.map((column) => `"${column}", ${table}[${column}]`).join(",\n        ");
        const capacity = (column: string) => `"${column}", VAR Review = ${table}[run_id] VAR Workspace = ${table}[workspace_id]
            RETURN MAXX(FILTER(ALL('gold_workspace_risk'), 'gold_workspace_risk'[run_id] = Review && 'gold_workspace_risk'[workspace_id] = Workspace), 'gold_workspace_risk'[${column}])`;
        // Deduplicate across the complete retained history BEFORE limiting or filtering status.
        const dedup = retained && key === "executions" ? `
    VAR Observations = FILTER(__EvidenceRows,
        VAR Execution = ${table}[execution_key]
        VAR LastObservation = TOPN(1, FILTER(__EvidenceRows, ${table}[execution_key] = Execution),
            ${table}[run_timestamp], DESC, ${table}[run_id], DESC)
        RETURN ${table}[run_id] = MAXX(LastObservation, ${table}[run_id]))` : "\n    VAR Observations = __EvidenceRows";
        return `DEFINE
    VAR ReviewRun = "${runId.replaceAll('"', '""')}"
    VAR ReviewTime = MAXX(FILTER(ALL('gold_run_summary'), 'gold_run_summary'[run_id] = ReviewRun), 'gold_run_summary'[run_timestamp])
    VAR __EvidenceRows = FILTER(ALL(${table}), ${retained ? `${table}[run_timestamp] <= ReviewTime` : `${table}[run_id] = ReviewRun`})${dedup}
EVALUATE
TOPN(${evidenceRowLimit + 1},
    SELECTCOLUMNS(Observations,
        ${projection},
        ${capacity("capacity_id")},
        ${capacity("capacity_name")}
    ),
    [run_timestamp], DESC, [review_item_key], ASC${detailOrder}
)`;
    };
    const [coverage, detail] = pairs[lens];
    return { coverage: query(coverage), detail: query(detail) };
}

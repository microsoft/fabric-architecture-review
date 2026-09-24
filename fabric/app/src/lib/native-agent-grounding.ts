// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

const nativeDomains = [
    {
        pattern: /\b(?:executions?|refresh(?:es)?|jobs?|job instances?|(?:notebook|pipeline) (?:runs?|failures?)|run history|duration_ms|gold_item_executions|gold_execution_coverage)\b/i,
        instruction: [
            "Use gold_item_executions with gold_execution_coverage for observed native refresh/job history.",
            "execution_key is stable across review snapshots; review_item_key identifies one review observation, not an execution.",
            "For history, FIRST deduplicate execution_key using run_timestamp DESC, run_id DESC; only THEN apply the requested event-time window, status filters, counts or duration aggregates.",
            "State whether the window uses start_time or end_time, the UTC timezone, inclusive start and exclusive end.",
            "Preserve completed, failed, cancelled, in_progress, not_started, deduped, disabled and unknown separately, including any unfamiliar recorded status; unknown is not success and active/cancelled are not failed.",
            "duration_ms is nullable int64 milliseconds; NULL is unknown, never zero. Convert seconds with / 1000.0. Duration is not CU, cost or query duration.",
            "Report source (powerbi_refresh_history or fabric_job_instances), collection_status, observed_execution_count, oldest_start_time, newest_start_time, history_scope and notice.",
            "Retained API observations are bounded, not complete weekly history. empty is successful collection with zero retained executions; partial, forbidden, not_found, error, not_collected and invalid_data are gaps.",
            "Blank item IDs are inventory gaps, not artifacts. No observations does not prove no executions occurred.",
        ].join(" "),
    },
    {
        pattern: /\b(?:dataflows?|Power Query|M (?:queries|syntax|signals)|DFLOW(?:[-_]\w+)*|folding|Table\.Buffer|Value\.NativeQuery|gold_dataflow_queries|gold_dataflows)\b/i,
        instruction: [
            "Use gold_dataflows and gold_dataflow_queries for Dataflow Gen2 definition coverage and metadata-only query signals.",
            "signal_codes is semicolon-delimited: DFLOW_TABLE_BUFFER, DFLOW_STOP_FOLDING, DFLOW_NATIVE_QUERY; signal_count counts distinct codes.",
            "Report definition_status, query_count, flagged_query_count, signal_count, recommendation and notice.",
            "inspected means selected lexical checks only, not proof of folding or runtime performance. DFLOW-001 is info, never fail; DFLOW-002 reports coverage.",
            "partial, parse_error, unavailable, forbidden, unsupported and inventory_unavailable are gaps.",
            "Empty dataflow_id denotes an inventory gap: retain its notice but exclude it from artifact counts. Successful inspected extraction may have zero queries; absent or incomplete coverage is not zero.",
            "Do not request or expose M expressions, connection literals, customer rows or raw collection errors.",
        ].join(" "),
    },
    {
        pattern: /\b(?:DAX|calculated[ -](?:columns?|tables?)|calculation[ -]items?|non[ -]measure|gold_dax_objects|gold_dax_object_coverage)\b/i,
        instruction: [
            "Use gold_dax_objects with gold_dax_object_coverage for NON-MEASURE calculated_column, calculated_table and calculation_item objects.",
            "Identify objects by workspace_id, model_id, table_name, object_type and object_name; report risk_level, risk_rank, risk_score, expression_length, signal_codes and signal_details_json as static metadata only, without expressions.",
            "definition_status complete means supported-type extraction, not semantic or runtime validation; partial and unavailable are gaps. Complete coverage with zero supported objects is successful empty extraction.",
            "Measures remain in gold_dax_measures with gold_dax_models coverage. Keep measure_count/flagged_measure_count separate from object_count/flagged_object_count; never join these detail populations to count them.",
            "DAX-001/002 scoring is measure-only. Non-measure coverage is supplemental, not a new scored rule; do not invent rule IDs.",
            "Report visual calculations and format-string expressions are not covered.",
        ].join(" "),
    },
    {
        pattern: /\b(?:ARCH-016|dependenc(?:y|ies)|duplicate activit(?:y|ies)|self[ -]dependenc(?:y|ies)|pipeline (?:structure|integrity))\b/i,
        instruction: [
            "ARCH-016 is a medium static pipeline structural finding, not measured execution reliability, runtime performance or critical-path duration.",
            "Use gold_findings evidence.items and gold_finding_targets for authoritative workspace/item IDs; never attribute by names.",
            "Filter evidence.items to the selected workspace_id and its review before summarizing signal_codes, coverage_status and reason_codes, not the entire evidence_json.",
            "The structural codes are duplicate_activity, missing_dependency, self_dependency and dependency_cycle, checked within each container scope; repeated names in different containers are not duplicates.",
        ].join(" "),
    },
] as const;

/** App-side grounding for the published central agent; no alternate data source. */
export function nativeAgentGrounding(question: string): string[] {
    const allNative = /\bnative evidence\b/i.test(question);
    const matches = nativeDomains.filter(({ pattern }) => allNative || pattern.test(question));
    if (!matches.length) return [];
    return [
        "This is the central Data Agent, not the workspace-owner agent. Do not claim to inherit owner-model row-level security (RLS); access is governed by the configured central agent and its data sources.",
        "Preserve the user's explicit capacity, workspace and item IDs, review/run selection and historical interval. Chat does not inherit page filters; ask for scope if ambiguous rather than guessing names.",
        "Unless an explicit review or historical interval is requested, use each workspace's latest review: rank gold_workspaces by workspace_id, run_timestamp DESC, run_id DESC and join facts on BOTH workspace_id and run_id. Show review timestamps and retain untouched workspaces after targeted runs.",
        "Do not substitute global MAX(run_timestamp) or is_latest for current whole-estate evidence. Run scores/findings may use gold_run_summary.is_latest only when labeled latest RUN scope, not the whole estate. Never sum run scores.",
        "Join native coverage/detail pairs on review_item_key and retain their run/workspace/item identity. Derive capacity scope from the matching review's workspace assignment, not the current assignment.",
        ...matches.map(({ instruction }) => instruction),
        "Keep static syntax signals, observed executions and unavailable evidence distinct. Static DAX/M/structural signals do not prove CU, cost, duration, folding outcomes or savings; measured item duration does not identify which expression caused it.",
        "Report missing tables, access failures, unavailable evidence, partial results and truncated results explicitly. Never turn these into a successful empty answer or a clean result.",
    ];
}

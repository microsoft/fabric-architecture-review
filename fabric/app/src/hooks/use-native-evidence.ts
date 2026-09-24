// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type { QueryTable } from "@microsoft/fabric-app-data";
import { useSemanticModelQuery } from "@/hooks/use-semantic-model-query";
import { readEvidenceCoverage, readEvidenceDetails, type EvidenceResult } from "@/lib/native-evidence";
import { nativeEvidenceQueries, type EvidenceLens } from "@/queries/live/native-evidence";
import { liveReviewQueries } from "@/queries/live";

function evidenceResult<T>(result: ReturnType<typeof useSemanticModelQuery>, parse: (table: QueryTable) => T[]): EvidenceResult<T> {
    if (result.error) return { status: "error", message: result.error.message };
    if (result.isLoading || !result.data) return { status: "loading" };
    if (result.data.status === "error") return { status: "error", message: result.data.error.message };
    try {
        return { status: "ready", rows: parse(result.data.table) };
    } catch (error) {
        return { status: "error", message: error instanceof Error ? error.message : String(error) };
    }
}

export function useNativeEvidence(lens: EvidenceLens, runId: string, history: boolean) {
    const queries = nativeEvidenceQueries(lens, runId, history);
    const coverage = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: queries.coverage });
    const detail = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: queries.detail });
    return {
        coverage: evidenceResult(coverage, (table) => readEvidenceCoverage(table, lens)),
        detail: evidenceResult(detail, (table) => readEvidenceDetails(table, lens)),
        retry: () => { void coverage.refetch(); void detail.refetch(); },
    };
}

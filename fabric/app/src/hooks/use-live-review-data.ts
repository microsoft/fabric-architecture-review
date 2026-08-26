//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { useMemo } from "react";
import { useSemanticModelQuery } from "@/hooks/use-semantic-model-query";
import { buildLiveReviewData, type LiveReviewTables } from "@/lib/live-review-adapter";
import { liveReviewQueries } from "@/queries/live";

export function useLiveReviewData() {
    const dimensionSummary = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.dimensionSummary });
    const estateNodes = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.estateNodes });
    const findingTargets = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.findingTargets });
    const findings = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.findings });
    const modelColumns = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.modelColumns });
    const modelTables = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.modelTables });
    const notebookSmells = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.notebookSmells });
    const runSummary = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.runSummary });
    const semanticModels = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.semanticModels });
    const workspaceRisk = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.workspaceRisk });
    const results = useMemo(() => ({ dimensionSummary, estateNodes, findingTargets, findings, modelColumns, modelTables, notebookSmells, runSummary, semanticModels, workspaceRisk }), [dimensionSummary, estateNodes, findingTargets, findings, modelColumns, modelTables, notebookSmells, runSummary, semanticModels, workspaceRisk]);
    const failed = Object.entries(results).find(([, result]) => result.error || result.data?.status === "error");
    const loading = Object.values(results).some((result) => result.isLoading || !result.data);
    const data = useMemo(() => {
        if (loading || failed) return null;
        const tables = Object.fromEntries(Object.entries(results).map(([name, result]) => [name, result.data?.status === "success" ? result.data.table : undefined]));
        return buildLiveReviewData(tables as LiveReviewTables);
    }, [failed, loading, results]);
    return {
        data,
        error: failed ? new Error(`${failed[0]}: ${failed[1].error?.message ?? "Semantic model query failed"}`) : null,
        loading,
    };
}
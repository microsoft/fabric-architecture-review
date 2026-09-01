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
    const daxModels = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.daxModels });
    const daxMeasures = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.daxMeasures });
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
    const results = useMemo(() => ({ daxModels, daxMeasures, dimensionSummary, estateNodes, findingTargets, findings, modelColumns, modelTables, notebookSmells, runSummary, semanticModels, workspaceRisk }), [daxModels, daxMeasures, dimensionSummary, estateNodes, findingTargets, findings, modelColumns, modelTables, notebookSmells, runSummary, semanticModels, workspaceRisk]);
    const failed = Object.entries(results).find(([, result]) => result.error || result.data?.status === "error");
    const loading = Object.values(results).some((result) => result.isLoading || !result.data);
    const data = useMemo(() => {
        if (loading || failed) return null;
        const table = (name: keyof LiveReviewTables) => {
            const result = results[name];
            if (result.data?.status !== "success") throw new Error(`${name}: query returned no table`);
            return result.data.table;
        };
        const tables: LiveReviewTables = {
            daxModels: table("daxModels"),
            daxMeasures: table("daxMeasures"),
            dimensionSummary: table("dimensionSummary"),
            estateNodes: table("estateNodes"),
            findingTargets: table("findingTargets"),
            findings: table("findings"),
            modelColumns: table("modelColumns"),
            modelTables: table("modelTables"),
            notebookSmells: table("notebookSmells"),
            runSummary: table("runSummary"),
            semanticModels: table("semanticModels"),
            workspaceRisk: table("workspaceRisk"),
        };
        return buildLiveReviewData(tables);
    }, [failed, loading, results]);
    return {
        data,
        error: failed ? new Error(`${failed[0]}: ${failed[1].error?.message ?? "Semantic model query failed"}`) : null,
        loading,
    };
}
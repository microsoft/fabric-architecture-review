// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { useMemo } from "react";
import { useSemanticModelQuery } from "@/hooks/use-semantic-model-query";
import { buildLiveReviewData, type LiveReviewTables } from "@/lib/live-review-adapter";
import { liveReviewQueries } from "@/queries/live";
export function useLiveReviewData() {
    const findings = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.findings });
    const runSummary = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.runSummary });
    const semanticModels = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.semanticModels });
    const workspaceRisk = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.workspaceRisk });
    const coverage = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.coverage });
    const capacities = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.capacities });
    const findingItems = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.findingItems });
    const dimensions = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.dimensions });
    const workspaces = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.workspaces });
    const capacityItems = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.capacityItems });
    const tenantSettings = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.tenantSettings });
    // Optional: an older deployed model has no gold_model_security table, and that must not stop the app.
    const modelSecurity = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.modelSecurity });
    const modelRoles = useSemanticModelQuery({ connection: liveReviewQueries.connection, query: liveReviewQueries.modelRoles });
    const results = useMemo(() => ({findings, runSummary, semanticModels, workspaceRisk, coverage, capacities, findingItems, dimensions, workspaces, capacityItems, tenantSettings}),
        [findings, runSummary, semanticModels, workspaceRisk, coverage, capacities, findingItems, dimensions, workspaces, capacityItems, tenantSettings]);
    const failed = Object.entries(results).find(([, r]) => r.error || r.data?.status === "error");
    const loading = Object.values(results).some(r => r.isLoading || !r.data) || modelSecurity.isLoading || modelRoles.isLoading;
    const data = useMemo(() => {
        if (failed || loading) return null;
        const tables = Object.fromEntries(Object.entries(results).map(([name, r]) => {
            if (r.data?.status !== "success") throw new Error(`${name}: no architectural table`);
            return [name, r.data.table];
        })) as unknown as LiveReviewTables;
        tables.modelSecurity = !modelSecurity.error && modelSecurity.data?.status === "success" ? modelSecurity.data.table : null;
        tables.modelRoles = !modelRoles.error && modelRoles.data?.status === "success" ? modelRoles.data.table : null;
        return buildLiveReviewData(tables);
    }, [results, failed, loading, modelSecurity, modelRoles]);
    return {data, loading, error: failed ? new Error(`${failed[0]}: ${failed[1].error?.message ?? "Architectural query failed"}`) : null};
}

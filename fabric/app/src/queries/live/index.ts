//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import capacities from "./capacities.dax?raw";
import capacityItems from "./capacity-items.dax?raw";
import dimensionSummary from "./dimension-summary.dax?raw";
import daxModels from "./dax-models.dax?raw";
import daxMeasures from "./dax-measures.dax?raw";
import estateNodes from "./estate-nodes.dax?raw";
import findingTargets from "./finding-targets.dax?raw";
import findings from "./findings.dax?raw";
import modelColumns from "./model-columns.dax?raw";
import modelTables from "./model-tables.dax?raw";
import notebookSmells from "./notebook-smells.dax?raw";
import runSummary from "./run-summary.dax?raw";
import semanticModels from "./semantic-models.dax?raw";
import tenantSettingChanges from "./tenant-setting-changes.dax?raw";
import workspaceRisk from "./workspace-risk.dax?raw";

export const liveReviewQueries = {
    connection: "reviewModel",
    capacities,
    capacityItems,
    daxModels,
    daxMeasures,
    dimensionSummary,
    estateNodes,
    findingTargets,
    findings,
    modelColumns,
    modelTables,
    notebookSmells,
    runSummary,
    semanticModels,
    tenantSettingChanges,
    workspaceRisk,
} as const;
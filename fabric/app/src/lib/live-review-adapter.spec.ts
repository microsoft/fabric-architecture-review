//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { describe, expect, it } from "vitest";
import type { QueryTable } from "@microsoft/fabric-app-data";
import { buildLiveReviewData, type LiveReviewTables } from "@/lib/live-review-adapter";

function table(columns: string[], rows: unknown[][]): QueryTable {
    return { columns: columns.map((name) => ({ name: `[${name}]`, dataType: "unknown" })), rows } as QueryTable;
}

describe("buildLiveReviewData", () => {
    it("joins live review rows without inventing artifact finding edges", () => {
        const tables: LiveReviewTables = {
            runSummary: table(["run_id", "client_name", "total_findings", "critical_fail", "high_fail", "assessment_coverage", "unknown_count", "missing_evidence_count", "score"], [["run-1", "Contoso", 89, 0, 7, 96, 2, 1, 50]]),
            dimensionSummary: table(["dimension", "fail_count", "score", "worst_severity"], [["architecture", 4, 56, "high"], ["notebook_code", 2, 70, "medium"]]),
            findings: table(["rule_id", "dimension", "severity", "severity_rank", "title", "affected", "recommendation"], [["ARCH-001", "architecture", "high", 3, "Layer naming", "1 affected", "Use layers"], ["NBCODE-003", "notebook_code", "medium", 2, "Notebook smell", "1 affected", "Refactor"]]),
            findingTargets: table(["rule_id", "workspace_id", "workspace_name"], [["ARCH-001", "ws-1", "Workspace One"]]),
            workspaceRisk: table(["workspace_id", "workspace_name", "capacity_name", "owner", "item_count", "issue_count", "risk_score", "status"], [["ws-1", "Workspace One", "F64", "Owner", 2, 3, 42, "amber"]]),
            estateNodes: table(["node_id", "node_type", "node_name", "workspace_id", "workspace_name", "owner", "issue_count", "risk_score"], [["model-1", "SemanticModel", "Sales", "ws-1", "Workspace One", "Data owner", 0, 0], ["nb-1", "Notebook", "Prepare Sales", "ws-1", "Workspace One", "Engineering", 0, 0], ["capacity-1", "Capacity", "F64", "", "", "", 0, 0]]),
            semanticModels: table(["model_id", "model_name", "workspace_id", "workspace_name", "storage_mode", "total_size", "table_count", "column_count", "calc_column_count"], [["model-1", "Sales", "ws-1", "Workspace One", "Direct Lake", 1073741824, 12, 80, 3]]),
            modelTables: table(["model_id", "table_name", "row_count", "total_size", "dictionary_size", "column_count"], [["model-1", "Sales Fact", 1000, 1048576, 2048, 8]]),
            modelColumns: table(["model_id", "table_name", "column_name", "data_type", "encoding", "cardinality", "total_size", "dictionary_size", "is_calculated"], [["model-1", "Sales Fact", "Customer", "String", "Hash", 500, 524288, 4096, false]]),
            notebookSmells: table(["rule_id", "notebook_name", "workspace_name", "cells", "notebook_url"], [["NBCODE-003", "Prepare Sales", "Workspace One", "4, 8", "https://app.fabric.microsoft.com/notebook"]]),
        };

        const result = buildLiveReviewData(tables);
        const room = result.estate.rooms[0];
        const model = room.items.find((item) => item.id === "model-1");
        const notebook = room.items.find((item) => item.id === "nb-1");

        expect(result.source).toBe("live");
        expect(result.estate.name).toBe("Contoso estate");
        expect(result.metrics[3]).toMatchObject({ label: "Assessment coverage", value: "96%", delta: "3 evidence gaps" });
        expect(room.findingIds).toEqual(["ARCH-001"]);
        expect(room.owner).toBe("Owner");
        expect(room.capacityName).toBe("F64");
        expect(result.findings[0].workspaceIds).toEqual(["ws-1"]);
        expect(model?.findingIds).toEqual([]);
        expect(model?.governance).toBeUndefined();
        expect(model?.modelProfile).toMatchObject({ storageMode: "Direct Lake", totalSize: "1.0 GB", tables: 12, tableStats: [{ name: "Sales Fact", rows: 1000 }], columnStats: [{ name: "Customer", cardinality: 500 }] });
        expect(notebook?.findingIds).toEqual(["NBCODE-003"]);
        expect(notebook?.notebookProfile).toMatchObject({ smellCount: 1, affectedCells: "4, 8" });
        expect(room.items).toHaveLength(2);
    });
});
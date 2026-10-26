// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { describe, expect, it } from "vitest";
import { liveReviewQueries } from "@/queries/live";
describe("replacement architecture queries", () => {
    it("queries typed source coverage rather than execution or expression history", () => {
        expect(liveReviewQueries.coverage).toContain("gold_source_coverage");
        for (const column of ["source_contract", "observed_at", "freshness_status", "profile_sha256"]) {
            expect(liveReviewQueries.coverage).toContain(column);
        }
        expect(Object.keys(liveReviewQueries)).toEqual(["connection", "findings", "runSummary", "dimensions", "workspaces", "capacityItems", "tenantSettings", "semanticModels", "workspaceRisk", "coverage", "findingItems", "capacities", "modelSecurity", "modelRoles"]);
    });
});

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { describe, expect, it } from "vitest";
import { liveReviewQueries } from "@/queries/live";
import { nativeAgentGrounding } from "./native-agent-grounding";
describe("architectural source allowlist", () => {
    it("never generates queries for excluded native optimizer subjects", () => {
        const queries = Object.values(liveReviewQueries).join("\n");
        expect(queries).not.toMatch(/gold_(?:dax|model_tables|model_columns|item_executions|execution_coverage|cost|dataflow_queries)/i);
        expect(queries).toContain("profile_sha256");
        expect(queries).toContain("rule_id] IN");
    });
    it("redirects excluded questions instead of supplying native optimization grounding", () => {
        const instructions = nativeAgentGrounding().join(" ");
        expect(instructions).toContain("Decline optimization");
        expect(instructions).toContain("Model security (SEC-024 to SEC-027)");
        expect(instructions).toContain("never no security");
        expect(instructions).not.toContain("gold_item_executions");
        expect(instructions).not.toContain("gold_dax_measures");
    });
});

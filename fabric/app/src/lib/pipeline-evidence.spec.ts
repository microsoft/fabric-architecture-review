// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { describe, expect, it } from "vitest";
import { readPipelineEvidence } from "@/lib/pipeline-evidence";

describe("pipeline structural evidence", () => {
    it("retains all structural checks and concrete identifiers without inventing targets", () => {
        expect(readPipelineEvidence(JSON.stringify({ items: [{ workspace_id: "w1", item_id: "p1", item_name: "Pipeline", signal_codes: ["duplicate_activity", "missing_dependency", "self_dependency", "dependency_cycle"], coverage_status: "partial" }] }))).toEqual({
            status: "ready", items: [{ workspaceId: "w1", itemId: "p1", itemName: "Pipeline", signals: ["duplicate_activity", "missing_dependency", "self_dependency", "dependency_cycle"], coverage: "partial" }],
        });
    });
    it.each(["", "not json", "{}", '{"items":[{"workspace_name":"Name","item_name":"Name","signal_codes":[]}]}'])("surfaces malformed or unscoped evidence: %s", (value) => {
        expect(readPipelineEvidence(value).status).toBe("error");
    });
});

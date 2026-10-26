// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReviewWorkbench } from "./review-workbench";
import { previewReviewData } from "@/lib/review-data";
vi.mock("@/components/agent-panel", () => ({AgentPanel: () => <aside>Architectural Agent</aside>}));
describe("architectural evidence UI", () => {
    it("has explicit no-score and no optimizer navigation", () => {
        render(<ReviewWorkbench />);
        expect(screen.getByText("No assessable controls")).toBeInTheDocument();
        expect(screen.queryByRole("button", {name: /DAX|execution|cost|efficiency/i})).not.toBeInTheDocument();
    });
    it("renders typed coverage provenance", () => {
        render(<ReviewWorkbench data={{...previewReviewData, coverage: [{workspaceId: "w", family: "pipeline_definition",
            status: "missing_evidence", source: "definitions-v1", observedAt: "2026-10-01", freshness: "unknown", notice: "Unavailable"}]}} />);
        fireEvent.click(screen.getByRole("button", {name: "Source coverage"}));
        expect(screen.getByText("definitions-v1")).toBeInTheDocument();
        expect(screen.getByText("missing evidence")).toBeInTheDocument();
    });
});

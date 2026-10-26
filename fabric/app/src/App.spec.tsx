// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { ReviewWorkbench } from "@/components/review-workbench";
import { previewReviewData } from "@/lib/review-data";
vi.mock("@/components/agent-panel", () => ({AgentPanel: () => <aside>Architectural Agent</aside>}));
describe("App architecture profile", () => {
    it("renders an accessible workbench with honest no-score preview", () => {
        render(<App />);
        expect(screen.getByRole("heading", {name: "Estate, prioritized"})).toBeInTheDocument();
        expect(screen.getByText("No assessable controls")).toBeInTheDocument();
    });
    it("does not render excluded rules even when supplied directly", () => {
        render(<ReviewWorkbench data={{...previewReviewData, findings: [{id: "DAX-001", dimension: "Architecture",
            severity: "high", title: "Forbidden expression", affected: "", recommendation: "", workspaceIds: []}]}} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        expect(screen.queryByText("Forbidden expression")).not.toBeInTheDocument();
    });
    it("rejects incompatible direct payloads", () => {
        render(<ReviewWorkbench data={{...previewReviewData, profileHash: "old", score: 100}} />);
        expect(screen.getByRole("alert")).toHaveTextContent("Incompatible profile");
        expect(screen.getByRole("alert")).toHaveTextContent("Run the pipeline again");
        expect(screen.getByText("No assessable controls")).toBeInTheDocument();
    });
});

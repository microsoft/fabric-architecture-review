// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewWorkbench } from "@/components/review-workbench";
import { architectureProfile, architectureProfileHash, previewReviewData, type ReviewFinding } from "@/lib/review-data";

vi.mock("@/components/agent-panel", () => ({AgentPanel: () => <aside>Architectural Agent</aside>}));

const finding = (over: Partial<ReviewFinding>): ReviewFinding => ({
    id: "ARCH-001", dimension: "Architecture", severity: "high", title: "Workspace holds only declared item types",
    affected: "", recommendation: "", workspaceIds: [], status: "pass", disposition: "control", scored: true, ...over,
});
const data = (findings: ReviewFinding[]) => ({
    ...previewReviewData, source: "live" as const, latestRunId: "run", profileId: architectureProfile,
    profileHash: architectureProfileHash, findings,
});

afterEach(() => vi.restoreAllMocks());

describe("Architectural findings table", () => {
    it("lists one row per workspace for a rule, failures first, without duplicate React keys", () => {
        const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
        render(<ReviewWorkbench data={data([
            finding({workspaceName: "sales", workspaceIds: ["w1"], status: "pass"}),
            finding({workspaceName: "finance", workspaceIds: ["w2"], status: "fail"}),
            finding({id: "SEC-003", workspaceName: "", status: "fail", title: "Publish to web"}),
        ])} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        const rows = screen.getAllByRole("row").slice(1);
        expect(rows).toHaveLength(3);
        expect(within(rows[0]).getByText("finance")).toBeInTheDocument();
        expect(within(rows[1]).getByText("Tenant-wide")).toBeInTheDocument();
        expect(within(rows[2]).getByText("sales")).toBeInTheDocument();
        expect(errors.mock.calls.flat().join(" ")).not.toContain("same key");
    });

    it("shows the next action and, on demand, what the result means and how to fix it", () => {
        render(<ReviewWorkbench data={data([finding({workspaceName: "sales", status: "unknown", recommendation: "old",
            nextAction: "Declare the requirement.", statusMeaning: "Nothing is declared.", whyItMatters: "Drift goes unseen.",
            howToFix: "Set the variable.", configure: "policy__allowed_item_types"})])} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        expect(screen.getByText("Declare the requirement.")).toBeInTheDocument();
        expect(screen.queryByText("old")).not.toBeInTheDocument();
        expect(screen.getByText("What it means")).toBeInTheDocument();
        for (const text of ["Nothing is declared.", /Drift goes unseen/, /Set the variable/, /policy__allowed_item_types/]) {
            expect(screen.getByText(text)).toBeInTheDocument();
        }
    });

    it("lists the items behind a finding for its own workspace only", () => {
        const item = (over = {}) => ({ruleId: "ARCH-013", workspaceId: "w1", itemId: "p1", itemName: "Load sales", itemType: "DataPipeline",
            reason: "An activity has no retry", ...over});
        render(<ReviewWorkbench data={{...data([finding({id: "ARCH-013", workspaceName: "finance", workspaceIds: ["w1"], status: "info"}),
            finding({id: "ARCH-013", workspaceName: "sales", workspaceIds: ["w2"], status: "info"})]),
            items: [item(), item({itemId: "p2", itemName: "Load orders", reason: ""}), item({workspaceId: "w2", itemId: "p3", itemName: "Other"})]}} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        expect(screen.getByText("2 items to look at")).toBeInTheDocument();
        expect(screen.getByText("1 item to look at")).toBeInTheDocument();
        expect(screen.getByText(/Load sales/)).toBeInTheDocument();
        expect(screen.getAllByText(/An activity has no retry/).length).toBeGreaterThan(0);
        expect(screen.getByText(/Other/)).toBeInTheDocument();
    });

    it("shows no item list when a finding names no items", () => {
        render(<ReviewWorkbench data={data([finding({workspaceName: "sales", workspaceIds: ["w1"]})])} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        expect(screen.queryByText(/to look at/)).not.toBeInTheDocument();
    });

    it("falls back to the recommendation when there is no guidance", () => {
        render(<ReviewWorkbench data={data([finding({workspaceName: "sales", recommendation: "Do the thing."})])} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        expect(screen.getByText("Do the thing.")).toBeInTheDocument();
        expect(screen.queryByText("What it means")).not.toBeInTheDocument();
    });

    it("filters by status", () => {
        render(<ReviewWorkbench data={data([
            finding({workspaceName: "sales", status: "pass"}), finding({workspaceName: "finance", status: "fail"}),
        ])} />);
        fireEvent.click(screen.getByRole("button", {name: "Findings"}));
        fireEvent.change(screen.getByLabelText(/Status/), {target: {value: "fail"}});
        expect(screen.getAllByRole("row")).toHaveLength(2);
        expect(screen.getByText("finance")).toBeInTheDocument();
        expect(screen.queryByText("sales")).not.toBeInTheDocument();
    });
});

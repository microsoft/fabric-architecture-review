// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { EvidencePanel, NativeEvidenceView } from "@/components/native-evidence";
import { ReviewDataProvider } from "@/hooks/review-data.context";
import { previewReviewData } from "@/lib/review-data";
import { nativeEvidencePreview } from "@/lib/native-evidence-preview";
import { itemScopeKey, type EvidenceCoverage, type EvidenceDetail } from "@/lib/native-evidence";

const example = nativeEvidencePreview.executions;
function panel(coverage: EvidenceCoverage[] = example.coverage, detail: EvidenceDetail[] = example.detail) {
    return render(<EvidencePanel lens="executions" history coverage={{ status: "ready", rows: coverage }} detail={{ status: "ready", rows: detail }} />);
}

describe("Native evidence", () => {
    it("navigates all three lenses without querying in preview and does not count inventory gaps as artifacts", () => {
        render(<NativeEvidenceView />);
        expect(screen.getByText(/Collection succeeded with no retained executions/)).toBeInTheDocument();
        expect(screen.getByText("forbidden")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Dataflow Gen2" }));
        expect(screen.getByText("1 artifact coverage rows · 1 inventory gaps")).toBeInTheDocument();
        expect(screen.getByLabelText("Item")).not.toHaveTextContent("Workspace inventory gap");
        expect(screen.getByText("DFLOW_TABLE_BUFFER · DFLOW_STOP_FOLDING")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Calculated DAX objects" }));
        expect(screen.getByText(/DAX-001\/002 measure counts and scoring remain separate/)).toBeInTheDocument();
        expect(screen.getByText(/calculated_column · Table: Sales/)).toBeInTheDocument();
        expect(screen.getByText(/calculated_table · Table: Calendar/)).toBeInTheDocument();
        expect(screen.getByText(/calculation_item · Table: Time intelligence/)).toBeInTheDocument();
    });

    it("cascades capacity, workspace and item filters by IDs even when names are duplicated", () => {
        const other = { ...example.coverage[0], reviewItemKey: "other", workspaceId: "other-workspace", capacityId: "other-capacity", itemId: "other-item" };
        panel([...example.coverage, other], [...example.detail, { ...example.detail[0], ...other }]);
        fireEvent.change(screen.getByLabelText("Capacity"), { target: { value: "other-capacity" } });
        expect(screen.getByLabelText("Workspace").querySelectorAll("option")).toHaveLength(2);
        fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: other.workspaceId } });
        fireEvent.change(screen.getByLabelText("Item"), { target: { value: itemScopeKey(other) } });
        expect(screen.getByText("1 artifact coverage rows · 0 inventory gaps")).toBeInTheDocument();
        expect(screen.queryByText("Sample Notebook")).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText("Capacity"), { target: { value: "all" } });
        expect(screen.getByLabelText("Workspace")).toHaveValue("all");
        expect(screen.getByLabelText("Item")).toHaveValue("all");
    });

    it("deduplicates history before status filters and does not hide coverage", () => {
        const row = example.detail[0];
        if (row.kind !== "execution") throw new Error("Expected execution fixture");
        panel(example.coverage, [
            { ...row, runTimestamp: "2026-09-20T00:00:00Z", status: "Failed" },
            row,
            { ...row, executionKey: "different", status: "Failed", durationMs: null },
        ]);
        expect(screen.getByText("2 unique observed executions in view")).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText("Execution status"), { target: { value: "Failed" } });
        expect(screen.getByText("1 unique observed executions in view")).toBeInTheDocument();
        expect(screen.getByText("3 artifact coverage rows · 0 inventory gaps")).toBeInTheDocument();
        expect(screen.getByText("empty")).toBeInTheDocument();
        expect(screen.getByText("Unavailable", { selector: "dd" })).toBeInTheDocument();
        expect(screen.queryByText("0 ms")).not.toBeInTheDocument();
    });

    it("renders query errors independently of successful details and exposes retry", () => {
        const retry = vi.fn();
        render(<EvidencePanel lens="executions" history={false} coverage={{ status: "error", message: "403 Forbidden" }} detail={{ status: "ready", rows: example.detail }} retry={retry} />);
        expect(screen.getByText("403 Forbidden")).toBeInTheDocument();
        expect(screen.getByText(/coverage could not be verified/)).toBeInTheDocument();
        expect(screen.getByText("120,000 ms")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Retry evidence queries" }));
        expect(retry).toHaveBeenCalledOnce();
    });

    it("does not present absent coverage or orphan detail rows as a pass", () => {
        panel([], example.detail);
        expect(screen.getByText(/No coverage rows in this scope/)).toBeInTheDocument();
        expect(screen.getByRole("alert")).toHaveTextContent("no matching review-item coverage");
    });

    it("retains execution inventory gaps when an item is selected without counting them as artifacts", () => {
        const gap = { ...example.coverage[0], reviewItemKey: "gap", itemId: "", itemName: "", status: "not_collected", count: 0, notice: "missing_item_identity" };
        panel([...example.coverage, gap]);
        fireEvent.change(screen.getByLabelText("Item"), { target: { value: itemScopeKey(example.coverage[0]) } });
        expect(screen.getByText("1 artifact coverage rows · 1 inventory gaps")).toBeInTheDocument();
        expect(screen.getByText("missing_item_identity")).toBeInTheDocument();
        expect(screen.getByText("Workspace inventory gap")).toBeInTheDocument();
    });

    it("keeps coverage when the detail query is unavailable", () => {
        render(<EvidencePanel lens="executions" history={false} coverage={{ status: "ready", rows: example.coverage }} detail={{ status: "error", message: "Missing table gold_item_executions" }} />);
        expect(screen.getByRole("alert")).toHaveTextContent("Missing table gold_item_executions");
        expect(screen.getByText("empty")).toBeInTheDocument();
        expect(screen.queryByText("0 unique observed executions in view")).not.toBeInTheDocument();
    });

    it("shows loading explicitly and refuses live queries without a review ID", () => {
        const { unmount } = render(<EvidencePanel lens="dataflows" history={false} coverage={{ status: "loading" }} detail={{ status: "loading" }} />);
        expect(screen.getAllByRole("status")).toHaveLength(2);
        unmount();
        render(<ReviewDataProvider value={{ ...previewReviewData, source: "live" }}><NativeEvidenceView /></ReviewDataProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("Review run ID is unavailable");
        expect(screen.queryByText("Synthetic sample evidence")).not.toBeInTheDocument();
    });

    it("selects review/history semantics without exposing an execution selector on static lenses", () => {
        render(<NativeEvidenceView />);
        expect(screen.getByText(/A review is one FAR pipeline run/)).toBeInTheDocument();
        expect(screen.getByLabelText("Observation window")).toHaveDisplayValue("Collected in this FAR review");
        expect(screen.getByText(/Their execution dates can be earlier than the review itself/)).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText("Observation window"), { target: { value: "history" } });
        expect(screen.getByText(/showing each execution once using its latest observation/)).toBeInTheDocument();
        expect(screen.getByText(/Capacity assignment is taken from each observation/)).toBeInTheDocument();
        fireEvent.click(within(screen.getByRole("group", { name: "Evidence lens" })).getByRole("button", { name: "Dataflow Gen2" }));
        expect(screen.queryByLabelText("Observation window")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Execution status")).not.toBeInTheDocument();
    });
});

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewWorkbench } from "@/components/review-workbench";
import { architectureProfile, architectureProfileHash, previewReviewData, type ModelSecurity } from "@/lib/review-data";

vi.mock("@/components/agent-panel", () => ({AgentPanel: () => <aside>Architectural Agent</aside>}));

const model = (over: Partial<ModelSecurity>): ModelSecurity => ({
    workspaceId: "w1", workspaceName: "Sales", capacityName: "F8", modelId: "m1", modelName: "Revenue", rls: "present", ols: "absent",
    rlsRequired: false, olsRequired: false, rlsOutcome: "not_required", olsOutcome: "not_required", inspection: "complete", inspectionReason: "",
    roleDetail: "not_collected", roles: [], ...over,
});
const data = (modelSecurity: ModelSecurity[], available = true) => ({
    ...previewReviewData, source: "live" as const, latestRunId: "run", profileId: architectureProfile,
    profileHash: architectureProfileHash, modelSecurity, modelSecurityAvailable: available,
});
const open = () => fireEvent.click(screen.getByRole("button", {name: "Model security"}));

afterEach(() => vi.restoreAllMocks());

describe("Model security view", () => {
    it("counts models by capacity and keeps unknown apart from no security", () => {
        render(<ReviewWorkbench data={data([
            model({}),
            model({modelId: "m2", modelName: "Payroll", rls: "absent", ols: "absent", rlsRequired: true, rlsOutcome: "fail"}),
            model({modelId: "m3", modelName: "Legacy", rls: "unknown", ols: "unknown", inspection: "unavailable", inspectionReason: "definition_unreadable", capacityName: "F16"}),
        ])} />);
        open();
        const stats = within(screen.getByLabelText("Model security metrics"));
        expect(stats.getByText("Models inspected").nextElementSibling).toHaveTextContent("3");
        expect(stats.getByText("Neither configured").nextElementSibling).toHaveTextContent("1");
        expect(stats.getByText("Not fully inspected").nextElementSibling).toHaveTextContent("1");
        expect(stats.getByText("Declared requirement not met").nextElementSibling).toHaveTextContent("1");
        const rollup = within(screen.getByLabelText("Security by capacity")).getAllByRole("row").slice(1);
        expect(rollup).toHaveLength(2);
        expect(screen.getByText("definition_unreadable")).toBeInTheDocument();
        expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
    });

    it("filters to the models whose declared requirement is not met", () => {
        render(<ReviewWorkbench data={data([
            model({}), model({modelId: "m2", modelName: "Payroll", rls: "absent", rlsRequired: true, rlsOutcome: "fail"}),
        ])} />);
        open();
        fireEvent.change(screen.getByLabelText(/Show/), {target: {value: "gaps"}});
        expect(screen.getByText("Payroll")).toBeInTheDocument();
        expect(screen.queryByText("Revenue")).not.toBeInTheDocument();
    });

    it("says so when the review has no model-security data", () => {
        render(<ReviewWorkbench data={data([], false)} />);
        open();
        expect(screen.getByText(/no model-security data/)).toBeInTheDocument();
    });

    it("lists a model's roles only when role detail was collected, and never reads no roles as no detail", () => {
        const role = {permission: "read", readsData: true, rowFilterTables: 0, dynamicFilterTables: 0, constantFilterTables: 0, hiddenTables: 0, hiddenColumns: 0};
        render(<ReviewWorkbench data={data([
            model({modelId: "m1", modelName: "Revenue", roleDetail: "collected", roles: [
                {...role, name: "Regional", filterKind: "dynamic", rowFilterTables: 2, dynamicFilterTables: 2},
                {...role, name: "Hidden", filterKind: "none", hiddenTables: 1, hiddenColumns: 3},
                {...role, name: "Admins", permission: "administrator", readsData: false, filterKind: "not_applicable"}]}),
            model({modelId: "m2", modelName: "Empty", roleDetail: "collected"}),
            model({modelId: "m3", modelName: "Off", roleDetail: "not_collected"}),
            model({modelId: "m4", modelName: "Bad", roleDetail: "unavailable", inspection: "unavailable"}),
            model({modelId: "m5", modelName: "Some", roleDetail: "partial", inspection: "partial"}),
        ])} />);
        open();
        expect(screen.getByText("3 roles")).toBeInTheDocument();
        expect(screen.getByText("Regional")).toBeInTheDocument();
        expect(screen.getByText(/Dynamic filter, uses the user's identity · 2 tables with a row filter · 0 hidden tables · 0 hidden columns/)).toBeInTheDocument();
        expect(screen.getByText(/No row filter · 0 tables with a row filter · 1 hidden table · 3 hidden columns/)).toBeInTheDocument();
        expect(screen.getByText("Not filtered by this role")).toBeInTheDocument();
        expect(screen.getByText("No roles defined")).toBeInTheDocument();
        expect(screen.getByText("Role detail not collected")).toBeInTheDocument();
        expect(screen.getByText("Roles could not be read")).toBeInTheDocument();
        expect(screen.getByText("Roles could not be fully read")).toBeInTheDocument();
    });
});

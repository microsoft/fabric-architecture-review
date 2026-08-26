//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import App from "@/App";

describe("App", () => {
    it("renders without throwing", () => {
        expect(() => render(<App />)).not.toThrow();
    });

    it("renders the architecture review workbench", () => {
        render(<App />);
        expect(screen.getAllByText("FAR")).toHaveLength(2);
        expect(screen.getByRole("heading", { name: "Estate, prioritized" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Findings by severity" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Risk versus item count" })).toBeInTheDocument();
    });

    it("opens a plotted workspace in its findings queue", () => {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: /Open findings for Sample Workspace 01/ }));

        expect(screen.getByRole("heading", { name: "Risk decision queue" })).toBeInTheDocument();
        expect(screen.getByLabelText("Workspace")).toHaveValue("finance");
    });

    it("keeps governance and semantic model optimization in separate report-aligned views", () => {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: "Governance" }));

        expect(screen.getByRole("heading", { name: "Govern ownership and trust" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Ownership and governance signals" })).toBeInTheDocument();
        expect(screen.queryByText("Calculated columns")).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Semantic model optimization" }));

        expect(screen.getByRole("heading", { name: "Optimize semantic models" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Semantic models by storage mode" })).toBeInTheDocument();
        expect(screen.getAllByText("Sample Model 01")).toHaveLength(2);
        expect(screen.getByText("Calculated columns")).toBeInTheDocument();
    });

    it("groups findings by workspace and filters the decision queue", () => {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: "Findings" }));

        expect(screen.getByRole("heading", { name: "Sample Workspace 01" })).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "customer" } });

        expect(screen.getByRole("heading", { name: "Sample Workspace 02" })).toBeInTheDocument();
        expect(screen.queryByRole("heading", { name: "Sample Workspace 01" })).not.toBeInTheDocument();
        expect(screen.getByText("GOV-001")).toBeInTheDocument();
    });

    it("drills into selectable VertiPaq details for one semantic model", () => {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: "Semantic model optimization" }));
        fireEvent.click(screen.getByRole("button", { name: /Sample Model 01.*Direct Lake/ }));

        expect(screen.getByRole("heading", { name: "Largest tables" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Largest columns" })).toBeInTheDocument();
        expect(screen.getByText("FactTransactions")).toBeInTheDocument();
        expect(screen.getByText(/TransactionId/)).toBeInTheDocument();
        expect(screen.getAllByRole("columnheader")).toHaveLength(10);
    });

    it("opens the selected overview finding in its workspace queue", () => {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: /ARCH-009.*Critical lineage path/ }));

        expect(screen.getByRole("heading", { name: "Risk decision queue" })).toBeInTheDocument();
        expect(screen.getByLabelText("Workspace")).toHaveValue("finance");
        expect(screen.getByText("ARCH-009")).toBeInTheDocument();
        expect(screen.queryByText("GOV-001")).not.toBeInTheDocument();
    });

    it("combines performance and cost while preserving architecture and notebooks", () => {
        render(<App />);

        fireEvent.click(screen.getByRole("button", { name: "Performance + cost" }));
        expect(screen.getByRole("heading", { name: "Balance speed and capacity efficiency" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Performance and cost posture" })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Architecture" }));
        expect(screen.getByRole("heading", { name: "Understand design and lineage" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Estate composition" })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Notebooks" }));
        expect(screen.getByRole("heading", { name: "Improve notebook engineering" })).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: "Notebook smells by severity" })).toBeInTheDocument();
        expect(screen.getByText("Unbounded Spark collect or toPandas action can exhaust driver memory.")).toBeInTheDocument();
        expect(screen.getByText("Affected cells")).toBeInTheDocument();
        expect(screen.getByText("4, 7")).toBeInTheDocument();
    });

    it("reports supported ownership separately from uncollected classification", () => {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: "Governance" }));
        fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "supply" } });

        expect(screen.getByRole("heading", { name: "Workspace ownership" })).toBeInTheDocument();
        expect(screen.getByText("Classification is not collected")).toBeInTheDocument();
        expect(screen.getByText(/No classification percentage is inferred/)).toBeInTheDocument();
    });
});

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EstateMap } from "./estate-map";
import { ReviewDataProvider } from "@/hooks/review-data.context";
import { createCampusLayout, getCampusDetailLevel } from "@/lib/campus-layout";
import { previewReviewData, type ReviewData, type WorkspaceRoom } from "@/lib/review-data";

const room = (id: string, name: string, capacityName: string, over: Partial<WorkspaceRoom> = {}): WorkspaceRoom => ({
    id, name, domain: "Production", capacityName, floor: "ground", status: "warning", riskScore: 40, findingIds: ["GOV-001"], items: [
        {id: `${id}-model`, name: `${name} model`, type: "model", fabricType: "SemanticModel", status: "risk", findingIds: ["GOV-001"], storageMode: "Import"},
        {id: `${id}-report`, name: `${name} report`, type: "report", fabricType: "Report", status: "healthy", findingIds: []},
        {id: `${id}-other`, name: `${name} agent`, type: "component", fabricType: "DataAgent", status: "healthy", findingIds: []},
    ], ...over,
});
const data: ReviewData = {...previewReviewData, source: "live", findings: [{id: "GOV-001", dimension: "Governance", severity: "high",
    title: "Workspace has a single administrator", affected: "", recommendation: "Add a second administrator.", workspaceIds: ["a"]}],
    estate: {...previewReviewData.estate, rooms: [room("a", "Finance", "F8"), room("b", "Sales", "F8"), room("c", "Ops", "F4")]}};
const renderMap = (onOpenArea?: () => void) => render(<ReviewDataProvider value={data}><EstateMap onOpenArea={onOpenArea} /></ReviewDataProvider>);

describe("EstateMap", () => {
    it("groups building floors by Fabric item type and keeps unknown types as other items", () => {
        renderMap();
        fireEvent.click(screen.getByRole("button", {name: /Finance/}));
        expect(screen.getByRole("region", {name: "Semantic models floor"})).toBeInTheDocument();
        expect(screen.getByRole("region", {name: "Reports floor"})).toBeInTheDocument();
        expect(screen.getByRole("region", {name: "Other items floor"})).toBeInTheDocument();
    });

    it("allocates unique land plots from the real Stockholm street network", () => {
        const small = createCampusLayout(5);
        const large = createCampusLayout(80);
        const metropolitan = createCampusLayout(1_200);
        const plots = new Set(large.positions.map((position) => `${position.x}:${position.z}`));
        expect(large.positions).toHaveLength(80);
        expect(plots.size).toBe(80);
        expect(large.roads.length).toBeGreaterThan(200);
        expect(large.parks.length).toBeGreaterThan(0);
        expect(large.labels.map((label) => label.name)).toEqual(expect.arrayContaining(["SÖDERMALM", "NORRMALM", "DJURGÅRDEN", "KUNGSHOLMEN", "JOHANNESHOV"]));
        expect(metropolitan.islandRadius).toBeGreaterThan(small.islandRadius);
    });

    it("scales to a four-digit tenant", () => {
        const massive = createCampusLayout(1_200);
        expect(massive.positions).toHaveLength(1_200);
        expect(new Set(massive.positions.map((position) => `${position.x}:${position.z}`)).size).toBe(1_200);
        expect(getCampusDetailLevel(1_200)).toBe("minimal");
    });

    it("offers an accessible fallback and opens a workspace inspector with its capacity and flags", () => {
        renderMap();
        expect(screen.getByText(/3D rendering is unavailable/i)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: /Finance/}));
        expect(screen.getByRole("complementary", {name: "Finance workspace inspector"})).toBeInTheDocument();
        expect(screen.getByText(/F8 · Risk 40\/100/)).toBeInTheDocument();
        expect(screen.getByText("Workspace has a single administrator")).toBeInTheDocument();
    });

    it("returns to the full-campus state", () => {
        renderMap();
        fireEvent.click(screen.getByRole("button", {name: /Sales/}));
        fireEvent.click(screen.getByRole("button", {name: "View full city"}));
        expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
        expect(screen.getByText(/Select a building to inspect/i)).toBeInTheDocument();
    });

    it("filters the estate by capacity and clears an incompatible selection", () => {
        renderMap();
        fireEvent.click(screen.getByRole("button", {name: /Finance/}));
        fireEvent.change(screen.getByRole("combobox", {name: "Filter estate by capacity"}), {target: {value: "F4"}});
        expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", {name: /Finance/})).not.toBeInTheDocument();
        expect(screen.getByRole("button", {name: /Ops/})).toBeInTheDocument();
    });

    it("shows the identity and type of a selected item and opens the findings for its workspace", () => {
        const onOpenArea = vi.fn();
        renderMap(onOpenArea);
        fireEvent.click(screen.getByRole("button", {name: /Finance/}));
        fireEvent.click(screen.getByRole("button", {name: /Semantic models.*Finance model/}));
        expect(screen.getByText("Item details")).toBeInTheDocument();
        expect(screen.getByText("a-model")).toBeInTheDocument();
        expect(screen.getByText("Import")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: "Open findings for this workspace"}));
        expect(onOpenArea).toHaveBeenCalledWith("findings", {workspaceId: "a", itemId: "a-model"});
    });
});

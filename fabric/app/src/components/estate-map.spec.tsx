//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EstateMap } from "@/components/estate-map";
import { createCampusLayout, getCampusDetailLevel } from "@/lib/campus-layout";

describe("EstateMap", () => {
    it("groups building floors by Fabric item type", () => {
        render(<EstateMap />);
        fireEvent.click(screen.getByRole("button", { name: /Sample Workspace 01/ }));

        expect(screen.getByRole("region", { name: "Semantic models floor" })).toBeInTheDocument();
        expect(screen.getByRole("region", { name: "Reports floor" })).toBeInTheDocument();
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
        expect(large.city.width).toBeGreaterThan(0);
        expect(metropolitan.islandRadius).toBeGreaterThan(small.islandRadius);
    });

    it("scales to a four-digit tenant and distributes workspaces across the city", () => {
        const large = createCampusLayout(80);
        const massive = createCampusLayout(1_200);
        const enterprise = createCampusLayout(5_000);
        const plots = new Set(massive.positions.map((position) => `${position.x}:${position.z}`));
        const enterprisePlots = new Set(enterprise.positions.map((position) => `${position.x}:${position.z}`));
        const spread = (layout: ReturnType<typeof createCampusLayout>) => {
            const xs = layout.positions.map((position) => position.x);
            return Math.max(...xs) - Math.min(...xs);
        };

        expect(massive.positions).toHaveLength(1_200);
        expect(plots.size).toBe(1_200);
        expect(massive.city.width).toBeGreaterThan(large.city.width);
        expect(enterprise.positions).toHaveLength(5_000);
        expect(enterprisePlots.size).toBe(5_000);
        expect(spread(enterprise)).toBeGreaterThan(spread(large));
        expect(getCampusDetailLevel(1_200)).toBe("minimal");
    });

    it("offers an accessible fallback and opens a workspace inspector", () => {
        render(<EstateMap />);

        expect(screen.getByText(/3D rendering is unavailable/i)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: /Sample Workspace 02/ }));
        expect(screen.getByRole("complementary", { name: "Sample Workspace 02 workspace inspector" })).toBeInTheDocument();
        expect(screen.getByText("Sample Warehouse 01")).toBeInTheDocument();
    });

    it("returns to the full-campus state", () => {
        render(<EstateMap />);

        fireEvent.click(screen.getByRole("button", { name: /Sample Workspace 03/ }));
        fireEvent.click(screen.getByRole("button", { name: "View full city" }));
        expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
        expect(screen.getByText(/Select a building to inspect/i)).toBeInTheDocument();
    });

    it("filters the estate by capacity and clears an incompatible selection", () => {
        render(<EstateMap />);

        fireEvent.click(screen.getByRole("button", { name: /Sample Workspace 01/ }));
        fireEvent.change(screen.getByRole("combobox", { name: "Filter estate by capacity" }), { target: { value: "Nordic Shared F32" } });

        expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Sample Workspace 01/ })).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Sample Workspace 03/ })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Sample Workspace 04/ })).toBeInTheDocument();
    });

    it("drills from a workspace into an artifact floor and its linked flags", () => {
        render(<EstateMap />);

        fireEvent.click(screen.getByRole("button", { name: /Sample Workspace 02/ }));
        fireEvent.click(screen.getByRole("button", { name: /Semantic models.*Sample Model 02/ }));

        expect(screen.getByText("Artifact governance")).toBeInTheDocument();
        expect(screen.getByText("Import")).toBeInTheDocument();
        expect(screen.getByText("Artifact flags")).toBeInTheDocument();
        expect(screen.getByText("Workspace has a single administrator")).toBeInTheDocument();
    });

    it("opens the right specialist view from a selected estate artifact", () => {
        const onOpenArea = vi.fn();
        render(<EstateMap onOpenArea={onOpenArea} />);

        fireEvent.click(screen.getByRole("button", { name: /Sample Workspace 01/ }));
        fireEvent.click(screen.getByRole("button", { name: /Semantic models.*Sample Model 01/ }));
        fireEvent.click(screen.getByRole("button", { name: "Open semantic model optimization" }));

        expect(onOpenArea).toHaveBeenCalledWith("models", {
            itemId: "finance-sm",
            workspaceId: "finance",
        });
    });
});
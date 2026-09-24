//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentPanel } from "@/components/agent-panel";

vi.mock("@/hooks/use-data-agent", () => ({
    useDataAgent: () => ({
        answer: "A long grounded answer",
        ask: vi.fn(),
        error: null,
        missingConfiguration: [],
        status: "answered",
    }),
}));

describe("AgentPanel", () => {
    it("exposes native evidence prompts and explains that chat scope is explicit", () => {
        render(<AgentPanel />);
        expect(screen.getByRole("button", { name: "Show refresh and job execution coverage for each workspace's latest review." })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Which Dataflow Gen2 queries have syntax signals or coverage gaps?" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Rank calculated DAX objects separately from measures." })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Show ARCH-016 structural defects with workspace and item IDs." })).toBeInTheDocument();
        expect(screen.getByText(/Chat does not inherit page filters/)).toBeInTheDocument();
        expect(screen.getByText(/central Data Agent, not the workspace-owner agent/)).toHaveTextContent("does not inherit owner-model row-level security (RLS)");
    });

    it("keeps the composer reachable while the conversation scrolls", () => {
        render(<AgentPanel />);

        const panel = screen.getByRole("complementary");
        const conversation = screen.getByRole("region", { name: "Review agent conversation" });
        const composer = screen.getByRole("textbox", { name: "Ask the review agent" }).closest("form");

        expect(panel).toHaveClass("h-full", "min-h-0", "overflow-hidden");
        expect(conversation).toHaveClass("min-h-0", "flex-1", "overflow-y-auto");
        expect(composer).toHaveClass("shrink-0");
    });
});
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
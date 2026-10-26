//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentPanel } from "@/components/agent-panel";
import { suggestedDataAgentQuestions } from "@/lib/data-agent-factory";

const agent = vi.hoisted(() => ({
    answer: "A long grounded answer", ask: vi.fn(), error: null as string | null,
    missingConfiguration: [], status: "answered", phase: "querying",
}));
vi.mock("@/hooks/use-data-agent", () => ({ useDataAgent: () => agent }));
beforeEach(() => {
    vi.clearAllMocks();
    agent.status = "answered";
    agent.phase = "querying";
    agent.error = null;
});

describe("AgentPanel", () => {
    it("offers only targeted suggestions and explains that chat scope is explicit", () => {
        render(<AgentPanel />);
        for (const prompt of suggestedDataAgentQuestions) {
            expect(screen.getByRole("button", { name: prompt.label })).toBeInTheDocument();
        }
        expect(screen.queryByRole("button", { name: "How much of production runs on one capacity?" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "What should we fix first?" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", {name: /DAX|refresh|execution|cost/i})).not.toBeInTheDocument();
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
    it.each(suggestedDataAgentQuestions)("sends the bounded query for $label", (prompt) => {
        render(<AgentPanel />);
        fireEvent.click(screen.getByRole("button", {name: prompt.label}));
        expect(agent.ask).toHaveBeenCalledWith(prompt.question);
        expect(prompt.question).toContain("Do not query other tables");
        expect(prompt.question).toMatch(/exactly one row|at most five rows/);
    });
    it("reports configuration without claiming a successful connection", () => {
        agent.status = "ready";
        render(<AgentPanel />);
        expect(screen.getByText("Ready to connect")).toBeInTheDocument();
        expect(screen.queryByText("MCP connected")).not.toBeInTheDocument();
        expect(screen.queryByText("Semantic model and Data Agent connected")).not.toBeInTheDocument();
    });
    it("shows the request phase and prevents another send while waiting", async () => {
        agent.ask.mockImplementationOnce(async () => { agent.status = "querying"; agent.phase = "authenticating"; });
        render(<AgentPanel />);
        fireEvent.click(screen.getByRole("button", {name: "Show the latest review score."}));
        await waitFor(() => expect(screen.getByText(/Signing in to Microsoft \(0s\)/)).toBeInTheDocument());
        expect(screen.getByText(/allow pop-ups if it does not appear/)).toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Send question"})).toBeDisabled();
    });
    it("lets the reviewer explicitly retry a failed question", async () => {
        agent.ask.mockImplementationOnce(async () => { agent.status = "error"; agent.error = "Service unavailable"; });
        render(<AgentPanel />);
        fireEvent.click(screen.getByRole("button", {name: "Show the latest review score."}));
        fireEvent.click(await screen.findByRole("button", {name: "Retry question"}));
        expect(agent.ask).toHaveBeenCalledTimes(2);
        expect(agent.ask.mock.calls[1][0]).toBe(agent.ask.mock.calls[0][0]);
    });
});
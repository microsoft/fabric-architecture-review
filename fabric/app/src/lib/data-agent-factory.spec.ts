//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
    buildAuthRedirectUri,
    buildDataAgentEndpoint,
    buildPopupRelayUri,
    getDataAgentConnection,
    groundDataAgentQuestion,
    RayfinDataAgentClient,
} from "@/lib/data-agent-factory";

const auth = vi.hoisted(() => ({
    initialize: vi.fn(),
    getActiveAccount: vi.fn(() => ({ homeAccountId: "user" })),
    getAllAccounts: vi.fn(() => []),
    setActiveAccount: vi.fn(),
    acquireTokenSilent: vi.fn(),
    acquireTokenPopup: vi.fn(),
}));

const mcp = vi.hoisted(() => ({
    connect: vi.fn(),
    listTools: vi.fn(),
    callTool: vi.fn(),
    close: vi.fn(),
    transport: vi.fn(),
}));

vi.mock("@azure/msal-browser", () => ({
    InteractionRequiredAuthError: class InteractionRequiredAuthError extends Error {},
    PublicClientApplication: vi.fn(function PublicClientApplication() {
        return auth;
    }),
}));

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({
    Client: vi.fn(function Client() {
        return mcp;
    }),
}));

vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
    StreamableHTTPClientTransport: vi.fn(function StreamableHTTPClientTransport(...args: unknown[]) {
        mcp.transport(...args);
    }),
}));

beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("VITE_RAYFIN_DATA_AGENT_WORKSPACE_ID", "agent-workspace");
    vi.stubEnv("VITE_RAYFIN_DATA_AGENT_ID", "data-agent");
    vi.stubEnv("VITE_RAYFIN_ENTRA_CLIENT_ID", "spa-client");
    vi.stubEnv("VITE_FABRIC_TENANT_ID", "tenant");
    auth.acquireTokenSilent.mockResolvedValue({ accessToken: "delegated-token" });
    mcp.listTools.mockResolvedValue({
        tools: [{ name: "ask_agent", inputSchema: { properties: { question: {} } } }],
    });
    mcp.callTool.mockResolvedValue({ content: [{ type: "text", text: "grounded answer" }] });
});

describe("Rayfin Data Agent client", () => {
    it("grounds notebook code-smell questions in latest-review evidence", () => {
        const question = "Which notebook has the highest criticality from code smells?";

        expect(groundDataAgentQuestion(question)).toContain(question);
        expect(groundDataAgentQuestion(question)).toContain("latest completed architecture review");
        expect(groundDataAgentQuestion(question)).toContain("list every notebook code-smell finding");
    });

    it("grounds governance and other main review topics in latest-review evidence", () => {
        const question = "Which workspace has the highest governance risk?";

        expect(groundDataAgentQuestion(` ${question} `)).toContain("latest completed architecture review");
        expect(groundDataAgentQuestion(` ${question} `)).toContain("governance findings");
        expect(groundDataAgentQuestion(` ${question} `)).not.toContain("notebook findings");
        expect(groundDataAgentQuestion(` ${question} `)).toContain(question);
    });

    it("leaves questions outside the review domain unchanged", () => {
        expect(groundDataAgentQuestion(" Hello there ")).toBe("Hello there");
    });

    it("builds the validated MCP endpoint for the published Data Agent", () => {
        expect(buildDataAgentEndpoint(
            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            "66666666-7777-4888-8999-aaaaaaaaaaaa",
        ).href).toBe(
            "https://api.fabric.microsoft.com/v1/mcp/workspaces/"
            + "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff/dataagents/"
            + "66666666-7777-4888-8999-aaaaaaaaaaaa/agent",
        );
    });

    it("uses a minimal same-origin page for popup callbacks", () => {
        expect(buildAuthRedirectUri("https://far.example/"))
            .toBe("https://far.example/auth-callback.html");
        expect(buildPopupRelayUri("https://far.example/"))
            .toBe("https://far.example/popup-relay.html");
    });

    it("invokes the cross-workspace Data Agent directly with a delegated token", async () => {
        const client = new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant");
        await expect(client.ask(" question ")).resolves.toBe("grounded answer");

        expect(auth.acquireTokenSilent).toHaveBeenCalledWith(expect.objectContaining({
            scopes: ["https://api.fabric.microsoft.com/.default"],
        }));
        expect(mcp.transport).toHaveBeenCalledWith(
            buildDataAgentEndpoint("agent-workspace", "data-agent"),
            { requestInit: { headers: { Authorization: "Bearer delegated-token" } } },
        );
        expect(mcp.callTool).toHaveBeenCalledWith({
            name: "ask_agent",
            arguments: { question: "question" },
        }, undefined, { timeout: 300_000 });
        expect(mcp.close).toHaveBeenCalledOnce();
    });

    it("sends grounded evidence requirements for notebook code-smell questions", async () => {
        const client = new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant");
        await client.ask("Which notebook has the highest criticality from code smells?");

        expect(mcp.callTool).toHaveBeenCalledWith({
            name: "ask_agent",
            arguments: {
                question: expect.stringContaining("list every notebook code-smell finding"),
            },
        }, undefined, { timeout: 300_000 });
    });

    it("reconnects once after a transient Data Agent failure", async () => {
        mcp.callTool
            .mockRejectedValueOnce(new Error("Streamable HTTP internal error"))
            .mockResolvedValueOnce({ content: [{ type: "text", text: "recovered answer" }] });
        const client = new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant");

        await expect(client.ask("Review the architecture findings")).resolves.toBe("recovered answer");
        expect(mcp.connect).toHaveBeenCalledTimes(2);
        expect(mcp.callTool).toHaveBeenCalledTimes(2);
        expect(mcp.close).toHaveBeenCalledTimes(2);
    });

    it("creates the browser client when all public settings are configured", () => {
        const connection = getDataAgentConnection();

        expect(connection.client).toBeInstanceOf(RayfinDataAgentClient);
        expect(connection.missingConfiguration).toEqual([]);
    });
});
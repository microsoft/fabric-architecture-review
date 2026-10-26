// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildAuthRedirectUri, buildDataAgentEndpoint, buildPopupRelayUri, describeDataAgentError, groundDataAgentQuestion, RayfinDataAgentClient } from "./data-agent-factory";
const auth = vi.hoisted(() => ({initialize: vi.fn(), getActiveAccount: vi.fn(() => ({homeAccountId: "user"})),
    getAllAccounts: vi.fn(() => []), setActiveAccount: vi.fn(), acquireTokenSilent: vi.fn(), acquireTokenPopup: vi.fn()}));
const mcp = vi.hoisted(() => ({connect: vi.fn(), listTools: vi.fn(), callTool: vi.fn(), close: vi.fn(), transport: vi.fn()}));
vi.mock("@azure/msal-browser", () => ({InteractionRequiredAuthError: class extends Error {}, PublicClientApplication: vi.fn(function(){return auth;})}));
vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({Client: vi.fn(function(){return mcp;})}));
vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({StreamableHTTPClientTransport: vi.fn(function(...args){mcp.transport(...args);})}));
beforeEach(() => {
    vi.clearAllMocks();
    auth.acquireTokenSilent.mockResolvedValue({accessToken: "delegated-token"});
    mcp.listTools.mockResolvedValue({tools: [{name: "ask", inputSchema: {properties: {question: {}}}}]});
    mcp.callTool.mockResolvedValue({content: [{type: "text", text: "Architecture answer"}]});
});
afterEach(() => vi.restoreAllMocks());
describe("architectural Agent client", () => {
    it("uses explicit current profile and refuses optimizer subjects", () => {
        const grounded = groundDataAgentQuestion("Tune DAX expressions and jobs");
        expect(grounded).toContain("far-architecture-v1");
        expect(grounded).toContain("Decline optimization");
        expect(grounded).not.toMatch(/gold_dax|gold_item_executions/);
        expect(grounded).toContain("Central chat does not inherit owner-model RLS");
    });
    it("sends scoped grounding to exactly one discovered tool with delegated authentication", async () => {
        const answer = await new RayfinDataAgentClient("workspace", "agent", "client", "tenant").ask("What architecture controls failed?");
        expect(answer).toBe("Architecture answer");
        expect(auth.acquireTokenSilent).toHaveBeenCalledWith(expect.objectContaining({scopes: ["https://api.fabric.microsoft.com/.default"]}));
        expect(mcp.callTool.mock.calls[0][0].arguments.question).toContain("latest compatible architectural review");
        expect(mcp.close).toHaveBeenCalled();
    });
    it("supports userQuestion and rejects ambiguous tools", async () => {
        mcp.listTools.mockResolvedValueOnce({tools: [{name: "ask", inputSchema: {properties: {userQuestion: {}}}}]});
        await new RayfinDataAgentClient("w", "a", "c", "t").ask("Architecture");
        expect(mcp.callTool.mock.calls[0][0].arguments.userQuestion).toContain("Architecture");
        mcp.listTools.mockResolvedValueOnce({tools: [{name: "one", inputSchema: {properties: {question: {}}}}, {name: "two", inputSchema: {properties: {question: {}}}}]});
        await expect(new RayfinDataAgentClient("w", "a", "c", "t").ask("Architecture")).rejects.toThrow("Expected one Data Agent tool");
    });
    it("keeps same-origin authentication redirects and published endpoint", () => {
        expect(buildAuthRedirectUri("https://app.example")).toBe("https://app.example/auth-callback.html");
        expect(buildPopupRelayUri("https://app.example")).toBe("https://app.example/popup-relay.html");
        expect(buildDataAgentEndpoint("w", "a").pathname).toBe("/v1/mcp/workspaces/w/dataagents/a/agent");
    });
    it("retries transient errors once without changing sources", async () => {
        mcp.callTool.mockRejectedValueOnce(new Error("429 throttling"));
        await new RayfinDataAgentClient("w", "a", "c", "t").ask("Architecture");
        expect(mcp.connect).toHaveBeenCalledTimes(2);
        expect(mcp.close).toHaveBeenCalledTimes(2);
    });
    it("reports authentication, discovery and query progress in order", async () => {
        const progress = vi.fn();
        await new RayfinDataAgentClient("w", "a", "c", "t").ask("Latest score", progress);
        expect(progress.mock.calls.map(([phase]) => phase)).toEqual(["authenticating", "connecting", "querying"]);
    });
    it("does not retry a slow service failure or lose its request ID", async () => {
        const clock = vi.spyOn(Date, "now").mockReturnValue(0);
        mcp.callTool.mockImplementationOnce(async () => {
            clock.mockReturnValue(10_000);
            throw new Error('internal error; requestId: request-123');
        });
        await expect(new RayfinDataAgentClient("w", "a", "c", "t").ask("Architecture")).rejects.toThrow("request-123");
        expect(mcp.callTool).toHaveBeenCalledTimes(1);
        expect(mcp.close).toHaveBeenCalledTimes(1);
    });
    it("never repeats a timed-out request, including a fast network timeout", async () => {
        mcp.callTool.mockRejectedValueOnce(new Error("Request timed out"));
        await expect(new RayfinDataAgentClient("w", "a", "c", "t").ask("Architecture")).rejects.toThrow("timed out");
        expect(mcp.connect).toHaveBeenCalledTimes(1);
    });
    it("does not send a question when sign-in fails", async () => {
        auth.acquireTokenSilent.mockRejectedValueOnce(new Error("popup_window_error"));
        await expect(new RayfinDataAgentClient("w", "a", "c", "t").ask("Architecture")).rejects.toThrow("popup_window_error");
        expect(mcp.connect).not.toHaveBeenCalled();
        expect(describeDataAgentError(new Error("popup_window_error"))).toContain("Allow pop-ups");
        expect(describeDataAgentError(new Error("popup_window_error"))).toContain("question was not sent");
    });
    it("keeps service diagnostic details and explains timeouts without masking other errors", () => {
        expect(describeDataAgentError(new Error("internal error; requestId: request-123"))).toContain("request-123");
        expect(describeDataAgentError(new Error("Request timed out"))).toContain("has not started another long attempt");
        expect(describeDataAgentError(new Error("Access denied"))).toBe("Access denied");
    });
});

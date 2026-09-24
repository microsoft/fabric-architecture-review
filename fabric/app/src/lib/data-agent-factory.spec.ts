//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
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

    it("grounds all native tables, joins and evidence boundaries", () => {
        const grounded = groundDataAgentQuestion("Summarize native evidence for workspace ID w1.");
        for (const table of ["gold_item_executions", "gold_execution_coverage", "gold_dataflows", "gold_dataflow_queries", "gold_dax_objects", "gold_dax_object_coverage"]) expect(grounded).toContain(table);
        expect(grounded).toContain("review_item_key");
        expect(grounded).toContain("BOTH workspace_id and run_id");
        expect(grounded).toContain("gold_workspaces");
        expect(grounded).toContain("run_timestamp DESC, run_id DESC");
        expect(grounded).toContain("retain untouched workspaces after targeted runs");
        expect(grounded).toContain("capacity scope from the matching review's workspace assignment");
        expect(grounded).toContain("missing tables, access failures");
        expect(grounded).toContain("partial results and truncated results");
        expect(grounded).toContain("Chat does not inherit page filters");
        expect(grounded).toContain("central Data Agent, not the workspace-owner agent");
        expect(grounded).toContain("Do not claim to inherit owner-model row-level security (RLS)");
    });

    it.each(["failed refreshes", "job executions", "notebook runs", "pipeline failures"])("preserves historical scope for %s rather than forcing latest-run answers", (topic) => {
        const question = `Count ${topic} in workspace ID w1, capacity ID c1 from 2026-08-01 through 2026-09-01.`;
        const grounded = groundDataAgentQuestion(question);
        expect(grounded).toContain(question);
        expect(grounded).not.toContain("For the latest completed architecture review");
        expect(grounded).toContain("FIRST deduplicate execution_key");
        expect(grounded).toContain("only THEN apply the requested event-time window, status filters");
        expect(grounded).toContain("NULL is unknown, never zero");
        expect(grounded).toContain("/ 1000.0");
        expect(grounded).toContain("empty is successful collection");
        expect(grounded).toContain("unknown is not success");
        expect(grounded).toContain("inclusive start and exclusive end");
    });

    it("keeps Dataflow lexical signals informational and inventory gaps out of artifact counts", () => {
        const grounded = groundDataAgentQuestion("Inspect Dataflow Gen2 folding and DFLOW-001.");
        expect(grounded).toContain("semicolon-delimited");
        expect(grounded).toContain("DFLOW_TABLE_BUFFER, DFLOW_STOP_FOLDING, DFLOW_NATIVE_QUERY");
        expect(grounded).toContain("DFLOW-001 is info, never fail");
        expect(grounded).toContain("DFLOW-002 reports coverage");
        expect(grounded).toContain("Empty dataflow_id denotes an inventory gap");
        expect(grounded).toContain("Do not request or expose M expressions");
        expect(grounded).not.toContain("Use gold_dax_objects");
    });

    it.each(["calculated columns", "calculated tables", "calculation items", "DAX objects"])("grounds %s without changing measure-only scoring", (topic) => {
        const grounded = groundDataAgentQuestion(`Rank ${topic} by risk.`);
        expect(grounded).toContain("gold_dax_objects with gold_dax_object_coverage");
        expect(grounded).toContain("calculated_column, calculated_table and calculation_item");
        expect(grounded).toContain("DAX-001/002 scoring is measure-only");
        expect(grounded).toContain("never join these detail populations to count them");
        expect(grounded).toContain("not semantic or runtime validation");
        expect(grounded).toContain("Report visual calculations and format-string expressions are not covered");
    });

    it("grounds structural pipeline findings with authoritative IDs, allowed defect codes and per-container scope", () => {
        const grounded = groundDataAgentQuestion("Show ARCH-016 dependency cycles for workspace ID w1.");
        expect(grounded).toContain("gold_findings evidence.items and gold_finding_targets");
        expect(grounded).toContain("Filter evidence.items to the selected workspace_id and its review");
        expect(grounded).toContain("duplicate_activity, missing_dependency, self_dependency and dependency_cycle");
        expect(grounded).toContain("not measured execution reliability");
        expect(grounded).toContain("never attribute by names");
    });

    it("keeps app package, lockfile and MCP client on the prepared root release", async () => {
        const version = readFileSync(resolve(process.cwd(), "..", "..", "VERSION"), "utf8").trim().split(".").map(Number).join(".");
        const manifest = JSON.parse(readFileSync(resolve(process.cwd(), "package.json"), "utf8"));
        const lock = JSON.parse(readFileSync(resolve(process.cwd(), "package-lock.json"), "utf8"));
        expect(manifest.version).toBe(version);
        expect(lock.version).toBe(version);
        expect(lock.packages[""].version).toBe(version);
        await new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant").ask("Hello");
        expect(Client).toHaveBeenCalledWith({ name: "fabric-architecture-review", version });
    });

    it("sends native grounding to the discovered published agent tool", async () => {
        await new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant").ask("Which refreshes failed last month?");
        expect(mcp.callTool).toHaveBeenCalledWith({
            name: "ask_agent",
            arguments: { question: expect.stringContaining("FIRST deduplicate execution_key") },
        }, undefined, { timeout: 300_000 });
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

    it("selects the Data Agent tool by schema rather than response order", async () => {
        mcp.listTools.mockResolvedValue({
            tools: [
                { name: "health", inputSchema: { properties: { probe: {} } } },
                { name: "ask_agent", inputSchema: { properties: { metadata: {}, question: {} } } },
            ],
        });
        const client = new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant");

        await expect(client.ask(" question ")).resolves.toBe("grounded answer");
        expect(mcp.callTool).toHaveBeenCalledWith({
            name: "ask_agent",
            arguments: { question: "question" },
        }, undefined, { timeout: 300_000 });
    });

    it("supports the published Data Agent userQuestion argument", async () => {
        mcp.listTools.mockResolvedValue({
            tools: [{
                name: "DataAgent_Fabric_Arch_Review_Data_Agent",
                inputSchema: { properties: { userQuestion: {} }, required: ["userQuestion"] },
            }],
        });
        const client = new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant");

        await expect(client.ask(" question ")).resolves.toBe("grounded answer");
        expect(mcp.callTool).toHaveBeenCalledWith({
            name: "DataAgent_Fabric_Arch_Review_Data_Agent",
            arguments: { userQuestion: "question" },
        }, undefined, { timeout: 300_000 });
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

    it("retries once after throttling", async () => {
        mcp.callTool
            .mockRejectedValueOnce(new Error("HTTP 429 Too Many Requests"))
            .mockResolvedValueOnce({ content: [{ type: "text", text: "recovered answer" }] });
        const client = new RayfinDataAgentClient("agent-workspace", "data-agent", "spa-client", "tenant");

        await expect(client.ask("Review the architecture findings")).resolves.toBe("recovered answer");
        expect(mcp.callTool).toHaveBeenCalledTimes(2);
    });

    it("creates the browser client when all public settings are configured", () => {
        const connection = getDataAgentConnection();

        expect(connection.client).toBeInstanceOf(RayfinDataAgentClient);
        expect(connection.missingConfiguration).toEqual([]);
    });
});
//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import {
    InteractionRequiredAuthError,
    PublicClientApplication,
    type AccountInfo,
} from "@azure/msal-browser";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

interface DataAgentClient {
    ask(question: string): Promise<string>;
}

interface DataAgentTool {
    name: string;
    inputSchema: {
        properties?: Record<string, unknown>;
    };
}

const fabricScopes = ["https://api.fabric.microsoft.com/.default"];
const requestTimeoutMs = 300_000;
const reviewEvidenceDomains = [
    { pattern: /\b(?:governance|owners?|ownership|endorsement|sensitivity)\b/i, label: "governance findings" },
    { pattern: /\b(?:security|access|permissions?)\b/i, label: "security findings" },
    { pattern: /\b(?:architecture|lineage|inventory|lakehouses?|pipelines?|reports?|warehouses?|workspaces?)\b/i, label: "architecture findings and inventory" },
    { pattern: /\b(?:semantic models?|storage modes?|Direct Lake|Import|DirectQuery)\b/i, label: "semantic-model and storage evidence" },
    { pattern: /\b(?:performance|capacity|duration|latency)\b/i, label: "performance and capacity findings" },
    { pattern: /\b(?:cost|spend|utilization|waste)\b/i, label: "cost and utilization findings" },
    { pattern: /\b(?:best practices?|BPA(?:-\d+)?)\b/i, label: "best-practice findings" },
    { pattern: /\b(?:tenant settings?|admin settings?)\b/i, label: "tenant-setting evidence" },
    { pattern: /\b(?:notebooks?|code[ -]?smells?|NBCODE(?:-\d+)?)\b/i, label: "notebook findings and code-smell evidence" },
] as const;

function getPublicSetting(name: string): string {
    return import.meta.env[name]?.trim() ?? "";
}

function extractTextContent(content: unknown): string {
    if (!Array.isArray(content)) return "";
    return content
        .filter((block): block is { type: "text"; text: string } => (
            typeof block === "object"
            && block !== null
            && "type" in block
            && block.type === "text"
            && "text" in block
            && typeof block.text === "string"
        ))
        .map((block) => block.text)
        .join("\n");
}

export function groundDataAgentQuestion(question: string): string {
    const normalizedQuestion = question.trim();
    const domains = reviewEvidenceDomains
        .filter(({ pattern }) => pattern.test(normalizedQuestion))
        .map(({ label }) => label);
    const referencesGeneralReview = /\b(?:findings?|review|risk|recommendations?|severity)\b/i.test(normalizedQuestion);
    if (domains.length === 0 && !referencesGeneralReview) return normalizedQuestion;

    const referencesNotebookSmells = /\bnotebooks?\b/i.test(normalizedQuestion)
        && /\b(?:code[ -]?smells?|NBCODE(?:-\d+)?)\b/i.test(normalizedQuestion);
    const evidenceTask = referencesNotebookSmells
        ? [
            "For the latest completed architecture review, list every notebook code-smell finding.",
            "Return the rule ID, notebook name, workspace name, severity, affected cells, and state explicitly when any field is unavailable.",
        ].join(" ")
        : [
            `For the latest completed architecture review, list every matching row from ${domains.length ? domains.join(", ") : "review findings and inventory"}.`,
        ].join(" ");
    return [
        evidenceTask,
        "Use only those returned rows and do not infer missing findings, values, rankings, or fields; state explicitly when evidence is unavailable or tied.",
        `Then answer: ${normalizedQuestion}`,
    ].join(" ");
}

function isTransientDataAgentError(error: unknown): boolean {
    const message = error instanceof Error ? error.message : String(error);
    return /\b(?:internal error|network|timeout|timed out|fetch failed|ECONNRESET|ETIMEDOUT|503|504)\b/i.test(message);
}

export function buildDataAgentEndpoint(workspaceId: string, dataAgentId: string): URL {
    return new URL(
        `https://api.fabric.microsoft.com/v1/mcp/workspaces/${encodeURIComponent(workspaceId)}`
        + `/dataagents/${encodeURIComponent(dataAgentId)}/agent`,
    );
}

export function buildAuthRedirectUri(origin = window.location.origin): string {
    return `${origin.replace(/\/$/, "")}/auth-callback.html`;
}

export function buildPopupRelayUri(origin = window.location.origin): string {
    return `${origin.replace(/\/$/, "")}/popup-relay.html`;
}

export interface DataAgentConnection {
    client: DataAgentClient | null;
    missingConfiguration: string[];
}

export class RayfinDataAgentClient implements DataAgentClient {
    private readonly msal: PublicClientApplication;

    constructor(
        private readonly workspaceId = getPublicSetting("VITE_RAYFIN_DATA_AGENT_WORKSPACE_ID"),
        private readonly dataAgentId = getPublicSetting("VITE_RAYFIN_DATA_AGENT_ID"),
        clientId = getPublicSetting("VITE_RAYFIN_ENTRA_CLIENT_ID"),
        tenantId = getPublicSetting("VITE_FABRIC_TENANT_ID"),
    ) {
        this.msal = new PublicClientApplication({
            auth: {
                clientId,
                authority: `https://login.microsoftonline.com/${tenantId}`,
                redirectUri: buildAuthRedirectUri(),
                popupRelayUri: buildPopupRelayUri(),
            },
            cache: { cacheLocation: "sessionStorage" },
        });
    }

    private async getAccessToken(): Promise<string> {
        await this.msal.initialize();
        const account = this.msal.getActiveAccount() ?? this.msal.getAllAccounts()[0];
        if (account) {
            this.msal.setActiveAccount(account);
            try {
                return (await this.msal.acquireTokenSilent({ account, scopes: fabricScopes })).accessToken;
            } catch (error) {
                if (!(error instanceof InteractionRequiredAuthError)) throw error;
            }
        }

        const response = await this.msal.acquireTokenPopup({
            account: account as AccountInfo | undefined,
            scopes: fabricScopes,
            prompt: account ? undefined : "select_account",
        });
        this.msal.setActiveAccount(response.account);
        return response.accessToken;
    }

    async ask(question: string): Promise<string> {
        const normalizedQuestion = question.trim();
        if (!normalizedQuestion || normalizedQuestion.length > 4_000) {
            throw new Error("Question must contain between 1 and 4,000 characters.");
        }
        const groundedQuestion = groundDataAgentQuestion(normalizedQuestion);

        const token = await this.getAccessToken();
        for (let attempt = 0; attempt < 2; attempt += 1) {
            const transport = new StreamableHTTPClientTransport(
                buildDataAgentEndpoint(this.workspaceId, this.dataAgentId),
                { requestInit: { headers: { Authorization: `Bearer ${token}` } } },
            );
            const client = new Client({ name: "fabric-architecture-review", version: "0.1.0" });
            try {
                await client.connect(transport);
                const tools = await client.listTools(undefined, { timeout: requestTimeoutMs });
                const tool = tools.tools[0] as DataAgentTool | undefined;
                if (!tool) throw new Error("The published Data Agent did not expose an MCP tool.");

                const questionArgument = Object.keys(tool.inputSchema.properties ?? {})[0];
                if (!questionArgument) throw new Error("The Data Agent tool did not advertise a question argument.");

                const result = await client.callTool({
                    name: tool.name,
                    arguments: { [questionArgument]: groundedQuestion },
                }, undefined, { timeout: requestTimeoutMs });
                const answer = extractTextContent(result.content);
                if (result.isError) throw new Error(answer || "The Data Agent returned an error.");
                if (!answer) throw new Error("The Data Agent returned an empty response.");
                return answer;
            } catch (error) {
                if (attempt === 1 || !isTransientDataAgentError(error)) throw error;
            } finally {
                await client.close();
            }
        }
        throw new Error("The Data Agent request did not complete.");
    }
}

let connection: DataAgentConnection | undefined;

export function getDataAgentConnection(): DataAgentConnection {
    if (connection) return connection;

    const settings = {
        workspaceId: getPublicSetting("VITE_RAYFIN_DATA_AGENT_WORKSPACE_ID"),
        dataAgentId: getPublicSetting("VITE_RAYFIN_DATA_AGENT_ID"),
        clientId: getPublicSetting("VITE_RAYFIN_ENTRA_CLIENT_ID"),
        tenantId: getPublicSetting("VITE_FABRIC_TENANT_ID"),
    };
    const missingConfiguration = Object.entries(settings)
        .filter(([, value]) => !value)
        .map(([name]) => name);
    connection = {
        client: missingConfiguration.length === 0
            ? new RayfinDataAgentClient(
                settings.workspaceId,
                settings.dataAgentId,
                settings.clientId,
                settings.tenantId,
            )
            : null,
        missingConfiguration,
    };
    return connection;
}
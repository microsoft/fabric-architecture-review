//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { execFileSync } from "node:child_process";
import process from "node:process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const FABRIC_RESOURCE = "https://api.fabric.microsoft.com";
const REQUEST_TIMEOUT_MS = 300_000;
const GUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function readArguments(args) {
    const values = {};
    for (let index = 0; index < args.length; index += 1) {
        const argument = args[index];
        if (!argument.startsWith("--")) continue;
        const value = args[index + 1];
        if (!value || value.startsWith("--")) throw new Error(`Missing value for ${argument}.`);
        values[argument.slice(2)] = value;
        index += 1;
    }
    return values;
}

function requireGuid(value, name) {
    if (!value || !GUID_PATTERN.test(value)) {
        throw new Error(`${name} must be a Fabric GUID.`);
    }
    return value;
}

function getFabricToken(tenantId) {
    if (tenantId) requireGuid(tenantId, "--tenant-id");
    const args = [
        "account",
        "get-access-token",
        "--resource",
        FABRIC_RESOURCE,
        "--query",
        "accessToken",
        "--output",
        "tsv",
    ];
    if (tenantId) args.push("--tenant", tenantId);

    const executable = process.platform === "win32" ? process.env.ComSpec ?? "cmd.exe" : "az";
    const commandArgs = process.platform === "win32" ? ["/d", "/s", "/c", "az.cmd", ...args] : args;
    return execFileSync(executable, commandArgs, {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "inherit"],
    }).trim();
}

function extractText(content) {
    if (!Array.isArray(content)) return "";
    return content
        .filter((block) => block?.type === "text" && typeof block.text === "string")
        .map((block) => block.text)
        .join("\n");
}

async function main() {
    const args = readArguments(process.argv.slice(2));
    const workspaceId = requireGuid(
        args["workspace-id"] ?? process.env.FABRIC_DATA_AGENT_WORKSPACE_ID,
        "--workspace-id",
    );
    const dataAgentId = requireGuid(
        args["agent-id"] ?? process.env.FABRIC_DATA_AGENT_ID,
        "--agent-id",
    );
    const question = args.question?.trim();
    if (!question) throw new Error("--question is required.");

    const token = getFabricToken(args["tenant-id"] ?? process.env.FABRIC_TENANT_ID);
    const endpoint = new URL(
        `${FABRIC_RESOURCE}/v1/mcp/workspaces/${workspaceId}/dataagents/${dataAgentId}/agent`,
    );
    const transport = new StreamableHTTPClientTransport(endpoint, {
        requestInit: { headers: { Authorization: `Bearer ${token}` } },
    });
    const client = new Client({ name: "fabric-architecture-review-cli", version: "1.0.0" });

    try {
        await client.connect(transport);
        const tools = await client.listTools(undefined, { timeout: REQUEST_TIMEOUT_MS });
        const tool = tools.tools[0];
        const questionArgument = Object.keys(tool?.inputSchema?.properties ?? {})[0];
        if (!tool || !questionArgument) throw new Error("The published Data Agent exposed no callable tool.");

        const result = await client.callTool({
            name: tool.name,
            arguments: { [questionArgument]: question },
        }, undefined, { timeout: REQUEST_TIMEOUT_MS });
        const answer = extractText(result.content);
        if (result.isError) throw new Error(answer || "The Data Agent returned an error.");
        if (!answer) throw new Error("The Data Agent returned an empty response.");
        process.stdout.write(`${answer}\n`);
    } finally {
        await client.close();
    }
}

main().catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`Data Agent query failed: ${message}\n`);
    process.exitCode = 1;
});
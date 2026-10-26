// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { afterEach, describe, expect, it, vi } from "vitest";
import { PublicClientApplication, ResponseMode } from "@azure/msal-node";
import { CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { RayfinAuth } from "../../node_modules/@microsoft/rayfin-cli/dist/auth/rayfin-auth.js";

afterEach(() => vi.restoreAllMocks());

describe("patched dependency compatibility", () => {
    it("keeps Rayfin interactive authentication on the supported form_post flow", async () => {
        const result = { accessToken: "synthetic-test-token" };
        const pca = {
            acquireTokenInteractive: vi.fn().mockResolvedValue(result),
            acquireTokenByDeviceCode: vi.fn(),
        };
        const auth = new RayfinAuth();
        expect(await auth.acquireTokenInteractive(pca, ["https://api.fabric.microsoft.com/.default"], true)).toBe(result);
        expect(pca.acquireTokenInteractive).toHaveBeenCalledWith(expect.objectContaining({
            responseMode: ResponseMode.FORM_POST,
            prompt: "select_account",
            openBrowser: expect.any(Function),
        }));
        expect(pca.acquireTokenByDeviceCode).not.toHaveBeenCalled();
    });

    it("accepts Rayfin's legacy form_post option in the real MSAL client", async () => {
        const client = new PublicClientApplication({
            auth: { clientId: "11111111-1111-4111-8111-111111111111" },
            system: { loggerOptions: { loggerCallback: () => {} } },
        });
        // Stop at URL generation: exercise request validation without signing in or contacting Entra.
        const stop = new Error("Stop before network authentication");
        const getUrl = vi.spyOn(client, "getAuthCodeUrl").mockRejectedValue(stop);
        const openBrowser = vi.fn();
        await expect(client.acquireTokenInteractive({
            scopes: ["https://api.fabric.microsoft.com/.default"],
            responseMode: ResponseMode.FORM_POST,
            openBrowser,
        })).rejects.toBe(stop);
        expect(getUrl).toHaveBeenCalledWith(expect.objectContaining({ responseMode: "form_post" }));
        expect(openBrowser).not.toHaveBeenCalled();
    });

    it("validates MCP tool requests with the patched Zod dependency", () => {
        const request = { method: "tools/call", params: { name: "ask", arguments: { question: "Latest review score" } } };
        expect(CallToolRequestSchema.parse(request)).toEqual(request);
        expect(CallToolRequestSchema.safeParse({ method: "tools/call", params: { name: 42 } }).success).toBe(false);
    });
});

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { execSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, it } from "vitest";

it("regenerates a stale model binding through the npm lifecycle command", () => {
    const appRoot = resolve(import.meta.dirname, "../..");
    const { scripts } = JSON.parse(readFileSync(join(appRoot, "package.json"), "utf8"));
    const fixture = mkdtempSync(join(tmpdir(), "far-app-generation-"));
    const workspaceId = "11111111-1111-4111-8111-111111111111";
    const itemId = "22222222-2222-4222-8222-222222222222";
    try {
        symlinkSync(join(appRoot, "node_modules"), join(fixture, "node_modules"), process.platform === "win32" ? "junction" : "dir");
        mkdirSync(join(fixture, "src"));
        writeFileSync(join(fixture, "package.json"), JSON.stringify({
            private: true, scripts: { "generate:fabric": scripts["generate:fabric"] },
        }));
        writeFileSync(join(fixture, "fabric.yaml"), [
            "activeProfile: default", "profiles:", "  default:", "    semanticModels:", "      reviewModel:",
            `        workspaceId: ${workspaceId}`, `        itemId: ${itemId}`, "",
        ].join("\n"));
        const output = join(fixture, "src", "fabric.generated.ts");
        writeFileSync(output, "export const fabricConfig = { stale: true };\n");

        execSync("npm run generate:fabric", { cwd: fixture, stdio: "pipe", timeout: 20_000 });

        const generated = readFileSync(output, "utf8");
        expect(generated).toContain("reviewModel");
        expect(generated).toContain(workspaceId);
        expect(generated).toContain(itemId);
        expect(generated).not.toContain("stale");
    } finally {
        rmSync(fixture, { recursive: true, force: true });
    }
}, 30_000);

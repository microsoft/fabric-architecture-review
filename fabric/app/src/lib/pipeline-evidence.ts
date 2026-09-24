// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

export interface PipelineStructuralItem {
    workspaceId: string;
    itemId: string;
    itemName: string;
    signals: string[];
    coverage: string;
}

export type PipelineEvidence = { status: "ready"; items: PipelineStructuralItem[] } | { status: "error"; message: string };

function object(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readPipelineEvidence(json: string): PipelineEvidence {
    try {
        const evidence: unknown = JSON.parse(json);
        if (!object(evidence) || !Array.isArray(evidence.items)) throw new Error("Pipeline item evidence is missing.");
        const items = evidence.items.map((item: unknown): PipelineStructuralItem => {
            if (!object(item) || !Array.isArray(item.signal_codes) || item.signal_codes.some((code) => typeof code !== "string")
                || typeof item.workspace_id !== "string" || !item.workspace_id || typeof item.item_id !== "string" || !item.item_id
                || typeof item.coverage_status !== "string") throw new Error("Pipeline evidence has invalid scope identifiers or structural signals.");
            return {
                workspaceId: item.workspace_id, itemId: item.item_id,
                itemName: typeof item.item_name === "string" ? item.item_name : item.item_id,
                signals: item.signal_codes.filter((code): code is string => typeof code === "string"),
                coverage: item.coverage_status,
            };
        });
        return { status: "ready", items };
    } catch (error) {
        return { status: "error", message: error instanceof Error ? error.message : String(error) };
    }
}

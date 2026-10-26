// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import type { FindingSeverity } from "@/lib/review-data";

export const severityClass: Record<FindingSeverity, string> = {
    critical: "bg-destructive-soft text-destructive",
    high: "bg-high-soft text-high-strong",
    medium: "bg-warning-soft text-warning-strong",
    low: "bg-info-soft text-info-strong",
    info: "bg-info-soft text-info-strong",
};

// Triage order: confirmed failures first, then gaps, then context.
export const statusOrder = ["fail", "missing_evidence", "unknown", "info", "pass", "not_applicable"];
export const statusRank = (status?: string) => { const i = statusOrder.indexOf(status ?? "unknown"); return i < 0 ? statusOrder.length : i; };
export const statusClass: Record<string, string> = {
    fail: "bg-destructive-soft text-destructive",
    pass: "bg-success-soft text-success-strong",
    missing_evidence: "bg-warning-soft text-warning-strong",
    unknown: "bg-warning-soft text-warning-strong",
};
const statusLabel: Record<string, string> = { missing_evidence: "missing evidence", not_applicable: "not applicable" };
export const statusText = (status?: string) => statusLabel[status ?? ""] ?? (status || "unknown");

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { architectureProfile, architectureProfileHash } from "@/lib/review-data";
export function nativeAgentGrounding(): string[] {
    return [`Use only architectural profile ${architectureProfile} with hash ${architectureProfileHash}; never blend profile/configuration versions.`,
        "Current architecture inventory, active controls, storage mode, static dependencies, ALM/access architecture and typed source coverage only. Decline optimization, expression tuning, operational monitoring, financial and capacity-sizing questions.",
        "Unknown/missing evidence is not pass. Signals and coverage are non-scored. No assessable controls means null score, not zero or 100.",
        "Model security (SEC-024 to SEC-027) is in scope: only declared models can fail; a model with neither row-level nor object-level security can be intended; unknown means not fully inspected and is never no security. A configured role is not proof access is correct, and Copilot answers within each reader's own permissions. Role detail is optional: when it was collected each role shows its permission, how many tables carry a row filter, how many tables and columns it hides, and whether the filter is dynamic (uses the user's identity) or static; when it was not collected say so and never say a model has no roles. Filter text, table and column names and members are never stored.",
        "Central chat does not inherit owner-model RLS or page filters. Preserve explicit IDs and review context, enforce configured-source permissions, and refuse unauthorized or raw-source requests.",
        "Cite run, provenance, observed time, freshness and coverage limitations. Never infer outcomes or measurements from structural metadata."];
}

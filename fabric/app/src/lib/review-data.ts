// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
export const architectureProfile = "far-architecture-v1";
export const architectureProfileHash = "1c9d63560003a274463f16686965d95cf593d93b5de01c8214e97a48ca9718d6";
export const activeArchitectureRules = new Set([
    "ARCH-001",
    "ARCH-002",
    "ARCH-003",
    "ARCH-004",
    "ARCH-005",
    "ARCH-006",
    "ARCH-007",
    "ARCH-008",
    "ARCH-009",
    "ARCH-010",
    "ARCH-011",
    "ARCH-012",
    "ARCH-013",
    "ARCH-014",
    "ARCH-015",
    "ARCH-016",
    "ARCH-017",
    "ARCH-018",
    "ARCH-019",
    "ARCH-020",
    "GOV-001",
    "GOV-002",
    "GOV-003",
    "GOV-004",
    "GOV-005",
    "GOV-006",
    "GOV-007",
    "GOV-008",
    "GOV-009",
    "GOV-010",
    "OPS-001",
    "OPS-002",
    "OPS-003",
    "OPS-004",
    "OPS-005",
    "OPS-006",
    "OPS-007",
    "OPS-008",
    "OPS-009",
    "OPS-010",
    "REL-001",
    "REL-002",
    "REL-003",
    "SEC-001",
    "SEC-002",
    "SEC-003",
    "SEC-004",
    "SEC-005",
    "SEC-006",
    "SEC-007",
    "SEC-008",
    "SEC-009",
    "SEC-010",
    "SEC-011",
    "SEC-012",
    "SEC-013",
    "SEC-014",
    "SEC-015",
    "SEC-016",
    "SEC-017",
    "SEC-018",
    "SEC-019",
    "SEC-020",
    "SEC-021",
    "SEC-022",
    "SEC-023",
    "SEC-024",
    "SEC-025",
    "SEC-026",
    "SEC-027",
]);
export const dimensionOrder = ["Architecture", "Governance", "Operations", "Reliability", "Security"] as const;
export type ReviewDimension = (typeof dimensionOrder)[number];
export type AssessmentDimension = ReviewDimension | "Source coverage";
export type FindingSeverity = "critical" | "high" | "medium" | "low" | "info";
export type EstateHealth = "healthy" | "warning" | "risk";
export type EstateItemType = "lakehouse" | "warehouse" | "model" | "report" | "notebook" | "pipeline" | "app" | "component";
export interface ReviewMetric { label: string; value: string; delta: string; trend: "up" | "down" | "steady"; intent: "score" | "risk" | "neutral" | "success"; }
export interface DimensionScore { dimension: AssessmentDimension; score: number | null; failed: number; assessed: number; total: number; }
export interface ReviewFinding {
    id: string; dimension: AssessmentDimension; severity: FindingSeverity; title: string; affected: string;
    recommendation: string; workspaceIds: string[]; workspaceName?: string; status?: string; disposition?: string; scored?: boolean;
    statusMeaning?: string; nextAction?: string; whyItMatters?: string; howToFix?: string; configure?: string;
}
export type SecurityState = "present" | "absent" | "unknown";
export interface EstateItem { id: string; name: string; type: EstateItemType; fabricType: string; status: EstateHealth; findingIds: string[]; storageMode?: string; security?: { rls: SecurityState; ols: SecurityState }; }
export interface WorkspaceRoom {
    id: string; name: string; domain: string; capacityName?: string; floor: "upper" | "ground"; status: EstateHealth; riskScore: number;
    owner?: string; items: EstateItem[]; findingIds: string[]; adminCount?: number | null;
}
export interface TenantEstate { name: string; region: string; capacity: string; rooms: WorkspaceRoom[]; }
export interface WorkspaceRisk { name: string; domain: string; riskScore: number; issues: number; status: "red" | "amber" | "green"; }
export interface CapacityItemDetail { id: string; name: string; type: string; workspaceId: string; workspaceName: string; }
export interface CapacityDetail {
    id: string; name: string; sku: string; kind: string; state: string; region: string; assignedWorkspaceCount: number | null;
    observedWorkspaceCount: number; observedItemCount: number; workspaceScopeLimited: boolean; adminCount: number | null;
    missingTags: string; items: CapacityItemDetail[];
}
export interface FindingItem { ruleId: string; workspaceId: string; itemId: string; itemName: string; itemType: string; reason: string; }
export interface SourceCoverage { workspaceId: string; workspaceName?: string; family: string; status: string; source: string; observedAt: string; freshness: string; notice: string; }
export interface TenantSetting { name: string; title: string; group: string; ruleId: string; status: string; posture: string; enabled: boolean | null; changeCount: number | null; }
// One role of a model, only when role detail was collected. Counts and a name; filters, tables, columns and members are never stored.
export type RoleFilterKind = "none" | "constant" | "static" | "dynamic" | "mixed" | "unknown" | "not_applicable";
export interface ModelRole {
    name: string; permission: string; readsData: boolean; filterKind: RoleFilterKind;
    rowFilterTables: number; dynamicFilterTables: number; constantFilterTables: number; hiddenTables: number; hiddenColumns: number;
}
// One semantic model: whether its definition has row-level (RLS) and object-level (OLS) security, whether you declared each
// as required, and the outcome. Unknown means it could not be inspected completely; it is never "no security".
// roleDetail says whether its roles were listed: collected, partial, unavailable or not_collected. No roles listed is not "no roles".
export interface ModelSecurity {
    workspaceId: string; workspaceName: string; capacityName: string; modelId: string; modelName: string;
    rls: SecurityState; ols: SecurityState; rlsRequired: boolean; olsRequired: boolean;
    rlsOutcome: string; olsOutcome: string; inspection: string; inspectionReason: string;
    roleDetail: string; roles: ModelRole[];
}
export interface ReviewData {
    latestRunId?: string; profileId?: string; profileHash?: string; configurationHash?: string; dataProfileHash?: string;
    score: number | null; scoreStatus: string; metrics: ReviewMetric[]; dimensionScores: DimensionScore[];
    findings: ReviewFinding[]; workspaceRisks: WorkspaceRisk[]; estate: TenantEstate;
    capacities: CapacityDetail[]; coverage: SourceCoverage[]; items: FindingItem[]; tenantSettings: TenantSetting[];
    modelSecurity: ModelSecurity[]; modelSecurityAvailable: boolean;
    source: "preview" | "live";
}
export const previewReviewData: ReviewData = {
    profileId: architectureProfile, profileHash: architectureProfileHash, score: null,
    scoreStatus: "no_assessable_controls", metrics: [], dimensionScores: [], findings: [],
    workspaceRisks: [], capacities: [], coverage: [], items: [], tenantSettings: [], modelSecurity: [], modelSecurityAvailable: false,
    estate: { name: "Architecture preview", region: "Not connected", capacity: "Not connected", rooms: [] },
    source: "preview",
};

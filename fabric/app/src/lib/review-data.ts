//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

export type ReviewDimension =
    | "Architecture"
    | "Security"
    | "Performance"
    | "Governance"
    | "Cost"
    | "Notebook";

export type FindingSeverity = "critical" | "high" | "medium" | "low";

export interface ReviewMetric {
    label: string;
    value: string;
    delta: string;
    trend: "up" | "down" | "steady";
    intent: "score" | "risk" | "neutral" | "success";
}

export interface DimensionScore {
    dimension: ReviewDimension;
    score: number;
    change: number;
}

export interface ReviewFinding {
    id: string;
    dimension: ReviewDimension;
    severity: FindingSeverity;
    title: string;
    affected: string;
    recommendation: string;
    workspaceIds: string[];
}

export interface WorkspaceRisk {
    name: string;
    domain: string;
    riskScore: number;
    issues: number;
    status: "red" | "amber" | "green";
}

export type EstateItemType = "lakehouse" | "warehouse" | "model" | "report" | "notebook" | "pipeline";
export type EstateHealth = "healthy" | "warning" | "risk";

export interface EstateItem {
    id: string;
    name: string;
    type: EstateItemType;
    status: EstateHealth;
    findingIds: string[];
    governance?: {
        endorsement: "none" | "promoted" | "certified";
        sensitivityLabel: string;
        owner: string;
    };
    modelProfile?: {
        storageMode: string;
        totalSize: string;
        tables: number;
        columns: number;
        calculatedColumns: number;
        tableStats?: VertiPaqTableStat[];
        columnStats?: VertiPaqColumnStat[];
    };
    notebookProfile?: {
        smellCount: number;
        affectedCells: string;
        notebookUrl?: string;
        smells: NotebookSmellDetail[];
    };
}

export interface NotebookSmellDetail {
    ruleId: string;
    description: string;
    severity: FindingSeverity;
    affectedCells: string;
}

export interface VertiPaqTableStat {
    name: string;
    rows: number;
    totalSize: string;
    totalSizeBytes: number;
    dictionarySize: string;
    columns: number;
}

export interface VertiPaqColumnStat {
    tableName: string;
    name: string;
    dataType: string;
    encoding: string;
    cardinality: number;
    totalSize: string;
    totalSizeBytes: number;
    dictionarySize: string;
    calculated: boolean;
}

export interface WorkspaceRoom {
    id: string;
    name: string;
    domain: string;
    capacityName?: string;
    floor: "upper" | "ground";
    status: EstateHealth;
    riskScore: number;
    owner?: string;
    items: EstateItem[];
    findingIds: string[];
}

export interface TenantEstate {
    name: string;
    region: string;
    capacity: string;
    rooms: WorkspaceRoom[];
}

export interface ReviewData {
    metrics: ReviewMetric[];
    dimensionScores: DimensionScore[];
    findings: ReviewFinding[];
    workspaceRisks: WorkspaceRisk[];
    estate: TenantEstate;
    source: "preview" | "live";
}

export const reviewMetrics: ReviewMetric[] = [
    { label: "Best-practice score", value: "78", delta: "+6 since last run", trend: "up", intent: "score" },
    { label: "Critical + high", value: "3", delta: "Sample findings", trend: "steady", intent: "risk" },
    { label: "Workspaces at risk", value: "3", delta: "of 5 sample workspaces", trend: "down", intent: "neutral" },
    { label: "Agent accuracy", value: "—", delta: "Requires live Data Agent", trend: "steady", intent: "neutral" },
];

export const dimensionScores: DimensionScore[] = [
    { dimension: "Architecture", score: 84, change: 8 },
    { dimension: "Security", score: 71, change: 2 },
    { dimension: "Performance", score: 76, change: 5 },
    { dimension: "Governance", score: 68, change: -1 },
    { dimension: "Cost", score: 88, change: 11 },
];

export const reviewFindings: ReviewFinding[] = [
    {
        id: "ARCH-009",
        dimension: "Architecture",
        severity: "high",
        title: "Critical lineage path crosses unmanaged boundaries",
        affected: "Sample Lakehouse 01 to Sample Model 01",
        recommendation: "Assign ownership to each lineage boundary and document the serving contract.",
        workspaceIds: ["finance"],
    },
    {
        id: "SEC-004",
        dimension: "Security",
        severity: "critical",
        title: "Production workspace access is broader than policy",
        affected: "Sample Model 01 in Sample Workspace 01",
        recommendation: "Replace direct member access with governed security groups.",
        workspaceIds: ["finance"],
    },
    {
        id: "GOV-001",
        dimension: "Governance",
        severity: "high",
        title: "Workspace has a single administrator",
        affected: "Sample Model 02 in Sample Workspace 02",
        recommendation: "Assign a second accountable workspace administrator.",
        workspaceIds: ["customer"],
    },
    {
        id: "PERF-013",
        dimension: "Performance",
        severity: "high",
        title: "Direct Lake fallback behavior is not explicit",
        affected: "Sample Model 03 in Sample Workspace 03",
        recommendation: "Declare fallback behavior and monitor fallback events.",
        workspaceIds: ["executive"],
    },
    {
        id: "COST-005",
        dimension: "Cost",
        severity: "medium",
        title: "Capacity utilization leaves a recurring idle window",
        affected: "Sample Capacity",
        recommendation: "Evaluate scheduled pause or workload consolidation.",
        workspaceIds: [],
    },
    {
        id: "NBCODE-003",
        dimension: "Notebook",
        severity: "medium",
        title: "Notebook contains repeated full-table actions",
        affected: "Sample Notebook 01 in Sample Workspace 04",
        recommendation: "Replace repeated actions with a persisted intermediate result and inspect only bounded samples.",
        workspaceIds: ["supply"],
    },
];

export const workspaceRisks: WorkspaceRisk[] = [
    { name: "Sample Workspace 01", domain: "Sample Domain A", riskScore: 91, issues: 8, status: "red" },
    { name: "Sample Workspace 02", domain: "Sample Domain B", riskScore: 78, issues: 6, status: "red" },
    { name: "Sample Workspace 03", domain: "Sample Domain C", riskScore: 64, issues: 4, status: "amber" },
    { name: "Sample Workspace 04", domain: "Sample Domain D", riskScore: 42, issues: 3, status: "amber" },
    { name: "Sample Workspace 05", domain: "Sample Domain E", riskScore: 18, issues: 1, status: "green" },
];

export const tenantEstate: TenantEstate = {
    name: "Anonymous preview estate",
    region: "Sample region",
    capacity: "Sample capacity",
    rooms: [
        {
            id: "finance",
            name: "Sample Workspace 01",
            domain: "Sample Domain A",
            capacityName: "Stockholm Production F64",
            floor: "upper",
            status: "risk",
            riskScore: 91,
            findingIds: ["ARCH-009", "SEC-004"],
            items: [
                { id: "finance-lh", name: "Sample Lakehouse 01", type: "lakehouse", status: "warning", findingIds: ["ARCH-009"] },
                { id: "finance-sm", name: "Sample Model 01", type: "model", status: "risk", findingIds: ["SEC-004"], governance: { endorsement: "none", sensitivityLabel: "Not set", owner: "Sample owner A" }, modelProfile: { storageMode: "Direct Lake", totalSize: "4.8 GB", tables: 34, columns: 286, calculatedColumns: 12, tableStats: [
                    { name: "FactTransactions", rows: 48320512, totalSize: "2.1 GB", totalSizeBytes: 2254857830, dictionarySize: "438 MB", columns: 46 },
                    { name: "FactLedger", rows: 18640755, totalSize: "1.2 GB", totalSizeBytes: 1288490189, dictionarySize: "276 MB", columns: 38 },
                    { name: "DimCustomer", rows: 2184560, totalSize: "420 MB", totalSizeBytes: 440401920, dictionarySize: "168 MB", columns: 31 },
                    { name: "DimProduct", rows: 482110, totalSize: "186 MB", totalSizeBytes: 195035136, dictionarySize: "74 MB", columns: 24 },
                ], columnStats: [
                    { tableName: "FactTransactions", name: "TransactionId", dataType: "Int64", encoding: "Value", cardinality: 48320512, totalSize: "612 MB", totalSizeBytes: 641728512, dictionarySize: "0 MB", calculated: false },
                    { tableName: "FactTransactions", name: "Description", dataType: "String", encoding: "Hash", cardinality: 8204310, totalSize: "486 MB", totalSizeBytes: 509607936, dictionarySize: "312 MB", calculated: false },
                    { tableName: "FactLedger", name: "PostingReference", dataType: "String", encoding: "Hash", cardinality: 12840110, totalSize: "358 MB", totalSizeBytes: 375390208, dictionarySize: "221 MB", calculated: false },
                    { tableName: "DimCustomer", name: "CustomerSegment", dataType: "String", encoding: "Hash", cardinality: 142, totalSize: "96 MB", totalSizeBytes: 100663296, dictionarySize: "2 MB", calculated: true },
                ] } },
                { id: "finance-rp", name: "Sample Report 01", type: "report", status: "healthy", findingIds: [], governance: { endorsement: "certified", sensitivityLabel: "Confidential", owner: "Sample owner A" } },
                { id: "finance-pl", name: "Sample Pipeline 01", type: "pipeline", status: "warning", findingIds: [] },
            ],
        },
        {
            id: "customer",
            name: "Sample Workspace 02",
            domain: "Sample Domain B",
            capacityName: "Stockholm Production F64",
            floor: "upper",
            status: "risk",
            riskScore: 78,
            findingIds: ["GOV-001"],
            items: [
                { id: "customer-wh", name: "Sample Warehouse 01", type: "warehouse", status: "healthy", findingIds: [] },
                { id: "customer-sm", name: "Sample Model 02", type: "model", status: "warning", findingIds: ["GOV-001"], governance: { endorsement: "promoted", sensitivityLabel: "General", owner: "Sample owner B" }, modelProfile: { storageMode: "Import", totalSize: "1.6 GB", tables: 18, columns: 142, calculatedColumns: 7 } },
                { id: "customer-rp", name: "Sample Report 02", type: "report", status: "healthy", findingIds: [], governance: { endorsement: "promoted", sensitivityLabel: "General", owner: "Sample owner B" } },
            ],
        },
        {
            id: "executive",
            name: "Sample Workspace 03",
            domain: "Sample Domain C",
            capacityName: "Nordic Shared F32",
            floor: "upper",
            status: "warning",
            riskScore: 64,
            findingIds: ["PERF-013"],
            items: [
                { id: "executive-sm", name: "Sample Model 03", type: "model", status: "warning", findingIds: ["PERF-013"], governance: { endorsement: "certified", sensitivityLabel: "Confidential", owner: "Sample owner C" }, modelProfile: { storageMode: "Direct Lake", totalSize: "2.1 GB", tables: 12, columns: 96, calculatedColumns: 2 } },
                { id: "executive-rp", name: "Sample Report 03", type: "report", status: "warning", findingIds: [], governance: { endorsement: "certified", sensitivityLabel: "Confidential", owner: "Sample owner C" } },
            ],
        },
        {
            id: "supply",
            name: "Sample Workspace 04",
            domain: "Sample Domain D",
            capacityName: "Nordic Shared F32",
            floor: "ground",
            status: "warning",
            riskScore: 42,
            findingIds: ["NBCODE-003"],
            items: [
                { id: "supply-lh", name: "Sample Lakehouse 02", type: "lakehouse", status: "healthy", findingIds: [] },
                { id: "supply-nb", name: "Sample Notebook 01", type: "notebook", status: "warning", findingIds: ["NBCODE-003"], notebookProfile: { smellCount: 1, affectedCells: "4, 7", smells: [{ ruleId: "NBCODE-003", description: "Unbounded Spark collect or toPandas action can exhaust driver memory.", severity: "medium", affectedCells: "4, 7" }] } },
                { id: "supply-rp", name: "Sample Report 04", type: "report", status: "healthy", findingIds: [], governance: { endorsement: "none", sensitivityLabel: "Not set", owner: "Sample owner D" } },
            ],
        },
        {
            id: "people",
            name: "Sample Workspace 05",
            domain: "Sample Domain E",
            capacityName: "Development F8",
            floor: "ground",
            status: "healthy",
            riskScore: 18,
            findingIds: [],
            items: [
                { id: "people-lh", name: "Sample Lakehouse 03", type: "lakehouse", status: "healthy", findingIds: [] },
                { id: "people-sm", name: "Sample Model 04", type: "model", status: "healthy", findingIds: [], governance: { endorsement: "certified", sensitivityLabel: "General", owner: "Sample owner E" }, modelProfile: { storageMode: "Direct Lake", totalSize: "860 MB", tables: 9, columns: 71, calculatedColumns: 0 } },
                { id: "people-rp", name: "Sample Report 05", type: "report", status: "healthy", findingIds: [], governance: { endorsement: "certified", sensitivityLabel: "General", owner: "Sample owner E" } },
            ],
        },
    ],
};

export const previewReviewData: ReviewData = {
    metrics: reviewMetrics,
    dimensionScores,
    findings: reviewFindings,
    workspaceRisks,
    estate: tenantEstate,
    source: "preview",
};

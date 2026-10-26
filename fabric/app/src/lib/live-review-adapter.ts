// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import type { QueryTable } from "@microsoft/fabric-app-data";
import {
    activeArchitectureRules, architectureProfile, architectureProfileHash, dimensionOrder, previewReviewData,
    type AssessmentDimension, type CapacityDetail, type DimensionScore, type EstateHealth, type EstateItem, type EstateItemType,
    type ModelRole, type ModelSecurity, type ReviewData, type ReviewFinding, type ReviewMetric, type RoleFilterKind, type SecurityState, type WorkspaceRisk, type WorkspaceRoom,
} from "@/lib/review-data";

export interface LiveReviewTables {
    findings: QueryTable; runSummary: QueryTable; semanticModels: QueryTable; workspaceRisk: QueryTable; coverage: QueryTable;
    capacities: QueryTable; findingItems: QueryTable; dimensions: QueryTable; workspaces: QueryTable; capacityItems: QueryTable;
    tenantSettings: QueryTable;
    modelSecurity?: QueryTable | null;
    modelRoles?: QueryTable | null;
}
type Row = Record<string, unknown>;
function records(table: QueryTable): Row[] {
    const names = table.columns.map(c => c.name.replace(/.*\[/, "").replace(/\]$/, ""));
    return table.rows.map(row => Object.fromEntries(names.map((name, i) => [name, row[i]])));
}
function compatible(row: Row) { return row.profile_id === architectureProfile && row.profile_sha256 === architectureProfileHash; }
const text = (value: unknown) => value == null ? "" : String(value);
const number = (value: unknown) => value == null || value === "" ? null : Number.isFinite(Number(value)) ? Number(value) : null;
const dimensionLabels: Record<string, AssessmentDimension> = Object.fromEntries(dimensionOrder.map(d => [d.toLowerCase(), d]));
const dimensionOf = (value: unknown): AssessmentDimension => dimensionLabels[text(value).toLowerCase()] ?? "Architecture";
const securityState = (value: unknown): SecurityState => value === "present" || value === "absent" ? value : "unknown";
const filterKinds: readonly RoleFilterKind[] = ["none", "constant", "static", "dynamic", "mixed", "unknown", "not_applicable"];
const filterKind = (value: unknown): RoleFilterKind => filterKinds.find(kind => kind === value) ?? "unknown";
const count = (value: unknown) => number(value) ?? 0;
const itemTypes: Record<string, EstateItemType> = {
    Lakehouse: "lakehouse", Warehouse: "warehouse", SemanticModel: "model", Dataset: "model", Report: "report",
    PaginatedReport: "report", Notebook: "notebook", DataPipeline: "pipeline", App: "app",
};
const environmentLabels: Record<string, string> = { production: "Production", nonproduction: "Non-production" };
// An unknown risk is shown as a warning: it must not look healthy, and it is not a confirmed failure.
const health = (status: string): EstateHealth => status === "critical" ? "risk" : status === "healthy" ? "healthy" : "warning";
const riskColour = (status: string): WorkspaceRisk["status"] => status === "critical" ? "red" : status === "healthy" ? "green" : "amber";
const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
function groupBy<T>(rows: T[], key: (row: T) => string) {
    const groups = new Map<string, T[]>();
    for (const row of rows) groups.set(key(row), [...(groups.get(key(row)) ?? []), row]);
    return groups;
}

export function buildLiveReviewData(tables: LiveReviewTables): ReviewData {
    const everyRun = records(tables.runSummary).sort((a, b) => text(b.run_timestamp).localeCompare(text(a.run_timestamp)));
    const runs = everyRun.filter(compatible);
    const current = runs[0];
    const latestRunId = current ? text(current.run_id) : undefined;
    const currentRows = (table: QueryTable) => records(table).filter(r => compatible(r) && latestRunId && r.run_id === latestRunId);
    const findings: ReviewFinding[] = currentRows(tables.findings).filter(r => activeArchitectureRules.has(text(r.rule_id))).map(r => ({
        id: text(r.rule_id), dimension: r.rule_kind === "coverage" ? "Source coverage" : dimensionOf(r.dimension),
        severity: ["critical", "high", "medium", "low", "info"].includes(text(r.severity)) ? text(r.severity) as ReviewFinding["severity"] : "info",
        title: text(r.title), affected: text(r.affected), recommendation: text(r.recommendation),
        statusMeaning: text(r.status_meaning), nextAction: text(r.next_action), whyItMatters: text(r.why_it_matters),
        howToFix: text(r.how_to_fix), configure: text(r.configure),
        workspaceIds: text(r.affected).split(", ").filter(Boolean), workspaceName: text(r.workspace_name), status: text(r.status),
        disposition: text(r.rule_kind), scored: (r.is_scored === 1 || r.is_scored === true) && !["signal", "coverage"].includes(text(r.rule_kind)),
    }));
    const items = currentRows(tables.findingItems).filter(r => activeArchitectureRules.has(text(r.rule_id))).map(r => ({ruleId: text(r.rule_id),
        workspaceId: text(r.workspace_id), itemId: text(r.item_id), itemName: text(r.item_name) || text(r.item_id),
        itemType: text(r.item_type), reason: text(r.reason)}));
    const score = number(current?.score);

    const failing = new Set(findings.filter(f => f.status === "fail" && f.scored).map(f => f.id));
    const rulesByItem = groupBy(items, i => i.itemId);
    const storageModes = new Map(currentRows(tables.semanticModels).map(m => [text(m.model_id), text(m.storage_mode)]));
    const rolesByModel = groupBy(tables.modelRoles ? currentRows(tables.modelRoles) : [], r => text(r.model_id));
    const modelSecurity: ModelSecurity[] = tables.modelSecurity ? currentRows(tables.modelSecurity).map(r => ({
        workspaceId: text(r.workspace_id), workspaceName: text(r.workspace_name), capacityName: text(r.capacity_name),
        modelId: text(r.model_id), modelName: text(r.model_name) || text(r.model_id),
        rls: securityState(r.rls_status), ols: securityState(r.ols_status),
        rlsRequired: r.rls_required === true, olsRequired: r.ols_required === true,
        rlsOutcome: text(r.rls_outcome), olsOutcome: text(r.ols_outcome),
        inspection: text(r.security_inspection) || "unavailable", inspectionReason: text(r.inspection_reason),
        // No role_detail value, or no roles table in an older deployed model, means role detail was not collected.
        roleDetail: tables.modelRoles ? text(r.role_detail) || "not_collected" : "not_collected",
        roles: (rolesByModel.get(text(r.model_id)) ?? []).map((role): ModelRole => ({
            name: text(role.role_name), permission: text(role.role_permission), readsData: role.reads_data === true,
            filterKind: filterKind(role.filter_kind), rowFilterTables: count(role.row_filter_table_count),
            dynamicFilterTables: count(role.dynamic_filter_table_count), constantFilterTables: count(role.constant_filter_table_count),
            hiddenTables: count(role.ols_table_count), hiddenColumns: count(role.ols_column_count),
        })).sort((a, b) => a.name.localeCompare(b.name)),
    })) : [];
    const securityByModel = new Map(modelSecurity.map(m => [m.modelId, {rls: m.rls, ols: m.ols}]));
    const inventory = new Map(currentRows(tables.workspaces).map(w => [text(w.workspace_id), w]));
    const estateItems = groupBy(currentRows(tables.capacityItems), r => text(r.workspace_id));
    const risks = currentRows(tables.workspaceRisk);
    const rooms: WorkspaceRoom[] = risks.map(w => {
        const id = text(w.workspace_id);
        const known = inventory.get(id);
        return {
            id, name: text(w.workspace_name), domain: environmentLabels[text(known?.environment)] ?? "Environment not declared",
            capacityName: text(w.capacity_name) || "No capacity", floor: "ground", status: health(text(w.status)),
            riskScore: number(w.risk_score) ?? 0, adminCount: number(known?.admin_count),
            findingIds: findings.filter(f => f.workspaceIds.includes(id)).map(f => f.id),
            items: (estateItems.get(id) ?? []).map((i): EstateItem => {
                const rules = [...new Set((rulesByItem.get(text(i.item_id)) ?? []).map(l => l.ruleId))];
                const fabricType = text(i.item_type);
                return {
                    id: text(i.item_id), name: text(i.item_name), type: itemTypes[fabricType] ?? "component", fabricType,
                    status: rules.some(rule => failing.has(rule)) ? "risk" : rules.length ? "warning" : "healthy",
                    findingIds: rules, storageMode: storageModes.get(text(i.item_id)) || undefined,
                    ...(securityByModel.has(text(i.item_id)) ? { security: securityByModel.get(text(i.item_id)) } : {}),
                };
            }),
        };
    });
    const workspaceRisks: WorkspaceRisk[] = risks.filter(w => number(w.risk_score) != null).map(w => ({
        name: text(w.workspace_name), domain: text(w.capacity_name) || "No capacity", riskScore: number(w.risk_score) ?? 0,
        issues: number(w.issue_count) ?? 0, status: riskColour(text(w.status)),
    }));

    const capacityItems = groupBy(currentRows(tables.capacityItems), r => text(r.capacity_id));
    const capacities: CapacityDetail[] = currentRows(tables.capacities).map(r => ({id: text(r.capacity_id), name: text(r.capacity_name), sku: text(r.sku),
        kind: text(r.kind), state: text(r.state), region: text(r.region), assignedWorkspaceCount: number(r.assigned_workspace_count),
        observedWorkspaceCount: number(r.observed_workspace_count) ?? 0, observedItemCount: number(r.observed_item_count) ?? 0,
        workspaceScopeLimited: r.workspace_scope_limited === true, adminCount: number(r.admin_count), missingTags: text(r.missing_tags),
        items: (capacityItems.get(text(r.capacity_id)) ?? []).map(i => ({id: text(i.item_id), name: text(i.item_name), type: text(i.item_type),
            workspaceId: text(i.workspace_id), workspaceName: text(i.workspace_name)}))}));

    const dimensionScores: DimensionScore[] = currentRows(tables.dimensions)
        .map(r => ({dimension: dimensionOf(r.dimension), score: number(r.score), failed: number(r.fail_count) ?? 0,
            assessed: number(r.assessed_count) ?? 0, total: number(r.total_findings) ?? 0}))
        .sort((a, b) => (dimensionOrder as readonly string[]).indexOf(a.dimension) - (dimensionOrder as readonly string[]).indexOf(b.dimension));

    const unresolved = (number(current?.unknown_count) ?? 0) + (number(current?.missing_evidence_count) ?? 0);
    const atRisk = rooms.filter(r => r.status === "risk").length;
    const coverage = number(current?.assessment_coverage);
    const metrics: ReviewMetric[] = !current ? [] : [
        {label: "Review score", value: score == null ? "Not assessed" : String(score), trend: "steady", intent: "score",
            delta: `${number(current.assessed_count) ?? 0} controls assessed`},
        {label: "Critical + high", value: String((number(current.critical_fail) ?? 0) + (number(current.high_fail) ?? 0)), trend: "steady", intent: "risk",
            delta: `${number(current.fail_count) ?? 0} failed controls in total`},
        {label: "Workspaces at risk", value: String(atRisk), trend: "steady", intent: "risk", delta: `of ${rooms.length} reviewed`},
        {label: "Assessment coverage", value: coverage == null ? "Not assessed" : `${coverage}%`,
            trend: "steady", intent: "neutral", delta: `${unresolved} checks unknown or missing evidence`},
    ];

    const regions = [...new Set(capacities.map(c => c.region).filter(Boolean))];
    return { ...previewReviewData, source: "live", latestRunId, profileId: current ? architectureProfile : undefined,
        profileHash: current ? architectureProfileHash : undefined, configurationHash: text(current?.configuration_sha256),
        dataProfileHash: everyRun[0] ? text(everyRun[0].profile_sha256) : undefined,
        score, scoreStatus: score == null ? "no_assessable_controls" : "assessed", findings, metrics, dimensionScores, workspaceRisks,
        estate: { name: text(current?.engagement_name) || "Fabric estate", region: regions.join(", "),
            capacity: `${capacities.length} ${capacities.length === 1 ? "capacity" : "capacities"} · ${sum(capacities.map(c => c.observedItemCount))} items`, rooms },
        coverage: currentRows(tables.coverage).map(r => ({workspaceId: text(r.workspace_id), workspaceName: text(r.workspace_name), family: text(r.evidence_family),
            status: text(r.collection_status), source: text(r.source_contract), observedAt: text(r.observed_at),
            freshness: text(r.freshness_status), notice: text(r.notice)})),
        items, capacities, modelSecurity, modelSecurityAvailable: Boolean(tables.modelSecurity),
        tenantSettings: currentRows(tables.tenantSettings).map(r => ({name: text(r.setting_name), title: text(r.title), group: text(r.setting_group),
            ruleId: text(r.rule_id), status: text(r.status), posture: text(r.posture), enabled: typeof r.enabled === "boolean" ? r.enabled : null,
            changeCount: number(r.change_count)})),
    };
}

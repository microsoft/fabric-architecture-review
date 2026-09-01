//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { lazy, Suspense, useId, useState } from "react";
import {
    Activity,
    ArrowDownRight,
    ArrowRight,
    ArrowUpRight,
    BookOpenCheck,
    Boxes,
    Check,
    ChevronRight,
    CircleDot,
    Code2,
    Database,
    ExternalLink,
    FileWarning,
    Focus,
    Gauge,
    GitFork,
    Info,
    LayoutDashboard,
    Menu,
    Moon,
    Network,
    Scale,
    ShieldCheck,
    Sun,
    TableProperties,
    Tags,
    UserRound,
    Columns3,
    X,
} from "lucide-react";
import { AgentPanel } from "@/components/agent-panel";
import type { EstateReviewArea, EstateReviewContext } from "@/components/estate-map";
import { LensBarPlot, OverviewPlots, type LensBarDatum } from "@/components/review-plots";
import { ReviewDataProvider, useReviewData } from "@/hooks/review-data.context";
import { useThemeContext } from "@/hooks/theme.context";
import {
    previewReviewData,
    type FindingSeverity,
    type ReviewData,
    type ReviewFinding,
    type WorkspaceRoom,
} from "@/lib/review-data";
import { cn } from "@/lib/utils";

const EstateMap = lazy(() => import("@/components/estate-map").then((module) => ({ default: module.EstateMap })));

function EstateMapFallback({ compact = false }: { compact?: boolean }) {
    return <div className={cn("campus-explorer grid place-items-center bg-muted text-200 text-muted-foreground", compact && "campus-explorer-compact")}>Loading estate topology…</div>;
}

type ViewId = "overview" | "findings" | EstateReviewArea | "estate" | "dax";

const navItems = [
    { id: "overview" as const, label: "Overview", icon: LayoutDashboard },
    { id: "findings" as const, label: "Findings", icon: FileWarning },
    { id: "estate" as const, label: "Estate map", icon: Network },
    { id: "governance" as const, label: "Governance", icon: ShieldCheck },
    { id: "models" as const, label: "Semantic model optimization", icon: Database },
    { id: "dax" as const, label: "DAX Analyzer", icon: Code2 },
    { id: "efficiency" as const, label: "Performance + cost", icon: Gauge },
    { id: "architecture" as const, label: "Architecture", icon: GitFork },
    { id: "notebooks" as const, label: "Notebooks", icon: BookOpenCheck },
];

const viewTitles: Record<ViewId, string> = {
    overview: "Estate, prioritized",
    findings: "Risk decision queue",
    estate: "3D estate",
    governance: "Governance",
    models: "Semantic model optimization",
    dax: "DAX Analyzer",
    efficiency: "Performance and cost",
    architecture: "Architecture",
    notebooks: "Notebook engineering",
};

const severityClass: Record<FindingSeverity, string> = {
    critical: "bg-destructive-soft text-destructive",
    high: "bg-high-soft text-high-strong",
    medium: "bg-warning-soft text-warning-strong",
    low: "bg-info-soft text-info-strong",
};

const metricDefinitions: Record<string, string> = {
    "Best-practice score": "Pass percentage among checks that produced a scored result in the latest review. Unknown and missing-evidence checks are not treated as passes.",
    "Critical + high": "Failed checks classified as critical or high severity in the latest review.",
    "Workspaces at risk": "Workspaces with a non-green risk status based on their issue count and aggregate finding risk.",
    "Assessment coverage": "Percentage of all review checks that produced a definitive result. Unknown and missing-evidence checks reduce coverage.",
    "Agent accuracy": "Deterministic evaluation result for the connected Data Agent when live evaluation evidence is available.",
};

const dimensionDefinitions: Record<string, string> = {
    Architecture: "Architecture, topology, lineage, and design checks that produced scored results.",
    Performance: "Query, refresh, storage-mode, and capacity-performance checks that produced scored results.",
    Cost: "Capacity utilization, avoidable spend, and consolidation checks that produced scored results.",
    Governance: "Ownership, endorsement, workspace administration, and governance checks that produced scored results.",
    "Operational excellence": "Deployment pipeline, Git integration, monitoring, and operational practice checks that produced scored results.",
    Security: "Access, sharing, gateway, and security-control checks that produced scored results.",
    "Tenant settings": "Fabric tenant-setting checks that produced scored results from collected administrator metadata.",
    "Best practices": "Artifact and semantic-model best-practice checks that produced scored results.",
    Notebook: "Static notebook code checks that produced scored results.",
};

function DefinitionTooltip({ definition, label, align = "left" }: { definition: string; label: string; align?: "left" | "right" }) {
    const tooltipId = useId();
    return (
        <span className="group relative inline-flex shrink-0">
            <button aria-describedby={tooltipId} aria-label={`About ${label}`} className="grid icon-size-300 place-items-center text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" type="button">
                <Info className="icon-size-150" aria-hidden="true" />
            </button>
            <span className={cn("pointer-events-none absolute top-[calc(100%+0.4rem)] z-30 hidden w-72 border border-border bg-foreground px-300 py-200 text-100 font-normal leading-200 text-background shadow-lg group-hover:block group-focus-within:block", align === "right" ? "right-0" : "left-0")} id={tooltipId} role="tooltip">
                {definition}
            </span>
        </span>
    );
}

function BrandMark() {
    return (
        <div className="flex items-center gap-300">
            <span className="relative grid icon-size-brand place-items-center overflow-hidden rounded-lg bg-primary text-primary-foreground">
                <Boxes className="icon-size-400" aria-hidden="true" />
                <span className="absolute bottom-0 right-0 icon-size-100 bg-warning" />
            </span>
            <div>
                <p className="font-heading text-300 font-bold leading-300">FAR</p>
                <p className="text-100 text-muted-foreground">Fabric Architecture Review</p>
            </div>
        </div>
    );
}

function Sidebar({ currentView, onNavigate }: { currentView: ViewId; onNavigate: (view: ViewId) => void }) {
    const { source } = useReviewData();
    return (
        <aside className="hidden w-sidebar shrink-0 flex-col border-r border-border bg-sidebar lg:flex">
            <div className="border-b border-border p-500">
                <BrandMark />
            </div>
            <nav className="flex flex-1 flex-col gap-100 p-300" aria-label="Primary navigation">
                <p className="px-300 pb-200 pt-300 text-100 font-semibold uppercase text-muted-foreground">Review</p>
                {navItems.map((item) => {
                    const Icon = item.icon;
                    const active = currentView === item.id;
                    return (
                        <button
                            className={cn(
                                "flex items-center gap-300 rounded-lg px-300 py-300 text-left text-300 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                active ? "bg-primary-soft text-primary-strong" : "text-muted-foreground hover:bg-accent hover:text-foreground",
                            )}
                            key={item.id}
                            onClick={() => onNavigate(item.id)}
                            type="button"
                        >
                            <Icon className="icon-size-200" aria-hidden="true" />
                            {item.label}
                            {active && <span className="ml-auto icon-size-100 rounded-full bg-primary" />}
                        </button>
                    );
                })}
                <div className="mt-auto rounded-xl border border-border bg-card p-300">
                    <div className="mb-200 flex items-center gap-200 text-200 font-semibold">
                        <ShieldCheck className="icon-size-200 text-success-strong" aria-hidden="true" />
                        {source === "live" ? "Connected assessment" : "Sample assessment"}
                    </div>
                    <p className="text-100 leading-200 text-muted-foreground">{source === "live" ? "Latest review data from the connected Fabric semantic model." : "Illustrative values only. Connect a model for live data."}</p>
                    <div className="mt-300 h-100 overflow-hidden rounded-full bg-muted">
                        <div className="h-full w-progress rounded-full bg-success" />
                    </div>
                </div>
            </nav>
            <div className="border-t border-border p-400 text-100 text-muted-foreground">Release 2026.08 · {source === "live" ? "Live" : "Preview"}</div>
        </aside>
    );
}

function MetricStrip() {
    const { metrics } = useReviewData();
    return (
        <section className="grid grid-cols-2 border-y border-border bg-card xl:grid-cols-4" aria-label="Review metrics">
            {metrics.map((metric, index) => (
                <article className={cn("relative p-400 md:p-500", index % 2 !== 0 && "border-l border-border", index > 1 && "border-t border-border xl:border-t-0", index === 2 && "xl:border-l")} key={metric.label}>
                    <div className="mb-300 flex items-start justify-between gap-200">
                        <div className="flex items-center gap-100">
                            <p className="text-200 font-medium text-muted-foreground">{metric.label}</p>
                            <DefinitionTooltip align={index === metrics.length - 1 ? "right" : "left"} definition={metricDefinitions[metric.label] ?? "Latest value calculated from the connected architecture review evidence."} label={metric.label} />
                        </div>
                        {metric.trend === "up" ? (
                            <ArrowUpRight className="icon-size-200 text-success-strong" aria-hidden="true" />
                        ) : metric.trend === "down" ? (
                            <ArrowDownRight className="icon-size-200 text-success-strong" aria-hidden="true" />
                        ) : (
                            <CircleDot className="icon-size-200 text-warning-strong" aria-hidden="true" />
                        )}
                    </div>
                    <div className="flex items-baseline gap-200">
                        <strong className="font-numeric text-hero-800 leading-hero-800">{metric.value}</strong>
                        {metric.label === "Best-practice score" && <span className="text-300 text-muted-foreground">/ 100</span>}
                    </div>
                    <p className="mt-100 text-100 text-muted-foreground">{metric.delta}</p>
                </article>
            ))}
        </section>
    );
}

function ScorePanel({ onOpenDax, onOpenFindings }: { onOpenDax: () => void; onOpenFindings: () => void }) {
    const { daxSummary, dimensionScores } = useReviewData();
    return (
        <section className="border-b border-border bg-card p-500 lg:border-b-0 lg:border-r">
            <div className="mb-500 flex items-start justify-between gap-300">
                <div>
                    <p className="section-kicker">Control posture</p>
                    <h2 className="section-title">Dimension pulse</h2>
                </div>
                <button className="icon-button" aria-label="Open findings" onClick={onOpenFindings} type="button">
                    <ArrowRight className="icon-size-200" aria-hidden="true" />
                </button>
            </div>
            <div className="space-y-400">
                {dimensionScores.map((item) => (
                    <div key={item.dimension}>
                        <div className="mb-200 flex items-center justify-between gap-300 text-200">
                            <span className="flex items-center gap-100 font-medium">{item.dimension}<DefinitionTooltip definition={`${dimensionDefinitions[item.dimension]} The score is the pass percentage among scored checks; unknown and missing-evidence checks are excluded.`} label={item.dimension} /></span>
                            <span className="flex items-center gap-200 font-numeric">
                                <strong>{item.score}</strong>
                                <span className={item.change >= 0 ? "text-success-strong" : "text-destructive"}>
                                    {item.change >= 0 ? "+" : ""}{item.change}
                                </span>
                            </span>
                        </div>
                        <div className="h-200 overflow-hidden rounded-full bg-muted">
                            <div className="h-full rounded-full bg-score" style={{ width: `${item.score}%` }} />
                        </div>
                    </div>
                ))}
            </div>
            <div className="mt-500 border-t border-border pt-400">
                <div className="flex items-start gap-300">
                    <span className="grid icon-size-500 shrink-0 place-items-center bg-primary-soft text-primary-strong"><Code2 className="icon-size-200" aria-hidden="true" /></span>
                    <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-100 text-300 font-semibold">DAX static analysis<DefinitionTooltip definition="Summarizes semantic-model definitions available for metadata-only DAX analysis. Flagged measures contain static syntax signals; this does not measure duration, capacity consumption, or runtime performance." label="DAX static analysis" /></p>
                        <p className="mt-100 text-200 text-muted-foreground">{daxSummary.measureCount.toLocaleString()} measures · {daxSummary.flaggedMeasureCount.toLocaleString()} flagged</p>
                        <p className="mt-100 text-100 text-muted-foreground">{daxSummary.definitionCoverage}% definition coverage · {daxSummary.availableModelCount} of {daxSummary.modelCount} models available</p>
                    </div>
                </div>
                <button className="mt-300 inline-flex items-center gap-100 text-200 font-semibold text-primary-strong underline" onClick={onOpenDax} type="button">Open DAX Analyzer<ArrowRight className="icon-size-100" aria-hidden="true" /></button>
                <p className="mt-200 text-100 leading-200 text-muted-foreground">Metadata coverage and static syntax signals only; no runtime performance is inferred.</p>
            </div>
        </section>
    );
}

function PriorityFindings({ expanded = false, onOpenFinding }: { expanded?: boolean; onOpenFinding: (finding: ReviewFinding) => void }) {
    const { findings: reviewFindings } = useReviewData();
    const findings = expanded ? reviewFindings : reviewFindings.slice(0, 3);
    return (
        <section className="bg-card p-500">
            <div className="mb-400 flex items-start justify-between gap-300">
                <div>
                    <p className="section-kicker">Decision queue</p>
                    <h2 className="section-title">Priority findings</h2>
                </div>
                <span className="rounded-full bg-destructive-soft px-300 py-100 text-100 font-semibold text-destructive">{reviewFindings.length} findings</span>
            </div>
            <div className="divide-y divide-border">
                {findings.map((finding) => (
                    <button className="group grid w-full gap-300 py-400 text-left md:grid-cols-finding focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" key={finding.id} onClick={() => onOpenFinding(finding)} type="button">
                        <div className="flex items-start gap-300">
                            <span className={cn("rounded-md px-200 py-100 font-monospace text-100 font-semibold", severityClass[finding.severity])}>{finding.id}</span>
                            <span>
                                <span className="block text-300 font-semibold leading-300 group-hover:text-primary-strong">{finding.title}</span>
                                <span className="mt-100 block text-200 text-muted-foreground">{finding.affected}</span>
                            </span>
                        </div>
                        <p className="text-200 leading-300 text-muted-foreground">{finding.recommendation}</p>
                        <ChevronRight className="hidden icon-size-200 self-center justify-self-end text-muted-foreground md:block" aria-hidden="true" />
                    </button>
                ))}
            </div>
        </section>
    );
}

function WorkspaceSelect({ rooms, value, onChange }: { rooms: WorkspaceRoom[]; value: string; onChange: (workspaceId: string) => void }) {
    const selectId = useId();
    return <label className="flex items-center gap-200 text-200 font-semibold text-muted-foreground" htmlFor={selectId}>
        <span>Workspace</span>
        <select className="min-w-0 rounded-lg border border-input bg-card px-300 py-200 text-200 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/30" id={selectId} onChange={(event) => onChange(event.target.value)} value={value}>
            <option value="all">All workspaces</option>
            {rooms.map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}
        </select>
    </label>;
}

function findingsForWorkspace(findings: ReviewFinding[], workspaceId: string) {
    return workspaceId === "all" ? findings : findings.filter((finding) => finding.workspaceIds.includes(workspaceId));
}

function FindingRows({ findings }: { findings: ReviewFinding[] }) {
    return <div className="divide-y divide-border">{findings.map((finding) => <article className="grid gap-300 px-400 py-400 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" key={finding.id}><div><span className={cn("inline-flex rounded-md px-200 py-100 font-monospace text-100 font-semibold", severityClass[finding.severity])}>{finding.id}</span><h4 className="mt-200 text-300 font-semibold">{finding.title}</h4><p className="mt-100 text-100 text-muted-foreground">{finding.affected}</p></div><p className="text-200 leading-300 text-muted-foreground">{finding.recommendation}</p></article>)}</div>;
}

function WorkspacePanel({ onOpenWorkspace }: { onOpenWorkspace: (workspaceId: string) => void }) {
    const { estate, workspaceRisks } = useReviewData();
    return (
        <section className="border-t border-border bg-card p-500">
            <div className="mb-400 flex items-end justify-between gap-300">
                <div>
                    <p className="section-kicker">Concentration</p>
                    <h2 className="section-title">Workspace hotspots</h2>
                </div>
                <p className="text-100 text-muted-foreground">Risk / 100</p>
            </div>
            <div className="space-y-100">
                {workspaceRisks.map((workspace, index) => (
                    <button className="grid w-full grid-cols-workspace items-center gap-300 rounded-lg px-200 py-300 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" key={workspace.name} onClick={() => { const room = estate.rooms.find((candidate) => candidate.name === workspace.name); if (room) onOpenWorkspace(room.id); }} type="button">
                        <span className="font-numeric text-200 text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
                        <span className="min-w-0">
                            <span className="block truncate text-300 font-semibold">{workspace.name}</span>
                            <span className="block text-100 text-muted-foreground">{workspace.domain} · {workspace.issues} issues</span>
                        </span>
                        <span className="h-200 overflow-hidden rounded-full bg-muted">
                            <span className={cn("block h-full rounded-full", workspace.status === "red" ? "bg-destructive" : workspace.status === "amber" ? "bg-warning" : "bg-success")} style={{ width: `${workspace.riskScore}%` }} />
                        </span>
                        <strong className="text-right font-numeric text-300">{workspace.riskScore}</strong>
                    </button>
                ))}
            </div>
        </section>
    );
}

function Overview({ onOpenDax, onOpenFindings, onOpenFinding, onOpenWorkspace }: { onOpenDax: () => void; onOpenFindings: () => void; onOpenFinding: (finding: ReviewFinding) => void; onOpenWorkspace: (workspaceId: string) => void }) {
    return (
        <>
            <MetricStrip />
            <OverviewPlots onOpenWorkspace={onOpenWorkspace} />
            <div className="grid border-b border-border lg:grid-cols-5">
                <div className="lg:col-span-2"><ScorePanel onOpenDax={onOpenDax} onOpenFindings={onOpenFindings} /></div>
                <div className="lg:col-span-3"><PriorityFindings onOpenFinding={onOpenFinding} /></div>
            </div>
            <div className="grid lg:grid-cols-2">
                <WorkspacePanel onOpenWorkspace={onOpenWorkspace} />
                <section className="border-t border-border bg-card p-500 lg:border-l">
                    <div className="mb-400 flex items-start justify-between gap-300">
                        <div>
                            <p className="section-kicker">Relationships</p>
                            <h2 className="section-title">Estate signal</h2>
                        </div>
                        <span className="inline-flex items-center gap-100 text-100 text-muted-foreground"><Activity className="icon-size-100" /> Live topology</span>
                    </div>
                    <Suspense fallback={<EstateMapFallback compact />}><EstateMap compact /></Suspense>
                </section>
            </div>
        </>
    );
}

function FindingsView({ initialWorkspaceId }: { initialWorkspaceId?: string }) {
    const { estate, findings } = useReviewData();
    const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId ?? "all");
    const workspaceGroups = estate.rooms
        .filter((room) => workspaceId === "all" || room.id === workspaceId)
        .map((room) => ({ room, findings: findings.filter((finding) => finding.workspaceIds.includes(room.id)) }))
        .filter((group) => group.findings.length > 0);
    const unassigned = workspaceId === "all" ? findings.filter((finding) => finding.workspaceIds.length === 0) : [];
    return (
        <div className="p-400 md:p-600">
            <div className="mb-500 flex flex-col gap-300 md:flex-row md:items-end md:justify-between">
                <div>
                    <p className="section-kicker">Triage workspace</p>
                    <h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Findings that change the plan</h2>
                </div>
                <WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={setWorkspaceId} />
            </div>
            <div className="space-y-400">
                {workspaceGroups.map(({ room, findings: workspaceFindings }) => <section className="overflow-hidden rounded-xl border border-border bg-card" key={room.id}><div className="flex items-center justify-between border-b border-border bg-secondary px-400 py-300"><div><h3 className="text-300 font-semibold">{room.name}</h3><p className="text-100 text-muted-foreground">{room.domain}</p></div><span className="font-numeric text-200 font-semibold">{workspaceFindings.length} flags</span></div><FindingRows findings={workspaceFindings} /></section>)}
                {unassigned.length > 0 && <section className="overflow-hidden rounded-xl border border-border bg-card"><div className="border-b border-border bg-secondary px-400 py-300"><h3 className="text-300 font-semibold">Tenant or capacity scope</h3></div><FindingRows findings={unassigned} /></section>}
                {workspaceGroups.length === 0 && unassigned.length === 0 && <p className="border border-border bg-card p-500 text-200 text-muted-foreground">No findings are linked to this workspace.</p>}
            </div>
        </div>
    );
}

function EstateView({ onOpenArea }: { onOpenArea: (area: EstateReviewArea, context: EstateReviewContext) => void }) {
    const { estate } = useReviewData();
    const items = estate.rooms.flatMap((room) => room.items);
    const estateSignals = [
        ["Workspaces at risk", estate.rooms.filter((room) => room.status === "risk").length.toString(), "Workspaces requiring immediate review"],
        ["Affected items", items.filter((item) => item.status !== "healthy").length.toString(), "Items carrying warning or risk signals"],
        ["Healthy items", items.filter((item) => item.status === "healthy").length.toString(), "Items with no current risk signal"],
    ];
    return (
        <div className="p-400 md:p-600">
            <div className="mb-500 flex items-end justify-between gap-300">
                <div>
                    <p className="section-kicker">Impact analysis</p>
                    <h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Explore the workspace skyline</h2>
                </div>
                <span className="hidden rounded-full border border-border bg-card px-300 py-200 text-200 text-muted-foreground md:inline-flex">{estate.rooms.length} workspaces · {estate.rooms.reduce((total, room) => total + room.items.length, 0)} items</span>
            </div>
            <Suspense fallback={<EstateMapFallback />}><EstateMap onOpenArea={onOpenArea} /></Suspense>
            <div className="mt-400 grid gap-300 md:grid-cols-3">
                {estateSignals.map(([label, value, detail]) => (
                    <article className="rounded-xl border border-border bg-card p-400" key={label}>
                        <p className="text-200 text-muted-foreground">{label}</p>
                        <p className="my-200 font-numeric text-hero-700 font-semibold">{value}</p>
                        <p className="text-100 text-muted-foreground">{detail}</p>
                    </article>
                ))}
            </div>
        </div>
    );
}

function SemanticModelOptimizationView({ initialContext }: { initialContext?: EstateReviewContext }) {
    const { estate, findings: reviewFindings } = useReviewData();
    const modelSelectId = useId();
    const [workspaceId, setWorkspaceId] = useState(initialContext?.workspaceId ?? "all");
    const [modelId, setModelId] = useState(initialContext?.itemId ?? "all");
    const allModels = estate.rooms.flatMap((room) => room.items
        .filter((item) => item.type === "model")
        .map((item) => ({ ...item, workspaceId: room.id, workspaceName: room.name, riskScore: room.riskScore })));
    const workspaceModels = allModels.filter((model) => workspaceId === "all" || model.workspaceId === workspaceId);
    const models = workspaceModels.filter((model) => modelId === "all" || model.id === modelId);
    const selectedModel = modelId === "all" ? null : workspaceModels.find((model) => model.id === modelId) ?? null;
    const directLake = models.filter((model) => model.modelProfile?.storageMode === "Direct Lake").length;
    const calculatedColumns = models.reduce((total, model) => total + (model.modelProfile?.calculatedColumns ?? 0), 0);
    const totalObjects = models.reduce((total, model) => total + (model.modelProfile?.tables ?? 0) + (model.modelProfile?.columns ?? 0), 0);
    const storageModeCounts = models.reduce<Record<string, number>>((counts, model) => {
        const mode = model.modelProfile?.storageMode || "Unknown";
        counts[mode] = (counts[mode] ?? 0) + 1;
        return counts;
    }, {});
    const storageModeData: LensBarDatum[] = Object.entries(storageModeCounts)
        .sort((left, right) => right[1] - left[1])
        .map(([mode, count]) => ({
            label: mode,
            value: count,
            detail: `${Math.round((count / Math.max(models.length, 1)) * 100)}% of models in scope`,
            tone: mode === "Direct Lake" ? "success" : mode === "Import" ? "info" : "warning",
        }));
    const optimizationFindings = findingsForWorkspace(reviewFindings.filter((finding) => finding.dimension === "Performance"), workspaceId)
        .filter((finding) => !selectedModel || selectedModel.findingIds.includes(finding.id));

    return (
        <div className="p-400 md:p-600">
            <div className="mb-500 flex flex-col gap-300 md:flex-row md:items-end md:justify-between">
                <div>
                    <p className="section-kicker">Developer lens</p>
                    <h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Optimize semantic models</h2>
                    <p className="mt-200 max-w-search text-200 leading-300 text-muted-foreground">VertiPaq footprint, storage mode, object complexity, calculated-column pressure and performance findings in one developer queue.</p>
                </div>
                <div className="flex flex-wrap gap-300">
                    <WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={(next) => { setWorkspaceId(next); setModelId("all"); }} />
                    <label className="flex items-center gap-200 text-200 font-semibold text-muted-foreground" htmlFor={modelSelectId}><span>Model</span><select className="min-w-0 rounded-lg border border-input bg-card px-300 py-200 text-200 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/30" id={modelSelectId} onChange={(event) => setModelId(event.target.value)} value={modelId}><option value="all">All semantic models</option>{workspaceModels.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
                </div>
            </div>

            <section className="grid border border-border bg-card sm:grid-cols-2 xl:grid-cols-4" aria-label="Semantic model optimization metrics">
                {[
                    ["Semantic models", models.length],
                    ["Direct Lake", directLake],
                    ["Calculated columns", calculatedColumns],
                    ["Model objects", totalObjects],
                ].map(([label, value], index) => <article className={cn("p-400", index > 0 && "border-t border-border sm:border-l sm:border-t-0", index === 2 && "sm:border-l-0 xl:border-l")} key={label}><p className="text-200 text-muted-foreground">{label}</p><p className="mt-200 font-numeric text-hero-700 font-semibold">{value}</p></article>)}
            </section>

            <div className="mt-400">
                <LensBarPlot kicker="Storage strategy" title="Semantic models by storage mode" description="A direct count of the selected models; mixed or unknown modes remain visible rather than being inferred as Direct Lake." data={storageModeData} />
            </div>

            <div className="mt-400 grid gap-400 xl:grid-cols-[minmax(0,1fr)_18rem]">
                <section className="overflow-hidden border border-border bg-card" aria-label="Semantic model engineering inventory">
                    <div className="grid grid-cols-[minmax(0,1fr)_6rem_5rem] gap-300 border-b border-border bg-secondary px-400 py-300 text-100 font-bold uppercase text-muted-foreground md:grid-cols-[minmax(0,1fr)_8rem_6rem_6rem_7rem]">
                        <span>Model / workspace</span><span>Storage</span><span>Size</span><span className="hidden md:block">Objects</span><span className="hidden md:block">Governance</span>
                    </div>
                    {models.map((model) => {
                        const flags = reviewFindings.filter((finding) => model.findingIds.includes(finding.id));
                        return <button aria-pressed={selectedModel?.id === model.id} className={cn("grid w-full grid-cols-[minmax(0,1fr)_6rem_5rem] gap-300 border-b border-border px-400 py-400 text-left transition-colors last:border-b-0 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_8rem_6rem_6rem_7rem]", selectedModel?.id === model.id && "border-l-4 border-l-primary bg-primary-soft")} key={model.id} onClick={() => setModelId(model.id)} type="button">
                            <div className="min-w-0"><p className="truncate text-300 font-semibold">{model.name}</p><p className="mt-100 truncate text-100 text-muted-foreground">{model.workspaceName} · {flags.length} open flag{flags.length === 1 ? "" : "s"}</p></div>
                            <span className="text-200 font-semibold">{model.modelProfile?.storageMode ?? "Unknown"}</span>
                            <span className="font-numeric text-200">{model.modelProfile?.totalSize ?? "—"}</span>
                            <span className="hidden font-numeric text-200 md:block">{(model.modelProfile?.tables ?? 0) + (model.modelProfile?.columns ?? 0)}</span>
                            <div className="hidden md:block"><span className={cn("inline-flex px-200 py-100 text-100 font-semibold", (model.modelProfile?.calculatedColumns ?? 0) > 5 ? "bg-warning-soft text-warning-strong" : "bg-success-soft text-success-strong")} title="More than five calculated columns is flagged for review">{model.modelProfile?.calculatedColumns ?? 0} calculated</span><p className="mt-100 text-100 text-muted-foreground">Risk {model.riskScore}</p></div>
                        </button>;
                    })}
                </section>
                <aside className="border border-border bg-agent-surface p-400 text-agent-foreground">
                    <p className="text-100 font-bold uppercase text-agent-positive">Optimization priority</p>
                    <h3 className="mt-200 font-heading text-500 font-semibold">Reduce expensive model structures first</h3>
                    <p className="mt-300 text-200 leading-300 text-agent-muted">Start with oversized columns, high-cardinality dictionaries, calculated columns and storage-mode fallbacks before tuning individual visuals.</p>
                    <div className="mt-400 space-y-300 border-t border-agent-border pt-400">
                        {optimizationFindings.length ? optimizationFindings.map((finding) => <div className="border-l-2 border-agent-accent pl-300" key={finding.id}><p className="font-monospace text-100 text-agent-positive">{finding.id} · {finding.severity}</p><p className="mt-100 text-200 font-semibold">{finding.title}</p></div>) : <p className="text-200 text-agent-muted">No performance flags are linked to this selection.</p>}
                    </div>
                </aside>
            </div>
            {selectedModel && <VertiPaqAnalyzer model={selectedModel} />}
        </div>
    );
}

function DaxAnalyzerView() {
    const { daxMeasures } = useReviewData();
    const capacitySelectId = useId();
    const modelSelectId = useId();
    const [capacityId, setCapacityId] = useState("all");
    const [modelId, setModelId] = useState("all");
    const capacities = [...new Map(daxMeasures.map((measure) => [measure.capacityId || measure.capacityName, {
        id: measure.capacityId || measure.capacityName,
        name: measure.capacityName,
    }])).values()].sort((left, right) => left.name.localeCompare(right.name));
    const capacityMeasures = daxMeasures.filter((measure) => capacityId === "all" || (measure.capacityId || measure.capacityName) === capacityId);
    const models = [...new Map(capacityMeasures.map((measure) => [measure.modelId, { id: measure.modelId, name: measure.modelName }])).values()]
        .sort((left, right) => left.name.localeCompare(right.name));
    const visibleMeasures = capacityMeasures
        .filter((measure) => modelId === "all" || measure.modelId === modelId)
        .sort((left, right) => right.riskScore - left.riskScore || left.measureName.localeCompare(right.measureName));
    const flagged = visibleMeasures.filter((measure) => measure.riskLevel === "high" || measure.riskLevel === "medium");
    const averageRisk = visibleMeasures.length
        ? Math.round(visibleMeasures.reduce((total, measure) => total + measure.riskScore, 0) / visibleMeasures.length)
        : 0;
    const distribution: LensBarDatum[] = (["high", "medium", "low", "none"] as const).map((level) => ({
        label: level === "none" ? "No pattern" : `${level[0].toUpperCase()}${level.slice(1)} risk`,
        value: visibleMeasures.filter((measure) => measure.riskLevel === level).length,
        detail: "Static metadata classification",
        tone: level === "high" ? "danger" : level === "medium" ? "warning" : level === "low" ? "info" : "success",
    }));
    const signalLabels: Record<string, string> = {
        crossjoin: "Cardinality expansion",
        iterator: "Iterator",
        materialization: "Materialization",
        nested_iterators: "Nested iterators",
        whole_table_filter: "Broad table filter",
    };
    const signalCounts = visibleMeasures.flatMap((measure) => measure.signalCodes.split(",").map((signal) => signal.trim()).filter(Boolean))
        .reduce<Record<string, number>>((counts, signal) => ({ ...counts, [signal]: (counts[signal] ?? 0) + 1 }), {});
    const signalData: LensBarDatum[] = Object.entries(signalCounts)
        .sort(([, left], [, right]) => right - left)
        .slice(0, 5)
        .map(([signal, count]) => ({
            label: signalLabels[signal] ?? signal.replaceAll("_", " "),
            value: count,
            detail: "Measures containing this syntax signal",
            tone: "warning",
        }));
    if (!signalData.length) signalData.push({ label: "No detected signals", value: 0, detail: "No static syntax signals in this selection", tone: "success" });

    return (
        <div className="p-400 md:p-600">
            <div className="mb-500 grid gap-400">
                <div className="min-w-0">
                    <p className="section-kicker">Semantic model developer lens</p>
                    <h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Review DAX risk patterns</h2>
                    <p className="mt-200 max-w-3xl text-200 leading-300 text-muted-foreground">Metadata-only analysis of potentially expensive iterators, cardinality expansion, broad filtering, materialization and expression complexity. No DAX is executed and these scores are not measured duration or cost.</p>
                </div>
                <div className="grid max-w-4xl gap-200 sm:grid-cols-2">
                    <label className="grid min-w-0 gap-100 text-200 font-semibold text-muted-foreground" htmlFor={capacitySelectId}><span>Capacity</span><select aria-label="Capacity" className="w-full min-w-0 rounded-lg border border-input bg-card px-300 py-200 text-200 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/30" id={capacitySelectId} onChange={(event) => { setCapacityId(event.target.value); setModelId("all"); }} value={capacityId}><option value="all">All capacities</option>{capacities.map((capacity) => <option key={capacity.id} value={capacity.id}>{capacity.name}</option>)}</select></label>
                    <label className="grid min-w-0 gap-100 text-200 font-semibold text-muted-foreground" htmlFor={modelSelectId}><span>Semantic model</span><select aria-label="Semantic model" className="w-full min-w-0 rounded-lg border border-input bg-card px-300 py-200 text-200 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/30" id={modelSelectId} onChange={(event) => setModelId(event.target.value)} value={modelId}><option value="all">All semantic models</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
                </div>
            </div>
            <section className="grid border border-border bg-card sm:grid-cols-2 xl:grid-cols-4" aria-label="DAX Analyzer metrics">
                {[["Measures", visibleMeasures.length], ["Flagged", flagged.length], ["High risk", visibleMeasures.filter((measure) => measure.riskLevel === "high").length], ["Average static risk", averageRisk]].map(([label, value], index) => <article className={cn("p-400", index > 0 && "border-t border-border sm:border-l sm:border-t-0", index === 2 && "sm:border-l-0 xl:border-l")} key={label}><p className="text-200 text-muted-foreground">{label}</p><p className="mt-200 font-numeric text-hero-700 font-semibold">{value}</p></article>)}
            </section>
            <div className="mt-400 grid gap-400 xl:grid-cols-2">
                <LensBarPlot kicker="Static classification" title="DAX measures by risk pattern" description="Counts reflect explainable syntax signals in semantic-model metadata, not runtime profiling." data={distribution} />
                <LensBarPlot kicker="Pattern frequency" title="Most common static signals" description="A measure can contribute to more than one signal; counts identify recurring review themes rather than execution impact." data={signalData} />
            </div>
            <section className="mt-400 overflow-hidden border border-border bg-card" aria-label="DAX measure risk details">
                <div className="grid grid-cols-[minmax(0,1fr)_6rem] gap-300 border-b border-border bg-secondary px-400 py-300 text-100 font-bold uppercase text-muted-foreground md:grid-cols-[minmax(0,1fr)_9rem_6rem_minmax(12rem,1fr)]"><span>Measure / model</span><span>Risk</span><span className="hidden md:block">Length</span><span className="hidden md:block">Static signals</span></div>
                {visibleMeasures.length ? visibleMeasures.map((measure) => <article className="grid grid-cols-[minmax(0,1fr)_6rem] gap-300 border-b border-border px-400 py-400 last:border-b-0 md:grid-cols-[minmax(0,1fr)_9rem_6rem_minmax(12rem,1fr)]" key={`${measure.modelId}:${measure.tableName}:${measure.measureName}`}><div className="min-w-0"><p className="text-300 font-semibold">{measure.measureName}</p><p className="mt-100 text-100 text-muted-foreground">{measure.modelName} · {measure.tableName} · {measure.workspaceName}</p><code className="mt-200 block overflow-hidden text-ellipsis whitespace-nowrap text-100 text-muted-foreground" title={measure.expressionPreview}>{measure.expressionPreview}</code></div><div><span className={cn("inline-flex px-200 py-100 text-100 font-bold uppercase", measure.riskLevel === "high" ? "bg-destructive-soft text-destructive" : measure.riskLevel === "medium" ? "bg-warning-soft text-warning-strong" : measure.riskLevel === "low" ? "bg-info-soft text-info-strong" : "bg-success-soft text-success-strong")}>{measure.riskLevel}</span><p className="mt-100 font-numeric text-100 text-muted-foreground">Score {measure.riskScore}</p></div><span className="hidden font-numeric text-200 md:block">{measure.expressionLength}</span><span className="hidden break-words text-200 text-muted-foreground md:block">{measure.signalCodes || "No risk pattern detected"}</span></article>) : <p className="p-500 text-200 text-muted-foreground">No DAX measure metadata is available for this selection.</p>}
            </section>
        </div>
    );
}

function VertiPaqAnalyzer({ model }: { model: ReturnType<typeof useReviewData>["estate"]["rooms"][number]["items"][number] & { workspaceName: string } }) {
    const [columnSort, setColumnSort] = useState<"size" | "cardinality">("size");
    const tables = [...(model.modelProfile?.tableStats ?? [])].sort((left, right) => right.totalSizeBytes - left.totalSizeBytes).slice(0, 10);
    const columns = [...(model.modelProfile?.columnStats ?? [])].sort((left, right) => columnSort === "size" ? right.totalSizeBytes - left.totalSizeBytes : right.cardinality - left.cardinality).slice(0, 10);
    const maxTableSize = Math.max(...tables.map((table) => table.totalSizeBytes), 1);
    const maxColumnSize = Math.max(...columns.map((column) => column.totalSizeBytes), 1);
    const capturedSize = tables.reduce((total, table) => total + table.totalSizeBytes, 0);
    const largestTableShare = capturedSize && tables[0] ? Math.round((tables[0].totalSizeBytes / capturedSize) * 100) : 0;
    return <section className="mt-500 overflow-hidden border border-border bg-card" aria-labelledby="vertipaq-title">
        <div className="border-b border-border bg-agent-surface p-400 text-agent-foreground md:p-500">
            <div className="flex flex-col gap-400 md:flex-row md:items-end md:justify-between">
                <div><p className="text-100 font-bold uppercase text-agent-positive">VertiPaq Analyzer</p><h3 className="mt-100 font-heading text-600 font-semibold" id="vertipaq-title">{model.name}</h3><p className="mt-100 text-200 text-agent-muted">{model.workspaceName} · {model.modelProfile?.storageMode ?? "Unknown"} storage</p></div>
                <p className="max-w-md text-200 leading-300 text-agent-muted">{tables.length ? `${tables[0].name} accounts for ${largestTableShare}% of captured table storage. Start with the largest objects before tuning individual measures.` : "Select a model with collected table and column statistics to inspect storage concentration."}</p>
            </div>
            <div className="mt-400 grid grid-cols-2 gap-px overflow-hidden bg-agent-border sm:grid-cols-4">
                {[["Model size", model.modelProfile?.totalSize ?? "—"], ["Tables", model.modelProfile?.tables ?? 0], ["Columns", model.modelProfile?.columns ?? 0], ["Calculated", model.modelProfile?.calculatedColumns ?? 0]].map(([label, value]) => <div className="bg-agent-highlight px-300 py-300" key={label}><p className="text-100 text-agent-muted">{label}</p><p className="mt-100 font-numeric text-400 font-semibold">{value}</p></div>)}
            </div>
        </div>
        <div className="grid xl:grid-cols-2">
            <section className="min-w-0 border-b border-border xl:border-b-0 xl:border-r" aria-labelledby="largest-tables-title">
                <div className="flex items-center gap-300 border-b border-border bg-secondary px-400 py-300"><TableProperties className="icon-size-300 text-primary-strong" aria-hidden="true" /><div><p className="section-kicker">Storage concentration</p><h4 className="section-title" id="largest-tables-title">Largest tables</h4></div></div>
                {tables.length ? <div className="overflow-x-auto"><table className="w-full min-w-[34rem] text-left text-200"><thead className="sticky top-0 z-10 bg-secondary text-100 uppercase text-muted-foreground"><tr><th className="px-300 py-200" scope="col">Table</th><th className="px-300 py-200" scope="col">Rows</th><th className="px-300 py-200" scope="col">Columns</th><th className="px-300 py-200" scope="col">Size</th><th className="px-300 py-200" scope="col">Dictionary</th></tr></thead><tbody className="divide-y divide-border">{tables.map((table) => <tr key={table.name}><td className="max-w-64 truncate px-300 py-300 font-semibold" title={table.name}>{table.name}</td><td className="px-300 py-300 font-numeric">{table.rows.toLocaleString()}</td><td className="px-300 py-300 font-numeric">{table.columns.toLocaleString()}</td><td className="min-w-32 px-300 py-300"><span className="font-numeric font-semibold">{table.totalSize}</span><span className="mt-100 block h-100 overflow-hidden rounded-full bg-muted"><span className="block h-full rounded-full bg-primary" style={{ width: `${Math.max(4, (table.totalSizeBytes / maxTableSize) * 100)}%` }} /></span></td><td className="px-300 py-300 font-numeric text-muted-foreground">{table.dictionarySize}</td></tr>)}</tbody></table><p className="border-t border-border px-300 py-200 text-100 text-muted-foreground md:hidden">Scroll horizontally to compare all table metrics.</p></div> : <AnalyzerEmptyState />}
            </section>
            <section className="min-w-0" aria-labelledby="largest-columns-title">
                <div className="flex flex-wrap items-center gap-300 border-b border-border bg-secondary px-400 py-300"><Columns3 className="icon-size-300 text-primary-strong" aria-hidden="true" /><div><p className="section-kicker">Column pressure</p><h4 className="section-title" id="largest-columns-title">Largest columns</h4></div><div className="ml-auto inline-flex border border-border bg-card p-100" aria-label="Sort columns"><button aria-pressed={columnSort === "size"} className={cn("px-200 py-100 text-100 font-semibold", columnSort === "size" ? "bg-primary text-primary-foreground" : "text-muted-foreground")} onClick={() => setColumnSort("size")} type="button">Size</button><button aria-pressed={columnSort === "cardinality"} className={cn("px-200 py-100 text-100 font-semibold", columnSort === "cardinality" ? "bg-primary text-primary-foreground" : "text-muted-foreground")} onClick={() => setColumnSort("cardinality")} type="button">Cardinality</button></div></div>
                {columns.length ? <div className="overflow-x-auto"><table className="w-full min-w-[42rem] text-left text-200"><thead className="sticky top-0 z-10 bg-secondary text-100 uppercase text-muted-foreground"><tr><th className="px-300 py-200" scope="col">Column</th><th className="px-300 py-200" scope="col">Type</th><th className="px-300 py-200" scope="col">Encoding</th><th className="px-300 py-200" scope="col">Cardinality</th><th className="px-300 py-200" scope="col">Size</th></tr></thead><tbody className="divide-y divide-border">{columns.map((column) => <tr className={column.calculated ? "bg-warning-soft/50" : undefined} key={`${column.tableName}.${column.name}`}><td className="max-w-72 truncate px-300 py-300 font-semibold" title={`${column.tableName}[${column.name}]`}>{column.tableName}[{column.name}]{column.calculated && <span className="ml-200 bg-warning-soft px-200 py-100 text-100 text-warning-strong">Calculated</span>}</td><td className="px-300 py-300">{column.dataType || "—"}</td><td className="px-300 py-300">{column.encoding || "—"}</td><td className="px-300 py-300 font-numeric">{column.cardinality.toLocaleString()}</td><td className="min-w-32 px-300 py-300"><span className="font-numeric font-semibold">{column.totalSize}</span><span className="mt-100 block h-100 overflow-hidden rounded-full bg-muted"><span className="block h-full rounded-full bg-high-strong" style={{ width: `${Math.max(4, (column.totalSizeBytes / maxColumnSize) * 100)}%` }} /></span></td></tr>)}</tbody></table><p className="border-t border-border px-300 py-200 text-100 text-muted-foreground md:hidden">Scroll horizontally to compare type, encoding, cardinality, and size.</p></div> : <AnalyzerEmptyState />}
            </section>
        </div>
    </section>;
}

function AnalyzerEmptyState() {
    return <div className="p-500"><p className="text-300 font-semibold">No detailed statistics collected</p><p className="mt-100 text-200 leading-300 text-muted-foreground">Run the review with VertiPaq statistics enabled, then refresh this model to inspect storage concentration.</p></div>;
}

function GovernanceView({ initialWorkspaceId }: { initialWorkspaceId?: string }) {
    const { estate, findings: reviewFindings } = useReviewData();
    const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId ?? "all");
    const rooms = estate.rooms.filter((room) => workspaceId === "all" || room.id === workspaceId);
    const workspaceOwners = rooms.map((room) => ({
        room,
        owner: room.owner || room.items.find((item) => item.governance?.owner && !item.governance.owner.startsWith("Not "))?.governance?.owner || "Not recorded",
    }));
    const ownedWorkspaces = workspaceOwners.filter(({ owner }) => owner !== "Not recorded").length;
    const classifiedItems = rooms.flatMap((room) => room.items
        .filter((item) => item.governance && (item.governance.endorsement !== "none" || !["Not set", "Not recorded"].includes(item.governance.sensitivityLabel)))
        .map((item) => ({ ...item, workspaceName: room.name })));
    const governanceFindings = findingsForWorkspace(reviewFindings.filter((finding) => ["Governance", "Security"].includes(finding.dimension)), workspaceId);
    const governanceCoverage: LensBarDatum[] = [
        { label: "Owner recorded", value: ownedWorkspaces, detail: `${rooms.length - ownedWorkspaces} workspace${rooms.length - ownedWorkspaces === 1 ? "" : "s"} without a recorded owner`, tone: "success" },
        { label: "Classified artifacts", value: classifiedItems.length, detail: "Artifacts with endorsement or sensitivity metadata", tone: "info" },
        { label: "Open controls", value: governanceFindings.length, detail: "Governance and security findings in scope", tone: governanceFindings.length ? "danger" : "success" },
    ];
    return <div className="p-400 md:p-600">
        <div className="mb-500 flex flex-col gap-300 md:flex-row md:items-end md:justify-between"><div>
            <p className="section-kicker">Workspaces and ownership</p>
            <h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Govern ownership and trust</h2>
            <p className="mt-200 max-w-search text-200 leading-300 text-muted-foreground">Workspace accountability from scanner admin metadata, plus the governance and access findings that need an owner.</p>
        </div><WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={setWorkspaceId} /></div>
        <section className="grid border border-border bg-card sm:grid-cols-3" aria-label="Governance metrics">
            {[["Workspaces in scope", rooms.length], ["Owner recorded", ownedWorkspaces], ["Open governance controls", governanceFindings.length]].map(([label, value], index) => <article className={cn("p-400", index > 0 && "border-t border-border sm:border-l sm:border-t-0")} key={label}><p className="text-200 text-muted-foreground">{label}</p><p className="mt-200 font-numeric text-hero-700 font-semibold">{value}</p></article>)}
        </section>
        <div className="mt-400"><LensBarPlot kicker="Coverage" title="Ownership and governance signals" description="Counts are reported independently because workspace ownership, artifact classification, and open controls have different scopes." data={governanceCoverage} /></div>
        <div className="mt-400 grid gap-400 xl:grid-cols-2">
            <section className="border border-border bg-card p-400"><p className="section-kicker">Accountability</p><h3 className="section-title">Workspace ownership</h3><p className="mt-200 text-200 leading-300 text-muted-foreground">Owner is the workspace administrator selected from scanner metadata. “Not recorded” means no eligible admin was returned for that workspace.</p><div className="mt-300 divide-y divide-border">{workspaceOwners.map(({ room, owner }) => <article className="flex items-center gap-300 py-300" key={room.id}><span className="grid icon-size-500 shrink-0 place-items-center bg-primary-soft text-primary-strong"><UserRound className="icon-size-200" /></span><div className="min-w-0"><p className="truncate text-300 font-semibold">{room.name}</p><p className="truncate text-100 text-muted-foreground">{owner}</p></div></article>)}</div></section>
            <section className="border border-border bg-card p-400"><p className="section-kicker">Classification coverage</p><h3 className="section-title">Endorsement and sensitivity</h3>{classifiedItems.length ? <div className="mt-300 divide-y divide-border">{classifiedItems.map((item) => <article className="grid grid-cols-[minmax(0,1fr)_7rem] gap-300 py-300" key={item.id}><div className="min-w-0"><p className="truncate text-300 font-semibold">{item.name}</p><p className="truncate text-100 text-muted-foreground">{item.workspaceName}</p></div><div className="text-right"><p className="text-100 font-semibold capitalize">{item.governance?.endorsement}</p><p className="mt-100 truncate text-100 text-muted-foreground">{item.governance?.sensitivityLabel}</p></div></article>)}</div> : <div className="mt-300 border border-dashed border-border bg-secondary p-400"><Tags className="icon-size-300 text-muted-foreground" /><p className="mt-300 text-300 font-semibold">Classification is not collected</p><p className="mt-100 text-200 leading-300 text-muted-foreground">The current gold model records workspace admins and governance findings, but it has no artifact endorsement or sensitivity-label columns. No classification percentage is inferred.</p></div>}</section>
            <section className="border border-border bg-card p-400"><p className="section-kicker">Control queue</p><h3 className="section-title">Governance and access findings</h3><div className="mt-300 divide-y divide-border">{governanceFindings.map((finding) => <article className="py-300" key={finding.id}><p className="font-monospace text-100 font-bold text-destructive">{finding.id} · {finding.severity}</p><p className="mt-100 text-300 font-semibold">{finding.title}</p><p className="mt-100 text-200 text-muted-foreground">{finding.recommendation}</p></article>)}</div></section>
        </div>
    </div>;
}

function EfficiencyView({ initialWorkspaceId }: { initialWorkspaceId?: string }) {
    const { dimensionScores, estate, findings: reviewFindings } = useReviewData();
    const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId ?? "all");
    const findings = findingsForWorkspace(reviewFindings.filter((finding) => ["Performance", "Cost"].includes(finding.dimension)), workspaceId);
    const performanceScore = dimensionScores.find((score) => score.dimension === "Performance")?.score ?? 0;
    const costScore = dimensionScores.find((score) => score.dimension === "Cost")?.score ?? 0;
    const efficiencyScores: LensBarDatum[] = [
        { label: "Performance", value: performanceScore, detail: "Query, refresh, and capacity pressure", tone: performanceScore >= 80 ? "success" : performanceScore >= 60 ? "warning" : "danger" },
        { label: "Cost", value: costScore, detail: "Spend, utilization, and consolidation", tone: costScore >= 80 ? "success" : costScore >= 60 ? "warning" : "danger" },
    ];
    return <div className="p-400 md:p-600">
        <div className="mb-500 flex flex-col gap-300 md:flex-row md:items-end md:justify-between"><div><p className="section-kicker">Capacity and query speed</p><h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Balance speed and capacity efficiency</h2><p className="mt-200 max-w-search text-200 leading-300 text-muted-foreground">One engineering workspace for throttling, refresh duration, storage-mode fallback, idle windows and capacity concentration.</p></div><WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={setWorkspaceId} /></div>
        <section className="grid gap-300 md:grid-cols-3">{[["Performance score", performanceScore.toString(), "Query, refresh and capacity pressure"], ["Cost score", costScore.toString(), "Spend, utilization and consolidation"], ["Combined actions", findings.length.toString(), "Ranked by user impact and avoidable cost"]].map(([label, value, detail]) => <article className="border border-border bg-card p-400" key={label}><Scale className="icon-size-300 text-primary-strong" /><p className="mt-300 text-200 text-muted-foreground">{label}</p><p className="mt-100 font-numeric text-hero-700 font-semibold">{value}</p><p className="mt-200 text-100 text-muted-foreground">{detail}</p></article>)}</section>
        <div className="mt-400"><LensBarPlot kicker="Review score" title="Performance and cost posture" description="Both review dimensions use the same zero-to-100 scale, making relative headroom visible without mixing in action counts." data={efficiencyScores} maximum={100} /></div>
        <section className="mt-400 border border-border bg-card p-400"><p className="section-kicker">Joint decision queue</p><div className="mt-300 divide-y divide-border">{findings.map((finding) => <article className="grid gap-300 py-400 md:grid-cols-[8rem_minmax(0,1fr)_minmax(0,1fr)]" key={finding.id}><p className="font-monospace text-100 font-bold text-primary-strong">{finding.dimension} · {finding.id}</p><div><h3 className="text-300 font-semibold">{finding.title}</h3><p className="mt-100 text-100 text-muted-foreground">{finding.affected}</p></div><p className="text-200 text-muted-foreground">{finding.recommendation}</p></article>)}</div></section>
    </div>;
}

function ArchitectureView({ initialWorkspaceId }: { initialWorkspaceId?: string }) {
    const { estate, findings: reviewFindings } = useReviewData();
    const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId ?? "all");
    const rooms = estate.rooms.filter((room) => workspaceId === "all" || room.id === workspaceId);
    const itemCounts = rooms.reduce<Record<string, number>>((counts, room) => {
        room.items.forEach((item) => { counts[item.type] = (counts[item.type] ?? 0) + 1; });
        return counts;
    }, {});
    const findings = findingsForWorkspace(reviewFindings.filter((finding) => finding.dimension === "Architecture"), workspaceId);
    const artifactComposition: LensBarDatum[] = Object.entries(itemCounts)
        .sort((left, right) => right[1] - left[1])
        .map(([type, count]) => ({ label: `${type[0].toUpperCase()}${type.slice(1)}`, value: count, tone: type === "model" ? "info" : type === "notebook" ? "warning" : "primary" }));
    return <div className="p-400 md:p-600">
        <div className="mb-500 flex flex-col gap-300 md:flex-row md:items-end md:justify-between"><div><p className="section-kicker">Modelling and design checks</p><h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Understand design and lineage</h2><p className="mt-200 max-w-search text-200 leading-300 text-muted-foreground">Workspace boundaries, artifact composition, lineage paths and architectural findings derived from the same estate graph as the campus.</p></div><WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={setWorkspaceId} /></div>
        <section className="grid border border-border bg-card sm:grid-cols-3 lg:grid-cols-6" aria-label="Architecture inventory">{Object.entries(itemCounts).map(([type, count], index) => <article className={cn("p-400", index > 0 && "border-t border-border sm:border-l sm:border-t-0")} key={type}><p className="text-100 font-semibold capitalize text-muted-foreground">{type}</p><p className="mt-200 font-numeric text-hero-700 font-semibold">{count}</p></article>)}</section>
        <div className="mt-400"><LensBarPlot kicker="Artifact mix" title="Estate composition" description="Artifact counts expose whether the selected architecture is concentrated around storage, processing, semantic serving, or reports." data={artifactComposition} /></div>
        <div className="mt-400 grid gap-400 xl:grid-cols-[minmax(0,1fr)_20rem]"><section className="border border-border bg-card p-400"><p className="section-kicker">Workspace topology</p><div className="mt-300 divide-y divide-border">{rooms.map((room) => <article className="flex items-center gap-300 py-300" key={room.id}><BuildingIcon /><div className="min-w-0 flex-1"><p className="truncate text-300 font-semibold">{room.name}</p><p className="text-100 text-muted-foreground">{room.items.length} artifacts · {room.domain}</p></div><p className="font-numeric text-200">Risk {room.riskScore}</p></article>)}</div></section><aside className="border border-border bg-agent-surface p-400 text-agent-foreground"><p className="text-100 font-bold uppercase text-agent-positive">Architecture flags</p>{findings.length ? findings.map((finding) => <div className="mt-400 border-l-2 border-agent-accent pl-300" key={finding.id}><p className="font-monospace text-100 text-agent-positive">{finding.id}</p><p className="mt-100 text-200 font-semibold">{finding.title}</p><p className="mt-200 text-100 text-agent-muted">{finding.recommendation}</p></div>) : <p className="mt-300 text-200 text-agent-muted">No architecture flags in this workspace.</p>}</aside></div>
    </div>;
}

function BuildingIcon() {
    return <span className="grid icon-size-600 shrink-0 place-items-center bg-primary-soft text-primary-strong"><Boxes className="icon-size-300" /></span>;
}

function NotebooksView({ initialWorkspaceId }: { initialWorkspaceId?: string }) {
    const { estate, findings: reviewFindings } = useReviewData();
    const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId ?? "all");
    const notebooks = estate.rooms.filter((room) => workspaceId === "all" || room.id === workspaceId).flatMap((room) => room.items.filter((item) => item.type === "notebook").map((item) => ({ ...item, workspaceName: room.name })));
    const notebookFindings = findingsForWorkspace(reviewFindings.filter((finding) => finding.dimension === "Notebook"), workspaceId);
    const notebookSmells = notebooks.flatMap((notebook) => notebook.notebookProfile?.smells ?? []);
    const smellSeverityData: LensBarDatum[] = (["critical", "high", "medium", "low"] as const).map((severity) => ({
        label: `${severity[0].toUpperCase()}${severity.slice(1)}`,
        value: notebookSmells.filter((smell) => smell.severity === severity).length,
        detail: "Distinct smell rules reported at this severity",
        tone: severity === "critical" ? "danger" : severity === "high" || severity === "medium" ? "warning" : "info",
    }));
    return <div className="p-400 md:p-600">
        <div className="mb-500 flex flex-col gap-300 md:flex-row md:items-end md:justify-between"><div><p className="section-kicker">Spark code anti-patterns</p><h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Improve notebook engineering</h2><p className="mt-200 max-w-search text-200 leading-300 text-muted-foreground">Notebook smells, affected cells, severity and direct Fabric links without mixing notebook remediation into estate governance.</p></div><WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={setWorkspaceId} /></div>
        <div className="mb-400"><LensBarPlot kicker="Code health" title="Notebook smells by severity" description="Counts come from collected rule matches; affected-cell totals are shown in each notebook rather than estimated here." data={smellSeverityData} /></div>
        <section className="space-y-400">{notebooks.length ? notebooks.map((notebook) => <article className="border border-border bg-card" key={notebook.id}><div className="flex flex-wrap items-center gap-300 border-b border-border bg-secondary px-400 py-300"><div className="min-w-0 flex-1"><h3 className="truncate text-300 font-semibold">{notebook.name}</h3><p className="truncate text-100 text-muted-foreground">{notebook.workspaceName}</p></div><span className="bg-warning-soft px-300 py-100 font-numeric text-100 font-bold text-warning-strong">{notebook.notebookProfile?.smellCount ?? 0} smells</span>{notebook.notebookProfile?.notebookUrl && <a className="inline-flex items-center gap-100 text-100 font-semibold text-primary-strong underline" href={notebook.notebookProfile.notebookUrl} rel="noreferrer" target="_blank">Open notebook<ExternalLink className="icon-size-100" /></a>}</div>{notebook.notebookProfile?.smells.length ? <div className="divide-y divide-border">{notebook.notebookProfile.smells.map((smell) => <section className="grid gap-300 p-400 md:grid-cols-[7rem_minmax(0,1fr)_9rem]" key={`${notebook.id}-${smell.ruleId}`}><div><p className="font-monospace text-100 font-bold text-warning-strong">{smell.ruleId}</p><p className="mt-100 text-100 font-semibold uppercase text-muted-foreground">{smell.severity}</p></div><div><p className="text-300 font-semibold">{smell.description}</p><p className="mt-100 text-100 text-muted-foreground">Pattern-based match; review the listed cells before changing code.</p></div><div><p className="text-100 font-bold uppercase text-muted-foreground">Affected cells</p><p className="mt-100 font-numeric text-300 font-semibold">{smell.affectedCells || "Not recorded"}</p></div></section>)}</div> : <p className="p-400 text-200 text-muted-foreground">No code smells were recorded for this notebook in the latest review.</p>}</article>) : <div className="border border-dashed border-border bg-secondary p-500"><p className="text-300 font-semibold">No notebooks in this workspace</p><p className="mt-100 text-200 text-muted-foreground">Select another workspace or rerun collection if notebooks were recently added.</p></div>}</section>
        <section className="mt-400 border border-border bg-card p-400"><p className="section-kicker">Notebook flags</p>{notebookFindings.length ? notebookFindings.map((finding) => <article className="mt-300 border-l-2 border-warning pl-300" key={finding.id}><p className="font-monospace text-100 font-bold text-warning-strong">{finding.id} · {finding.severity}</p><h3 className="mt-100 text-300 font-semibold">{finding.title}</h3><p className="mt-100 text-200 text-muted-foreground">{finding.recommendation}</p></article>) : <p className="mt-300 text-200 text-muted-foreground">No notebook flags in this workspace.</p>}</section>
    </div>;
}

function SelectionContext({ context, onClear }: { context: EstateReviewContext; onClear: () => void }) {
    const { estate } = useReviewData();
    const room = estate.rooms.find((candidate) => candidate.id === context.workspaceId);
    const item = room?.items.find((candidate) => candidate.id === context.itemId);
    return <div className="flex items-center gap-300 border-b border-primary/30 bg-primary-soft px-400 py-300 text-200 text-primary-strong md:px-500"><Focus className="icon-size-200" /><p className="min-w-0 flex-1 truncate"><strong>Estate selection:</strong> {room?.name ?? context.workspaceId}{item ? ` / ${item.name}` : ""}</p><button className="font-semibold underline" onClick={onClear} type="button">Show all</button></div>;
}

function ReviewWorkbenchContent() {
    const [currentView, setCurrentView] = useState<ViewId>("overview");
    const [reviewContext, setReviewContext] = useState<EstateReviewContext | null>(null);
    const [mobileNavOpen, setMobileNavOpen] = useState(false);
    const [agentOpen, setAgentOpen] = useState(false);
    const { estate, source } = useReviewData();
    const { isDark, toggleTheme } = useThemeContext();

    const navigate = (view: ViewId) => {
        setCurrentView(view);
        setReviewContext(null);
        setMobileNavOpen(false);
    };
    const openEstateArea = (view: EstateReviewArea, context: EstateReviewContext) => {
        setCurrentView(view);
        setReviewContext(context);
    };
    const openFindings = (workspaceId?: string) => {
        setCurrentView("findings");
        setReviewContext(workspaceId ? { workspaceId } : null);
    };

    return (
        <div className="flex h-full overflow-hidden bg-background font-base text-foreground">
            <Sidebar currentView={currentView} onNavigate={navigate} />
            <div className="flex min-w-0 flex-1 flex-col">
                <header className="flex min-h-header items-center gap-300 border-b border-border bg-card px-400 md:px-500">
                    <button className="icon-button lg:hidden" aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"} onClick={() => setMobileNavOpen((open) => !open)} type="button">
                        {mobileNavOpen ? <X className="icon-size-200" /> : <Menu className="icon-size-200" />}
                    </button>
                    <div className="lg:hidden"><BrandMark /></div>
                    <div className="hidden min-w-0 flex-1 md:block"><p className="text-100 font-semibold uppercase text-muted-foreground">Architecture review workspace</p><p className="truncate text-200 font-medium">{estate.name}</p></div>
                    <div className="ml-auto flex items-center gap-200">
                        <span className="hidden items-center gap-200 rounded-full bg-warning-soft px-300 py-100 text-100 font-semibold text-warning-strong sm:inline-flex">
                            <Gauge className="icon-size-100" aria-hidden="true" /> {source === "live" ? "Live review" : "Design preview"}
                        </span>
                        <button className="icon-button" aria-label="Toggle color theme" onClick={toggleTheme} type="button">
                            {isDark ? <Sun className="icon-size-200" /> : <Moon className="icon-size-200" />}
                        </button>
                    </div>
                </header>

                {mobileNavOpen && (
                    <nav className="border-b border-border bg-card p-300 lg:hidden" aria-label="Mobile navigation">
                        <div className="grid grid-cols-2 gap-200 sm:grid-cols-4">
                            {navItems.map((item) => {
                                const Icon = item.icon;
                                return <button className={cn("flex flex-col items-center gap-100 rounded-lg px-200 py-300 text-100 font-semibold", currentView === item.id ? "bg-primary-soft text-primary-strong" : "text-muted-foreground")} key={item.id} onClick={() => navigate(item.id)} type="button"><Icon className="icon-size-200" />{item.label}</button>;
                            })}
                        </div>
                    </nav>
                )}

                <div className="grid min-h-0 flex-1 xl:grid-cols-main-agent">
                    <main className="min-w-0 overflow-y-auto">
                        <div className="flex items-end justify-between gap-300 border-b border-border bg-background px-400 py-400 md:px-500">
                            <div>
                                <p className="section-kicker">{source === "live" ? `${estate.name} · ${estate.region}` : "Anonymous sample · Design preview"}</p>
                                <h1 className="font-heading text-hero-700 font-semibold leading-hero-700 md:text-hero-800 md:leading-hero-800">
                                    {viewTitles[currentView]}
                                </h1>
                            </div>
                            <div className="hidden items-center gap-200 text-200 text-muted-foreground md:flex"><Check className="icon-size-200 text-success-strong" /> {estate.rooms.length} workspaces</div>
                        </div>
                        {reviewContext && <SelectionContext context={reviewContext} onClear={() => setReviewContext(null)} />}
                        {currentView === "overview" && <Overview onOpenDax={() => navigate("dax")} onOpenFindings={() => openFindings()} onOpenFinding={(finding) => openFindings(finding.workspaceIds[0])} onOpenWorkspace={openFindings} />}
                        {currentView === "findings" && <FindingsView initialWorkspaceId={reviewContext?.workspaceId} />}
                        {currentView === "estate" && <EstateView onOpenArea={openEstateArea} />}
                        {currentView === "governance" && <GovernanceView initialWorkspaceId={reviewContext?.workspaceId} />}
                        {currentView === "models" && <SemanticModelOptimizationView initialContext={reviewContext ?? undefined} />}
                        {currentView === "dax" && <DaxAnalyzerView />}
                        {currentView === "efficiency" && <EfficiencyView initialWorkspaceId={reviewContext?.workspaceId} />}
                        {currentView === "architecture" && <ArchitectureView initialWorkspaceId={reviewContext?.workspaceId} />}
                        {currentView === "notebooks" && <NotebooksView initialWorkspaceId={reviewContext?.workspaceId} />}
                    </main>
                    <div className="hidden min-h-0 xl:block"><AgentPanel /></div>
                </div>
            </div>

            <button className="fixed bottom-400 right-400 z-40 flex items-center gap-200 rounded-full bg-agent-accent px-400 py-300 font-semibold text-agent-accent-foreground shadow-xl xl:hidden" aria-label="Open review agent" onClick={() => setAgentOpen(true)} type="button"><SparklesIcon /><span className="text-200">Review agent</span></button>
            {agentOpen && (
                <div className="fixed inset-0 z-50 flex justify-end bg-overlay" role="dialog" aria-modal="true" aria-label="Review agent">
                    <button className="absolute inset-0 cursor-default" aria-label="Close review agent" onClick={() => setAgentOpen(false)} type="button" />
                    <div className="relative h-full w-agent-drawer max-w-full shadow-2xl">
                        <button className="absolute right-300 top-300 z-10 grid icon-size-600 place-items-center rounded-lg border border-agent-border bg-agent-highlight" aria-label="Close review agent" onClick={() => setAgentOpen(false)} type="button"><X className="icon-size-200" /></button>
                        <AgentPanel />
                    </div>
                </div>
            )}
        </div>
    );
}

export function ReviewWorkbench({ data = previewReviewData }: { data?: ReviewData }) {
    return <ReviewDataProvider value={data}><ReviewWorkbenchContent /></ReviewDataProvider>;
}

function SparklesIcon() {
    return <Activity className="icon-size-300" aria-hidden="true" />;
}

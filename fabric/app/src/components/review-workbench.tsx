// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { lazy, Suspense, useState } from "react";
import { Activity, ArrowRight, ChevronRight, Check, FileWarning, Focus, Gauge, LayoutDashboard, ListChecks, Menu, Moon, Network, Server, Settings2, ShieldCheck, Sparkles, Sun, Radar, X } from "lucide-react";
import { AgentPanel } from "@/components/agent-panel";
import type { EstateReviewArea, EstateReviewContext } from "@/components/estate-map";
import { OverviewPlots } from "@/components/review-plots";
import { CapacitiesView, CoverageView, FindingsView, ItemsView, ModelSecurityView, TenantSettingsView } from "@/components/review-views";
import { BrandMark, DefinitionTooltip, PageIntro } from "@/components/workbench-parts";
import { ReviewDataProvider, useReviewData } from "@/hooks/review-data.context";
import { useThemeContext } from "@/hooks/theme.context";
import { activeArchitectureRules, architectureProfile, architectureProfileHash, previewReviewData, type FindingSeverity, type ReviewData, type ReviewFinding } from "@/lib/review-data";
import { severityClass } from "@/lib/status-style";
import { cn } from "@/lib/utils";

const EstateMap = lazy(() => import("@/components/estate-map").then((module) => ({ default: module.EstateMap })));

function EstateMapFallback({ compact = false }: { compact?: boolean }) {
    return <div className={cn("campus-explorer grid place-items-center bg-muted text-200 text-muted-foreground", compact && "campus-explorer-compact")}>Loading estate topology…</div>;
}

type ViewId = "overview" | "findings" | "estate" | "items" | "capacities" | "model-security" | "tenant-settings" | "coverage";

const navItems = [
    { id: "overview" as const, label: "Overview", icon: LayoutDashboard },
    { id: "findings" as const, label: "Findings", icon: FileWarning },
    { id: "estate" as const, label: "Estate map", icon: Network },
    { id: "items" as const, label: "Items to review", icon: ListChecks },
    { id: "capacities" as const, label: "Capacities", icon: Server },
    { id: "model-security" as const, label: "Model security", icon: ShieldCheck },
    { id: "tenant-settings" as const, label: "Tenant settings", icon: Settings2 },
    { id: "coverage" as const, label: "Source coverage", icon: Radar },
];

const viewTitles: Record<ViewId, string> = {
    overview: "Estate, prioritized",
    findings: "Risk decision queue",
    estate: "3D estate",
    items: "Items to review",
    capacities: "Capacities",
    "model-security": "Model security",
    "tenant-settings": "Tenant settings",
    coverage: "Source coverage",
};

const metricDefinitions: Record<string, string> = {
    "Review score": "Pass percentage among the controls that produced a scored result in the latest review. Unknown and missing-evidence controls are not treated as passes, and signals never lower it.",
    "Critical + high": "Failed controls classified as critical or high severity in the latest review.",
    "Workspaces at risk": "Workspaces whose confirmed failures give a risk score of 60 or more.",
    "Assessment coverage": "Share of the review's controls that produced a definitive result. Unknown and missing-evidence controls reduce it.",
};

const dimensionDefinitions: Record<string, string> = {
    Architecture: "Workspace boundaries, item types, storage modes, hosting, lineage and design.",
    Governance: "Ownership, administrators, endorsement and workspace practice.",
    Operations: "Capacity health, monitoring, scheduling and operational practice.",
    Reliability: "Concentration on one capacity, failure handling and dependencies between environments.",
    Security: "Tenant switches, sharing, networking, access and item-level grants.",
};

const severityRank: Record<FindingSeverity, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

function Sidebar({ currentView, onNavigate }: { currentView: ViewId; onNavigate: (view: ViewId) => void }) {
    const { source, latestRunId } = useReviewData();
    return (
        <aside className="hidden w-sidebar shrink-0 flex-col border-r border-border bg-sidebar lg:flex">
            <div className="border-b border-border p-500"><BrandMark /></div>
            <nav className="flex flex-1 flex-col gap-100 p-300" aria-label="Primary navigation">
                <p className="px-300 pb-200 pt-300 text-100 font-semibold uppercase text-muted-foreground">Review</p>
                {navItems.map((item) => {
                    const Icon = item.icon;
                    const active = currentView === item.id;
                    return (
                        <button className={cn("flex items-center gap-300 rounded-lg px-300 py-300 text-left text-300 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", active ? "bg-primary-soft text-primary-strong" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
                            key={item.id} onClick={() => onNavigate(item.id)} type="button" aria-current={active ? "page" : undefined}>
                            <Icon className="icon-size-200" aria-hidden="true" />
                            {item.label}
                            {active && <span className="ml-auto icon-size-100 rounded-full bg-primary" />}
                        </button>
                    );
                })}
                <div className="mt-auto rounded-xl border border-border bg-card p-300">
                    <div className="mb-200 flex items-center gap-200 text-200 font-semibold">
                        <ShieldCheck className="icon-size-200 text-success-strong" aria-hidden="true" />
                        {source === "live" ? "Connected assessment" : "Design preview"}
                    </div>
                    <p className="text-100 leading-200 text-muted-foreground">{source === "live" ? "Latest review data from the connected Fabric semantic model." : "No review is loaded. Connect a model for live data."}</p>
                    {latestRunId && <p className="mt-200 break-all font-monospace text-100 text-muted-foreground">Run {latestRunId.slice(0, 8)}</p>}
                </div>
            </nav>
            <div className="border-t border-border p-400 text-100 text-muted-foreground">{architectureProfile} · {source === "live" ? "Live" : "Preview"}</div>
        </aside>
    );
}

function MetricStrip() {
    const { metrics } = useReviewData();
    if (!metrics.length) return null;
    return (
        <section className="grid grid-cols-2 border-y border-border bg-card xl:grid-cols-4" aria-label="Review metrics">
            {metrics.map((metric, index) => (
                <article className={cn("relative p-400 md:p-500", index % 2 !== 0 && "border-l border-border", index > 1 && "border-t border-border xl:border-t-0", index === 2 && "xl:border-l")} key={metric.label}>
                    <div className="mb-300 flex items-center gap-100">
                        <p className="text-200 font-medium text-muted-foreground">{metric.label}</p>
                        <DefinitionTooltip align={index === metrics.length - 1 ? "right" : "left"} definition={metricDefinitions[metric.label] ?? "Latest value calculated from the connected review."} label={metric.label} />
                    </div>
                    <div className="flex items-baseline gap-200">
                        <strong className="font-numeric text-hero-800 leading-hero-800">{metric.value}</strong>
                        {metric.label === "Review score" && /^\d/.test(metric.value) && <span className="text-300 text-muted-foreground">/ 100</span>}
                    </div>
                    <p className="mt-100 text-100 text-muted-foreground">{metric.delta}</p>
                </article>
            ))}
        </section>
    );
}

function ScorePanel({ onOpenFindings }: { onOpenFindings: () => void }) {
    const { dimensionScores, score } = useReviewData();
    return (
        <section className="border-b border-border bg-card p-500 lg:border-b-0 lg:border-r">
            <div className="mb-500 flex items-start justify-between gap-300">
                <div>
                    <p className="section-kicker">Control posture</p>
                    <h2 className="section-title">Dimension pulse</h2>
                    <p className="mt-100 text-200 text-muted-foreground">{score == null ? "No assessable controls" : `Overall ${score} / 100`}</p>
                </div>
                <button className="icon-button" aria-label="Open findings" onClick={onOpenFindings} type="button"><ArrowRight className="icon-size-200" aria-hidden="true" /></button>
            </div>
            <div className="space-y-400">
                {dimensionScores.map((item) => (
                    <div key={item.dimension}>
                        <div className="mb-200 flex items-center justify-between gap-300 text-200">
                            <span className="flex items-center gap-100 font-medium">{item.dimension}<DefinitionTooltip definition={`${dimensionDefinitions[item.dimension] ?? ""} The score is the pass percentage among scored controls; unknown and missing-evidence controls are excluded.`} label={item.dimension} /></span>
                            <span className="flex items-center gap-200 font-numeric">
                                {item.score == null ? <span className="text-muted-foreground">Not scored</span> : <strong>{item.score}</strong>}
                                {item.failed > 0 && <span className="text-destructive">{item.failed} failed</span>}
                            </span>
                        </div>
                        <div className="h-200 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-score" style={{ width: `${item.score ?? 0}%` }} /></div>
                        <p className="mt-100 text-100 text-muted-foreground">{item.assessed} of {item.total} checks assessed</p>
                    </div>
                ))}
                {dimensionScores.length === 0 && <p className="text-200 text-muted-foreground">No dimension was assessed.</p>}
            </div>
        </section>
    );
}

function PriorityFindings({ onOpenFinding }: { onOpenFinding: (finding: ReviewFinding, single: boolean) => void }) {
    const { findings } = useReviewData();
    const failures = findings.filter((f) => f.status === "fail" && f.scored);
    const failed = Object.values(failures.reduce<Record<string, ReviewFinding[]>>((groups, f) => ({ ...groups, [f.id]: [...(groups[f.id] ?? []), f] }), {}))
        .map((group) => ({ finding: group[0], places: group.map((f) => f.workspaceName || "Tenant-wide") }))
        .sort((a, b) => severityRank[a.finding.severity] - severityRank[b.finding.severity] || a.finding.id.localeCompare(b.finding.id));
    return (
        <section className="bg-card p-500">
            <div className="mb-400 flex items-start justify-between gap-300">
                <div><p className="section-kicker">Decision queue</p><h2 className="section-title">Priority findings</h2></div>
                <span className="rounded-full bg-destructive-soft px-300 py-100 text-100 font-semibold text-destructive">{failures.length} failed in {failed.length} controls</span>
            </div>
            <div className="divide-y divide-border">
                {failed.slice(0, 6).map(({ finding, places }) => (
                    <button className="group grid w-full gap-300 py-400 text-left md:grid-cols-finding focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" key={finding.id} onClick={() => onOpenFinding(finding, places.length === 1)} type="button">
                        <div className="flex items-start gap-300">
                            <span className={cn("h-fit shrink-0 whitespace-nowrap rounded-md px-200 py-100 font-monospace text-100 font-semibold", severityClass[finding.severity])}>{finding.id}</span>
                            <span>
                                <span className="block text-300 font-semibold leading-300 group-hover:text-primary-strong">{finding.title}</span>
                                <span className="mt-100 block text-200 text-muted-foreground">{places.length === 1 ? places[0] : `${places.length} places`} · {finding.dimension}</span>
                            </span>
                        </div>
                        <p className="text-200 leading-300 text-muted-foreground">{finding.nextAction || finding.recommendation}</p>
                        <ChevronRight className="hidden icon-size-200 self-center justify-self-end text-muted-foreground md:block" aria-hidden="true" />
                    </button>
                ))}
                {failed.length === 0 && <p className="py-400 text-200 text-muted-foreground">No scored control failed in this review.</p>}
            </div>
        </section>
    );
}

function WorkspacePanel({ onOpenWorkspace }: { onOpenWorkspace: (workspaceId: string) => void }) {
    const { estate, workspaceRisks } = useReviewData();
    const ranked = [...workspaceRisks].sort((a, b) => b.riskScore - a.riskScore).slice(0, 8);
    return (
        <section className="border-t border-border bg-card p-500">
            <div className="mb-400 flex items-end justify-between gap-300">
                <div><p className="section-kicker">Concentration</p><h2 className="section-title">Workspace hotspots</h2></div>
                <p className="text-100 text-muted-foreground">Risk / 100</p>
            </div>
            <div className="space-y-100">
                {ranked.map((workspace, index) => (
                    <button className="grid w-full grid-cols-workspace items-center gap-300 rounded-lg px-200 py-300 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" key={workspace.name}
                        onClick={() => { const room = estate.rooms.find((candidate) => candidate.name === workspace.name); if (room) onOpenWorkspace(room.id); }} type="button">
                        <span className="font-numeric text-200 text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
                        <span className="min-w-0">
                            <span className="block truncate text-300 font-semibold">{workspace.name}</span>
                            <span className="block text-100 text-muted-foreground">{workspace.domain} · {workspace.issues} {workspace.issues === 1 ? "issue" : "issues"}</span>
                        </span>
                        <span className="h-200 overflow-hidden rounded-full bg-muted">
                            <span className={cn("block h-full rounded-full", workspace.status === "red" ? "bg-destructive" : workspace.status === "amber" ? "bg-warning" : "bg-success")} style={{ width: `${workspace.riskScore}%` }} />
                        </span>
                        <strong className="text-right font-numeric text-300">{workspace.riskScore}</strong>
                    </button>
                ))}
                {ranked.length === 0 && <p className="text-200 text-muted-foreground">No workspace has a risk score yet.</p>}
            </div>
        </section>
    );
}

function Overview({ onOpenFindings, onOpenFinding, onOpenWorkspace }: { onOpenFindings: () => void; onOpenFinding: (finding: ReviewFinding, single: boolean) => void; onOpenWorkspace: (workspaceId: string) => void }) {
    const { findings, estate } = useReviewData();
    return (
        <>
            <MetricStrip />
            {findings.length > 0 && <OverviewPlots onOpenWorkspace={onOpenWorkspace} />}
            <div className="grid border-b border-border lg:grid-cols-5">
                <div className="lg:col-span-2"><ScorePanel onOpenFindings={onOpenFindings} /></div>
                <div className="lg:col-span-3"><PriorityFindings onOpenFinding={onOpenFinding} /></div>
            </div>
            <div className="grid lg:grid-cols-2">
                <WorkspacePanel onOpenWorkspace={onOpenWorkspace} />
                <section className="border-t border-border bg-card p-500 lg:border-l">
                    <div className="mb-400 flex items-start justify-between gap-300">
                        <div><p className="section-kicker">Relationships</p><h2 className="section-title">Estate signal</h2></div>
                        <span className="inline-flex items-center gap-100 text-100 text-muted-foreground"><Activity className="icon-size-100" /> {estate.rooms.length} workspaces</span>
                    </div>
                    <Suspense fallback={<EstateMapFallback compact />}><EstateMap compact /></Suspense>
                </section>
            </div>
        </>
    );
}

function EstateView({ onOpenArea }: { onOpenArea: (area: EstateReviewArea, context: EstateReviewContext) => void }) {
    const { estate } = useReviewData();
    const items = estate.rooms.flatMap((room) => room.items);
    const signals = [
        ["Workspaces at risk", estate.rooms.filter((room) => room.status === "risk").length.toString(), "Workspaces with confirmed failures"],
        ["Items to review", items.filter((item) => item.status !== "healthy").length.toString(), "Items a finding points at"],
        ["Items with no flag", items.filter((item) => item.status === "healthy").length.toString(), "Items no finding names; this is not a clean bill of health"],
    ];
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Impact analysis" title="Explore the workspace skyline"
                aside={<span className="hidden rounded-full border border-border bg-card px-300 py-200 text-200 text-muted-foreground md:inline-flex">{estate.rooms.length} workspaces · {items.length} items</span>}>
                Each building is a workspace and each floor a type of item. Red buildings carry confirmed failures.
            </PageIntro>
            <Suspense fallback={<EstateMapFallback />}><EstateMap onOpenArea={onOpenArea} /></Suspense>
            <div className="mt-400 grid gap-300 md:grid-cols-3">
                {signals.map(([label, value, detail]) => (
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

function SelectionContext({ context, onClear }: { context: EstateReviewContext; onClear: () => void }) {
    const { estate } = useReviewData();
    const room = estate.rooms.find((candidate) => candidate.id === context.workspaceId);
    const item = room?.items.find((candidate) => candidate.id === context.itemId);
    return <div className="flex items-center gap-300 border-b border-primary/30 bg-primary-soft px-400 py-300 text-200 text-primary-strong md:px-500"><Focus className="icon-size-200" /><p className="min-w-0 flex-1 truncate"><strong>Estate selection:</strong> {room?.name ?? context.workspaceId}{item ? ` / ${item.name}` : ""}</p><button className="font-semibold underline" onClick={onClear} type="button">Show all</button></div>;
}

function CompatibilityNotice({ dataProfileHash }: { dataProfileHash?: string }) {
    return (
        <div className="m-400 border border-warning bg-warning-soft p-400 text-200 text-warning-strong md:m-500" role="alert">
            <p className="font-semibold">Incompatible profile. Collect a current architectural review; these results are not comparable.</p>
            <p className="mt-100">
                This app expects rule profile <code className="font-monospace">{architectureProfileHash.slice(0, 8)}</code>
                {dataProfileHash ? <>, but the newest run in the model was produced by <code className="font-monospace">{dataProfileHash.slice(0, 8)}</code></> : <>, and the model holds no run yet</>}.
                Run the pipeline again to produce a matching review.
            </p>
        </div>
    );
}

function ReviewWorkbenchContent({ compatible, dataProfileHash }: { compatible: boolean; dataProfileHash?: string }) {
    const [currentView, setCurrentView] = useState<ViewId>("overview");
    const [reviewContext, setReviewContext] = useState<EstateReviewContext | null>(null);
    const [mobileNavOpen, setMobileNavOpen] = useState(false);
    const [agentOpen, setAgentOpen] = useState(false);
    const { estate, source, latestRunId } = useReviewData();
    const { isDark, toggleTheme } = useThemeContext();

    const navigate = (view: ViewId) => { setCurrentView(view); setReviewContext(null); setMobileNavOpen(false); };
    const openEstateArea = (_area: EstateReviewArea, context: EstateReviewContext) => { setCurrentView("findings"); setReviewContext(context); };
    const openFindings = (workspaceId?: string) => { setCurrentView("findings"); setReviewContext(workspaceId ? { workspaceId } : null); };

    return (
        <div className="flex h-full overflow-hidden bg-background font-base text-foreground">
            <Sidebar currentView={currentView} onNavigate={navigate} />
            <div className="flex min-w-0 flex-1 flex-col">
                <header className="flex min-h-header items-center gap-300 border-b border-border bg-card px-400 md:px-500">
                    <button className="icon-button lg:hidden" aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"} onClick={() => setMobileNavOpen((open) => !open)} type="button">
                        {mobileNavOpen ? <X className="icon-size-200" /> : <Menu className="icon-size-200" />}
                    </button>
                    <div className="lg:hidden"><BrandMark /></div>
                    <div className="hidden min-w-0 flex-1 md:block"><p className="text-100 font-semibold uppercase text-muted-foreground">Fabric Architecture Review · architecture review</p><p className="truncate text-200 font-medium">{estate.name}</p></div>
                    <div className="ml-auto flex items-center gap-200">
                        <span className="hidden items-center gap-200 rounded-full bg-warning-soft px-300 py-100 text-100 font-semibold text-warning-strong sm:inline-flex">
                            <Gauge className="icon-size-100" aria-hidden="true" /> {source === "live" ? (latestRunId ? `Run ${latestRunId.slice(0, 8)}` : "No compatible run") : "Design preview"}
                        </span>
                        <button className="icon-button" aria-label="Toggle color theme" onClick={toggleTheme} type="button">{isDark ? <Sun className="icon-size-200" /> : <Moon className="icon-size-200" />}</button>
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
                                <p className="section-kicker">{source === "live" ? `${estate.name}${estate.region ? ` · ${estate.region}` : ""}` : "Design preview · not assessed"}</p>
                                <h1 className="font-heading text-hero-700 font-semibold leading-hero-700 md:text-hero-800 md:leading-hero-800">{viewTitles[currentView]}</h1>
                            </div>
                            <div className="hidden items-center gap-200 text-200 text-muted-foreground md:flex"><Check className="icon-size-200 text-success-strong" /> {estate.rooms.length} workspaces</div>
                        </div>
                        {!compatible && <CompatibilityNotice dataProfileHash={dataProfileHash} />}
                        {reviewContext && <SelectionContext context={reviewContext} onClear={() => setReviewContext(null)} />}
                        {currentView === "overview" && <Overview onOpenFindings={() => openFindings()} onOpenFinding={(finding, single) => openFindings(single ? finding.workspaceIds[0] : undefined)} onOpenWorkspace={openFindings} />}
                        {currentView === "findings" && <FindingsView key={reviewContext?.workspaceId ?? "all"} initialWorkspaceId={reviewContext?.workspaceId} />}
                        {currentView === "estate" && <EstateView onOpenArea={openEstateArea} />}
                        {currentView === "items" && <ItemsView />}
                        {currentView === "capacities" && <CapacitiesView />}
                        {currentView === "model-security" && <ModelSecurityView />}
                        {currentView === "tenant-settings" && <TenantSettingsView />}
                        {currentView === "coverage" && <CoverageView />}
                    </main>
                    <div className="hidden min-h-0 xl:block"><AgentPanel /></div>
                </div>
            </div>

            <button className="fixed bottom-400 right-400 z-40 flex items-center gap-200 rounded-full bg-agent-accent px-400 py-300 font-semibold text-agent-accent-foreground shadow-xl xl:hidden" aria-label="Open review agent" onClick={() => setAgentOpen(true)} type="button"><Sparkles className="icon-size-200" aria-hidden="true" /><span className="text-200">Review agent</span></button>
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
    const compatible = data.profileId === architectureProfile && data.profileHash === architectureProfileHash;
    // An incompatible payload is never shown: scores from different rule logic are not comparable.
    const shown: ReviewData = compatible ? { ...data, findings: data.findings.filter(f => activeArchitectureRules.has(f.id)), items: data.items.filter(i => activeArchitectureRules.has(i.ruleId)) } : { ...previewReviewData, source: data.source, latestRunId: data.latestRunId, profileId: undefined, profileHash: undefined };
    return <ReviewDataProvider value={shown}><ReviewWorkbenchContent compatible={compatible} dataProfileHash={data.dataProfileHash ?? data.profileHash} /></ReviewDataProvider>;
}

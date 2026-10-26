// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { useState } from "react";
import { PageIntro, SelectField, StatGrid, StatusBadge, WorkspaceSelect } from "@/components/workbench-parts";
import { useReviewData } from "@/hooks/review-data.context";
import { dimensionOrder, type ModelSecurity, type ReviewFinding, type RoleFilterKind } from "@/lib/review-data";
import { severityClass, statusOrder, statusRank, statusText } from "@/lib/status-style";
import { cn } from "@/lib/utils";

const tableHead = "border-b border-border bg-secondary px-300 py-300 text-left text-100 font-semibold uppercase text-muted-foreground";
const tableCell = "border-b border-border px-300 py-300 align-top text-200";

export function FindingsView({ initialWorkspaceId }: { initialWorkspaceId?: string }) {
    const { estate, findings: all, items } = useReviewData();
    const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId ?? "all");
    const [statusFilter, setStatusFilter] = useState("all");
    const [dimensionFilter, setDimensionFilter] = useState("all");
    const findings = all.filter(f => (workspaceId === "all" || f.workspaceIds.includes(workspaceId))
        && (dimensionFilter === "all" || f.dimension === dimensionFilter));
    const listed = findings.filter(f => statusFilter === "all" || (f.status || "unknown") === statusFilter)
        .sort((a, b) => statusRank(a.status) - statusRank(b.status) || a.id.localeCompare(b.id) || (a.workspaceName ?? "").localeCompare(b.workspaceName ?? ""));
    const itemsOf = (f: ReviewFinding) => items.filter(i => i.ruleId === f.id && f.workspaceIds.includes(i.workspaceId));
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Triage workspace" title="Every result, failures first"
                aside={<div className="flex flex-wrap items-center gap-300">
                    <WorkspaceSelect rooms={estate.rooms} value={workspaceId} onChange={setWorkspaceId} />
                    <SelectField label="Dimension" value={dimensionFilter} onChange={setDimensionFilter}>
                        <option value="all">All dimensions</option>
                        {[...dimensionOrder, "Source coverage"].map(d => <option key={d} value={d}>{d}</option>)}
                    </SelectField>
                    <SelectField label="Status" value={statusFilter} onChange={setStatusFilter}>
                        <option value="all">All ({findings.length})</option>
                        {statusOrder.filter(s => findings.some(f => (f.status || "unknown") === s)).map(s => <option key={s} value={s}>{statusText(s)} ({findings.filter(f => (f.status || "unknown") === s).length})</option>)}
                    </SelectField>
                </div>}>
                Failed controls come first, then gaps in evidence, then context. Signals and coverage never lower the score.
            </PageIntro>
            <div className="overflow-x-auto rounded-xl border border-border bg-card">
                <table className="w-full border-collapse">
                    <thead><tr><th className={tableHead}>Workspace</th><th className={tableHead}>Control</th><th className={tableHead}>Status</th><th className={tableHead}>Kind</th><th className={tableHead}>Evidence and action</th></tr></thead>
                    <tbody>{listed.map((f, i) => <tr key={`${f.id}-${f.workspaceIds.join("-")}-${i}`}>
                        <td className={tableCell}>{f.workspaceName || "Tenant-wide"}</td>
                        <td className={cn(tableCell, "whitespace-nowrap")}><span className={cn("rounded-md px-200 py-100 font-monospace text-100 font-semibold", severityClass[f.severity])}>{f.id}</span><p className="mt-100 text-100 text-muted-foreground">{f.dimension}</p></td>
                        <td className={tableCell}><StatusBadge status={f.status} /></td>
                        <td className={tableCell}>{f.disposition}</td>
                        <td className={tableCell}>
                            <strong className="text-300">{f.title}</strong>
                            <p className="mt-100 text-muted-foreground">{f.nextAction || f.recommendation}</p>
                            {(f.statusMeaning || f.whyItMatters || f.howToFix || f.configure) && <details className="mt-200"><summary className="cursor-pointer font-semibold text-primary-strong">What it means</summary>
                                {f.statusMeaning && <p className="mt-100">{f.statusMeaning}</p>}
                                {f.whyItMatters && <p className="mt-100"><b>Why it matters.</b> {f.whyItMatters}</p>}
                                {f.howToFix && <p className="mt-100"><b>How to fix.</b> {f.howToFix}</p>}
                                {f.configure && <p className="mt-100"><b>Configure.</b> {f.configure}</p>}</details>}
                            {itemsOf(f).length > 0 && <details className="mt-200"><summary className="cursor-pointer font-semibold text-primary-strong">{itemsOf(f).length} {itemsOf(f).length === 1 ? "item" : "items"} to look at</summary>
                                <ul className="mt-100 space-y-100">{itemsOf(f).map(item => <li key={item.itemId}>{item.itemName} · {item.itemType}{item.reason && <> — {item.reason}</>}<br /><small className="text-muted-foreground">{item.itemId}</small></li>)}</ul></details>}
                            <small className="mt-100 block break-all text-muted-foreground">{f.workspaceIds.join(", ")}</small>
                        </td>
                    </tr>)}</tbody>
                </table>
                {listed.length === 0 && <p className="p-500 text-200 text-muted-foreground">No findings match these filters.</p>}
            </div>
        </div>
    );
}

export function ItemsView() {
    const { findings, items, estate } = useReviewData();
    const [rule, setRule] = useState("all");
    const rules = [...new Set(items.map(i => i.ruleId))].sort();
    const titleOf = (id: string) => findings.find(f => f.id === id)?.title ?? "";
    const workspaceOf = (id: string) => estate.rooms.find(r => r.id === id)?.name ?? id;
    const shown = items.filter(i => rule === "all" || i.ruleId === rule);
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Where to look" title="The items behind the findings"
                aside={<SelectField label="Rule" value={rule} onChange={setRule}>
                    <option value="all">All rules ({items.length})</option>
                    {rules.map(r => <option key={r} value={r}>{r} ({items.filter(i => i.ruleId === r).length})</option>)}
                </SelectField>}>
                The pipelines, lakehouses, models and other items that a finding points at, by name and type. Only rules that record item identities are listed here.
            </PageIntro>
            <div className="overflow-x-auto rounded-xl border border-border bg-card">
                <table className="w-full table-fixed border-collapse">
                    <colgroup><col className="w-[18%]" /><col className="w-[16%]" /><col className="w-[28%]" /><col className="w-[38%]" /></colgroup>
                    <thead><tr><th className={tableHead}>Rule</th><th className={tableHead}>Workspace</th><th className={tableHead}>Item</th><th className={tableHead}>Why it is listed</th></tr></thead>
                    <tbody>{shown.map((i, n) => <tr key={`${i.ruleId}-${i.itemId}-${n}`}>
                        <td className={tableCell}><span className="font-monospace text-100 font-semibold">{i.ruleId}</span><p className="mt-100 text-100 text-muted-foreground">{titleOf(i.ruleId)}</p></td>
                        <td className={tableCell}>{workspaceOf(i.workspaceId)}</td>
                        <td className={cn(tableCell, "break-words")}><strong>{i.itemName}</strong><p className="text-100 text-muted-foreground">{i.itemType}</p><small className="break-all text-muted-foreground">{i.itemId}</small></td>
                        <td className={cn(tableCell, "break-words")}><span className="text-muted-foreground">{i.reason || "Named by the rule"}</span></td>
                    </tr>)}</tbody>
                </table>
                {shown.length === 0 && <p className="p-500 text-200 text-muted-foreground">No rule in this review recorded specific items.</p>}
            </div>
        </div>
    );
}

export function CapacitiesView() {
    const { capacities, findings, estate } = useReviewData();
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Hosting" title="Capacity health and ownership">
                One finding per capacity, shared by every workspace on it. Static hosting facts, not utilisation or cost.
            </PageIntro>
            <div className="space-y-400">
                {capacities.map(c => {
                    const own = findings.filter(f => f.workspaceName === `Capacity: ${c.name}`);
                    const hosted = estate.rooms.filter(r => r.capacityName === c.name);
                    return (
                        <section className="overflow-hidden rounded-xl border border-border bg-card" key={c.id} aria-label={`Capacity ${c.name}`}>
                            <div className="flex flex-wrap items-center justify-between gap-300 border-b border-border bg-secondary px-400 py-300">
                                <div><h3 className="text-300 font-semibold">{c.name}</h3><p className="text-100 text-muted-foreground">{c.sku} · {c.kind} · {c.region || "Region unknown"}</p></div>
                                <span className={cn("rounded-md px-200 py-100 text-100 font-semibold", c.state === "Active" ? "bg-success-soft text-success-strong" : "bg-warning-soft text-warning-strong")}>{c.state || "State unknown"}</span>
                            </div>
                            <dl className="grid gap-300 p-400 sm:grid-cols-4">
                                <div><dt className="text-100 text-muted-foreground">Workspaces</dt><dd className="font-numeric text-hero-700 font-semibold">{c.observedWorkspaceCount}{c.assignedWorkspaceCount != null && c.assignedWorkspaceCount !== c.observedWorkspaceCount ? ` / ${c.assignedWorkspaceCount}` : ""}</dd></div>
                                <div><dt className="text-100 text-muted-foreground">Items</dt><dd className="font-numeric text-hero-700 font-semibold">{c.observedItemCount}</dd></div>
                                <div><dt className="text-100 text-muted-foreground">Administrators</dt><dd className="font-numeric text-hero-700 font-semibold">{c.adminCount ?? "?"}</dd></div>
                                <div><dt className="text-100 text-muted-foreground">Missing tags</dt><dd className="text-200 font-semibold">{c.missingTags || "None"}</dd></div>
                            </dl>
                            {c.workspaceScopeLimited && <p className="border-t border-border px-400 py-300 text-200 text-warning-strong">This review covered only some workspaces, so counts for this capacity are partial.</p>}
                            {hosted.length > 0 && <p className="border-t border-border px-400 py-300 text-200 text-muted-foreground"><b className="text-foreground">Hosts:</b> {hosted.map(r => r.name).join(", ")}</p>}
                            <div className="divide-y divide-border border-t border-border">
                                {own.map(f => <article className="grid gap-200 px-400 py-300 md:grid-cols-[8rem_minmax(0,1fr)_auto]" key={f.id}>
                                    <span className="font-monospace text-100 font-semibold">{f.id}</span>
                                    <div><p className="text-300 font-semibold">{f.title}</p><p className="mt-100 text-200 text-muted-foreground">{f.nextAction || f.recommendation}</p></div>
                                    <div className="md:justify-self-end"><StatusBadge status={f.status} /></div>
                                </article>)}
                                {own.length === 0 && <p className="px-400 py-300 text-200 text-muted-foreground">No capacity finding was recorded.</p>}
                            </div>
                        </section>
                    );
                })}
                {capacities.length === 0 && <p className="border border-border bg-card p-500 text-200 text-muted-foreground">No capacity was recorded in this review.</p>}
            </div>
        </div>
    );
}

export function TenantSettingsView() {
    const { tenantSettings } = useReviewData();
    const linked = tenantSettings.filter(s => s.ruleId);
    const others = tenantSettings.filter(s => !s.ruleId);
    const failing = linked.filter(s => s.status === "fail");
    const row = (s: (typeof tenantSettings)[number]) => <tr key={s.name}>
        <td className={tableCell}><strong>{s.title || s.name}</strong><p className="text-100 text-muted-foreground">{s.group}</p></td>
        <td className={tableCell}><strong>{s.enabled == null ? "Unknown" : s.enabled ? "On" : "Off"}</strong><p className="text-100 text-muted-foreground">{s.posture}</p></td>
        <td className={cn(tableCell, "whitespace-nowrap font-monospace text-100")}>{s.ruleId || "—"}</td>
        <td className={tableCell}>{s.ruleId ? <StatusBadge status={s.status} /> : <span className="text-100 text-muted-foreground">not evaluated</span>}</td>
    </tr>;
    const head = <thead><tr><th className={tableHead}>Setting</th><th className={tableHead}>State</th><th className={tableHead}>Control</th><th className={tableHead}>Result</th></tr></thead>;
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Administrator controls" title="Settings, and the controls they feed">
                Every tenant setting recorded for the run and the control it feeds. A setting without a control is recorded for context and is not judged.
            </PageIntro>
            <StatGrid label="Tenant setting metrics" stats={[["Settings recorded", tenantSettings.length], ["Feeding a control", linked.length], ["Failing a control", failing.length]]} />
            <section className="mt-400 overflow-x-auto rounded-xl border border-border bg-card">
                <table className="w-full border-collapse">{head}<tbody>{[...linked].sort((a, b) => statusRank(a.status) - statusRank(b.status)).map(row)}</tbody></table>
                {linked.length === 0 && <p className="p-500 text-200 text-muted-foreground">No tenant setting feeds a control in this review.</p>}
            </section>
            {others.length > 0 && <details className="mt-400 rounded-xl border border-border bg-card">
                <summary className="cursor-pointer px-400 py-300 text-300 font-semibold">{others.length} other recorded settings</summary>
                <div className="overflow-x-auto"><table className="w-full border-collapse">{head}<tbody>{others.map(row)}</tbody></table></div>
            </details>}
        </div>
    );
}

export function CoverageView() {
    const { coverage } = useReviewData();
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Evidence" title="What the review is based on">
                Which evidence was collected, from which source, and how fresh it is. Missing or unknown evidence is never treated as a pass.
            </PageIntro>
            <div className="overflow-x-auto rounded-xl border border-border bg-card">
                <table className="w-full border-collapse">
                    <thead><tr><th className={tableHead}>Workspace</th><th className={tableHead}>Evidence family</th><th className={tableHead}>Status</th><th className={tableHead}>Source contract</th><th className={tableHead}>Observation and freshness</th></tr></thead>
                    <tbody>{coverage.map((c, i) => <tr key={i}>
                        <td className={tableCell}>{c.workspaceName || (c.workspaceId ? c.workspaceId : "Tenant-wide")}</td>
                        <td className={tableCell}>{c.family}</td>
                        <td className={tableCell}><StatusBadge status={c.status} /></td>
                        <td className={tableCell}>{c.source}<p className="text-100 text-muted-foreground">{c.notice}</p></td>
                        <td className={tableCell}>{c.observedAt}<br /><span className="text-100 text-muted-foreground">{c.freshness}</span></td>
                    </tr>)}</tbody>
                </table>
                {coverage.length === 0 && <p className="p-500 text-200 text-muted-foreground">No source coverage was recorded.</p>}
            </div>
        </div>
    );
}

const securityLabel = { present: "Configured", absent: "None configured", unknown: "Unknown" } as const;
const securityClass = {
    present: "bg-success-soft text-success-strong", absent: "bg-warning-soft text-warning-strong", unknown: "bg-muted text-muted-foreground",
} as const;
const filterLabel: Record<RoleFilterKind, string> = {
    none: "No row filter", constant: "Constant filter, restricts nothing", static: "Static filter",
    dynamic: "Dynamic filter, uses the user's identity", mixed: "Static and dynamic filters",
    unknown: "Filter not determined", not_applicable: "Not filtered by this role",
};
const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

// A model's roles, when role detail was collected. No roles listed is only "no roles" when the detail was collected.
function ModelRoles({ model }: { model: ModelSecurity }) {
    const note = (text: string) => <span className="text-100 text-muted-foreground">{text}</span>;
    if (model.roleDetail === "not_collected") return note("Role detail not collected");
    if (model.roleDetail === "unavailable") return note("Roles could not be read");
    if (!model.roles.length) return note(model.roleDetail === "partial" ? "Roles could not be fully read" : "No roles defined");
    return (
        <details>
            <summary className="cursor-pointer text-200">{plural(model.roles.length, "role")}{model.roleDetail === "partial" && " (not all could be read)"}</summary>
            <ul className="mt-100 space-y-200">{model.roles.map(role => <li key={role.name} className="text-100">
                <strong className="break-words">{role.name}</strong> <span className="text-muted-foreground">{role.permission}</span>
                <p className="text-muted-foreground">{filterLabel[role.filterKind]}
                    {role.readsData && ` · ${plural(role.rowFilterTables, "table")} with a row filter · ${plural(role.hiddenTables, "hidden table")} · ${plural(role.hiddenColumns, "hidden column")}`}</p>
            </li>)}</ul>
        </details>
    );
}

export function ModelSecurityView() {
    const { modelSecurity, modelSecurityAvailable } = useReviewData();
    const [capacity, setCapacity] = useState("all");
    const [focus, setFocus] = useState("all");
    const capacityOf = (m: (typeof modelSecurity)[number]) => m.capacityName || "No capacity";
    const capacityNames = [...new Set(modelSecurity.map(capacityOf))].sort();
    const neither = (m: (typeof modelSecurity)[number]) => m.rls === "absent" && m.ols === "absent";
    const gap = (m: (typeof modelSecurity)[number]) => m.rlsOutcome === "fail" || m.olsOutcome === "fail";
    const notInspected = (m: (typeof modelSecurity)[number]) => m.inspection !== "complete";
    const scoped = modelSecurity.filter(m => capacity === "all" || capacityOf(m) === capacity);
    const shown = scoped.filter(m => focus === "all" || (focus === "gaps" && gap(m)) || (focus === "neither" && neither(m)) || (focus === "unverified" && notInspected(m)))
        .sort((a, b) => Number(gap(b)) - Number(gap(a)) || capacityOf(a).localeCompare(capacityOf(b)) || a.modelName.localeCompare(b.modelName));
    const rollup = capacityNames.map(name => {
        const rows = modelSecurity.filter(m => capacityOf(m) === name);
        return { name, models: rows.length, rls: rows.filter(m => m.rls === "present").length, ols: rows.filter(m => m.ols === "present").length,
            neither: rows.filter(neither).length, notInspected: rows.filter(notInspected).length, gaps: rows.filter(gap).length };
    });
    const state = (value: keyof typeof securityLabel) => <span className={cn("inline-flex whitespace-nowrap rounded-md px-200 py-100 text-100 font-semibold", securityClass[value])}>{securityLabel[value]}</span>;
    return (
        <div className="p-400 md:p-600">
            <PageIntro kicker="Access design" title="Row-level and object-level security by model"
                aside={modelSecurityAvailable && <div className="flex flex-wrap items-center gap-300">
                    <SelectField label="Capacity" value={capacity} onChange={setCapacity}>
                        <option value="all">All capacities</option>
                        {capacityNames.map(name => <option key={name} value={name}>{name}</option>)}
                    </SelectField>
                    <SelectField label="Show" value={focus} onChange={setFocus}>
                        <option value="all">All models ({scoped.length})</option>
                        <option value="gaps">Declared requirement not met ({scoped.filter(gap).length})</option>
                        <option value="neither">Neither configured ({scoped.filter(neither).length})</option>
                        <option value="unverified">Not fully inspected ({scoped.filter(notInspected).length})</option>
                    </SelectField>
                </div>}>
                Read from each model's definition. Only the models you declare as requiring row-level or object-level security can fail. A model with neither can be intended, and unknown means it could not be inspected completely, never that it has no security. A configured filter is not proof that access is correct, and role membership is not checked.
            </PageIntro>
            {!modelSecurityAvailable ? <p className="border border-border bg-card p-500 text-200 text-muted-foreground">This review has no model-security data. Redeploy the review model and run a new review to see it.</p> : <>
                <StatGrid label="Model security metrics" stats={[
                    ["Models inspected", modelSecurity.length], ["With row-level security", modelSecurity.filter(m => m.rls === "present").length],
                    ["With object-level security", modelSecurity.filter(m => m.ols === "present").length], ["Neither configured", modelSecurity.filter(neither).length],
                    ["Not fully inspected", modelSecurity.filter(notInspected).length], ["Declared requirement not met", modelSecurity.filter(gap).length]]} />
                <section className="mt-400 overflow-x-auto rounded-xl border border-border bg-card" aria-label="Security by capacity">
                    <table className="w-full border-collapse">
                        <thead><tr><th className={tableHead}>Capacity</th><th className={tableHead}>Models</th><th className={tableHead}>Row-level</th><th className={tableHead}>Object-level</th><th className={tableHead}>Neither</th><th className={tableHead}>Not fully inspected</th><th className={tableHead}>Requirement not met</th></tr></thead>
                        <tbody>{rollup.map(r => <tr key={r.name}><td className={tableCell}><strong>{r.name}</strong></td><td className={tableCell}>{r.models}</td><td className={tableCell}>{r.rls}</td><td className={tableCell}>{r.ols}</td><td className={tableCell}>{r.neither}</td><td className={tableCell}>{r.notInspected}</td><td className={tableCell}>{r.gaps}</td></tr>)}</tbody>
                    </table>
                </section>
                <div className="mt-400 overflow-x-auto rounded-xl border border-border bg-card">
                    <table className="w-full border-collapse">
                        <thead><tr><th className={tableHead}>Model</th><th className={tableHead}>Capacity</th><th className={tableHead}>Row-level</th><th className={tableHead}>Object-level</th><th className={tableHead}>Inspection</th><th className={tableHead}>Roles</th></tr></thead>
                        <tbody>{shown.map(m => <tr key={`${m.workspaceId}-${m.modelId}`}>
                            <td className={cn(tableCell, "break-words")}><strong>{m.modelName}</strong><p className="text-100 text-muted-foreground">{m.workspaceName}</p><small className="break-all text-muted-foreground">{m.modelId}</small></td>
                            <td className={tableCell}>{capacityOf(m)}</td>
                            <td className={tableCell}>{state(m.rls)}{m.rlsRequired && <p className="mt-100 flex items-center gap-200 text-100 text-muted-foreground">Required <StatusBadge status={m.rlsOutcome} /></p>}</td>
                            <td className={tableCell}>{state(m.ols)}{m.olsRequired && <p className="mt-100 flex items-center gap-200 text-100 text-muted-foreground">Required <StatusBadge status={m.olsOutcome} /></p>}</td>
                            <td className={tableCell}>{m.inspection}{m.inspectionReason && <p className="text-100 text-muted-foreground">{m.inspectionReason}</p>}</td>
                            <td className={tableCell}><ModelRoles model={m} /></td>
                        </tr>)}</tbody>
                    </table>
                    {shown.length === 0 && <p className="p-500 text-200 text-muted-foreground">No model matches these filters.</p>}
                </div>
            </>}
        </div>
    );
}
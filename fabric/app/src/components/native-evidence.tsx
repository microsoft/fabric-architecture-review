// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { useState } from "react";
import { useReviewData } from "@/hooks/review-data.context";
import { useNativeEvidence } from "@/hooks/use-native-evidence";
import {
    hasCoverage, itemScopeKey, latestExecutionObservations,
    type EvidenceCoverage, type EvidenceDetail, type EvidenceResult, type EvidenceScope,
} from "@/lib/native-evidence";
import { nativeEvidencePreview } from "@/lib/native-evidence-preview";
import type { EvidenceLens } from "@/queries/live/native-evidence";
import { cn } from "@/lib/utils";

const labels: Record<EvidenceLens, string> = {
    executions: "Observed executions", dataflows: "Dataflow Gen2", daxObjects: "Calculated DAX objects",
};
const statusDescriptions: Record<string, string> = {
    collected: "History collected; only retained observations are available.",
    empty: "Collection succeeded with no retained executions. This is not a collection failure.",
    partial: "Only part of the requested evidence was collected. Missing evidence is not a pass.",
    forbidden: "The collector did not have permission to read this evidence.",
    not_found: "The requested artifact or history endpoint was not found.",
    error: "Collection failed. Execution outcome is unknown.",
    not_collected: "Collection was not performed. Execution outcome is unknown.",
    invalid_data: "The returned evidence could not be validated.",
    inspected: "Static definition inspected. This does not validate folding or runtime performance.",
    parse_error: "The definition could not be parsed completely.",
    unavailable: "Definition evidence is unavailable; absence of signals is not a clean result.",
    unsupported: "The definition format is not supported by this inspection.",
    inventory_unavailable: "Workspace inventory is unavailable. This row is a gap, not a Dataflow artifact.",
    complete: "Typed static definition coverage is complete, not runtime validation.",
};

function utc(value: string | null) { return value ? `${value.replace("T", " ").replace(".000Z", "").replace(/Z$/, "")} UTC` : "Unavailable"; }

function Select({ label, value, options, onChange }: {
    label: string; value: string; options: { id: string; name: string }[]; onChange: (value: string) => void;
}) {
    return <label className="flex min-w-0 flex-col gap-100 text-100 font-semibold">{label}
        <select aria-label={label} className="max-w-full rounded-lg border border-border bg-card px-300 py-200 text-200" value={value} onChange={(event) => onChange(event.target.value)}>
            <option value="all">{label === "Capacity" ? "All capacities" : label === "Execution status" ? "All execution statuses" : `All ${label.toLowerCase()}s`}</option>
            {options.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
        </select>
    </label>;
}

function options(rows: EvidenceScope[], field: "capacity" | "workspace" | "item") {
    const unique = new Map<string, string>();
    for (const row of rows) {
        if (field === "item" && !row.itemId) continue;
        const id = field === "item" ? itemScopeKey(row) : field === "workspace" ? row.workspaceId : row.capacityId || "unassigned";
        const name = field === "item" ? `${row.itemName || row.itemId} (${row.workspaceName || row.workspaceId})` : field === "workspace" ? row.workspaceName || row.workspaceId : row.capacityName || "Unassigned capacity";
        unique.set(id, name);
    }
    return [...unique].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

function QueryState({ result, label, retry }: { result: EvidenceResult<unknown>; label: string; retry?: () => void }) {
    if (result.status === "ready") return null;
    if (result.status === "loading") return <p role="status" className="p-400 text-muted-foreground">Loading {label}...</p>;
    return <div role="alert" className="border border-destructive bg-destructive-soft p-400 text-200">
        <p className="font-semibold">{label} unavailable</p><p>{result.message}</p>
        <p className="mt-200">Check the error details, app/model compatibility and the signed-in identity's access. No clean result can be inferred.</p>
        {retry && <button className="mt-300 rounded-lg border border-border bg-card px-300 py-200" onClick={retry} type="button">Retry evidence queries</button>}
    </div>;
}

function CoverageRow({ row }: { row: EvidenceCoverage }) {
    const complete = ["collected", "empty", "inspected", "complete"].includes(row.status);
    return <article className="border-t border-border py-300">
        <div className="flex flex-wrap items-center justify-between gap-200">
            <h4 className="text-300 font-semibold">{row.itemId ? row.itemName || row.itemId : "Workspace inventory gap"}</h4>
            <span className={cn("px-200 py-100 text-100 font-semibold", complete ? "bg-info-soft text-info-strong" : "bg-warning-soft text-warning-strong")}>{row.status}</span>
        </div>
        <p className="mt-100 text-100 text-muted-foreground">{row.workspaceName || row.workspaceId} · {row.itemType} · {row.capacityName || "Unassigned capacity"}</p>
        <p className="mt-200 text-200">{statusDescriptions[row.status]}</p>
        <p className="mt-100 text-100 text-muted-foreground">{row.count} collected {row.flaggedCount === null ? "execution observations" : `definitions · ${row.flaggedCount} flagged`}{row.historyScope && ` · ${row.historyScope}`}</p>
        {row.source && <p className="mt-100 text-100 text-muted-foreground">{row.source} · Retained start window: {utc(row.oldestStart)} to {utc(row.newestStart)}</p>}
        {row.notice && <p className="mt-200 text-200 text-warning-strong">{row.notice}</p>}
        <details className="mt-200 break-words text-100 text-muted-foreground"><summary className="cursor-pointer">Review and scope identifiers</summary>
            <p>Run: {row.runId} · {utc(row.runTimestamp)}</p><p>Workspace: {row.workspaceId}</p>
            {row.itemId && <p>Item: {row.itemId}</p>}<p>Review item: {row.reviewItemKey}</p>
        </details>
    </article>;
}

function DetailRow({ row }: { row: EvidenceDetail }) {
    return <article className="border-t border-border py-300">
        <h4 className="text-300 font-semibold">{row.kind === "execution" ? row.itemName : row.kind === "query" ? row.queryName : row.objectName}</h4>
        <p className="mt-100 text-100 text-muted-foreground">{row.workspaceName || row.workspaceId} · {row.itemName || row.itemId} · {row.capacityName || "Unassigned capacity"}</p>
        {row.kind === "execution" ? <>
            <p className="mt-200 text-200 font-semibold">{row.status} · {row.itemType} · {row.executionType || "Type unavailable"}</p>
            <dl className="mt-200 grid gap-100 text-200 sm:grid-cols-2">
                <div><dt className="text-muted-foreground">Start (UTC)</dt><dd>{utc(row.start)}</dd></div>
                <div><dt className="text-muted-foreground">End (UTC)</dt><dd>{utc(row.end)}</dd></div>
                <div><dt className="text-muted-foreground">Observed duration</dt><dd>{row.durationMs === null ? "Unavailable" : `${row.durationMs.toLocaleString()} ms`}</dd></div>
                <div><dt className="text-muted-foreground">Source</dt><dd className="break-words">{row.source}</dd></div>
            </dl>
        </> : <>
            {row.kind === "daxObject" && <p className="mt-200 text-200">{row.objectType} · Table: {row.tableName || "Not recorded"} · {row.riskLevel} static risk · Score {row.riskScore} (rank {row.riskRank}) · {row.expressionLength} characters</p>}
            <p className="mt-200 break-words text-200">{row.signals.length ? row.signals.join(" · ") : "No syntax signal detected in the inspected definition; not a runtime pass."}</p>
            {row.kind === "query" && <><p className="mt-100 text-100 text-muted-foreground">{row.signalCount} syntax signals</p><p className="mt-200 text-200">{row.recommendation}</p>{row.notice && <p className="mt-100 text-100 text-muted-foreground">{row.notice}</p>}</>}
            {row.kind === "daxObject" && <details className="mt-200 text-100"><summary className="cursor-pointer">Signal metadata</summary><pre className="whitespace-pre-wrap break-words">{row.signalDetails || "No signal details recorded."}</pre></details>}
        </>}
        <details className="mt-200 break-words text-100 text-muted-foreground"><summary className="cursor-pointer">Observation and scope identifiers</summary>
            <p>Run: {row.runId} · {utc(row.runTimestamp)}</p><p>Workspace: {row.workspaceId} · Item: {row.itemId}</p><p>Review item: {row.reviewItemKey}</p>
            {row.kind === "execution" && <p>Execution: {row.executionId || "Not recorded"} · Key: {row.executionKey}</p>}
        </details>
    </article>;
}

export function EvidencePanel({ lens, history, coverage, detail, retry }: {
    lens: EvidenceLens; history: boolean; coverage: EvidenceResult<EvidenceCoverage>; detail: EvidenceResult<EvidenceDetail>; retry?: () => void;
}) {
    const [capacityId, setCapacityId] = useState("all");
    const [workspaceId, setWorkspaceId] = useState("all");
    const [itemId, setItemId] = useState("all");
    const [status, setStatus] = useState("all");
    const [visibleCount, setVisibleCount] = useState(50);
    const coverageRows = coverage.status === "ready" ? coverage.rows : [];
    const detailRows = detail.status === "ready" ? detail.rows : [];
    const allRows = [...coverageRows, ...detailRows];
    const inCapacity = (row: EvidenceScope) => capacityId === "all" || (row.capacityId || "unassigned") === capacityId;
    const inWorkspace = (row: EvidenceScope) => inCapacity(row) && (workspaceId === "all" || row.workspaceId === workspaceId);
    const inScope = (row: EvidenceScope) => inWorkspace(row) && (itemId === "all" || itemScopeKey(row) === itemId);
    const visibleCoverage = coverageRows.filter((row) => inScope(row) || (!row.itemId && inWorkspace(row)));
    const observed = history && lens === "executions" ? latestExecutionObservations(detailRows) : detailRows;
    const scopedDetails = observed.filter(inScope);
    const orphaned = coverage.status === "ready" ? scopedDetails.filter((row) => !hasCoverage(row, coverageRows)) : [];
    const visibleDetails = scopedDetails.filter((row) => row.kind !== "execution" || status === "all" || row.status === status);
    const statuses = [...new Set(scopedDetails.flatMap((row) => row.kind === "execution" ? [row.status] : []))].sort();
    return <div className="space-y-400">
        <div className="grid gap-300 sm:grid-cols-2 xl:grid-cols-3">
            <Select label="Capacity" value={capacityId} options={options(allRows, "capacity")} onChange={(id) => { setCapacityId(id); setWorkspaceId("all"); setItemId("all"); setStatus("all"); setVisibleCount(50); }} />
            <Select label="Workspace" value={workspaceId} options={options(allRows.filter(inCapacity), "workspace")} onChange={(id) => { setWorkspaceId(id); setItemId("all"); setStatus("all"); setVisibleCount(50); }} />
            <Select label="Item" value={itemId} options={options(allRows.filter(inWorkspace), "item")} onChange={(id) => { setItemId(id); setStatus("all"); setVisibleCount(50); }} />
            {lens === "executions" && <Select label="Execution status" value={status} options={statuses.map((value) => ({ id: value, name: value }))} onChange={(value) => { setStatus(value); setVisibleCount(50); }} />}
        </div>
        <div className="grid gap-400 2xl:grid-cols-2">
            <section className="min-w-0 border border-border bg-card p-400" aria-label="Evidence coverage">
                <p className="section-kicker">Collection and definition coverage</p><h3 className="section-title">Coverage and gaps</h3>
                <p className="my-200 text-100 text-muted-foreground">{history ? "Per-review collection observations, not unique-execution totals." : "Coverage recorded by this FAR review."} Coverage is not filtered by execution status.</p>
                <QueryState result={coverage} label="Coverage query" retry={retry} />
                {coverage.status === "ready" && <><p className="mb-200 text-100">{visibleCoverage.filter((row) => row.itemId).length} artifact coverage rows · {visibleCoverage.filter((row) => !row.itemId).length} inventory gaps</p>
                    {visibleCoverage.length === 0 && <p className="py-300 text-200 text-warning-strong">No coverage rows in this scope. Evidence availability is unknown, not a successful empty collection.</p>}
                    {visibleCoverage.slice(0, visibleCount).map((row) => <CoverageRow row={row} key={row.reviewItemKey} />)}
                </>}
            </section>
            <section className="min-w-0 border border-border bg-card p-400" aria-label="Evidence details">
                <p className="section-kicker">{lens === "executions" ? "Observed execution evidence" : "Static syntax evidence"}</p>
                <h3 className="section-title">{labels[lens]}</h3>
                <QueryState result={detail} label="Detail query" retry={retry} />
                {coverage.status !== "ready" && detail.status === "ready" && <p role="alert" className="my-300 text-200 text-warning-strong">Detail observations are available, but coverage could not be verified.</p>}
                {orphaned.length > 0 && <p role="alert" className="my-300 text-200 text-warning-strong">{orphaned.length} detail rows have no matching review-item coverage. Evidence is incomplete; these are not clean results.</p>}
                {detail.status === "ready" && <><p className="my-200 text-100">{visibleDetails.length} {lens === "executions" ? "unique observed executions" : "static definitions"} in view</p>
                    {visibleDetails.length === 0 && <p className="py-300 text-200 text-muted-foreground">No detail observations match this scope. Consult coverage before interpreting absence.</p>}
                    {visibleDetails.slice(0, visibleCount).map((row, index) => <DetailRow key={`${row.reviewItemKey}-${index}`} row={row} />)}
                </>}
            </section>
        </div>
        {(visibleCoverage.length > visibleCount || visibleDetails.length > visibleCount) && <button className="rounded-lg border border-border bg-card px-400 py-200 text-200" onClick={() => setVisibleCount((count) => count + 50)} type="button">Show 50 more evidence rows</button>}
    </div>;
}

function LiveEvidencePanel({ lens, runId, history }: { lens: EvidenceLens; runId: string; history: boolean }) {
    const evidence = useNativeEvidence(lens, runId, history);
    return <EvidencePanel lens={lens} history={history} {...evidence} />;
}

export function NativeEvidenceView() {
    const { source, latestRunId } = useReviewData();
    const [lens, setLens] = useState<EvidenceLens>("executions");
    const [history, setHistory] = useState(false);
    const preview = nativeEvidencePreview[lens];
    return <div className="p-400 md:p-600">
        <div className="mb-400"><p className="section-kicker">Native evidence</p>
            <h2 className="font-heading text-hero-800 font-semibold leading-hero-800">Inspect observations, signals and gaps</h2>
            <p className="mt-200 text-200 text-muted-foreground">Observed refresh and job history is separate from static definition signals. Neither proves capacity cost or CU impact.</p>
        </div>
        <div className="mb-400 flex flex-wrap gap-200" role="group" aria-label="Evidence lens">
            {(Object.keys(labels) as EvidenceLens[]).map((value) => <button key={value} type="button" aria-pressed={lens === value} className={cn("rounded-lg border border-border px-300 py-200 text-200 font-semibold", lens === value ? "bg-primary-soft text-primary-strong" : "bg-card")} onClick={() => { setLens(value); setHistory(false); }}>{labels[value]}</button>)}
        </div>
        <div className="mb-400 border border-border bg-card p-400 text-200">
            <p className="break-words font-semibold">{source === "preview" ? "Synthetic sample evidence" : `FAR review run: ${latestRunId || "Unavailable"}`}</p>
            <p className="mt-200 text-muted-foreground">A review is one FAR pipeline run. This page shows evidence saved by FAR, not live monitoring of your workspaces.</p>
            {lens === "executions" ? <>
                <label className="mt-200 flex flex-col gap-100 text-100 font-semibold">Observation window
                    <select aria-label="Observation window" className="rounded-lg border border-border bg-card px-300 py-200 text-200" value={history ? "history" : "latest"} onChange={(event) => setHistory(event.target.value === "history")}>
                        <option value="latest">Collected in this FAR review</option><option value="history">Collected across reviews up to this one</option>
                    </select>
                </label>
                <p className="mt-200 text-muted-foreground">{history ? "Combines saved execution observations from this and earlier FAR reviews, showing each execution once using its latest observation." : "Shows executions returned by the native history APIs during this FAR review. Their execution dates can be earlier than the review itself."}</p>
                <p className="mt-200 text-muted-foreground">Bounded retained API observations are not a complete weekly log. History uses the latest observation of each execution key before status filters. Times are UTC; missing duration remains unavailable, not zero.</p>
                {history && <p className="mt-200 text-muted-foreground">Capacity assignment is taken from each observation's review, not today's assignment.</p>}
            </> : <p className="mt-200 text-muted-foreground">{lens === "dataflows" ? "Metadata-only M syntax signals (DFLOW-001) and coverage gaps (DFLOW-002). Buffering, stop-folding and native-query syntax do not prove folding behavior or runtime impact. M expressions are not shown." : "Calculated columns, calculated tables and calculation items only. No expressions are shown. Coverage is not runtime validation. DAX-001/002 measure counts and scoring remain separate in DAX Analyzer."}</p>}
        </div>
        {source === "preview" ? <EvidencePanel key={`${lens}-${history}`} lens={lens} history={history} coverage={{ status: "ready", rows: preview.coverage }} detail={{ status: "ready", rows: preview.detail }} />
            : latestRunId ? <LiveEvidencePanel key={`${lens}-${history}-${latestRunId}`} lens={lens} history={history} runId={latestRunId} />
                : <p role="alert" className="border border-warning bg-warning-soft p-400 text-200">Review run ID is unavailable. Native evidence cannot be joined safely to this review.</p>}
    </div>;
}

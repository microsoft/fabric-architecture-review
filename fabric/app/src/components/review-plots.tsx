//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { useReviewData } from "@/hooks/review-data.context";
import type { FindingSeverity, ReviewFinding, TenantEstate, WorkspaceRisk } from "@/lib/review-data";

export interface LensBarDatum {
    label: string;
    value: number;
    detail?: string;
    tone?: "primary" | "success" | "warning" | "danger" | "info";
}

const barToneClass: Record<NonNullable<LensBarDatum["tone"]>, string> = {
    primary: "bg-primary",
    success: "bg-success-strong",
    warning: "bg-warning",
    danger: "bg-destructive",
    info: "bg-info-strong",
};

export function LensBarPlot({ kicker, title, description, data, maximum }: {
    kicker: string;
    title: string;
    description: string;
    data: LensBarDatum[];
    maximum?: number;
}) {
    const scaleMaximum = Math.max(maximum ?? 0, ...data.map((datum) => datum.value), 1);

    return (
        <figure className="border border-border bg-card p-400" aria-label={title}>
            <figcaption>
                <p className="section-kicker">{kicker}</p>
                <h3 className="section-title">{title}</h3>
                <p className="mt-200 text-200 leading-300 text-muted-foreground">{description}</p>
            </figcaption>
            <div className="mt-400 space-y-300">
                {data.map((datum) => {
                    const percentage = Math.min(100, Math.max(0, (datum.value / scaleMaximum) * 100));
                    return (
                        <div key={datum.label}>
                            <div className="mb-100 flex items-baseline justify-between gap-300">
                                <span className="text-200 font-semibold">{datum.label}</span>
                                <span className="font-numeric text-200 font-semibold">{datum.value.toLocaleString()}</span>
                            </div>
                            <div
                                aria-label={`${datum.label}: ${datum.value}${datum.detail ? `, ${datum.detail}` : ""}`}
                                aria-valuemax={scaleMaximum}
                                aria-valuemin={0}
                                aria-valuenow={datum.value}
                                className="h-200 overflow-hidden bg-muted"
                                role="meter"
                            >
                                <span className={`block h-full ${barToneClass[datum.tone ?? "primary"]}`} style={{ width: `${percentage}%` }} />
                            </div>
                            {datum.detail && <p className="mt-100 text-100 text-muted-foreground">{datum.detail}</p>}
                        </div>
                    );
                })}
            </div>
        </figure>
    );
}

const severitySeries: { severity: FindingSeverity; label: string; stroke: string; swatch: string }[] = [
    { severity: "critical", label: "Critical", stroke: "stroke-destructive", swatch: "bg-destructive" },
    { severity: "high", label: "High", stroke: "stroke-high-strong", swatch: "bg-high-strong" },
    { severity: "medium", label: "Medium", stroke: "stroke-warning", swatch: "bg-warning" },
    { severity: "low", label: "Low", stroke: "stroke-info-strong", swatch: "bg-info-strong" },
];

function SeverityDonut({ findings }: { findings: ReviewFinding[] }) {
    const total = findings.length;
    const counts = severitySeries.map((series) => findings.filter((finding) => finding.severity === series.severity).length);
    const segments = severitySeries.map((series, index) => {
        const count = counts[index];
        const percentage = total ? (count / total) * 100 : 0;
        const offset = total ? counts.slice(0, index).reduce((sum, value) => sum + value, 0) / total * 100 : 0;
        return { ...series, count, percentage, offset };
    });

    return (
        <article className="border-b border-border bg-card p-500 xl:border-b-0 xl:border-r" aria-labelledby="severity-plot-title">
            <p className="section-kicker">Failure mix</p>
            <h2 className="section-title" id="severity-plot-title">Findings by severity</h2>
            <div className="mt-400 grid grid-cols-[8rem_minmax(0,1fr)] items-center gap-500">
                <svg className="aspect-square w-full" viewBox="0 0 128 128" role="img" aria-label={`${total} findings by severity`}>
                    <circle className="fill-none stroke-muted" cx="64" cy="64" r="46" strokeWidth="14" />
                    {segments.filter((segment) => segment.count > 0).map((segment) => (
                        <circle
                            className={`fill-none ${segment.stroke}`}
                            cx="64"
                            cy="64"
                            key={segment.severity}
                            pathLength="100"
                            r="46"
                            strokeDasharray={`${segment.percentage} ${100 - segment.percentage}`}
                            strokeDashoffset={-segment.offset}
                            strokeLinecap="butt"
                            strokeWidth="14"
                            transform="rotate(-90 64 64)"
                        >
                            <title>{segment.label}: {segment.count}</title>
                        </circle>
                    ))}
                    <text className="fill-foreground font-numeric text-[24px] font-semibold" x="64" y="61" textAnchor="middle">{total}</text>
                    <text className="fill-muted-foreground text-[9px]" x="64" y="76" textAnchor="middle">open flags</text>
                </svg>
                <div className="space-y-300">
                    {segments.map((segment) => (
                        <div className="flex items-center gap-200" key={segment.severity}>
                            <span className={`icon-size-100 shrink-0 ${segment.swatch}`} aria-hidden="true" />
                            <span className="min-w-0 flex-1 text-200 text-muted-foreground">{segment.label}</span>
                            <strong className="font-numeric text-300">{segment.count}</strong>
                        </div>
                    ))}
                </div>
            </div>
        </article>
    );
}

function WorkspaceRiskScatter({ estate, workspaceRisks, onOpenWorkspace }: {
    estate: TenantEstate;
    workspaceRisks: WorkspaceRisk[];
    onOpenWorkspace: (workspaceId: string) => void;
}) {
    const width = 560;
    const height = 238;
    const margin = { top: 16, right: 18, bottom: 34, left: 42 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const points = workspaceRisks
        .map((workspace) => {
            const room = estate.rooms.find((candidate) => candidate.name === workspace.name);
            return room ? { ...workspace, id: room.id, itemCount: room.items.length } : null;
        })
        .filter((point): point is NonNullable<typeof point> => point !== null)
        .sort((left, right) => right.riskScore - left.riskScore)
        .slice(0, 12);
    const maxItems = Math.max(...points.map((point) => point.itemCount), 1);
    const x = (itemCount: number) => margin.left + (itemCount / maxItems) * plotWidth;
    const y = (riskScore: number) => margin.top + (1 - riskScore / 100) * plotHeight;
    const ticks = [0, 25, 50, 75, 100];

    return (
        <article className="min-w-0 bg-card p-500" aria-labelledby="risk-plot-title">
            <div className="flex items-end justify-between gap-300">
                <div>
                    <p className="section-kicker">Estate hotspots</p>
                    <h2 className="section-title" id="risk-plot-title">Risk versus item count</h2>
                </div>
                <p className="text-right text-100 text-muted-foreground">Bubble size = open issues<br />Top 12 by risk</p>
            </div>
            <svg className="mt-300 aspect-[2.35/1] w-full overflow-visible" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Workspace risk plotted against Fabric item count">
                {ticks.map((tick) => (
                    <g key={tick}>
                        <line className="stroke-border" x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} />
                        <text className="fill-muted-foreground text-[9px]" x={margin.left - 8} y={y(tick) + 3} textAnchor="end">{tick}</text>
                    </g>
                ))}
                <line className="stroke-border-strong" x1={margin.left} x2={width - margin.right} y1={height - margin.bottom} y2={height - margin.bottom} />
                <text className="fill-muted-foreground text-[9px]" x={margin.left} y={height - 9}>Fewer items</text>
                <text className="fill-muted-foreground text-[9px]" x={width - margin.right} y={height - 9} textAnchor="end">More items</text>
                <text className="fill-muted-foreground text-[9px]" transform={`translate(11 ${margin.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">Risk score</text>
                {points.map((point) => {
                    const circleClass = point.status === "red" ? "fill-destructive stroke-destructive-soft" : point.status === "amber" ? "fill-warning stroke-warning-soft" : "fill-success stroke-success-soft";
                    const radius = Math.min(18, 7 + Math.sqrt(Math.max(point.issues, 1)) * 2);
                    const open = () => onOpenWorkspace(point.id);
                    return (
                        <g
                            aria-label={`Open findings for ${point.name}: risk ${point.riskScore}, ${point.itemCount} items, ${point.issues} issues`}
                            className="cursor-pointer outline-none focus-visible:[&_circle]:stroke-foreground"
                            key={point.id}
                            onClick={open}
                            onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") open(); }}
                            role="button"
                            tabIndex={0}
                        >
                            <circle className={`${circleClass} opacity-85 transition-opacity hover:opacity-100`} cx={x(point.itemCount)} cy={y(point.riskScore)} r={radius} strokeWidth="4" />
                            <title>{point.name}: risk {point.riskScore}, {point.itemCount} items, {point.issues} issues</title>
                        </g>
                    );
                })}
            </svg>
        </article>
    );
}

export function OverviewPlots({ onOpenWorkspace }: { onOpenWorkspace: (workspaceId: string) => void }) {
    const { estate, findings, workspaceRisks } = useReviewData();
    return (
        <section className="grid border-b border-border xl:grid-cols-[minmax(18rem,0.72fr)_minmax(0,1.28fr)]" aria-label="Review risk plots">
            <SeverityDonut findings={findings} />
            <WorkspaceRiskScatter estate={estate} workspaceRisks={workspaceRisks} onOpenWorkspace={onOpenWorkspace} />
        </section>
    );
}

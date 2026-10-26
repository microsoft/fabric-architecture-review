// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.
import { useId, type ReactNode } from "react";
import { Boxes, Info } from "lucide-react";
import type { WorkspaceRoom } from "@/lib/review-data";
import { statusClass, statusText } from "@/lib/status-style";
import { cn } from "@/lib/utils";

export function StatusBadge({ status }: { status?: string }) {
    return <span className={cn("inline-flex whitespace-nowrap rounded-md px-200 py-100 text-100 font-semibold", statusClass[status ?? "unknown"] ?? "bg-muted text-muted-foreground")}>{statusText(status)}</span>;
}

export function DefinitionTooltip({ definition, label, align = "left" }: { definition: string; label: string; align?: "left" | "right" }) {
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

export function BrandMark() {
    return (
        <div className="flex items-center gap-300">
            <span className="relative grid icon-size-brand place-items-center overflow-hidden rounded-lg bg-primary text-primary-foreground">
                <Boxes className="icon-size-400" aria-hidden="true" />
                <span className="absolute bottom-0 right-0 icon-size-100 bg-warning" />
            </span>
            <div>
                <p className="font-heading text-300 font-bold leading-300">FAR</p>
                <p className="text-100 text-muted-foreground">Architecture review</p>
            </div>
        </div>
    );
}

export function PageIntro({ kicker, title, children, aside }: { kicker: string; title: string; children?: ReactNode; aside?: ReactNode }) {
    return (
        <div className="mb-500 flex flex-col gap-400">
            <div>
                <p className="section-kicker">{kicker}</p>
                <h2 className="font-heading text-hero-700 font-semibold leading-hero-700">{title}</h2>
                {children && <p className="mt-200 max-w-search text-200 leading-300 text-muted-foreground">{children}</p>}
            </div>
            {aside}
        </div>
    );
}

export function StatGrid({ label, stats }: { label: string; stats: [string, string | number, string?][] }) {
    return (
        <section className="grid border border-border bg-card sm:grid-cols-3" aria-label={label}>
            {stats.map(([name, value, detail], index) => (
                <article className={cn("p-400", index > 0 && "border-t border-border sm:border-l sm:border-t-0")} key={name}>
                    <p className="text-200 text-muted-foreground">{name}</p>
                    <p className="mt-200 font-numeric text-hero-700 font-semibold">{value}</p>
                    {detail && <p className="mt-100 text-100 text-muted-foreground">{detail}</p>}
                </article>
            ))}
        </section>
    );
}

export function SelectField({ label, value, onChange, children }: { label: string; value: string; onChange: (value: string) => void; children: ReactNode }) {
    const id = useId();
    return (
        <label className="flex items-center gap-200 text-200 font-semibold text-muted-foreground" htmlFor={id}>
            <span>{label}</span>
            <select className="min-w-0 rounded-lg border border-input bg-card px-300 py-200 text-200 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/30" id={id} onChange={(event) => onChange(event.target.value)} value={value}>
                {children}
            </select>
        </label>
    );
}

export function WorkspaceSelect({ rooms, value, onChange }: { rooms: WorkspaceRoom[]; value: string; onChange: (workspaceId: string) => void }) {
    return (
        <SelectField label="Workspace" value={value} onChange={onChange}>
            <option value="all">All workspaces</option>
            {rooms.map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}
        </SelectField>
    );
}

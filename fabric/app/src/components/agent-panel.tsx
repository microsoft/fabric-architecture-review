//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { useId, useState } from "react";
import { ArrowUp, Bot, CheckCircle2, CircleDashed, LoaderCircle, Sparkles, TriangleAlert } from "lucide-react";
import { useDataAgent } from "@/hooks/use-data-agent";

const suggestedPrompts = [
    "What should we fix first?",
    "Which workspaces need an owner?",
    "Where can we reduce capacity cost?",
];

export function AgentPanel() {
    const questionId = useId();
    const [question, setQuestion] = useState("");
    const [submittedQuestion, setSubmittedQuestion] = useState<string | null>(null);
    const { answer, ask, error, missingConfiguration, status } = useDataAgent();
    const isConfigured = status !== "unconfigured";

    const submitQuestion = async (value: string) => {
        const nextQuestion = value.trim();
        if (!nextQuestion || status === "querying") return;
        setSubmittedQuestion(nextQuestion);
        setQuestion("");
        await ask(nextQuestion);
    };

    return (
        <aside className="flex h-full min-h-0 flex-col overflow-hidden border-l border-border bg-agent-surface text-agent-foreground">
            <div className="shrink-0 border-b border-agent-border p-500">
                <div className="mb-400 flex items-start justify-between gap-300">
                    <div className="flex items-center gap-300">
                        <span className="grid icon-size-600 place-items-center rounded-xl bg-agent-accent text-agent-accent-foreground">
                            <Bot className="icon-size-300" aria-hidden="true" />
                        </span>
                        <div>
                            <p className="font-heading text-400 font-semibold">Review agent</p>
                            <p className="text-200 text-agent-muted">{isConfigured ? "Grounded in the live assessment" : "Live connection required"}</p>
                        </div>
                    </div>
                    <span className="inline-flex items-center gap-100 rounded-full border border-agent-border px-200 py-100 text-100 text-agent-muted">
                        {status === "querying" ? <LoaderCircle className="icon-size-100 animate-spin" aria-hidden="true" /> : <CircleDashed className="icon-size-100" aria-hidden="true" />}
                        {status === "unconfigured" ? "Setup needed" : status === "querying" ? "Querying" : "MCP connected"}
                    </span>
                </div>
                <div className="flex items-center gap-200 rounded-lg bg-agent-highlight px-300 py-200 text-200 text-agent-muted">
                    {isConfigured ? <CheckCircle2 className="icon-size-200 text-agent-positive" aria-hidden="true" /> : <CircleDashed className="icon-size-200" aria-hidden="true" />}
                    {isConfigured ? "Semantic model and Data Agent connected" : "No live semantic model or Data Agent connected"}
                </div>
            </div>

            <div aria-label="Review agent conversation" className="flex min-h-0 flex-1 flex-col gap-500 overflow-y-auto p-500" role="region">
                <div className="rounded-xl border border-agent-border bg-agent-highlight p-400">
                    <div className="mb-200 flex items-center gap-200 text-200 font-semibold text-agent-accent-foreground">
                        <Sparkles className="icon-size-200" aria-hidden="true" />
                        {isConfigured ? "Daily brief" : "Preview boundary"}
                    </div>
                    <p className="text-300 leading-300 text-agent-foreground">
                        {isConfigured ? "Ask the Data Agent for a grounded summary of the connected assessment." : "The values shown in design preview are anonymous samples. Configure the live connection before release validation."}
                    </p>
                </div>

                {submittedQuestion ? (
                    <div className="space-y-300" aria-live="polite">
                        <div className="ml-600 rounded-xl rounded-br-sm bg-agent-question p-300 text-300">
                            {submittedQuestion}
                        </div>
                        <div className="rounded-xl rounded-bl-sm border border-agent-border bg-agent-highlight p-400">
                            <p className="mb-200 text-100 font-semibold uppercase text-agent-muted">
                                {status === "querying" ? "Consulting Fabric" : status === "answered" ? "Grounded answer" : status === "error" ? "Query failed" : "Connection required"}
                            </p>
                            {status === "querying" && <p className="text-300 leading-300 text-agent-muted">Discovering the published tool and inspecting the review data...</p>}
                            {answer && <p className="whitespace-pre-wrap text-300 leading-300">{answer}</p>}
                            {error && <p className="text-300 leading-300">{error}</p>}
                            {!isConfigured && (
                                <p className="text-300 leading-300">
                                    Configure the public OAuth client, tenant, and published Data Agent ID. The source workspace defaults to the connected review model.
                                </p>
                            )}
                        </div>
                    </div>
                ) : (
                    <div>
                        <p className="mb-300 text-200 font-semibold text-agent-muted">Try asking</p>
                        <div className="flex flex-col gap-200">
                            {suggestedPrompts.map((prompt) => (
                                <button
                                    className="rounded-lg border border-agent-border bg-transparent px-300 py-200 text-left text-300 transition-colors hover:bg-agent-highlight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-agent-accent"
                                    key={prompt}
                                    onClick={() => void submitQuestion(prompt)}
                                    type="button"
                                >
                                    {prompt}
                                </button>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            <form
                className="shrink-0 border-t border-agent-border p-400"
                onSubmit={(event) => {
                    event.preventDefault();
                    void submitQuestion(question);
                }}
            >
                <label className="sr-only" htmlFor={questionId}>Ask the review agent</label>
                {!isConfigured && (
                    <div className="mb-300 flex items-start gap-200 rounded-lg border border-agent-border bg-agent-highlight p-300 text-200 text-agent-muted">
                        <TriangleAlert className="mt-100 icon-size-200 shrink-0 text-agent-accent" aria-hidden="true" />
                        <span>Data Agent configuration missing: {missingConfiguration.join(", ")}</span>
                    </div>
                )}
                <div className="flex items-end gap-200 rounded-xl border border-agent-border bg-agent-highlight p-200 focus-within:ring-2 focus-within:ring-agent-accent">
                    <textarea
                        className="min-h-600 flex-1 resize-none bg-transparent px-200 py-100 text-300 outline-none placeholder:text-agent-muted"
                        id={questionId}
                        onChange={(event) => setQuestion(event.target.value)}
                        placeholder="Ask about risk, cost, or design..."
                        rows={2}
                        value={question}
                    />
                    <button
                        aria-label="Send question"
                        className="grid icon-size-600 shrink-0 place-items-center rounded-lg bg-agent-accent text-agent-accent-foreground transition-transform hover:scale-105 disabled:cursor-not-allowed disabled:opacity-40"
                        disabled={!question.trim() || status === "querying"}
                        type="submit"
                    >
                        <ArrowUp className="icon-size-200" aria-hidden="true" />
                    </button>
                </div>
            </form>
        </aside>
    );
}

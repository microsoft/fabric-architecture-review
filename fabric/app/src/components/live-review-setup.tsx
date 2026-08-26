//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { AlertTriangle, DatabaseZap } from "lucide-react";
import { ReviewWorkbench } from "@/components/review-workbench";
import { useLiveReviewData } from "@/hooks/use-live-review-data";

export function LiveReviewSetup() {
    const { data, error, loading } = useLiveReviewData();
    if (data) return <ReviewWorkbench data={data} />;

    return (
        <main className="grid min-h-screen place-items-center bg-background p-500">
            <section className="w-full max-w-xl border border-border bg-card p-600 shadow-xl" aria-labelledby="live-review-title">
                <div className="mb-500 flex items-center justify-between gap-400">
                    <span className="grid icon-size-800 place-items-center rounded-lg bg-primary-soft text-primary-strong">
                        {error ? <AlertTriangle className="icon-size-400" aria-hidden="true" /> : <DatabaseZap className="icon-size-400" aria-hidden="true" />}
                    </span>
                </div>
                <p className="section-kicker">Live Fabric review</p>
                <h1 className="font-heading text-700 font-bold" id="live-review-title">{error ? "The review data could not be loaded" : "Loading the latest architecture review"}</h1>
                <p className="mt-300 text-300 leading-500 text-muted-foreground">
                    {error ? error.message : loading ? "Querying the registered FAR semantic model for the latest completed run." : "Preparing the estate."}
                </p>
            </section>
        </main>
    );
}
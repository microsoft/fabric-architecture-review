//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { useRef, useState } from "react";
import { getDataAgentConnection } from "@/lib/data-agent-factory";

export type DataAgentStatus = "unconfigured" | "ready" | "querying" | "answered" | "error";

export function useDataAgent() {
    const connection = getDataAgentConnection();
    const requestInFlight = useRef(false);
    const [status, setStatus] = useState<DataAgentStatus>(connection.client ? "ready" : "unconfigured");
    const [answer, setAnswer] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const ask = async (question: string) => {
        if (!connection.client || requestInFlight.current) return;
        requestInFlight.current = true;
        setStatus("querying");
        setAnswer(null);
        setError(null);

        try {
            setAnswer(await connection.client.ask(question));
            setStatus("answered");
        } catch (caught) {
            setError(caught instanceof Error ? caught.message : String(caught));
            setStatus("error");
        } finally {
            requestInFlight.current = false;
        }
    };

    return {
        answer,
        ask,
        error,
        missingConfiguration: connection.missingConfiguration,
        status,
    };
}
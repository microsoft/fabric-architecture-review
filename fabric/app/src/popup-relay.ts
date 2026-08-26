//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

const continueButton = document.querySelector<HTMLButtonElement>("#continue-sign-in");
const errorMessage = document.querySelector<HTMLElement>("#sign-in-error");
const responseType = "msal:popup-relay-response:v1";
const relayCloseDelayMs = 1_000;
const relayTimeoutMs = 300_000;

interface RelayRequest {
    id: string;
    method: "GET";
    url: string;
}

function parseRelayRequest(): RelayRequest {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const request = JSON.parse(params.get("req") ?? "") as Partial<RelayRequest>;
    window.history.replaceState(null, "", window.location.origin + window.location.pathname);

    if (!request.id || request.method !== "GET" || !request.url) {
        throw new Error("The sign-in relay request is invalid.");
    }
    return request as RelayRequest;
}

function runPopupRelayWithCloseGrace(): void {
    const opener = window.opener;
    if (!opener) throw new Error("The sign-in relay was opened without its parent application.");

    const request = parseRelayRequest();
    const channel = new BroadcastChannel(request.id);
    const childPopup = window.open(
        request.url,
        "msalPopupRelayChild",
        "width=520,height=640,scrollbars=yes",
    );
    if (!childPopup) {
        channel.close();
        opener.postMessage({ type: responseType, id: request.id, error: "popup_window_error" }, window.location.origin);
        return;
    }

    let settled = false;
    const timeout = window.setTimeout(() => relayError("timed_out"), relayTimeoutMs);

    const cleanup = () => {
        channel.close();
        window.clearTimeout(timeout);
    };
    const relay = (message: { payload?: string; error?: string }) => {
        if (settled) return;
        settled = true;
        cleanup();
        opener.postMessage({ type: responseType, id: request.id, ...message }, window.location.origin);
        window.setTimeout(() => window.close(), relayCloseDelayMs);
    };
    const relayError = (error: string) => relay({ error });

    channel.onmessage = (event: MessageEvent<{ payload?: string }>) => {
        if (event.data?.payload) relay({ payload: event.data.payload });
    };
}

continueButton?.addEventListener("click", () => {
    continueButton.disabled = true;
    try {
        runPopupRelayWithCloseGrace();
    } catch (error) {
        continueButton.disabled = false;
        if (errorMessage) {
            errorMessage.textContent = error instanceof Error ? error.message : String(error);
        }
    }
});
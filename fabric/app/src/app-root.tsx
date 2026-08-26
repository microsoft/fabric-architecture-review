//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { lazy, Suspense } from "react";
import { ErrorBoundary } from "react-error-boundary";
import { AuthGate } from "@/components/auth-gate.component";
import { LiveReviewSetup } from "@/components/live-review-setup";
import { ErrorFallback } from "@/ErrorFallback";
import { ThemeContext } from "@/hooks/theme.context";
import { useAppTheme } from "@/hooks/use-theme";
import { AuthProvider } from "@/hooks/use-auth";
import { bootstrapAuth } from "@/services/rayfin-auth.service";

const isDesignPreview = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("preview") === "1";
const rayfinAuthService = isDesignPreview ? null : bootstrapAuth();
const PreviewApp = isDesignPreview ? lazy(() => import("@/App")) : null;

export function AppRoot() {
    const { isDark, toggleTheme } = useAppTheme();
    const content = isDesignPreview ? (
        <Suspense fallback={<div className="grid min-h-screen place-items-center bg-background text-muted-foreground">Loading design preview…</div>}>
            <PreviewApp />
        </Suspense>
    ) : (
        <AuthProvider rayfinAuthService={rayfinAuthService!}>
            <AuthGate>
                <LiveReviewSetup />
            </AuthGate>
        </AuthProvider>
    );

    return (
        <ThemeContext.Provider value={{ isDark, toggleTheme }}>
            <ErrorBoundary FallbackComponent={ErrorFallback}>
                {content}
            </ErrorBoundary>
        </ThemeContext.Provider>
    );
}

//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LiveReviewSetup } from "@/components/live-review-setup";

vi.mock("@/hooks/use-live-review-data", () => ({
    useLiveReviewData: () => ({ data: null, error: null, loading: true }),
}));

describe("LiveReviewSetup", () => {
    it("loads the live semantic model without presenting preview assessment data", () => {
        render(<LiveReviewSetup />);

        expect(screen.getByRole("heading", { name: "Loading the latest architecture review" })).toBeInTheDocument();
        expect(screen.queryByText(/Contoso/i)).not.toBeInTheDocument();
        expect(screen.queryByText("Your estate, prioritized")).not.toBeInTheDocument();
    });
});
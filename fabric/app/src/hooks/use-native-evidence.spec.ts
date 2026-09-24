// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useNativeEvidence } from "@/hooks/use-native-evidence";

const { query, semanticModel } = vi.hoisted(() => ({ query: vi.fn(), semanticModel: vi.fn() }));
vi.mock("@/lib/fabric-client", () => ({
    getFabricClient: () => ({ semanticModel }),
}));

describe("native evidence transport", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        semanticModel.mockReturnValue({ query });
    });

    it("uses reviewModel and preserves successful empty results separately from denied detail queries", async () => {
        query.mockImplementation((dax: string) => Promise.resolve(dax.includes("'gold_execution_coverage'") ? { status: "success", table: { columns: [], rows: [] } } : { status: "error", error: { message: "Forbidden detail query" } }));
        const { result } = renderHook(() => useNativeEvidence("executions", "run-pinned", false));
        await waitFor(() => expect(result.current.detail.status).toBe("error"));
        expect(result.current.coverage).toEqual({ status: "ready", rows: [] });
        expect(result.current.detail).toEqual({ status: "error", message: "Forbidden detail query" });
        expect(semanticModel).toHaveBeenCalledWith("reviewModel");
        expect(query.mock.calls.every(([dax]) => dax.includes('VAR ReviewRun = "run-pinned"'))).toBe(true);
        await act(async () => { result.current.retry(); });
        expect(query).toHaveBeenCalledTimes(4);
        expect(query.mock.calls.slice(2).every(([, options]) => options.bypassCache)).toBe(true);
    });

    it("reports malformed query results rather than empty success", async () => {
        query.mockResolvedValue({ status: "success", table: { columns: [{ name: "[unexpected]" }], rows: [["bad"]] } });
        const { result } = renderHook(() => useNativeEvidence("dataflows", "run-pinned", false));
        await waitFor(() => expect(result.current.coverage.status).toBe("error"));
        expect(result.current.detail.status).toBe("error");
    });
});

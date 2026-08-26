//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { createContext, useContext, type ReactNode } from "react";
import { previewReviewData, type ReviewData } from "@/lib/review-data";

const ReviewDataContext = createContext<ReviewData>(previewReviewData);

export function ReviewDataProvider({ children, value }: { children: ReactNode; value: ReviewData }) {
    return <ReviewDataContext.Provider value={value}>{children}</ReviewDataContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useReviewData() {
    return useContext(ReviewDataContext);
}
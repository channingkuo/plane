/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ViewFlags } from "@plane/types";

/** Layouts use the page toolbar for creation; property editing keeps its existing permissions. */
export function getIssueLayoutViewFlags(flags?: Partial<ViewFlags>): ViewFlags {
  return {
    ...flags,
    enableInlineEditing: !!flags?.enableInlineEditing,
    enableQuickAdd: false,
    enableIssueCreation: false,
  };
}

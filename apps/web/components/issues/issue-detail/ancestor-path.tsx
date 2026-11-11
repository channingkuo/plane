/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TProjectHierarchy } from "@/services/project-overview.service";

export function ancestorPath(hierarchy: TProjectHierarchy | undefined, issueId: string | undefined) {
  const result: TProjectHierarchy[string][] = [];
  const visited = new Set(issueId ? [issueId] : []);
  let parent = issueId ? hierarchy?.[issueId]?.parent_id : undefined;
  while (parent && !visited.has(parent)) {
    const row = hierarchy?.[parent];
    if (!row) break;
    visited.add(parent);
    result.push(row);
    parent = row.parent_id;
  }
  // Reverse a new local array; this package targets ES2022.
  // oxlint-disable-next-line unicorn/no-array-reverse
  return result.reverse();
}

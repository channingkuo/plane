/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi } from "vitest";
import type { TIssue } from "@plane/types";
import { ProjectIssues } from "../project/issue.store";
import type { IIssueRootStore } from "../root.store";
import type { IProjectIssuesFilter } from "../project/filter.store";

vi.mock("@/lib/store-context", () => ({ store: {} }));

describe("workflow timeline ordering", () => {
  it("retains server-computed family ranks across pages and puts unsaved rows last", () => {
    const rows = [
      { id: "requirement-late", workflow_order: 4, created_at: "2026-11-20", sequence_id: 1 },
      { id: "local-new", created_at: "2026-11-30", sequence_id: 10 },
      { id: "test", workflow_order: 3, created_at: "2026-11-15", sequence_id: 3 },
      { id: "development", workflow_order: 2, created_at: "2026-11-13", sequence_id: 4 },
      { id: "requirement-early", workflow_order: 1, created_at: "2026-11-12", sequence_id: 2 },
    ] as TIssue[];
    const root = {
      issues: { getIssuesByIds: (ids: string[]) => rows.filter((row) => ids.includes(row.id)) },
      rootStore: { router: {} },
    } as unknown as IIssueRootStore;
    const store = new ProjectIssues(root, {} as IProjectIssuesFilter);
    expect(
      store.issuesSortWithOrderBy(
        rows.map((row) => row.id),
        "workflow_order"
      )
    ).toEqual(["requirement-early", "development", "test", "requirement-late", "local-new"]);
    expect(
      store.issuesSortWithOrderBy(
        rows.slice(0, 3).map((row) => row.id),
        "workflow_order"
      )
    ).toEqual(["test", "requirement-late", "local-new"]);
    expect(
      store.issuesSortWithOrderBy(
        rows.map((row) => row.id),
        "-created_at"
      )[0]
    ).toBe("local-new");
  });
});

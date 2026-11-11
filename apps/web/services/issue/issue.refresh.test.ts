/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { mutate } from "swr";
import type { TIssue } from "@plane/types";
import { IssueService } from "./issue.service";

vi.mock("swr", () => ({ mutate: vi.fn() }));
afterEach(() => vi.restoreAllMocks());

describe("work item change notifications", () => {
  it.each([
    [{ name: "Updated title" }, false, false],
    [{ priority: "high" }, false, false],
    [{ target_date: "2026-11-12" }, false, false],
    [{ state_id: "complete" }, true, true],
    [{ parent_id: "parent" }, true, true],
    [{ assignee_ids: ["member"] }, true, false],
  ] as [Partial<TIssue>, boolean, boolean][])(
    "only revalidates affected data for %o",
    async (data, listChanged, hierarchyChanged) => {
      vi.mocked(mutate).mockClear();
      const service = new IssueService();
      vi.spyOn(service, "patch").mockResolvedValue({ data: { id: "item" }, headers: {} } as never);
      const listener = vi.fn();
      window.addEventListener("plane:workflow-updated", listener);
      try {
        await service.patchIssue("workspace", "project", "item", data);
        expect(listener).toHaveBeenCalledOnce();
        expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({
          workspaceSlug: "workspace",
          projectId: "project",
          issueId: "item",
          listChanged,
        });
        expect(mutate).toHaveBeenCalledTimes(hierarchyChanged ? 1 : 0);
      } finally {
        window.removeEventListener("plane:workflow-updated", listener);
      }
    }
  );

  it("waits for authoritative workflow changes before notifying the layout", async () => {
    const service = new IssueService();
    vi.spyOn(service, "patch").mockResolvedValue({
      data: { id: "item" },
      headers: { "x-plane-workflow": "enabled" },
    } as never);
    const retrieve = vi.spyOn(service, "retrieve").mockResolvedValue({ id: "item", sub_issues_count: 1 } as TIssue);
    const listener = vi.fn(() => expect(retrieve).toHaveBeenCalledOnce());
    window.addEventListener("plane:workflow-updated", listener);
    try {
      const result = await service.patchIssue("workspace", "project", "item", { state_id: "complete" });
      expect(result.sub_issues_count).toBe(1);
      expect(listener).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener("plane:workflow-updated", listener);
    }
  });
});

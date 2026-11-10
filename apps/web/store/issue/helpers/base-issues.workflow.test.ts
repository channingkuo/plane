/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TIssue } from "@plane/types";
import { BaseIssuesStore } from "./base-issues.store";
import { workflowService } from "@/services/workflow.service";

vi.mock("@/lib/store-context", () => ({ store: {} }));
vi.mock("@/services/workflow.service", () => ({ workflowService: { creation: vi.fn() } }));

function fixture() {
  const issues = new Map<string, Partial<TIssue>>([
    ["child", { id: "child", state_id: "todo", parent_id: "parent", start_date: null, target_date: null }],
    ["parent", { id: "parent", state_id: "done", parent_id: null }],
  ]);
  const store = Object.create(BaseIssuesStore.prototype) as BaseIssuesStore;
  const updateIssue = vi.fn((id: string, patch: Partial<TIssue>) => issues.set(id, { ...issues.get(id), ...patch }));
  Object.assign(store, {
    rootIssueStore: { issues: { getIssueById: (id: string) => issues.get(id), updateIssue, removeIssue: vi.fn() } },
    issueService: { patchIssue: vi.fn(), deleteIssue: vi.fn(), updateIssueDates: vi.fn(), retrieve: vi.fn() },
    updateIssueList: vi.fn(),
    updateParentStats: vi.fn(),
    fetchParentStats: vi.fn(),
  });
  return { store, issues };
}

describe("workflow state reconciliation", () => {
  beforeEach(() => vi.mocked(workflowService.creation).mockResolvedValue({ enabled: true }));

  it("restores the state, list and parent counts when a transition is rejected", async () => {
    const { store, issues } = fixture();
    vi.mocked(store.issueService.patchIssue).mockRejectedValue({ code: "WORKFLOW_ROLE_FORBIDDEN" });
    await expect(store.issueUpdate("ws", "project", "child", { state_id: "done" })).rejects.toMatchObject({
      code: "WORKFLOW_ROLE_FORBIDDEN",
    });
    expect(issues.get("child")?.state_id).toBe("todo");
    expect(store.updateIssueList).toHaveBeenLastCalledWith(
      expect.objectContaining({ state_id: "todo" }),
      expect.objectContaining({ state_id: "done" })
    );
    expect(store.updateParentStats).toHaveBeenLastCalledWith(
      expect.objectContaining({ state_id: "done" }),
      expect.objectContaining({ state_id: "todo" })
    );
    expect(store.fetchParentStats).toHaveBeenCalledWith("ws", "project");
  });

  it("restores explicit empty dates after a failed batch", async () => {
    const { store, issues } = fixture();
    vi.mocked(store.issueService.updateIssueDates).mockRejectedValue({ code: "WORKFLOW_INVALID_DATE" });
    await expect(
      store.updateIssueDates("ws", [{ id: "child", start_date: "2026-11-10", target_date: "2026-11-12" }], "project")
    ).rejects.toBeTruthy();
    expect(issues.get("child")?.start_date).toBeNull();
    expect(issues.get("child")?.target_date).toBeNull();
  });

  it("refreshes an automatically reopened parent after a successful child update", async () => {
    const { store, issues } = fixture();
    vi.mocked(store.issueService.patchIssue).mockResolvedValue({ ...issues.get("child"), name: "Changed" });
    vi.mocked(store.issueService.retrieve).mockResolvedValue({
      id: "parent",
      state_id: "reviewed",
      parent_id: null,
    } as TIssue);
    await store.issueUpdate("ws", "project", "child", { name: "Changed" });
    expect(issues.get("parent")?.state_id).toBe("reviewed");
    expect(store.updateIssueList).toHaveBeenCalledWith(
      expect.objectContaining({ id: "parent", state_id: "reviewed" }),
      expect.objectContaining({ id: "parent", state_id: "done" })
    );
  });

  it("restores parent counts when deletion is rejected", async () => {
    const { store } = fixture();
    vi.mocked(store.issueService.deleteIssue).mockRejectedValue({ code: "WORKFLOW_REQUIRED_FIELDS_MISSING" });
    await expect(store.removeIssue("ws", "project", "child")).rejects.toBeTruthy();
    expect(store.updateParentStats).toHaveBeenLastCalledWith(undefined, expect.objectContaining({ id: "child" }));
    expect(store.rootIssueStore.issues.removeIssue).not.toHaveBeenCalled();
  });
});

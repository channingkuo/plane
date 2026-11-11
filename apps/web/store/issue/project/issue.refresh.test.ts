/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi } from "vitest";
import { ProjectIssues } from "./issue.store";
import type { TIssuesResponse } from "@plane/types";
import type { IIssueRootStore } from "../root.store";
import type { IProjectIssuesFilter } from "./filter.store";

vi.mock("@/lib/store-context", () => ({ store: {} }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function fixture() {
  const root = {
    rootStore: { router: {}, projectRoot: { project: { fetchProjectDetails: vi.fn() } } },
    issues: { addIssue: vi.fn(), getIssueById: vi.fn() },
    issueDetail: { relation: { extractRelationsFromIssues: vi.fn() } },
  } as unknown as IIssueRootStore;
  const store = new ProjectIssues(root, {} as IProjectIssuesFilter);
  Object.assign(store, {
    groupedIssueIds: { state: ["first", "second"] },
    groupedIssueCount: { state: 2 },
    issuePaginationData: { state: { nextCursor: "next-page" } },
    paginationOptions: { canGroup: true, perPageCount: 30 },
    controller: new AbortController(),
    workItemScope: "mine",
    issueFilterStore: { getFilterParams: vi.fn() },
    issueService: { getIssues: vi.fn() },
  });
  return store;
}

const response = { results: [], total_count: 2 } as unknown as TIssuesResponse;

describe("background work item API refresh", () => {
  it("keeps cards, counts and cursors mounted while a refresh request is pending", async () => {
    const store = fixture();
    const request = deferred<TIssuesResponse>();
    vi.mocked(store.issueService.getIssues).mockReturnValue(request.promise);
    const ids = store.groupedIssueIds;
    const counts = store.groupedIssueCount;
    const cursor = store.issuePaginationData;
    const refresh = store.fetchIssues("workspace", "project", "mutation", { canGroup: true, perPageCount: 30 }, true);
    expect(store.groupedIssueIds).toBe(ids);
    expect(store.groupedIssueCount).toBe(counts);
    expect(store.issuePaginationData).toBe(cursor);
    request.resolve(response);
    await refresh;
    expect(store.rootIssueStore.issues.addIssue).toHaveBeenCalledOnce();
  });

  it("keeps the current content and clears the busy indicator if revalidation fails", async () => {
    const store = fixture();
    const ids = store.groupedIssueIds;
    vi.mocked(store.issueService.getIssues).mockRejectedValue(new Error("offline"));
    await expect(store.fetchIssues("ws", "project", "mutation", { canGroup: true, perPageCount: 30 })).rejects.toThrow(
      "offline"
    );
    expect(store.groupedIssueIds).toBe(ids);
    expect(store.getIssueLoader()).toBeUndefined();
  });

  it("does not apply an old response or dismiss the newer request's busy indicator", async () => {
    const store = fixture();
    const old = deferred<TIssuesResponse>();
    const latest = deferred<TIssuesResponse>();
    vi.mocked(store.issueService.getIssues).mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
    const first = store.fetchIssues("ws", "project", "mutation", { canGroup: true, perPageCount: 30 });
    const oldSignal = store.controller.signal;
    const second = store.fetchIssues("ws", "project", "mutation", { canGroup: true, perPageCount: 30 });
    expect(oldSignal.aborted).toBe(true);
    old.resolve(response);
    await first;
    expect(store.rootIssueStore.issues.addIssue).not.toHaveBeenCalled();
    expect(store.getIssueLoader()).toBe("mutation");
    latest.resolve(response);
    await second;
    expect(store.rootIssueStore.issues.addIssue).toHaveBeenCalledOnce();
  });

  it("clears old data when entering a different layout/project for the initial load", async () => {
    const store = fixture();
    const request = deferred<TIssuesResponse>();
    vi.mocked(store.issueService.getIssues).mockReturnValue(request.promise);
    const load = store.fetchIssues("ws", "other-project", "init-loader", { canGroup: true, perPageCount: 30 });
    expect(store.groupedIssueIds).toBeUndefined();
    request.resolve(response);
    await load;
  });

  it("revalidates already loaded pages instead of shrinking a long list back to one page", () => {
    const store = fixture();
    store.groupedIssueIds = { state: Array.from({ length: 90 }, (_, index) => String(index)) };
    expect(store.refreshPaginationOptions("mutation", { canGroup: true, perPageCount: 30 }).perPageCount).toBe(91);
    expect(store.refreshPaginationOptions("init-loader", { canGroup: true, perPageCount: 30 }).perPageCount).toBe(30);
  });
});

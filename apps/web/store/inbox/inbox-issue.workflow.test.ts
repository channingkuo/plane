/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi } from "vitest";
import { EInboxIssueStatus, type TInboxIssue } from "@plane/types";
import type { CoreRootStore } from "../root.store";
import { InboxIssueStore } from "./inbox-issue.store";

vi.mock("@/lib/store-context", () => ({ store: {} }));

function fixture() {
  const update = vi.fn();
  const root = {
    projectRoot: { project: { projectMap: { project: { intake_count: 1 } } } },
    projectInbox: { inboxIssuePaginationInfo: { total_results: 1 } },
    issue: { issues: { addIssue: vi.fn() } },
  };
  const store = new InboxIssueStore(
    "ws",
    "project",
    {
      id: "intake",
      status: EInboxIssueStatus.PENDING,
      issue: { id: "requirement", state_id: "pending", name: "Requirement" },
    } as TInboxIssue,
    root as unknown as CoreRootStore
  );
  store.inboxIssueService.update = update;
  return { store, root, update };
}

describe("Intake acceptance", () => {
  it("submits edits and acceptance together and retains the authoritative state", async () => {
    const { store, root, update } = fixture();
    update.mockResolvedValue({
      status: EInboxIssueStatus.ACCEPTED,
      issue: { id: "requirement", state_id: "reviewed", name: "Edited", assignee_ids: ["admin"] },
    });
    const payload = { name: "Edited", assignee_ids: ["admin"], state_id: "reviewed" };
    const result = await store.updateInboxIssueStatus(EInboxIssueStatus.ACCEPTED, payload);
    expect(update).toHaveBeenCalledExactlyOnceWith("ws", "project", "requirement", { status: 1, issue: payload });
    expect(result?.state_id).toBe("reviewed");
    expect(store.issue.state_id).toBe("reviewed");
    expect(root.issue.issues.addIssue).toHaveBeenCalledWith([expect.objectContaining({ state_id: "reviewed" })]);
    expect(root.projectRoot.project.projectMap.project.intake_count).toBe(0);
  });

  it("propagates rejected acceptance and keeps the submission and counts unchanged", async () => {
    const { store, root, update } = fixture();
    update.mockRejectedValue({ code: "WORKFLOW_REQUIRED_FIELDS_MISSING", details: { fields: ["assignees"] } });
    await expect(store.updateInboxIssueStatus(EInboxIssueStatus.ACCEPTED, { assignee_ids: [] })).rejects.toMatchObject({
      code: "WORKFLOW_REQUIRED_FIELDS_MISSING",
    });
    expect(store.status).toBe(EInboxIssueStatus.PENDING);
    expect(store.issue.state_id).toBe("pending");
    expect(root.projectRoot.project.projectMap.project.intake_count).toBe(1);
    expect(root.projectInbox.inboxIssuePaginationInfo.total_results).toBe(1);
    expect(root.issue.issues.addIssue).not.toHaveBeenCalled();
  });
});

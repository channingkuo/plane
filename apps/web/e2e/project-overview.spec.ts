/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { readFileSync } from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const fixture = JSON.parse(readFileSync("/tmp/plane-overview-browser-fixture.json", "utf8")) as {
  workspaceSlug: string;
  projectId: string;
  userId: string;
  memberId: string;
  cookieName: string;
  cookie: string;
  memberCookie: string;
  states: Record<string, string>;
};
const api = process.env.WORKFLOW_E2E_API_URL ?? "http://127.0.0.1:8127";
const projectPath = `/api/workspaces/${fixture.workspaceSlug}/projects/${fixture.projectId}/`;
const base = `/${fixture.workspaceSlug}/projects/${fixture.projectId}`;
const today = new Date().toISOString().slice(0, 10);
test.describe.configure({ mode: "serial" });

async function create(request: APIRequestContext, name: string, extra = {}) {
  const response = await request.post(api + projectPath + "issues/", { data: { name, ...extra } });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function layout(page: Page, name: string) {
  const props = await (await page.request.get(api + projectPath + "user-properties/")).json();
  const saved = await page.request.patch(api + projectPath + "user-properties/", {
    data: {
      display_filters: {
        ...props.display_filters,
        layout: name,
        group_by: ["list", "kanban"].includes(name) ? "state" : null,
        sub_group_by: null,
      },
    },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await page.goto(`${base}/issues/`);
}

test.beforeEach(async ({ context }) => {
  await context.addCookies([{ name: fixture.cookieName, value: fixture.cookie, domain: "127.0.0.1", path: "/" }]);
});

test("pending Intake, ancestor navigation, automatic test task and project overview", async ({ page }) => {
  test.setTimeout(120_000);
  const states = { ...fixture.states };
  // State sequence allocation and all subsequent workflow edits share a project lock.
  /* oxlint-disable no-await-in-loop */
  for (const name of ["Developing", "Development ready", "Testing"]) {
    const response = await page.request.post(api + projectPath + "states/", {
      data: { name, group: "started", color: "#b45309" },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    states[name] = (await response.json()).id;
  }
  const old = await (await page.request.get(api + projectPath + "workflow/")).json();
  const saved = await page.request.put(api + projectPath + "workflow/", {
    data: {
      ...old,
      enabled: true,
      task_initial_state_id: states.Ready,
      intake_submission_state_id: states.Review,
      intake_acceptance_state_id: states.Reviewed,
      requirement_completed_state_id: states.Live,
      requirement_reopen_state_id: states.Reviewed,
      test_task_initial_state_id: states.Testing,
      requirements: [],
      transitions: [
        { branch: "requirement", from_state_id: states.Reviewed, to_state_id: states.Live, roles: [20] },
        { branch: "requirement", from_state_id: states.Live, to_state_id: states.Reviewed, roles: [20] },
        { branch: "task", from_state_id: states.Ready, to_state_id: states.Developing, roles: [20, 15] },
        {
          branch: "task",
          from_state_id: states.Developing,
          to_state_id: states["Development ready"],
          roles: [20, 15],
          creates_test_task: true,
        },
      ],
    },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const root = await create(page.request, "Ancestor root");
  const middle = await create(page.request, "Ancestor middle", { parent_id: root.id });
  const child = await create(page.request, "Implementation", {
    parent_id: middle.id,
    start_date: today,
    target_date: today,
  });
  const intake = await page.request.post(api + projectPath + "intake-issues/", {
    data: { issue: { name: "Pending intake visible", start_date: today, target_date: today } },
  });
  expect(intake.ok(), await intake.text()).toBeTruthy();
  await layout(page, "kanban");
  await expect(page.getByText("Pending intake visible", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  // Ancestor identifiers are accessible links even inside a card's own link.
  const first = page.getByRole("link", { name: new RegExp(`-${root.sequence_id}$`) }).first();
  await expect(first).toBeVisible();
  await first.click();
  await expect(page).toHaveURL(new RegExp(`/browse/.*-${root.sequence_id}/`));
  await page.goto(`${base}/issues/${child.id}/`);
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Developing", exact: true }).click();
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Development ready", exact: true }).click();
  await expect
    .poll(async () => {
      const response = await page.request.get(api + projectPath + "issues/", {
        params: { work_item_scope: "all", per_page: "100" },
      });
      return (await response.json()).results.some(
        (row: { name: string }) => row.name === "【自动生成测试任务】Implementation"
      );
    })
    .toBeTruthy();
  await page.goto(`${base}/overview/`);
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByLabel("Testing: 1", { exact: true })).toBeVisible();
  await expect(page.getByText("【自动生成测试任务】Implementation", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Pending intake visible", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: "/tmp/workflow-project-overview.png", fullPage: true });
  await page.getByText("Implementation", { exact: true }).first().click();
  await expect(page.getByTestId("work-item-state-select")).toBeVisible();
});

test("members default to their assignments plus all reviews across five layouts", async ({ page, context }) => {
  test.setTimeout(120_000);
  const mine = await create(page.request, "Member assigned work", {
    assignee_ids: [fixture.memberId],
    start_date: today,
    target_date: today,
  });
  await create(page.request, "Someone else work", {
    assignee_ids: [fixture.userId],
    start_date: today,
    target_date: today,
  });
  const intake = await page.request.post(api + projectPath + "intake-issues/", {
    data: { issue: { name: "Everyone reviews this", start_date: today, target_date: today } },
  });
  expect(intake.ok()).toBeTruthy();
  const pending = await intake.json();
  const accepted = await page.request.patch(api + projectPath + `intake-issues/${pending.issue.id}/`, {
    data: { status: 1 },
  });
  expect(accepted.ok(), await accepted.text()).toBeTruthy();
  await context.addCookies([{ name: fixture.cookieName, value: fixture.memberCookie, domain: "127.0.0.1", path: "/" }]);
  /* oxlint-disable no-await-in-loop */
  for (const name of ["list", "kanban", "spreadsheet", "calendar", "gantt_chart"]) {
    await layout(page, name);
    await expect(page.getByText("Member assigned work", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Someone else work", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Everyone reviews this", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Pending intake visible", { exact: true }).first()).toBeVisible();
  }
  await page.getByRole("combobox", { name: "Work item scope", exact: true }).click();
  await page.getByRole("option", { name: "All work items", exact: true }).click();
  await expect(page.getByText("Someone else work", { exact: true }).first()).toBeVisible();
  await page.goto(`${base}/overview/`);
  await expect(page.getByText("Someone else work", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Member assigned work", { exact: true }).first()).toBeVisible();
  expect(mine.id).toBeTruthy();
});

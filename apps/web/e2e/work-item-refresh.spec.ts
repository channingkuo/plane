/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Fixture mutations use the project workflow lock and must finish in order.
/* eslint-disable no-await-in-loop */

import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const fixture = JSON.parse(
  readFileSync(process.env.WORKFLOW_E2E_FIXTURE ?? "/tmp/plane-workflow-browser-fixture.json", "utf8")
) as {
  workspaceSlug: string;
  projectId: string;
  cookieName: string;
  cookie: string;
  states: Record<string, string>;
};
const api = process.env.WORKFLOW_E2E_API_URL ?? "http://127.0.0.1:8127";
const projectPath = `/api/workspaces/${fixture.workspaceSlug}/projects/${fixture.projectId}/`;

test("workflow drag keeps the board mounted and scrolled while fetching the generated test task", async ({
  context,
  page,
}) => {
  test.setTimeout(120_000);
  await context.addCookies([{ name: fixture.cookieName, value: fixture.cookie, domain: "127.0.0.1", path: "/" }]);
  const states: string[] = [];
  for (const name of ["Refresh developing", "Refresh complete"]) {
    const response = await page.request.post(api + projectPath + "states/", {
      data: { name: `${name} ${Date.now()}`, group: "started", color: "#234567" },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    states.push((await response.json()).id);
  }
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  const configured = await page.request.put(api + projectPath + "workflow/", {
    data: {
      ...config,
      enabled: true,
      task_initial_state_id: fixture.states.Ready,
      test_task_initial_state_id: fixture.states.Reviewed,
      intake_submission_state_id: fixture.states.Review,
      intake_acceptance_state_id: fixture.states.Reviewed,
      requirement_completed_state_id: fixture.states.Live,
      requirement_reopen_state_id: fixture.states.Reviewed,
      transitions: [
        {
          branch: "requirement",
          from_state_id: fixture.states.Reviewed,
          to_state_id: fixture.states.Live,
          roles: [20],
        },
        {
          branch: "requirement",
          from_state_id: fixture.states.Live,
          to_state_id: fixture.states.Reviewed,
          roles: [20],
        },
        { branch: "task", from_state_id: fixture.states.Ready, to_state_id: states[0], roles: [20] },
        { branch: "task", from_state_id: states[0], to_state_id: states[1], roles: [20], creates_test_task: true },
      ],
      requirements: [],
    },
  });
  expect(configured.ok(), await configured.text()).toBeTruthy();
  const ids: string[] = [];
  for (const name of ["Refresh transition source", "Refresh untouched card"]) {
    const created = await page.request.post(api + projectPath + "issues/", { data: { name } });
    expect(created.ok()).toBeTruthy();
    const id = (await created.json()).id;
    ids.push(id);
    expect(
      (await page.request.patch(api + projectPath + `issues/${id}/`, { data: { state_id: states[0] } })).ok()
    ).toBeTruthy();
  }
  const props = await (await page.request.get(api + projectPath + "user-properties/")).json();
  expect(
    (
      await page.request.patch(api + projectPath + "user-properties/", {
        data: {
          display_filters: { ...props.display_filters, layout: "kanban", group_by: "state", sub_group_by: null },
        },
      })
    ).ok()
  ).toBeTruthy();
  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/`);
  const source = page.locator(`a[id^="issue_${ids[0]}_"]`).first();
  const sentinel = page.locator(`a[id^="issue_${ids[1]}_"]`).first();
  await expect(source).toBeAttached();
  const board = page.locator("div.horizontal-scrollbar.overflow-x-auto").first();
  await board.evaluate((element) => {
    element.scrollLeft = element.scrollWidth;
  });
  await expect(source).toBeInViewport();
  const boardHandle = await board.elementHandle();
  const sentinelHandle = await sentinel.elementHandle();
  const scrollLeft = await board.evaluate((element) => element.scrollLeft);
  expect(scrollLeft).toBeGreaterThan(0);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let listRequests = 0;
  await page.route(`${api}${projectPath}issues/?*`, async (route) => {
    listRequests++;
    await gate;
    await route.continue();
  });
  const navigations: string[] = [];
  page.on("request", (request) => {
    if (request.isNavigationRequest()) navigations.push(request.url());
  });
  await source.dragTo(page.locator(`[id^="${states[1]}__"]`));
  await expect.poll(() => listRequests).toBeGreaterThan(0);
  expect(await boardHandle!.evaluate((element) => element.isConnected)).toBe(true);
  expect(await sentinelHandle!.evaluate((element) => element.isConnected)).toBe(true);
  expect(await board.evaluate((element) => element.scrollLeft)).toBeCloseTo(scrollLeft, 0);
  release();
  await expect
    .poll(async () => {
      const response = await page.request.get(api + projectPath + `issues/?parent=${ids[0]}&per_page=1000`);
      const data = await response.json();
      return data.results?.some((item: { name: string }) => item.name === "测试：Refresh transition source");
    })
    .toBe(true);
  await expect(page.getByText("测试：Refresh transition source", { exact: true })).toBeAttached();
  expect(await boardHandle!.evaluate((element) => element.isConnected)).toBe(true);
  expect(await sentinelHandle!.evaluate((element) => element.isConnected)).toBe(true);
  expect(await board.evaluate((element) => element.scrollLeft)).toBeCloseTo(scrollLeft, 0);
  expect(navigations).toEqual([]);
  await page.screenshot({ path: "/tmp/work-item-background-refresh.png" });
});

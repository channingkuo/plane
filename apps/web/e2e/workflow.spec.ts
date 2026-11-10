/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const fixture = JSON.parse(
  readFileSync(process.env.WORKFLOW_E2E_FIXTURE ?? "/tmp/plane-workflow-browser-fixture.json", "utf8")
) as {
  workspaceSlug: string;
  projectId: string;
  userId: string;
  cookieName: string;
  cookie: string;
  states: Record<string, string>;
};
const api = process.env.WORKFLOW_E2E_API_URL ?? "http://127.0.0.1:8127";
const projectPath = `/api/workspaces/${fixture.workspaceSlug}/projects/${fixture.projectId}/`;
const settingsPath = `/${fixture.workspaceSlug}/settings/projects/${fixture.projectId}/workflow/`;

async function choose(page: Page, label: string, option: string, index = 0) {
  await page.getByRole("combobox", { name: label, exact: true }).nth(index).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

test.beforeEach(async ({ context, page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T12:00:00Z"));
  await context.addCookies([{ name: fixture.cookieName, value: fixture.cookie, domain: "127.0.0.1", path: "/" }]);
  const current = await (await context.request.get(api + projectPath + "workflow/")).json();
  if (current.revision > 0) {
    const reset = await context.request.put(api + projectPath + "workflow/", {
      data: {
        ...current,
        enabled: false,
        requirements: [],
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
        ],
      },
    });
    expect(reset.ok()).toBeTruthy();
  }
  // Keep repeated runs independent when later tests introduce stricter field rules.
  const listed = await (await context.request.get(api + projectPath + "issues/?per_page=1000")).json();
  const rows = Array.isArray(listed) ? listed : listed.results;
  const ids = Array.isArray(rows) ? rows.map((issue: { id: string }) => issue.id) : [];
  if (ids.length)
    expect(
      (await context.request.delete(api + projectPath + "bulk-delete-issues/", { data: { issue_ids: ids } })).ok()
    ).toBeTruthy();
});

test("configuration persists, stale saves preserve input, and child changes aggregate to the requirement", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto(settingsPath);
  await choose(page, "Task initial state", "Ready");
  await choose(page, "Intake submission state", "Review");
  await choose(page, "Intake acceptance state", "Reviewed");
  await choose(page, "Requirement completion state", "Live");
  await choose(page, "Requirement reopening state", "Reviewed");
  await page.getByRole("button", { name: "Add required administrator paths", exact: true }).click();
  await page.getByRole("button", { name: "Add transition", exact: true }).click();
  await choose(page, "To state", "Live");
  await page.getByRole("button", { name: "Add transition", exact: true }).click();
  await choose(page, "From state", "Live", 1);
  await choose(page, "To state", "Ready", 1);
  await page.getByRole("checkbox", { name: "Enable workflow", exact: true }).check();
  await page.getByRole("button", { name: "Validate", exact: true }).click();
  await expect(page.getByText("Configuration is valid.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect(page.getByText("Workflow configuration saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Enable workflow", exact: true })).toBeChecked();
  const configuration = await (await page.request.get(api + projectPath + "workflow/")).json();
  const concurrent = await page.request.put(api + projectPath + "workflow/", {
    data: { ...configuration, enabled: false },
  });
  expect(concurrent.ok()).toBeTruthy();
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Your input is preserved");
  await expect(page.getByRole("checkbox", { name: "Enable workflow", exact: true })).toBeChecked();
  await page.getByRole("button", { name: "Discard input and load latest configuration", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Enable workflow", exact: true })).not.toBeChecked();
  const latest = await (await page.request.get(api + projectPath + "workflow/")).json();
  expect(
    (await page.request.put(api + projectPath + "workflow/", { data: { ...latest, enabled: true } })).ok()
  ).toBeTruthy();

  const submission = await page.request.post(api + projectPath + "intake-issues/", {
    data: { issue: { name: "Browser requirement" } },
  });
  expect(submission.ok()).toBeTruthy();
  const submitted = await submission.json();
  const parentId = submitted.issue?.id ?? submitted.id;
  const accepted = await page.request.patch(api + projectPath + `intake-issues/${parentId}/`, { data: { status: 1 } });
  expect(accepted.ok()).toBeTruthy();
  const created = await page.request.post(api + projectPath + "issues/", {
    data: { name: "Browser child", parent_id: parentId },
  });
  expect(created.ok()).toBeTruthy();
  const childId = (await created.json()).id;

  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/${parentId}/`);
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Live", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("1");
  await expect(page.getByRole("link", { name: "Browser child", exact: true }).first()).toBeVisible();
  const parentBefore = await (await page.request.get(api + projectPath + `issues/${parentId}/`)).json();
  expect(parentBefore.state_id).toBe(fixture.states.Reviewed);

  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/${childId}/`);
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Live", exact: true }).click();
  await expect
    .poll(async () => (await (await page.request.get(api + projectPath + `issues/${parentId}/`)).json()).state_id)
    .toBe(fixture.states.Live);
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Ready", exact: true }).click();
  await expect
    .poll(async () => (await (await page.request.get(api + projectPath + `issues/${parentId}/`)).json()).state_id)
    .toBe(fixture.states.Reviewed);
});

test("missing fields are completed in one transition submission", async ({ page }) => {
  test.setTimeout(90_000);
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  const configured = await page.request.put(api + projectPath + "workflow/", {
    data: {
      ...config,
      enabled: true,
      transitions: [
        ...config.transitions,
        { branch: "task", from_state_id: fixture.states.Ready, to_state_id: fixture.states.Live, roles: [20] },
      ],
      requirements: [
        { branch: "task", state_id: fixture.states.Live, fields: ["assignees", "start_date", "target_date"] },
      ],
    },
  });
  expect(configured.ok()).toBeTruthy();
  const created = await page.request.post(api + projectPath + "issues/", { data: { name: "Browser fields task" } });
  expect(created.ok()).toBeTruthy();
  const childId = (await created.json()).id;
  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/${childId}/`);
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Live", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Complete transition fields", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("workflow-assignees").click();
  await page
    .getByRole("option", { name: /workflow/i })
    .first()
    .click();
  await page.keyboard.press("Escape");
  await dialog.getByTestId("workflow-start-date").click();
  await page.getByRole("button", { name: /November 10(th)?, 2026/ }).click();
  await dialog.getByTestId("workflow-due-date").click();
  await page.getByRole("button", { name: /November 12(th)?, 2026/ }).click();
  await dialog.getByRole("button", { name: "Apply transition", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const actual = await (await page.request.get(api + projectPath + `issues/${childId}/`)).json();
  expect(actual.state_id).toBe(fixture.states.Live);
  expect(actual.assignee_ids).toContain(fixture.userId);
  expect(actual.start_date).toBe("2026-11-10");
  expect(actual.target_date).toBe("2026-11-12");
});

test("empty requirement completion can be cancelled and explicitly confirmed", async ({ page }) => {
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  expect(
    (await page.request.put(api + projectPath + "workflow/", { data: { ...config, enabled: true } })).ok()
  ).toBeTruthy();
  const submitted = await page.request.post(api + projectPath + "intake-issues/", {
    data: { issue: { name: "Empty browser requirement" } },
  });
  expect(submitted.ok()).toBeTruthy();
  const intake = await submitted.json();
  const parentId = intake.issue?.id ?? intake.id;
  expect(
    (await page.request.patch(api + projectPath + `intake-issues/${parentId}/`, { data: { status: 1 } })).ok()
  ).toBeTruthy();
  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/${parentId}/`);
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Live", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Complete transition fields", exact: true });
  await expect(dialog).toContainText("This requirement has no child work items");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect((await (await page.request.get(api + projectPath + `issues/${parentId}/`)).json()).state_id).toBe(
    fixture.states.Reviewed
  );
  await page.getByTestId("work-item-state-select").click();
  await page.getByRole("option", { name: "Live", exact: true }).click();
  await dialog.getByRole("button", { name: "Apply transition", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect
    .poll(async () => (await (await page.request.get(api + projectPath + `issues/${parentId}/`)).json()).state_id)
    .toBe(fixture.states.Live);
});

test("a permission change while the selector is open rolls the optimistic state back", async ({ page }) => {
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  const transitions = [
    ...config.transitions,
    { branch: "task", from_state_id: fixture.states.Ready, to_state_id: fixture.states.Live, roles: [20] },
  ];
  const saved = await page.request.put(api + projectPath + "workflow/", {
    data: { ...config, enabled: true, transitions },
  });
  expect(saved.ok()).toBeTruthy();
  const current = await saved.json();
  const created = await page.request.post(api + projectPath + "issues/", { data: { name: "Browser rollback task" } });
  expect(created.ok()).toBeTruthy();
  const issueId = (await created.json()).id;
  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/${issueId}/`);
  const trigger = page.getByTestId("work-item-state-select");
  await trigger.click();
  await expect(page.getByRole("option", { name: "Live", exact: true })).toBeVisible();
  expect(
    (
      await page.request.put(api + projectPath + "workflow/", {
        data: {
          ...current,
          transitions: current.transitions.map((rule: { branch: string }) =>
            rule.branch === "task" ? Object.assign({}, rule, { roles: [15] }) : rule
          ),
        },
      })
    ).ok()
  ).toBeTruthy();
  const rejected = page.waitForResponse(
    (response) => response.url().endsWith(`/issues/${issueId}/`) && response.request().method() === "PATCH"
  );
  await page.getByRole("option", { name: "Live", exact: true }).click();
  expect((await rejected).status()).toBe(403);
  await expect(trigger).toContainText("Ready");
  expect((await (await page.request.get(api + projectPath + `issues/${issueId}/`)).json()).state_id).toBe(
    fixture.states.Ready
  );
});

test("kanban drag collects required fields before committing a transition", async ({ page }) => {
  test.setTimeout(120_000);
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  expect(
    (
      await page.request.put(api + projectPath + "workflow/", {
        data: {
          ...config,
          enabled: true,
          transitions: [
            ...config.transitions,
            { branch: "task", from_state_id: fixture.states.Ready, to_state_id: fixture.states.Live, roles: [20] },
          ],
          requirements: [{ branch: "task", state_id: fixture.states.Live, fields: ["start_date"] }],
        },
      })
    ).ok()
  ).toBeTruthy();
  const created = await page.request.post(api + projectPath + "issues/", { data: { name: "Browser drag fields" } });
  expect(created.ok()).toBeTruthy();
  const issueId = (await created.json()).id;
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
  const card = page.locator(`a[id^="issue_${issueId}_"]`).first();
  const column = page.locator(`[id^="${fixture.states.Live}__"]`);
  await expect(card).toBeVisible();
  await card.dragTo(column);
  const dialog = page.getByRole("dialog", { name: "Complete transition fields", exact: true });
  await expect(dialog).toBeVisible();
  expect((await (await page.request.get(api + projectPath + `issues/${issueId}/`)).json()).state_id).toBe(
    fixture.states.Ready
  );
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await card.dragTo(column);
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("workflow-start-date").click();
  await page.getByRole("button", { name: /November 10(th)?, 2026/ }).click();
  await dialog.getByRole("button", { name: "Apply transition", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect
    .poll(async () => (await (await page.request.get(api + projectPath + `issues/${issueId}/`)).json()).state_id)
    .toBe(fixture.states.Live);
});

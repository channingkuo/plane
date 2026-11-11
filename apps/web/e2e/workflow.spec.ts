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

test("all five layouts keep work-item creation in the page toolbar", async ({ page }) => {
  test.setTimeout(120_000);
  const created = await page.request.post(api + projectPath + "issues/", {
    data: { name: "Layout visibility task", start_date: "2026-11-10", target_date: "2026-11-12" },
  });
  expect(created.ok()).toBeTruthy();
  const issueId = (await created.json()).id;
  const props = await (await page.request.get(api + projectPath + "user-properties/")).json();
  // Each layout changes the same page and saved preference, so verification is sequential.
  /* oxlint-disable no-await-in-loop */
  for (const layout of ["list", "kanban", "swimlanes", "spreadsheet", "calendar", "gantt_chart"]) {
    expect(
      (
        await page.request.patch(api + projectPath + "user-properties/", {
          data: {
            display_filters: {
              ...props.display_filters,
              layout: layout === "swimlanes" ? "kanban" : layout,
              group_by: ["list", "kanban", "swimlanes"].includes(layout) ? "state" : null,
              sub_group_by: layout === "swimlanes" ? "priority" : null,
            },
          },
        })
      ).ok()
    ).toBeTruthy();
    await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/`);
    await expect(page.getByText("Layout visibility task", { exact: true }).first()).toBeVisible();
    expect(
      await page
        .getByText("New work item", { exact: true })
        .filter({ visible: true })
        .evaluateAll((elements) => elements.every((element) => !!element.closest('aside, [role="complementary"]')))
    ).toBe(true);
    await expect(page.getByRole("button", { name: "Add work item", exact: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Add", exact: true })).toHaveCount(0);
    if (layout === "calendar") {
      await page.getByText("12", { exact: true }).first().hover();
      await expect(page.getByText("Add work item", { exact: true })).toHaveCount(1);
    }
  }
  /* oxlint-enable no-await-in-loop */
  expect((await page.request.delete(api + projectPath + `issues/${issueId}/`)).ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("button", { name: "Add work item", exact: true })).toHaveCount(1);
  expect(
    await page
      .getByText("New work item", { exact: true })
      .filter({ visible: true })
      .evaluateAll((elements) => elements.every((element) => !!element.closest('aside, [role="complementary"]')))
  ).toBe(true);
});

test("missing task paths explain the configuration and adding a path enables dragging", async ({ page }) => {
  test.setTimeout(120_000);
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  expect(
    (
      await page.request.put(api + projectPath + "workflow/", {
        data: {
          ...config,
          enabled: true,
          requirements: [
            { branch: "task", state_id: fixture.states.Reviewed, fields: ["assignees", "start_date", "target_date"] },
          ],
        },
      })
    ).ok()
  ).toBeTruthy();
  const created = await page.request.post(api + projectPath + "issues/", {
    data: {
      name: "Configured path task",
      assignee_ids: [fixture.userId],
      start_date: "2026-11-10",
      target_date: "2026-11-12",
    },
  });
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
  const column = page.locator(`[id^="${fixture.states.Reviewed}__"]`);
  await expect(card).toBeVisible();
  await card.dragTo(column);
  await expect(page.getByRole("alert")).toContainText("No transition is configured between these states");
  expect((await (await page.request.get(api + projectPath + `issues/${issueId}/`)).json()).state_id).toBe(
    fixture.states.Ready
  );
  await page.goto(settingsPath);
  await expect(page.getByRole("status")).toContainText("This branch has no transitions");
  await page.getByRole("button", { name: "Add transition", exact: true }).click();
  await choose(page, "From state", "Ready");
  await choose(page, "To state", "Reviewed");
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect(page.getByText("Workflow configuration saved.", { exact: true })).toBeVisible();
  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/issues/`);
  await expect(card).toBeVisible();
  await card.dragTo(column);
  await expect
    .poll(async () => (await (await page.request.get(api + projectPath + `issues/${issueId}/`)).json()).state_id)
    .toBe(fixture.states.Reviewed);
});

test("Intake shows configured states and accepts form edits in one request", async ({ page }) => {
  test.setTimeout(120_000);
  const config = await (await page.request.get(api + projectPath + "workflow/")).json();
  // A non-Triage submission state reproduces the deployed UI's legacy alias bug.
  expect(
    (
      await page.request.put(api + projectPath + "workflow/", {
        data: {
          ...config,
          enabled: true,
          task_initial_state_id: fixture.states.Ready,
          intake_submission_state_id: fixture.states.Ready,
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
          ],
          requirements: [
            {
              branch: "requirement",
              state_id: fixture.states.Ready,
              fields: ["assignees", "start_date", "target_date"],
            },
          ],
        },
      })
    ).ok()
  ).toBeTruthy();
  await page.goto(`/${fixture.workspaceSlug}/projects/${fixture.projectId}/intake/`);
  await page.getByRole("button", { name: "Add work item", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create intake work item", exact: true });
  await dialog.getByPlaceholder("Title", { exact: true }).fill("Browser intake submission");
  await expect(dialog.getByText("Ready", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Create work item", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Assignees");
  await expect(dialog.getByRole("alert")).toContainText("Start date");
  await dialog.getByTestId("intake-assignees").click();
  await page
    .getByRole("option", { name: /workflow/i })
    .first()
    .click();
  // Close the picker by clicking the title: Escape is also bound to closing the whole Intake modal.
  await dialog.getByPlaceholder("Title", { exact: true }).click();
  await dialog.getByTestId("intake-start-date").click();
  await page.getByRole("button", { name: /November 10(th)?, 2026/ }).click();
  await dialog.getByTestId("intake-due-date").click();
  await page.getByRole("button", { name: /November 12(th)?, 2026/ }).click();
  const response = page.waitForResponse((r) => r.url().endsWith("/inbox-issues/") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "Create work item", exact: true }).click();
  const saved = await response;
  expect(saved.ok()).toBeTruthy();
  const body = await saved.json();
  expect(body.issue.state_id).toBe(fixture.states.Ready);
  expect(body.issue.assignee_ids).toContain(fixture.userId);
  expect(body.issue.start_date).toBe("2026-11-10");
  expect(body.issue.target_date).toBe("2026-11-12");
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText("Browser intake submission", { exact: true }).first()).toBeVisible();
  await page.locator(`a[href*="inboxIssueId=${body.issue.id}"]`).click();
  await expect(page.getByRole("button", { name: "State, Ready", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  const acceptance = page.getByRole("dialog", { name: /Move .* to project work items/ });
  await expect(acceptance.getByTestId("create-work-item-state-select")).toContainText("Reviewed");
  await expect(acceptance.getByTestId("create-work-item-state-select")).toBeDisabled();
  await acceptance.getByPlaceholder("Title", { exact: true }).fill("Browser reviewed submission");
  const updates: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "PATCH") updates.push(request.url());
  });
  const accepted = page.waitForResponse(
    (r) => r.url().includes(`/inbox-issues/${body.issue.id}/`) && r.request().method() === "PATCH"
  );
  await acceptance.getByRole("button", { name: "Add to project", exact: true }).click();
  const result = await accepted;
  expect(result.ok()).toBeTruthy();
  const reviewed = await result.json();
  expect(reviewed.status).toBe(1);
  expect(reviewed.issue.name).toBe("Browser reviewed submission");
  expect(reviewed.issue.state_id).toBe(fixture.states.Reviewed);
  await expect(acceptance).not.toBeVisible();
  await expect(page.getByText("Work item could not be updated", { exact: true })).not.toBeVisible();
  expect(updates.filter((url) => url.includes(`/inbox-issues/${body.issue.id}/`))).toHaveLength(1);
  expect(updates.filter((url) => url.includes(`/issues/${body.issue.id}/`))).toHaveLength(0);
});

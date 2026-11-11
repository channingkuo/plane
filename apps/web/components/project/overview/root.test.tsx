/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectOverview } from "./root";
import { projectOverviewService } from "@/services/project-overview.service";

const { addIssue, initGantt } = vi.hoisted(() => ({ addIssue: vi.fn(), initGantt: vi.fn() }));
vi.mock("@/lib/store-context", async () => ({
  StoreContext: (await import("react")).createContext({ issue: { issues: { addIssue } } }),
}));
vi.mock("@plane/i18n", () => ({
  useTranslation: () => ({ t: (key: string, args?: { count: number }) => `${key}${args ? ` ${args.count}` : ""}` }),
}));
vi.mock("@/hooks/use-timeline-chart", () => ({ useTimeLineChart: () => ({ initGantt }) }));
vi.mock("@/services/project-overview.service", () => ({ projectOverviewService: { overview: vi.fn() } }));
vi.mock("@/components/gantt-chart/root", () => ({
  GanttChartRoot: ({ blockIds }: { blockIds: string[] }) => <div data-testid="timeline-ids">{blockIds.join(",")}</div>,
}));
vi.mock("@/components/issues/peek-overview", () => ({ IssuePeekOverview: () => null }));
vi.mock("@/components/issues/issue-layouts/gantt/blocks", () => ({
  IssueGanttBlock: () => null,
  IssueGanttSidebarBlock: () => null,
}));

// The component creates its service once; retain the shared service mock across tests.
vi.mock("@/services/issue/issue.service", () => ({
  IssueService: vi.fn(function () {
    return { getIssues: mocks.getIssues, patchIssue: vi.fn() };
  }),
}));
const mocks = vi.hoisted(() => ({ getIssues: vi.fn() }));

function mount() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false, dedupingInterval: 0 }}>
      <ProjectOverview workspaceSlug="workspace" projectId="project" />
    </SWRConfig>
  );
}

describe("project overview", () => {
  beforeEach(() => {
    vi.mocked(projectOverviewService.overview).mockResolvedValue({
      total: 2,
      undated_count: 1,
      states: [
        { id: "review", name: "待评审", color: "#123456", group: "backlog", count: 1 },
        { id: "custom", name: "自定义状态", color: "#abc123", group: "started", count: 1 },
        { id: "done", name: "完成", color: "#abcdef", group: "completed", count: 0 },
      ],
    });
    mocks.getIssues.mockResolvedValue({ results: [{ id: "undated" }], next_page_results: false, next_cursor: "" });
  });

  it("shows custom and zero-count states and always requests the entire project", async () => {
    mount();
    expect(await screen.findByLabelText("完成: 0")).toBeTruthy();
    expect(screen.getByLabelText("自定义状态: 1")).toBeTruthy();
    expect(screen.getByText("project_overview.undated 1")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("timeline-ids").textContent).toBe("undated"));
    expect(mocks.getIssues).toHaveBeenCalledWith(
      "workspace",
      "project",
      expect.objectContaining({ work_item_scope: "all" })
    );
    expect(addIssue).toHaveBeenCalledWith([{ id: "undated" }]);
  });

  it("loads subsequent pages without dropping undated items", async () => {
    mocks.getIssues.mockImplementation(async (_slug, _project, params) => ({
      results: [{ id: params.cursor ? "next" : "undated" }],
      next_cursor: "next-cursor",
      next_page_results: !params.cursor,
    }));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "project_overview.load_more" }));
    await waitFor(() => expect(screen.getByTestId("timeline-ids").textContent).toBe("undated,next"));
    expect(mocks.getIssues).toHaveBeenCalledWith(
      "workspace",
      "project",
      expect.objectContaining({ cursor: "next-cursor", work_item_scope: "all" })
    );
  });

  it("reports a load failure and supports retry", async () => {
    vi.mocked(projectOverviewService.overview).mockRejectedValueOnce(new Error("offline"));
    mount();
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "project_overview.retry" }));
    expect(await screen.findByText("待评审")).toBeTruthy();
  });
});

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IGanttBlock } from "@plane/types";
import { IssueGanttSidebar } from "./sidebar";

const mocks = vi.hoisted(() => ({
  blocks: {} as Record<string, IGanttBlock>,
  activeId: null as string | null,
  updateActiveBlockId: vi.fn(),
}));
vi.mock("@plane/i18n", () => ({
  useTranslation: () => ({ t: (_key: string, { days }: { days: number }) => `${days} 天` }),
}));
vi.mock("@/hooks/use-timeline-chart", () => {
  const store = {
    getBlockById: (id: string) => mocks.blocks[id],
    isBlockActive: (id: string) => mocks.activeId === id,
    updateActiveBlockId: mocks.updateActiveBlockId,
    getNumberOfDaysFromPosition: (width?: number) => (width === undefined ? undefined : Math.round(width / 20)),
  };
  return { useTimeLineChart: () => store, useTimeLineChartStore: () => store };
});
vi.mock("@/hooks/store/use-issue-detail", () => ({ useIssueDetail: () => ({ getIsIssuePeeked: () => false }) }));
vi.mock("@/hooks/use-issue-layout-store", () => ({
  useIssuesStore: () => ({ issues: { getIssueLoader: () => false } }),
}));
vi.mock("@/components/core/multiple-select", () => ({ MultipleSelectEntityAction: () => null }));
vi.mock("@/components/core/render-if-visible-HOC", () => ({
  default: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("../gantt-dnd-HOC", () => ({
  GanttDnDHOC: ({ children }: { children: (isDragging: boolean) => ReactNode }) => children(false),
}));
vi.mock("@/components/issues/issue-layouts/gantt/blocks", () => ({
  IssueGanttSidebarBlock: ({ issueId }: { issueId: string }) => <span>{issueId}</span>,
}));

function block(id: string, dates: Partial<IGanttBlock> = {}): IGanttBlock {
  return {
    id,
    name: id,
    data: { id },
    sort_order: undefined,
    start_date: undefined,
    target_date: undefined,
    ...dates,
  };
}

const props = {
  blockUpdateHandler: vi.fn(),
  ganttContainerRef: { current: null },
  enableReorder: false,
  enableSelection: false,
  showAllBlocks: true,
};

function row(id: string) {
  return screen.getByText(id).closest(".group\\/list-block")!;
}

describe("work item timeline sidebar", () => {
  beforeEach(() => {
    mocks.activeId = null;
    mocks.updateActiveBlockId.mockClear();
    mocks.blocks = {
      undated: block("undated"),
      scheduled: block("scheduled", {
        start_date: "2026-11-11",
        target_date: "2026-11-13",
        position: { marginLeft: 0, width: 60 },
      }),
      "same-day": block("same-day", {
        start_date: "2026-11-11",
        target_date: "2026-11-11",
        position: { marginLeft: 0, width: 20 },
      }),
      "start-only": block("start-only", { start_date: "2026-11-11", position: { marginLeft: 0, width: 20 } }),
      "end-only": block("end-only", { target_date: "2026-11-13", position: { marginLeft: 0, width: 20 } }),
    };
  });

  it("alternates row backgrounds in display order, including undated items and newly loaded rows", () => {
    const { rerender } = render(<IssueGanttSidebar {...props} blockIds={["undated", "scheduled"]} />);
    expect(row("undated").classList.contains("bg-surface-1")).toBe(true);
    expect(row("scheduled").classList.contains("bg-layer-1")).toBe(true);

    rerender(<IssueGanttSidebar {...props} blockIds={["scheduled", "undated", "same-day"]} />);
    expect(row("scheduled").classList.contains("bg-surface-1")).toBe(true);
    expect(row("undated").classList.contains("bg-layer-1")).toBe(true);
    expect(row("same-day").classList.contains("bg-surface-1")).toBe(true);
  });

  it("shows localized inclusive durations only when both dates exist and updates after resizing", () => {
    const { rerender } = render(<IssueGanttSidebar {...props} blockIds={Object.keys(mocks.blocks)} />);
    expect(screen.getByText("3 天")).toBeTruthy();
    expect(screen.getByText("1 天")).toBeTruthy();
    for (const id of ["undated", "start-only", "end-only"]) {
      expect(row(id).textContent).toBe(id);
    }
    mocks.blocks.scheduled = { ...mocks.blocks.scheduled, position: { marginLeft: 0, width: 100 } };
    rerender(<IssueGanttSidebar {...props} blockIds={Object.keys(mocks.blocks)} />);
    expect(screen.getByText("5 天")).toBeTruthy();
    expect(screen.queryByText("3 天")).toBeNull();
  });

  it("keeps hover and selection highlights above the striped background", () => {
    mocks.activeId = "scheduled";
    mocks.blocks.scheduled.data.is_workflow_requirement = true;
    const selectionHelpers = {
      getIsEntitySelected: (id: string) => id === "scheduled",
      getIsEntityActive: () => false,
    } as unknown as NonNullable<Parameters<typeof IssueGanttSidebar>[0]["selectionHelpers"]>;
    render(
      <IssueGanttSidebar
        {...props}
        highlightRequirements
        selectionHelpers={selectionHelpers}
        blockIds={["undated", "scheduled"]}
      />
    );
    expect(row("scheduled").classList.contains("bg-accent-subtle")).toBe(true);
    expect(row("scheduled").firstElementChild?.classList.contains("bg-accent-primary/10")).toBe(true);
    fireEvent.mouseEnter(row("scheduled"));
    expect(mocks.updateActiveBlockId).toHaveBeenCalledWith("scheduled");
    fireEvent.mouseLeave(row("scheduled"));
    expect(mocks.updateActiveBlockId).toHaveBeenLastCalledWith(null);
  });

  it("highlights consecutive reviewed requirements while leaving development and test rows striped", () => {
    mocks.blocks.undated.data.is_workflow_requirement = true;
    mocks.blocks.scheduled.data.is_workflow_requirement = true;
    mocks.blocks["same-day"].data.is_workflow_requirement = false;
    render(
      <IssueGanttSidebar
        {...props}
        highlightRequirements
        blockIds={["undated", "scheduled", "same-day", "start-only"]}
      />
    );
    expect(row("undated").classList.contains("bg-accent-subtle")).toBe(true);
    expect(row("scheduled").classList.contains("bg-accent-subtle")).toBe(true);
    expect(row("same-day").classList.contains("bg-surface-1")).toBe(true);
    expect(row("start-only").classList.contains("bg-layer-1")).toBe(true);
  });

  it("uses normal stripes for other sorting modes even when requirement metadata is cached", () => {
    mocks.blocks.undated.data.is_workflow_requirement = true;
    render(<IssueGanttSidebar {...props} blockIds={["undated"]} />);
    expect(row("undated").classList.contains("bg-accent-subtle")).toBe(false);
    expect(row("undated").classList.contains("bg-surface-1")).toBe(true);
  });
});

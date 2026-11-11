/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { IssueIdentifier } from "./issue-identifier";
import { ancestorPath } from "./ancestor-path";
import { projectOverviewService } from "@/services/project-overview.service";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: () => ({ workspaceSlug: "workspace" }), useRouter: () => ({ push }) }));
vi.mock("@/hooks/store/use-project", () => ({ useProject: () => ({ getProjectIdentifierById: () => "TEST" }) }));
vi.mock("@/hooks/store/use-issue-detail", () => ({
  useIssueDetail: () => ({ issue: { getIssueById: () => ({ id: "child", sequence_id: 10, parent_id: "middle" }) } }),
}));
vi.mock("@/services/project-overview.service", () => ({ projectOverviewService: { hierarchy: vi.fn() } }));

const hierarchy = {
  root: { id: "root", parent_id: null, sequence_id: 1, project_id: "project" },
  middle: { id: "middle", parent_id: "root", sequence_id: 7, project_id: "project" },
  child: { id: "child", parent_id: "middle", sequence_id: 10, project_id: "project" },
};

describe("work item ancestor identifiers", () => {
  it("links every ancestor and opens it without opening the containing card", async () => {
    vi.mocked(projectOverviewService.hierarchy).mockResolvedValue(hierarchy);
    const cardClick = vi.fn();
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <a href="/current" onClick={cardClick}>
          <IssueIdentifier issueId="child" projectId="project" />
        </a>
      </SWRConfig>
    );
    fireEvent.click(await screen.findByRole("link", { name: "TEST-1" }));
    expect(push).toHaveBeenLastCalledWith("/workspace/projects/project/issues/root/");
    fireEvent.keyDown(screen.getByRole("link", { name: "TEST-7" }), { key: "Enter" });
    expect(push).toHaveBeenLastCalledWith("/workspace/projects/project/issues/middle/");
    expect(cardClick).not.toHaveBeenCalled();
    expect(screen.getByText("TEST-10").parentElement?.className).toContain("11px");
    expect(screen.getByText("TEST-1").className).not.toContain("11px");
  });

  it("uses project hierarchy even when ancestors are outside the current page", () => {
    expect(ancestorPath(hierarchy, "child").map((row) => row.sequence_id)).toEqual([1, 7]);
    expect(ancestorPath(hierarchy, "middle").map((row) => row.sequence_id)).toEqual([1]);
    expect(ancestorPath(hierarchy, "root")).toEqual([]);
  });

  it("stops at inaccessible, missing or cyclic ancestors", () => {
    expect(ancestorPath({ child: hierarchy.child }, "child")).toEqual([]);
    expect(ancestorPath({ ...hierarchy, root: { ...hierarchy.root, parent_id: "child" } }, "child")).toHaveLength(2);
  });
});

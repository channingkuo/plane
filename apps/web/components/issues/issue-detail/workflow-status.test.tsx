/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { TIssueWorkflow } from "@plane/types";
import { WorkflowStatus } from "./workflow-status";

const state = vi.hoisted(() => ({ data: {} as TIssueWorkflow }));
vi.mock("swr", () => ({ default: () => ({ data: state.data, mutate: vi.fn() }) }));
vi.mock("mobx-react", () => ({ observer: (component: unknown) => component }));
vi.mock("@plane/i18n", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/hooks/store/use-project-state", () => ({ useProjectState: () => ({ getStateById: () => undefined }) }));
vi.mock("@/services/workflow.service", () => ({ workflowService: {} }));

const renderStatus = () => render(<WorkflowStatus workspaceSlug="workspace" projectId="project" issueId="source" />);

describe("workflow completion status", () => {
  it("shows a development source's missing completion fields", () => {
    state.data = {
      enabled: true,
      branch: "task",
      blocked_reason: { code: "WORKFLOW_REQUIRED_FIELDS_MISSING", details: { fields: ["target_date"] } },
    };
    renderStatus();
    expect(screen.getByRole("status").textContent).toContain("project_settings.workflow.target_date");
    expect(screen.queryByText(/requirement_completed_state_id/)).toBeNull();
  });

  it("keeps reviewed requirement progress visible", () => {
    state.data = {
      enabled: true,
      branch: "requirement",
      reviewed: true,
      completed_descendant_count: 2,
      descendant_count: 3,
    };
    renderStatus();
    expect(screen.getByRole("status").textContent).toContain("2/3");
  });

  it("hides the panel once the source is no longer blocked", () => {
    state.data = { enabled: true, branch: "task", blocked_reason: {} };
    const { rerender } = renderStatus();
    expect(screen.queryByRole("status")).toBeNull();
    state.data = { enabled: false };
    rerender(<WorkflowStatus workspaceSlug="workspace" projectId="project" issueId="source" />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectWorkflowSettings } from "./workflow";
import { workflowService } from "@/services/workflow.service";
import type { TProjectWorkflow } from "@plane/types";

vi.mock("@plane/i18n", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/services/workflow.service", () => ({
  workflowService: { configuration: vi.fn(), save: vi.fn(), validate: vi.fn() },
}));

const initial: TProjectWorkflow = {
  enabled: false,
  revision: 1,
  task_initial_state_id: "todo",
  intake_submission_state_id: "review",
  intake_acceptance_state_id: "doing",
  requirement_completed_state_id: "done",
  requirement_reopen_state_id: "doing",
  transitions: [],
  requirements: [],
  states: [
    { id: "todo", name: "Todo", group: "unstarted", color: "#000000" },
    { id: "review", name: "Review", group: "triage", color: "#000000" },
    { id: "doing", name: "Doing", group: "started", color: "#000000" },
    { id: "done", name: "Done", group: "completed", color: "#000000" },
  ],
};
const key = (label: string) => `project_settings.workflow.${label}`;
const mount = () =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <ProjectWorkflowSettings workspaceSlug="workspace" projectId="project" />
    </SWRConfig>
  );

describe("project workflow configuration", () => {
  beforeEach(() => {
    vi.mocked(workflowService.configuration).mockResolvedValue(structuredClone(initial));
    vi.mocked(workflowService.save).mockImplementation(async (_slug, _project, draft) => ({
      ...draft,
      revision: draft.revision + 1,
    }));
    vi.mocked(workflowService.validate).mockResolvedValue({ valid: true });
  });

  it("saves the entire edited configuration and required administrator paths", async () => {
    mount();
    const enabled = await screen.findByRole("checkbox", { name: key("enabled") });
    fireEvent.click(enabled);
    fireEvent.click(screen.getByRole("button", { name: key("required_paths") }));
    fireEvent.click(screen.getByRole("button", { name: key("save") }));
    await waitFor(() => expect(workflowService.save).toHaveBeenCalled());
    const draft = vi.mocked(workflowService.save).mock.calls.at(-1)![2];
    expect(draft.enabled).toBe(true);
    expect(draft.transitions).toHaveLength(2);
    expect(draft.transitions.every((rule) => rule.branch === "requirement" && rule.roles.includes(20))).toBe(true);
    expect(initial.transitions).toHaveLength(0);
    await screen.findByText(key("saved"));
  });

  it("keeps input after a revision conflict", async () => {
    vi.mocked(workflowService.save).mockRejectedValue({ response: { status: 409 } });
    mount();
    const enabled = await screen.findByRole("checkbox", { name: key("enabled") });
    fireEvent.click(enabled);
    fireEvent.click(screen.getByRole("button", { name: key("save") }));
    await screen.findByRole("alert");
    expect(enabled.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(key("conflict"))).toBeTruthy();
  });

  it("validates without saving", async () => {
    vi.mocked(workflowService.save).mockClear();
    mount();
    fireEvent.click(await screen.findByRole("button", { name: key("validate") }));
    await screen.findByText(key("valid"));
    expect(workflowService.validate).toHaveBeenCalledWith(
      "workspace",
      "project",
      expect.objectContaining({ revision: 1 })
    );
    expect(workflowService.save).not.toHaveBeenCalled();
  });

  it("does not expose an editor after a configuration permission failure", async () => {
    vi.mocked(workflowService.configuration).mockRejectedValue({ response: { status: 403 } });
    mount();
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: key("save") })).toBeNull();
  });
});

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StateSelect } from "./state-select";
import { IntakeStateSelect } from "../intake-state/intake-state-select";
import { workflowService } from "@/services/workflow.service";

const { states } = vi.hoisted(() => ({
  states: {
    todo: { id: "todo", name: "Todo", group: "unstarted", color: "#000000" },
    pending: { id: "pending", name: "Pending review", group: "backlog", color: "#000000" },
    reviewed: { id: "reviewed", name: "Reviewed", group: "started", color: "#000000" },
    triage: { id: "triage", name: "Triage", group: "triage", color: "#000000" },
  } as const,
}));

vi.mock("mobx-react", () => ({ observer: (component: unknown) => component }));
vi.mock("next/navigation", () => ({ useParams: () => ({ workspaceSlug: "ws" }) }));
vi.mock("@plane/i18n", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/services/workflow.service", () => ({ workflowService: { creation: vi.fn() } }));
vi.mock("./workflow-dialog", () => ({ WorkflowFieldsDialog: () => null }));
vi.mock("@/hooks/store/use-project-state", () => ({
  useProjectState: () => ({
    getStateById: (id: keyof typeof states) => (states[id]?.group !== "triage" ? states[id] : undefined),
    getIntakeStateById: (id: string) => (id === "triage" ? states.triage : undefined),
    getProjectIntakeState: () => states.triage,
  }),
}));
function MockSelect({ value, disabled }: { value?: { name: string }; disabled?: boolean }) {
  return (
    <button type="button" disabled={disabled}>
      {value?.name}
    </button>
  );
}
vi.mock("@plane/blocks/property-select", () => ({ StateSelect: MockSelect, IntakeStateSelect: MockSelect }));

function Form({ intake = false }: { intake?: boolean }) {
  const [value, setValue] = useState("pending");
  return (
    <StateSelect
      projectId="project"
      value={value}
      onChange={setValue}
      variant="pill-md"
      isForWorkItemCreation
      isIntakeAcceptance={intake}
    />
  );
}

describe("Intake workflow states", () => {
  beforeEach(() =>
    vi.mocked(workflowService.creation).mockResolvedValue({
      enabled: true,
      initial_state_id: "todo",
      fields: ["assignees"],
      intake: { state: states.pending, fields: [], acceptance: { state: states.reviewed, fields: ["target_date"] } },
    })
  );

  it("shows the issue's actual non-Triage state in Intake details", () => {
    render(<IntakeStateSelect workspaceSlug="ws" projectId="project" value="pending" onChange={() => {}} disabled />);
    expect(screen.getByRole("button", { name: "Pending review" })).toBeTruthy();
    expect(screen.queryByText("Triage")).toBeNull();
  });

  it.each([
    [true, "Reviewed", "target_date"],
    [false, "Todo", "assignees"],
  ] as const)(
    "uses the correct initial state and required fields (Intake acceptance: %s)",
    async (intake, name, field) => {
      render(
        <SWRConfig value={{ provider: () => new Map() }}>
          <Form intake={intake} />
        </SWRConfig>
      );
      await waitFor(() => expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true));
      expect(screen.getByText(new RegExp(`project_settings.workflow.${field}`))).toBeTruthy();
    }
  );
});

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { FormEvent, MouseEvent } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowFieldsDialog } from "./workflow-dialog";
import { requestWorkflowFields, WorkflowFieldsHost } from "./workflow-fields-host";

vi.mock("@plane/i18n", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

function MockDateSelect(props: { testId: string; value: Date | null; onChange: (date: Date | null) => void }) {
  return (
    <input
      type="date"
      data-testid={props.testId}
      value={props.value?.toISOString().slice(0, 10) ?? ""}
      onChange={(event) => props.onChange(event.target.value ? new Date(`${event.target.value}T12:00:00Z`) : null)}
    />
  );
}
function MockMemberSelect(props: { value: string[]; onChange: (ids: string[]) => void }) {
  return (
    <button type="button" onClick={() => props.onChange(["member"])}>
      {props.value.join(",") || "Choose assignee"}
    </button>
  );
}
vi.mock("@plane/blocks/property-select", () => ({ DateSelect: MockDateSelect }));
vi.mock("@/components/dropdowns/member/member-select", () => ({ MemberSelect: MockMemberSelect }));

const label = (key: string) => `project_settings.workflow.${key}`;
afterEach(() => vi.unstubAllGlobals());

describe("workflow field submission", () => {
  it("submits from a card portal without bubbling clicks or submits to the card", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    const parentClick = vi.fn((event: MouseEvent) => event.preventDefault());
    const parentSubmit = vi.fn((event: FormEvent) => event.preventDefault());
    render(
      <form onSubmit={parentSubmit}>
        <a href="/work-item" onClick={parentClick}>
          <WorkflowFieldsDialog
            projectId="project"
            initial={{}}
            emptyRequirement={false}
            onSave={onSave}
            onClose={onClose}
          />
        </a>
      </form>
    );
    fireEvent.click(screen.getByRole("button", { name: "Choose assignee" }));
    fireEvent.change(screen.getByTestId("workflow-start-date"), { target: { value: "2026-11-11" } });
    fireEvent.change(screen.getByTestId("workflow-due-date"), { target: { value: "2026-11-17" } });
    fireEvent.click(screen.getByRole("button", { name: label("apply_transition") }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledExactlyOnceWith({
        assignee_ids: ["member"],
        start_date: "2026-11-11",
        target_date: "2026-11-17",
      })
    );
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(parentClick).not.toHaveBeenCalled();
    expect(parentSubmit).not.toHaveBeenCalled();
  });

  it("retains fields and reports a server rejection before allowing retry", async () => {
    const onSave = vi
      .fn()
      .mockRejectedValueOnce({ code: "WORKFLOW_REQUIRED_FIELDS_MISSING", details: { fields: ["assignees"] } })
      .mockResolvedValueOnce(undefined);
    const onClose = vi.fn();
    render(
      <WorkflowFieldsDialog
        projectId="project"
        initial={{ start_date: "2026-11-11", target_date: "2026-11-17" }}
        emptyRequirement={false}
        onSave={onSave}
        onClose={onClose}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: label("apply_transition") }));
    expect((await screen.findByRole("alert")).textContent).toContain(label("assignees"));
    expect((screen.getByTestId("workflow-start-date") as HTMLInputElement).value).toBe("2026-11-11");
    expect((screen.getByTestId("workflow-due-date") as HTMLInputElement).value).toBe("2026-11-17");
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Choose assignee" }));
    fireEvent.click(screen.getByRole("button", { name: label("apply_transition") }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onSave).toHaveBeenLastCalledWith({
      assignee_ids: ["member"],
      start_date: "2026-11-11",
      target_date: "2026-11-17",
    });
  });

  it("processes distinct queued drag dialogs without crypto.randomUUID", async () => {
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    render(<WorkflowFieldsHost />);
    const saveFirst = vi.fn().mockResolvedValue(undefined);
    const saveSecond = vi.fn().mockResolvedValue(undefined);
    let first!: Promise<void>;
    let second!: Promise<void>;
    await act(async () => {
      first = requestWorkflowFields({
        projectId: "project",
        initial: { start_date: "2026-11-11" },
        emptyRequirement: false,
        save: saveFirst,
      });
      second = requestWorkflowFields({
        projectId: "project",
        initial: { start_date: "2026-11-12" },
        emptyRequirement: false,
        save: saveSecond,
      });
    });
    expect((screen.getByTestId("workflow-start-date") as HTMLInputElement).value).toBe("2026-11-11");
    fireEvent.click(screen.getByRole("button", { name: label("cancel") }));
    await first;
    expect(saveFirst).not.toHaveBeenCalled();
    expect((screen.getByTestId("workflow-start-date") as HTMLInputElement).value).toBe("2026-11-12");
    fireEvent.click(screen.getByRole("button", { name: label("apply_transition") }));
    await act(async () => {
      await second;
    });
    expect(saveSecond).toHaveBeenCalledExactlyOnceWith({
      assignee_ids: [],
      start_date: "2026-11-12",
      target_date: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("rejects a request when no field editor is mounted", async () => {
    await expect(
      requestWorkflowFields({ projectId: "project", initial: {}, emptyRequirement: false, save: vi.fn() })
    ).rejects.toThrow("Workflow field editor is unavailable");
  });
});

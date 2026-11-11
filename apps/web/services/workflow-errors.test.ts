/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { describe, expect, it } from "vitest";
import { getWorkflowErrorMessage } from "./workflow-errors";

const t = (key: string) => key.split(".").at(-1)!;
describe("workflow save errors", () => {
  it("explains missing paths for Axios and response body errors", () => {
    const error = { code: "WORKFLOW_TRANSITION_NOT_ALLOWED", message: "generic" };
    expect(getWorkflowErrorMessage(error, t)).toBe("transition_not_configured");
    expect(getWorkflowErrorMessage({ response: { data: error } }, t)).toBe("transition_not_configured");
  });
  it("names the missing fields", () => {
    expect(
      getWorkflowErrorMessage(
        { code: "WORKFLOW_REQUIRED_FIELDS_MISSING", details: { fields: ["assignees", "start_date"] } },
        t
      )
    ).toBe("required: assignees, start_date");
  });
  it("preserves server permission messages and serializer validation", () => {
    expect(getWorkflowErrorMessage({ message: "Only project administrators may accept" }, t)).toContain("Only project");
    expect(getWorkflowErrorMessage({ issue: { name: ["This field is required."] } }, t)).toBe(
      "This field is required."
    );
    expect(getWorkflowErrorMessage(undefined, t)).toBe("save_error");
  });
});

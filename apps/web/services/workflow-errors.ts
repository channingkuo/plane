/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TWorkflowField } from "@plane/types";

const messages = (value: unknown): string[] => {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(messages);
  if (value && typeof value === "object") return Object.values(value).flatMap(messages);
  return [];
};

/** Services may throw either an Axios error or the response body itself. */
export function getWorkflowErrorMessage(error: unknown, translate: (key: string) => string): string {
  const raw = error as { response?: { data?: unknown } } | null;
  const body = (raw?.response?.data ?? error) as {
    code?: string;
    message?: unknown;
    detail?: unknown;
    details?: { fields?: string[] };
  } | null;
  if (body?.code === "WORKFLOW_TRANSITION_NOT_ALLOWED")
    return translate("project_settings.workflow.transition_not_configured");
  if (body?.code === "WORKFLOW_REQUIRED_FIELDS_MISSING") {
    const fields = body.details?.fields?.filter((field): field is TWorkflowField =>
      ["assignees", "start_date", "target_date"].includes(field)
    );
    if (fields?.length)
      return `${translate("project_settings.workflow.required")}: ${fields.map((field) => translate(`project_settings.workflow.${field}`)).join(", ")}`;
  }
  if (typeof body?.message === "string") return body.message;
  if (typeof body?.detail === "string") return body.detail;
  // DRF validation errors are field-to-message mappings, sometimes nested under issue.
  return messages(body).join("; ") || translate("project_settings.workflow.save_error");
}

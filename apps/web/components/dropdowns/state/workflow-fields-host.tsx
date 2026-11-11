/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import type { TIssue } from "@plane/types";
import { WorkflowFieldsDialog } from "./workflow-dialog";

const eventName = "plane:workflow-fields-request";
// These keys identify dialogs within this page, so no secure-context crypto API is needed.
let requestSequence = 0;
type Request = {
  key: string;
  projectId: string;
  initial: Partial<TIssue>;
  emptyRequirement: boolean;
  save: (patch: Partial<TIssue>) => Promise<void>;
  finish: () => void;
  handled: boolean;
};

/** A drag/drop action can collect fields without coupling stores to React. */
export function requestWorkflowFields(request: Omit<Request, "key" | "finish" | "handled">): Promise<void> {
  return new Promise((resolve, reject) => {
    const detail: Request = {
      ...request,
      key: `workflow-fields-${++requestSequence}`,
      finish: resolve,
      handled: false,
    };
    window.dispatchEvent(new CustomEvent(eventName, { detail }));
    if (!detail.handled) reject(new Error("Workflow field editor is unavailable"));
  });
}

export function WorkflowFieldsHost() {
  const [queue, setQueue] = useState<Request[]>([]);
  useEffect(() => {
    const receive = (event: Event) => {
      const request = (event as CustomEvent<Request>).detail;
      if (request.handled) return;
      request.handled = true;
      setQueue((current) => [...current, request]);
    };
    window.addEventListener(eventName, receive);
    return () => window.removeEventListener(eventName, receive);
  }, []);
  const request = queue[0];
  if (!request) return null;
  return (
    <WorkflowFieldsDialog
      key={request.key}
      projectId={request.projectId}
      initial={request.initial}
      emptyRequirement={request.emptyRequirement}
      onSave={request.save}
      onClose={() => {
        request.finish();
        setQueue((current) => current.slice(1));
      }}
    />
  );
}

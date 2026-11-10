/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useEffect } from "react";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { useProjectState } from "@/hooks/store/use-project-state";
import { workflowService } from "@/services/workflow.service";

export const WorkflowStatus = observer(function WorkflowStatus({
  workspaceSlug,
  projectId,
  issueId,
}: {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
}) {
  const { t } = useTranslation();
  const { getStateById } = useProjectState();
  const { data, mutate } = useSWR(["issue-workflow", workspaceSlug, projectId, issueId], () =>
    workflowService.issue(workspaceSlug, projectId, issueId)
  );
  useEffect(() => {
    const refresh = () => void mutate();
    window.addEventListener("plane:workflow-updated", refresh);
    return () => window.removeEventListener("plane:workflow-updated", refresh);
  }, [mutate]);
  if (!data?.enabled || data.branch !== "requirement" || !data.reviewed) return null;
  return (
    <div className="my-2 rounded border border-subtle px-3 py-2 text-body-sm-regular" role="status">
      <p>
        {t("project_settings.workflow.progress")}: {data.completed_descendant_count ?? 0}/{data.descendant_count ?? 0}
      </p>
      <p>
        {t("project_settings.workflow.requirement_completed_state_id")}:{" "}
        {getStateById(data.completion_state_id ?? "")?.name ?? "—"}
      </p>
      <p>
        {t("project_settings.workflow.requirement_reopen_state_id")}:{" "}
        {getStateById(data.reopen_state_id ?? "")?.name ?? "—"}
      </p>
      {data.blocked_reason?.code && (
        <p>
          {t("project_settings.workflow.blocked")}{" "}
          {data.blocked_reason.details?.fields?.map((field) => t(`project_settings.workflow.${field}`)).join(", ")}
        </p>
      )}
    </div>
  );
});

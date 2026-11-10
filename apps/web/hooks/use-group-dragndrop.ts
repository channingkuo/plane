/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import { workflowService } from "@/services/workflow.service";
import { requestWorkflowFields } from "@/components/dropdowns/state/workflow-fields-host";
import { useProjectState } from "./store/use-project-state";
import { useParams } from "next/navigation";
import { setToast } from "@plane/blocks/toast";
import type { SetToastProps } from "@plane/blocks/toast";
import type { EIssuesStoreType, TIssue, TIssueGroupByOptions, TIssueOrderByOptions } from "@plane/types";
import type { GroupDropLocation } from "@/components/issues/issue-layouts/utils";
import { handleGroupDragDrop } from "@/components/issues/issue-layouts/utils";
import { ISSUE_FILTER_DEFAULT_DATA } from "@/store/issue/helpers/base-issues.store";
import { useIssueDetail } from "./store/use-issue-detail";
import { useIssues } from "./store/use-issues";
import { useIssuesActions } from "./use-issues-actions";

type DNDStoreType =
  | EIssuesStoreType.PROJECT
  | EIssuesStoreType.MODULE
  | EIssuesStoreType.CYCLE
  | EIssuesStoreType.PROJECT_VIEW
  | EIssuesStoreType.PROFILE
  | EIssuesStoreType.ARCHIVED
  | EIssuesStoreType.WORKSPACE_DRAFT
  | EIssuesStoreType.TEAM
  | EIssuesStoreType.TEAM_VIEW
  | EIssuesStoreType.EPIC
  | EIssuesStoreType.TEAM_PROJECT_WORK_ITEMS;

export const useGroupIssuesDragNDrop = (
  storeType: DNDStoreType,
  orderBy: TIssueOrderByOptions | undefined,
  groupBy: TIssueGroupByOptions | undefined,
  subGroupBy?: TIssueGroupByOptions
) => {
  const { workspaceSlug } = useParams();
  const { t } = useTranslation();
  const { getStateById } = useProjectState();

  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const { updateIssue } = useIssuesActions(storeType);
  const {
    issues: { getIssueIds, addCycleToIssue, removeCycleFromIssue, changeModulesInIssue },
  } = useIssues(storeType);

  /**
   * update Issue on Drop, checks if modules or cycles are changed and then calls appropriate functions
   * @param projectId
   * @param issueId
   * @param data
   * @param issueUpdates
   */
  const updateIssueOnDrop = async (
    projectId: string,
    issueId: string,
    data: Partial<TIssue>,
    issueUpdates: {
      [groupKey: string]: {
        ADD: string[];
        REMOVE: string[];
      };
    },
    confirmed = false
  ): Promise<void> => {
    if (data.state_id && data.state_id !== getIssueById(issueId)?.state_id && workspaceSlug) {
      const rules = await workflowService.preview(String(workspaceSlug), projectId, issueId, data);
      if (rules.enabled) {
        const target = rules.targets?.find((item) => item.state_id === data.state_id);
        const repairable = [
          "WORKFLOW_REQUIRED_FIELDS_MISSING",
          "WORKFLOW_INVALID_ASSIGNEE",
          "WORKFLOW_INVALID_DATE",
        ].includes(target?.reason?.code ?? "");
        const emptyRequirement =
          rules.branch === "requirement" &&
          rules.descendant_count === 0 &&
          getStateById(data.state_id)?.group === "completed";
        if (!confirmed && (repairable || emptyRequirement)) {
          await requestWorkflowFields({
            projectId,
            initial: { ...getIssueById(issueId), ...data },
            emptyRequirement,
            save: async (fields) => updateIssueOnDrop(projectId, issueId, { ...data, ...fields }, issueUpdates, true),
          });
          return;
        }
        if (data.state_id !== getIssueById(issueId)?.state_id && (!target || !target.allowed)) {
          throw target?.reason ?? { message: t("project_settings.workflow.save_error") };
        }
      }
    }
    const errorToastProps: SetToastProps = {
      type: "error",
      title: "Error!",
      message: "Error while updating work item",
    };
    const moduleKey = ISSUE_FILTER_DEFAULT_DATA["module"];
    const cycleKey = ISSUE_FILTER_DEFAULT_DATA["cycle"];

    const isModuleChanged = Object.keys(data).includes(moduleKey);
    const isCycleChanged = Object.keys(data).includes(cycleKey);

    if (isCycleChanged && workspaceSlug) {
      if (data[cycleKey]) {
        addCycleToIssue(workspaceSlug.toString(), projectId, data[cycleKey]?.toString() ?? "", issueId).catch(() =>
          setToast(errorToastProps)
        );
      } else {
        removeCycleFromIssue(workspaceSlug.toString(), projectId, issueId).catch(() => setToast(errorToastProps));
      }
      delete data[cycleKey];
    }

    if (isModuleChanged && workspaceSlug && issueUpdates[moduleKey]) {
      changeModulesInIssue(
        workspaceSlug.toString(),
        projectId,
        issueId,
        issueUpdates[moduleKey].ADD,
        issueUpdates[moduleKey].REMOVE
      ).catch(() => setToast(errorToastProps));
      delete data[moduleKey];
    }

    if (updateIssue) await updateIssue(projectId, issueId, data);
  };

  const handleOnDrop = async (source: GroupDropLocation, destination: GroupDropLocation) => {
    if (
      source.columnId &&
      destination.columnId &&
      destination.columnId === source.columnId &&
      destination.id === source.id
    )
      return;

    await handleGroupDragDrop(
      source,
      destination,
      getIssueById,
      getIssueIds,
      updateIssueOnDrop,
      groupBy,
      subGroupBy,
      orderBy !== "sort_order"
    ).catch((err) => {
      setToast({
        title: "Error!",
        type: "error",
        message: err?.message ?? err?.detail ?? "Failed to perform this action",
      });
    });
  };

  return handleOnDrop;
};

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { useCallback, useMemo, useEffect, useState } from "react";
import useSWR from "swr";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { StateSelect as StateSelectBlock } from "@plane/blocks/property-select";
import type { StateOption } from "@plane/blocks/property-select";
import type { SelectPaginationParams, SelectTooltip, SelectTooltipOverride, SelectVariant } from "@plane/blocks/select";
import { useTranslation } from "@plane/i18n";
import type { IState, TPaginatedResponse } from "@plane/types";
import type { TIssue, TIssueWorkflow } from "@plane/types";
import { workflowService } from "@/services/workflow.service";
import { WorkflowFieldsDialog } from "./workflow-dialog";
// hooks
import { useProjectState } from "@/hooks/store/use-project-state";

type StateSelectWebProps = {
  issueId?: string;
  issueValues?: Partial<TIssue>;
  isForWorkItemCreation?: boolean;
  isIntakeAcceptance?: boolean;
  onWorkflowChange?: (patch: Partial<TIssue>) => Promise<unknown> | unknown;
  /** Project whose states are offered. */
  projectId: string | undefined;
  /** Current state id (`null` / `undefined` when none). */
  value: string | null | undefined;
  /** Emits the next state id. */
  onChange: (stateId: string) => void;
  variant: SelectVariant;
  disabled?: boolean;
  placeholder?: string;
  onClose?: () => void;
  className?: string;
  tooltip?: SelectTooltip;
  appendElement?: ReactNode;
  /** Forwarded to the block's trigger — see `StateSelectProps["testId"]` doc comment. */
  testId?: string;
  /** Tab order of the trigger, for forms that sequence focus explicitly (`ETabIndices`). */
  tabIndex?: number;
};

const toOption = (state: IState): StateOption => ({
  id: state.id,
  name: state.name,
  color: state.color,
  group: state.group,
  order: state.order,
});

/**
 * Web binding for the presentational `StateSelect` block. Resolves the project's states from the
 * project-state store (fetched on first open when the project has not been loaded yet), filters them
 * by the search query and maps the selected id to a `StateOption`.
 *
 * Workflow capabilities are fetched on open. Creation uses the configured initial state;
 * existing work items expose permitted paths and offer a single patch for missing fields.
 */
export const StateSelect = observer(function StateSelect(props: StateSelectWebProps) {
  const {
    projectId,
    value,
    onChange,
    variant,
    disabled,
    placeholder,
    onClose,
    className,
    tooltip,
    appendElement,
    testId,
    tabIndex,
  } = props;
  const { issueId, issueValues, isForWorkItemCreation, isIntakeAcceptance, onWorkflowChange } = props;
  const [rules, setRules] = useState<TIssueWorkflow | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  const [blockedChildren, setBlockedChildren] = useState<{ id: string; name: string }[]>([]);
  // router params
  const { workspaceSlug } = useParams();
  const { data: creation, isLoading: creationLoading } = useSWR(
    isForWorkItemCreation && workspaceSlug && projectId ? ["workflow-creation", workspaceSlug, projectId] : null,
    () => workflowService.creation(String(workspaceSlug), projectId!)
  );
  const initialStateId = isIntakeAcceptance ? creation?.intake?.acceptance?.state.id : creation?.initial_state_id;
  const requiredFields = isIntakeAcceptance ? creation?.intake?.acceptance?.fields : creation?.fields;
  useEffect(() => {
    if (creation?.enabled && initialStateId && value !== initialStateId) onChange(initialStateId);
  }, [creation?.enabled, initialStateId, value, onChange]);
  // store hooks
  const { getProjectStateIds, getStateById, fetchProjectStates } = useProjectState();
  // translation
  const { t } = useTranslation();
  const resolvedTooltip = useMemo<SelectTooltipOverride | undefined>(() => {
    if (!tooltip) return undefined;
    const override = typeof tooltip === "object" ? tooltip : undefined;
    return {
      heading: override?.heading ?? t("common.state"),
      emptyContent: override?.emptyContent ?? t("common.none"),
    };
  }, [tooltip, t]);

  const getValues = useCallback(
    async ({ search }: SelectPaginationParams): Promise<TPaginatedResponse<StateOption[]>> => {
      if (!workspaceSlug || !projectId) return { results: [] };
      let capabilities: TIssueWorkflow | null = null;
      if (issueId) {
        capabilities = await workflowService.issue(String(workspaceSlug), projectId, issueId);
        setRules(capabilities);
      }
      let stateIds = getProjectStateIds(projectId);
      if (!stateIds) {
        await fetchProjectStates(workspaceSlug.toString(), projectId);
        stateIds = getProjectStateIds(projectId);
      }
      const query = search?.trim().toLowerCase();
      const results = (stateIds ?? [])
        .map((stateId) => getStateById(stateId))
        .filter((state): state is IState => !!state)
        .filter(
          (state) =>
            !capabilities?.enabled ||
            state.id === value ||
            capabilities.targets?.some(
              (target) =>
                target.state_id === state.id &&
                (target.allowed ||
                  [
                    "WORKFLOW_REQUIRED_FIELDS_MISSING",
                    "WORKFLOW_INVALID_ASSIGNEE",
                    "WORKFLOW_INVALID_DATE",
                    "WORKFLOW_CHILDREN_INCOMPLETE",
                  ].includes(target.reason?.code ?? ""))
            )
        )
        .filter((state) => !query || state.name.toLowerCase().includes(query))
        .map(toOption);
      return { results, next_page_results: false };
    },
    [workspaceSlug, projectId, issueId, value, getProjectStateIds, fetchProjectStates, getStateById]
  );

  // Resolved in the observer render (not in a memo) so MobX re-renders when the state loads.
  const selectedState = value ? getStateById(value) : undefined;
  const selected = selectedState ? toOption(selectedState) : null;

  const handleChange = async (stateId: string) => {
    setProblem("");
    setBlockedChildren([]);
    if (rules?.enabled && issueId && projectId && workspaceSlug) {
      const target = rules.targets?.find((item) => item.state_id === stateId);
      const emptyRequirement =
        rules.branch === "requirement" && rules.descendant_count === 0 && getStateById(stateId)?.group === "completed";
      if (
        (["WORKFLOW_REQUIRED_FIELDS_MISSING", "WORKFLOW_INVALID_ASSIGNEE", "WORKFLOW_INVALID_DATE"].includes(
          target?.reason?.code ?? ""
        ) ||
          emptyRequirement) &&
        onWorkflowChange
      ) {
        setPending(stateId);
        return;
      }
      if (target && !target.allowed) {
        setProblem(
          `${target.reason?.message ?? t("project_settings.workflow.save_error")} ${target.reason?.details?.unfinished_count ?? ""}`
        );
        setBlockedChildren(target.reason?.details?.unfinished_issues ?? []);
        return;
      }
    }
    try {
      await onChange(stateId);
    } catch (failure) {
      const error = failure as { message?: string; details?: { unfinished_issues?: { id: string; name: string }[] } };
      setProblem(error?.message ?? t("project_settings.workflow.save_error"));
      setBlockedChildren(error?.details?.unfinished_issues ?? []);
    }
  };
  return (
    <>
      <StateSelectBlock
        getValues={getValues}
        value={selected}
        onChange={(stateId) => void handleChange(stateId)}
        variant={variant}
        disabled={disabled || (isForWorkItemCreation && (creationLoading || creation?.enabled))}
        placeholder={placeholder ?? t("state")}
        onClose={onClose}
        className={className}
        tooltip={resolvedTooltip}
        appendElement={appendElement}
        testId={testId}
        tabIndex={tabIndex}
      />
      {problem && <span role="alert">{problem}</span>}
      {!!blockedChildren.length && (
        <ul>
          {blockedChildren.map((child) => (
            <li key={child.id}>
              <a href={`/${workspaceSlug}/projects/${projectId}/issues/${child.id}`}>{child.name}</a>
            </li>
          ))}
        </ul>
      )}
      {creation?.enabled && !!requiredFields?.length && (
        <span className="text-body-xs-regular">
          {t("project_settings.workflow.required")}:{" "}
          {requiredFields.map((field) => t(`project_settings.workflow.${field}`)).join(", ")}
        </span>
      )}
      {pending && projectId && (
        <WorkflowFieldsDialog
          projectId={projectId}
          initial={issueValues ?? {}}
          emptyRequirement={rules?.branch === "requirement" && rules.descendant_count === 0}
          onSave={async (patch) => {
            await onWorkflowChange?.({ ...patch, state_id: pending });
          }}
          onClose={() => setPending(null)}
        />
      )}
    </>
  );
});

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TStateGroups } from "./state";

export type TWorkflowBranch = "task" | "requirement";
export type TWorkflowField = "assignees" | "start_date" | "target_date";
export type TWorkflowState = { id: string; name: string; group: TStateGroups | "triage"; color: string };
export type TWorkflowTransition = {
  branch: TWorkflowBranch;
  from_state_id: string;
  to_state_id: string;
  roles: number[];
  creates_test_task?: boolean;
};
export type TWorkflowRequirement = { branch: TWorkflowBranch; state_id: string; fields: TWorkflowField[] };
export type TProjectWorkflow = {
  enabled: boolean;
  revision: number;
  task_initial_state_id: string | null;
  test_task_initial_state_id?: string | null;
  intake_submission_state_id: string | null;
  intake_acceptance_state_id: string | null;
  requirement_completed_state_id: string | null;
  requirement_reopen_state_id: string | null;
  transitions: TWorkflowTransition[];
  requirements: TWorkflowRequirement[];
  states?: TWorkflowState[];
};
export type TWorkflowProblem = {
  code: string;
  message?: string;
  details?: { fields?: string[]; unfinished_count?: number; unfinished_issues?: { id: string; name: string }[] };
};
export type TIssueWorkflow = {
  descendant_count?: number;
  completed_descendant_count?: number;
  enabled: boolean;
  revision?: number;
  branch?: TWorkflowBranch;
  reviewed?: boolean;
  completion_state_id?: string;
  reopen_state_id?: string;
  can_accept?: boolean;
  can_change_requirement_state?: boolean;
  blocked_reason?: TWorkflowProblem | Record<string, never>;
  targets?: { state_id: string; allowed: boolean; reason: TWorkflowProblem | null }[];
};

export type TWorkflowCreationContext = {
  enabled: boolean;
  revision?: number;
  initial_state_id?: string;
  fields?: TWorkflowField[];
  intake?: {
    state: TWorkflowState;
    fields: TWorkflowField[];
    acceptance: { state: TWorkflowState; fields: TWorkflowField[] };
  };
};

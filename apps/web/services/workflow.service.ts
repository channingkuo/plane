/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import type { TIssueWorkflow, TProjectWorkflow, TWorkflowCreationContext } from "@plane/types";
import { APIService } from "./api.service";

export class WorkflowService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  private path(slug: string, projectId: string) {
    return `/api/workspaces/${slug}/projects/${projectId}/`;
  }
  async configuration(slug: string, projectId: string): Promise<TProjectWorkflow> {
    return (await this.get(`${this.path(slug, projectId)}workflow/`)).data;
  }
  async save(slug: string, projectId: string, data: TProjectWorkflow): Promise<TProjectWorkflow> {
    return (await this.put(`${this.path(slug, projectId)}workflow/`, data)).data;
  }
  async validate(slug: string, projectId: string, data: TProjectWorkflow): Promise<{ valid: boolean }> {
    return (await this.post(`${this.path(slug, projectId)}workflow/validate/`, data)).data;
  }
  async issue(slug: string, projectId: string, issueId: string): Promise<TIssueWorkflow> {
    return (await this.get(`${this.path(slug, projectId)}issues/${issueId}/workflow/`)).data;
  }
  async preview(slug: string, projectId: string, issueId: string, patch: object): Promise<TIssueWorkflow> {
    return (await this.post(`${this.path(slug, projectId)}issues/${issueId}/workflow/preview/`, patch)).data;
  }
  async creation(slug: string, projectId: string): Promise<TWorkflowCreationContext> {
    return (await this.get(`${this.path(slug, projectId)}workflow/creation/`)).data;
  }
}

export const workflowService = new WorkflowService();

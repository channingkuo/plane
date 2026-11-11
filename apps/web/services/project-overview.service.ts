/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "./api.service";

export type TProjectHierarchy = Record<
  string,
  { id: string; parent_id: string | null; sequence_id: number; project_id: string }
>;
export type TProjectOverview = {
  total: number;
  undated_count: number;
  states: { id: string; name: string; color: string; group: string; count: number }[];
};

class ProjectOverviewService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }
  async hierarchy(slug: string, projectId: string): Promise<TProjectHierarchy> {
    return (await this.get(`/api/workspaces/${slug}/projects/${projectId}/issue-hierarchy/`)).data;
  }
  async overview(slug: string, projectId: string): Promise<TProjectOverview> {
    return (await this.get(`/api/workspaces/${slug}/projects/${projectId}/overview/`)).data;
  }
}
export const projectOverviewService = new ProjectOverviewService();

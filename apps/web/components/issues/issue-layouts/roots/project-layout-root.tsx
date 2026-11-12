/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useEffect } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
// plane constants
import { ISSUE_DISPLAY_FILTERS_BY_PAGE } from "@plane/constants";
import { EUserPermissions } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { useUserPermissions } from "@/hooks/store/user";
import { Select, SelectContent, SelectItem, SelectList, SelectTrigger } from "@makeplane/propel/components/select";
import { EIssueLayoutTypes, EIssuesStoreType } from "@plane/types";
import { Spinner } from "@plane/blocks/spinner";
// components
import { ProjectLevelWorkItemFiltersHOC } from "@/components/work-item-filters/filters-hoc/project-level";
import { WorkItemFiltersRow } from "@/components/work-item-filters/filters-row";
// hooks
import { useIssues } from "@/hooks/store/use-issues";
import { IssuesStoreContext } from "@/hooks/use-issue-layout-store";
// local imports
import { IssuePeekOverview } from "../../peek-overview";
import { CalendarLayout } from "../calendar/roots/project-root";
import { BaseGanttRoot } from "../gantt";
import { KanBanLayout } from "../kanban/roots/project-root";
import { ListLayout } from "../list/roots/project-root";
import { ProjectSpreadsheetLayout } from "../spreadsheet/roots/project-root";

function ProjectIssueLayout(props: { activeLayout: EIssueLayoutTypes | undefined }) {
  switch (props.activeLayout) {
    case EIssueLayoutTypes.LIST:
      return <ListLayout />;
    case EIssueLayoutTypes.KANBAN:
      return <KanBanLayout />;
    case EIssueLayoutTypes.CALENDAR:
      return <CalendarLayout />;
    case EIssueLayoutTypes.GANTT:
      return <BaseGanttRoot />;
    case EIssueLayoutTypes.SPREADSHEET:
      return <ProjectSpreadsheetLayout />;
    default:
      return null;
  }
}

export const ProjectLayoutRoot = observer(function ProjectLayoutRoot() {
  // router
  const { workspaceSlug: routerWorkspaceSlug, projectId: routerProjectId } = useParams();
  const workspaceSlug = routerWorkspaceSlug ? routerWorkspaceSlug.toString() : undefined;
  const projectId = routerProjectId ? routerProjectId.toString() : undefined;
  // hooks
  const { issues, issuesFilter } = useIssues(EIssuesStoreType.PROJECT);
  const { t } = useTranslation();
  const { workspaceProjectsPermissions } = useUserPermissions();
  const isMember =
    !!workspaceSlug &&
    !!projectId &&
    workspaceProjectsPermissions[workspaceSlug]?.[projectId] === EUserPermissions.MEMBER;
  useEffect(() => {
    let refreshTimer: number | undefined;
    const refresh = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          workspaceSlug: string;
          projectId: string;
          listChanged?: boolean;
          orderingChanged?: boolean;
        }>
      ).detail;
      const isWorkflowOrder =
        !!projectId && issuesFilter?.getIssueFilters(projectId)?.displayFilters?.order_by === "workflow_order";
      if (detail?.listChanged === false && !(detail.orderingChanged && isWorkflowOrder)) return;
      if (detail?.workspaceSlug === workspaceSlug && detail?.projectId === projectId && workspaceSlug && projectId) {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(() => {
          void issues.fetchIssuesWithExistingPagination(workspaceSlug, projectId, "mutation").catch((error) => {
            console.error("Could not refresh project work items", error);
          });
        }, 100);
      }
    };
    window.addEventListener("plane:workflow-updated", refresh);
    return () => {
      window.removeEventListener("plane:workflow-updated", refresh);
      window.clearTimeout(refreshTimer);
      issues.setWorkItemScope("mine");
    };
  }, [issues, issuesFilter, workspaceSlug, projectId]);
  // derived values
  const workItemFilters = projectId ? issuesFilter?.getIssueFilters(projectId) : undefined;
  const activeLayout = workItemFilters?.displayFilters?.layout;

  useSWR(
    workspaceSlug && projectId ? `PROJECT_ISSUES_${workspaceSlug}_${projectId}` : null,
    async () => {
      if (workspaceSlug && projectId) {
        await issuesFilter?.fetchFilters(workspaceSlug, projectId);
      }
    },
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  if (!workspaceSlug || !projectId || !workItemFilters) return <></>;
  return (
    <IssuesStoreContext.Provider value={EIssuesStoreType.PROJECT}>
      <ProjectLevelWorkItemFiltersHOC
        enableSaveView
        entityType={EIssuesStoreType.PROJECT}
        entityId={projectId}
        filtersToShowByLayout={ISSUE_DISPLAY_FILTERS_BY_PAGE.issues.filters}
        initialWorkItemFilters={workItemFilters}
        updateFilters={issuesFilter?.updateFilterExpression.bind(issuesFilter, workspaceSlug, projectId)}
        projectId={projectId}
        workspaceSlug={workspaceSlug}
      >
        {({ filter: projectWorkItemsFilter }) => (
          <div className="relative flex h-full w-full flex-col overflow-hidden">
            {isMember && (
              <div className="border-b border-subtle px-4 py-2">
                <Select
                  items={[
                    { value: "mine", label: t("project_overview.my_scope") },
                    { value: "all", label: t("project_overview.all_scope") },
                  ]}
                  value={issues.workItemScope}
                  onValueChange={(value) => {
                    if (value !== "mine" && value !== "all") return;
                    issues.setWorkItemScope(value);
                    void issues.fetchIssuesWithExistingPagination(workspaceSlug, projectId, "mutation");
                  }}
                >
                  <SelectTrigger size="md" aria-label={t("project_overview.scope")} />
                  <SelectContent>
                    <SelectList>
                      <SelectItem value="mine" label={t("project_overview.my_scope")} />
                      <SelectItem value="all" label={t("project_overview.all_scope")} />
                    </SelectList>
                  </SelectContent>
                </Select>
              </div>
            )}
            {projectWorkItemsFilter && <WorkItemFiltersRow filter={projectWorkItemsFilter} />}
            <div className="relative h-full w-full overflow-auto bg-surface-1">
              {/* mutation loader */}
              {issues?.getIssueLoader() === "mutation" && (
                <div className="shadow-sm fixed top-[70px] right-[20px] z-50 flex h-[40px] w-[40px] items-center justify-center rounded-sm bg-layer-1">
                  <Spinner className="h-4 w-4" />
                </div>
              )}
              <ProjectIssueLayout activeLayout={activeLayout} />
            </div>
            {/* peek overview */}
            <IssuePeekOverview />
          </div>
        )}
      </ProjectLevelWorkItemFiltersHOC>
    </IssuesStoreContext.Provider>
  );
});

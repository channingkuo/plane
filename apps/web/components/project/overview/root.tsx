/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useContext, useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import { Button } from "@makeplane/propel/components/button";
import { useTranslation } from "@plane/i18n";
import { GANTT_TIMELINE_TYPE } from "@plane/types";
import type { TIssue, TIssuesResponse, IBlockUpdateData } from "@plane/types";
import { setToast } from "@plane/blocks/toast";
import { projectOverviewService } from "@/services/project-overview.service";
import { IssueService } from "@/services/issue/issue.service";
import { StoreContext } from "@/lib/store-context";
import { useTimeLineChart } from "@/hooks/use-timeline-chart";
import { TimeLineTypeContext } from "@/components/gantt-chart/contexts";
import { GanttChartRoot } from "@/components/gantt-chart/root";
import { BLOCK_HEIGHT } from "@/components/gantt-chart/constants";
import { IssueGanttBlock, IssueGanttSidebarBlock } from "@/components/issues/issue-layouts/gantt/blocks";
import { ProjectOverviewSummary } from "./summary";
import { IssuePeekOverview } from "@/components/issues/peek-overview";

const issueService = new IssueService();

export const ProjectOverview = observer(function ProjectOverview({
  workspaceSlug,
  projectId,
}: {
  workspaceSlug: string;
  projectId: string;
}) {
  const { t } = useTranslation();
  const store = useContext(StoreContext);
  if (!store) throw new Error("ProjectOverview must be used within StoreProvider");
  const { addIssue } = store.issue.issues;
  const { initGantt } = useTimeLineChart(GANTT_TIMELINE_TYPE.ISSUE);
  const [updating, setUpdating] = useState(false);
  const summary = useSWR(["project-overview", workspaceSlug, projectId], () =>
    projectOverviewService.overview(workspaceSlug, projectId)
  );
  const pages = useSWRInfinite<TIssuesResponse>(
    (_index, previous) =>
      previous && !previous.next_page_results
        ? null
        : ["project-overview-timeline", workspaceSlug, projectId, previous?.next_cursor ?? ""],
    ([, slug, project, cursor]: string[]) =>
      issueService.getIssues(slug, project, {
        work_item_scope: "all",
        per_page: "100",
        order_by: "sequence_id",
        ...(cursor ? { cursor } : {}),
      }),
    { revalidateOnFocus: false }
  );
  const rows = useMemo(
    () => pages.data?.flatMap((page) => (Array.isArray(page.results) ? page.results : [])) ?? [],
    [pages.data]
  );
  const ids = useMemo(() => rows.map((row) => row.id), [rows]);
  useEffect(() => {
    initGantt();
  }, [initGantt]);
  useEffect(() => {
    addIssue(rows);
  }, [rows, addIssue]);
  const refresh = async () => {
    await Promise.allSettled([summary.mutate(), pages.mutate()]);
  };
  const update = async (id: string, patch: Partial<TIssue>) => {
    setUpdating(true);
    try {
      await issueService.patchIssue(workspaceSlug, projectId, id, patch);
      await refresh();
    } catch {
      setToast({ type: "error", title: t("toast.error"), message: t("project_overview.update_error") });
      await pages.mutate().catch(() => undefined);
    } finally {
      setUpdating(false);
    }
  };
  const canLoadMore = pages.data?.at(-1)?.next_page_results ?? false;
  if (summary.error)
    return (
      <p role="alert" className="p-6">
        {t("project_overview.load_error")}{" "}
        <Button
          size="sm"
          stretch="auto"
          variant="secondary"
          label={t("project_overview.retry")}
          onClick={() => void refresh()}
        />
      </p>
    );
  if (!summary.data)
    return (
      <p role="status" className="p-6">
        {t("project_overview.loading")}
      </p>
    );
  return (
    <div className="flex h-full flex-col overflow-y-auto bg-surface-1">
      <div className="flex items-center justify-between border-b border-subtle px-6 py-4">
        <h1 className="text-h4-semibold">{t("project_overview.title")}</h1>
        <Button
          size="sm"
          stretch="auto"
          variant="secondary"
          label={t("project_overview.refresh")}
          onClick={() => void refresh()}
        />
      </div>
      <div className="p-6">
        <ProjectOverviewSummary overview={summary.data} />
      </div>
      <section
        className="flex min-h-[500px] flex-1 flex-col border-t border-subtle"
        aria-label={t("project_overview.timeline")}
      >
        <div className="px-6 py-4">
          <h2 className="text-h5-medium">{t("project_overview.timeline")}</h2>
          <p className="mt-1 text-body-sm-regular text-secondary">
            {t("project_overview.undated", { count: summary.data.undated_count })}
          </p>
          {updating && <p role="status">{t("project_overview.updating")}</p>}
          {pages.error && (
            <p role="alert">
              {t("project_overview.load_error")}{" "}
              <Button
                size="sm"
                stretch="auto"
                variant="secondary"
                label={t("project_overview.retry")}
                onClick={() => void pages.mutate()}
              />
            </p>
          )}
          {pages.isLoading && <p role="status">{t("project_overview.loading")}</p>}
          {summary.data.total === 0 && <p className="py-4">{t("project_overview.empty")}</p>}
        </div>
        <TimeLineTypeContext.Provider value={GANTT_TIMELINE_TYPE.ISSUE}>
          <div className="min-h-[400px] flex-1">
            <GanttChartRoot
              title={t("project_overview.timeline")}
              loaderTitle={t("project_overview.timeline")}
              blockIds={ids}
              blockUpdateHandler={(row: TIssue, patch: IBlockUpdateData) =>
                void update(row.id, { start_date: patch.start_date, target_date: patch.target_date })
              }
              blockToRender={(row: TIssue) => <IssueGanttBlock issueId={row.id} />}
              sidebarToRender={() => (
                <div>
                  {ids.map((id) => (
                    <div key={id} className="flex items-center px-3" style={{ height: BLOCK_HEIGHT }}>
                      <IssueGanttSidebarBlock issueId={id} />
                    </div>
                  ))}
                </div>
              )}
              enableBlockLeftResize={!updating}
              enableBlockRightResize={!updating}
              enableBlockMove={!updating}
              enableAddBlock={!updating}
              updateBlockDates={async (updates) => {
                setUpdating(true);
                try {
                  await issueService.updateIssueDates(workspaceSlug, projectId, updates);
                  await refresh();
                } catch {
                  setToast({ type: "error", title: t("toast.error"), message: t("project_overview.update_error") });
                  await pages.mutate().catch(() => undefined);
                } finally {
                  setUpdating(false);
                }
              }}
              showAllBlocks
              border={false}
            />
          </div>
        </TimeLineTypeContext.Provider>
        {canLoadMore && (
          <div className="px-6 py-3">
            <Button
              size="sm"
              stretch="auto"
              variant="secondary"
              disabled={pages.isValidating}
              label={t("project_overview.load_more")}
              onClick={() => void pages.setSize(pages.size + 1)}
            />
          </div>
        )}
      </section>
      <IssuePeekOverview />
    </div>
  );
});

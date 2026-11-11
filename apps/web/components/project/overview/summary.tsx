/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";
import type { TProjectOverview } from "@/services/project-overview.service";

export function ProjectOverviewSummary({ overview }: { overview: TProjectOverview }) {
  const { t } = useTranslation();
  return (
    <section aria-label={t("project_overview.status_summary")}>
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="text-h5-medium">{t("project_overview.status_summary")}</h2>
        <span className="text-body-sm-regular text-secondary">
          {t("project_overview.total", { count: overview.total })}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        {overview.states.map((state) => (
          <div key={state.id} className="rounded-lg border border-subtle bg-surface-1 p-4" data-testid="overview-state">
            <div className="flex items-center gap-2 text-body-sm-regular text-secondary">
              <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: state.color }} />
              <span className="truncate" title={state.name}>
                {state.name}
              </span>
            </div>
            <p className="mt-3 text-h3-semibold tabular-nums" aria-label={`${state.name}: ${state.count}`}>
              {state.count}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

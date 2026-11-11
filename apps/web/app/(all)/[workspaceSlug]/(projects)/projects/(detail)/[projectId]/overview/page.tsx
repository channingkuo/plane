/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useParams } from "react-router";
import { ProjectOverview } from "@/components/project/overview/root";
import { PageHead } from "@/components/core/page-title";
import { useTranslation } from "@plane/i18n";

export default function ProjectOverviewPage() {
  const { workspaceSlug, projectId } = useParams();
  const { t } = useTranslation();
  if (!workspaceSlug || !projectId) return null;
  return (
    <>
      <PageHead title={t("project_overview.title")} />
      <ProjectOverview workspaceSlug={workspaceSlug} projectId={projectId} />
    </>
  );
}

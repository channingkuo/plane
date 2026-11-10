/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
import { ProjectWorkflowSettings } from "@/components/project/settings/workflow";
import { useUserPermissions } from "@/hooks/store/user";
import type { Route } from "./+types/page";

function WorkflowSettingsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  if (!allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug))
    return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;
  return (
    <SettingsContentWrapper>
      <SettingsHeading title={t("project_settings.workflow.heading")} />
      <ProjectWorkflowSettings workspaceSlug={workspaceSlug} projectId={projectId} />
    </SettingsContentWrapper>
  );
}

export default observer(WorkflowSettingsPage);

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { projectOverviewService } from "@/services/project-overview.service";
import { ancestorPath } from "./ancestor-path";
// Cards can themselves be anchors; nested anchors are invalid HTML.
/* oxlint-disable jsx_a11y/prefer-tag-over-role */
// plane imports
import type { TIssueIdentifierProps } from "@plane/types";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import { IdentifierText } from "@/components/issues/issue-detail/identifier-text";

export const IssueIdentifier = observer(function IssueIdentifier(props: TIssueIdentifierProps) {
  const { projectId, variant, size, displayProperties, enableClickToCopyIdentifier = false } = props;
  // store hooks
  const { getProjectIdentifierById } = useProject();
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  // Determine if the component is using store data or not
  const isUsingStoreData = "issueId" in props;
  // derived values
  const issue = isUsingStoreData ? getIssueById(props.issueId) : null;
  const projectIdentifier = isUsingStoreData ? getProjectIdentifierById(projectId) : props.projectIdentifier;
  const issueSequenceId = isUsingStoreData ? issue?.sequence_id : props.issueSequenceId;
  const shouldRenderIssueID = displayProperties ? displayProperties.key : true;
  const { workspaceSlug } = useParams();
  const router = useRouter();
  const issueId = isUsingStoreData ? props.issueId : undefined;
  const { data: hierarchy } = useSWR(
    shouldRenderIssueID && workspaceSlug && issueId ? ["project-issue-hierarchy", workspaceSlug, projectId] : null,
    () => projectOverviewService.hierarchy(workspaceSlug.toString(), projectId),
    { dedupingInterval: 5000 }
  );
  const ancestors = ancestorPath(hierarchy, issueId);

  if (!shouldRenderIssueID) return null;

  return (
    <div className="flex shrink-0 items-center gap-1 whitespace-nowrap">
      {ancestors.map((ancestor) => (
        <span key={ancestor.id} className="inline-flex items-center gap-1 text-caption-md-medium text-tertiary">
          <span
            role="link"
            tabIndex={0}
            className="hover:text-primary hover:underline"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              const href = `/${workspaceSlug}/projects/${ancestor.project_id}/issues/${ancestor.id}/`;
              if (event.metaKey || event.ctrlKey) window.open(href, "_blank", "noopener,noreferrer");
              else router.push(href);
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              event.stopPropagation();
              router.push(`/${workspaceSlug}/projects/${ancestor.project_id}/issues/${ancestor.id}/`);
            }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            {getProjectIdentifierById(ancestor.project_id)}-{ancestor.sequence_id}
          </span>
          <span aria-hidden="true">/</span>
        </span>
      ))}
      <span className={ancestors.length ? "[&_*]:!text-[11px]" : undefined}>
        <IdentifierText
          identifier={`${projectIdentifier}-${issueSequenceId}`}
          enableClickToCopyIdentifier={enableClickToCopyIdentifier}
          variant={variant}
          size={size}
        />
      </span>
    </div>
  );
});

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, Project, State


class ProjectOverviewEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id):
        project = get_object_or_404(Project, pk=project_id, workspace__slug=slug)
        issues = Issue.project_objects.filter(project=project)
        counts = {
            row["state_id"]: row["count"] for row in issues.order_by().values("state_id").annotate(count=Count("id"))
        }
        from plane.db.models import ProjectWorkflow

        workflow = ProjectWorkflow.objects.filter(project=project, enabled=True).first()
        states = (
            State.all_state_objects.filter(project=project, deleted_at__isnull=True)
            .filter(~Q(group="triage") | Q(pk=workflow.intake_submission_state_id if workflow else None))
            .order_by("sequence", "id")
        )
        return Response(
            {
                "total": sum(counts.values()),
                "undated_count": issues.filter(Q(start_date__isnull=True) | Q(target_date__isnull=True)).count(),
                "states": [
                    {
                        "id": state.pk,
                        "name": state.name,
                        "color": state.color,
                        "group": state.group,
                        "count": counts.get(state.pk, 0),
                    }
                    for state in states
                ],
            }
        )


class ProjectIssueHierarchyEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id):
        from plane.services.workflow.configuration import project_role

        project = get_object_or_404(Project, pk=project_id, workspace__slug=slug)
        issues = Issue.objects.filter(project=project, is_draft=False)
        if project_role(request.user, project) == 5 and not project.guest_view_all_features:
            issues = issues.filter(created_by=request.user)
        return Response({str(row["id"]): row for row in issues.values("id", "parent_id", "sequence_id", "project_id")})

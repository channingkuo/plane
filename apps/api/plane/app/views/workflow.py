# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.shortcuts import get_object_or_404
from plane.app.permissions import ROLE, allow_permission
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.app.serializers.workflow import WorkflowConfigurationSerializer, WorkflowPreviewSerializer
from plane.db.models import Project, ProjectWorkflow
from plane.services.workflow.configuration import (
    canonical,
    configuration,
    save_configuration,
    state_options,
    workspace_admin,
)
from plane.services.workflow.rules import STATE_FIELDS
from plane.app.permissions.project import ProjectEntityPermission
from plane.db.models import Issue, ProjectMember
from plane.services.workflow.queries import capabilities
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


class ProjectWorkflowEndpoint(BaseAPIView):
    def get_project(self, request, slug, project_id):
        project = get_object_or_404(Project, workspace__slug=slug, pk=project_id)
        workspace_admin(request.user, project)
        return project

    def get(self, request, slug, project_id):
        project = self.get_project(request, slug, project_id)
        workflow = ProjectWorkflow.objects.filter(project=project).first()
        data = (
            canonical(configuration(workflow))
            if workflow
            else {
                "enabled": False,
                "revision": 0,
                **dict.fromkeys(STATE_FIELDS),
                "transitions": [],
                "requirements": [],
            }
        )
        return Response({**data, "states": state_options(project)})

    def put(self, request, slug, project_id):
        project = self.get_project(request, slug, project_id)
        serializer = WorkflowConfigurationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(save_configuration(project, request.user, serializer.validated_data))


class ProjectWorkflowValidationEndpoint(ProjectWorkflowEndpoint):
    http_method_names = ["post", "options"]

    def post(self, request, slug, project_id):
        project = self.get_project(request, slug, project_id)
        serializer = WorkflowConfigurationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(save_configuration(project, request.user, serializer.validated_data, preview=True))


class WorkflowReadPermission(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and ProjectMember.objects.filter(
                workspace__slug=view.workspace_slug, project_id=view.project_id, member=request.user, is_active=True
            ).exists()
        )


class IssueWorkflowEndpoint(BaseAPIView):
    permission_classes = [WorkflowReadPermission]

    def get_issue(self, request, slug, project_id, issue_id):
        issue = get_object_or_404(
            Issue.objects.select_related("project"), pk=issue_id, project_id=project_id, workspace__slug=slug
        )
        from plane.services.workflow.configuration import project_role

        if (
            project_role(request.user, issue.project) == 5
            and not issue.project.guest_view_all_features
            and issue.created_by_id != request.user.pk
        ):
            raise PermissionDenied()
        return issue

    def get(self, request, slug, project_id, issue_id):
        return Response(capabilities(self.get_issue(request, slug, project_id, issue_id), request.user))

    def post(self, request, slug, project_id, issue_id):
        from rest_framework import serializers

        if not isinstance(request.data, dict):
            raise serializers.ValidationError("变更必须是对象")
        serializer = WorkflowPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            capabilities(self.get_issue(request, slug, project_id, issue_id), request.user, serializer.validated_data)
        )


class WorkflowCreationContextEndpoint(BaseAPIView):
    permission_classes = [ProjectEntityPermission]

    def get(self, request, slug, project_id):
        project = get_object_or_404(Project, pk=project_id, workspace__slug=slug)
        workflow = ProjectWorkflow.objects.filter(project=project, enabled=True).first()
        if not workflow:
            return Response({"enabled": False})
        data = canonical(configuration(workflow))
        return Response(
            {
                "enabled": True,
                "revision": workflow.revision,
                "initial_state_id": data["task_initial_state_id"],
                "fields": next(
                    (
                        r["fields"]
                        for r in data["requirements"]
                        if r["branch"] == "task" and r["state_id"] == data["task_initial_state_id"]
                    ),
                    [],
                ),
            }
        )


class WorkflowBulkUpdateEndpoint(BaseAPIView):
    """Atomic property edits for configured projects; legacy CE has no bulk route."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id):
        from plane.services.workflow.batch import controlled_batch_properties

        return Response(controlled_batch_properties(project_id, request))

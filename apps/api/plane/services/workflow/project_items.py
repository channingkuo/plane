# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared project-layout scope. This is a default view, not an authorization rule."""

from django.db.models import Exists, OuterRef, Q

from plane.db.models import IssueAssignee, ProjectWorkflow
from .configuration import project_role


def member_scope(queryset, project, actor, scope=None):
    if scope == "overview":
        return overview_scope(queryset, project, actor)
    if scope == "all" or project_role(actor, project) != 15:
        return queryset
    workflow = ProjectWorkflow.objects.filter(project=project, enabled=True).first()
    review_states = [workflow.intake_submission_state_id, workflow.intake_acceptance_state_id] if workflow else []
    assigned = IssueAssignee.objects.filter(issue_id=OuterRef("pk"), assignee=actor, deleted_at__isnull=True)
    return queryset.alias(assigned_to_me=Exists(assigned)).filter(
        Q(assigned_to_me=True) | Q(state_id__in=review_states)
    )


def overview_scope(queryset, project, actor):
    """Members see every requirement, plus tasks they are currently assigned to."""
    if project_role(actor, project) != 15:
        return queryset
    assigned = IssueAssignee.objects.filter(issue_id=OuterRef("pk"), assignee=actor, deleted_at__isnull=True)
    return queryset.alias(assigned_to_me=Exists(assigned)).filter(
        Q(assigned_to_me=True) | Q(workflow_context__origin="intake", workflow_context__deleted_at__isnull=True)
    )

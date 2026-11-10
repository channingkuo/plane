# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from crum import get_current_user
from django.db import transaction
from django.utils import timezone
from rest_framework.serializers import ModelSerializer

from plane.db.models import Project, ProjectWorkflow, IssueWorkflowContext, Issue, IntakeIssue
from .configuration import WorkflowError, canonical, configuration, project_role, issue_values, validate_assignees
from .execution import audit
from .rules import Violation, validate_fields


@transaction.atomic
def accept_intake(serializer, instance, values):
    project = Project.objects.select_for_update().get(pk=instance.project_id)
    actor = get_current_user()
    workflow = ProjectWorkflow.objects.get(project=project, enabled=True)
    config = canonical(configuration(workflow))
    if project_role(actor, project) != 20:
        raise WorkflowError(Violation("WORKFLOW_INTAKE_FORBIDDEN", "只有项目管理员可以接收 Intake", status=403))
    instance = IntakeIssue.objects.select_for_update().get(pk=instance.pk, project=project)
    context = IssueWorkflowContext.objects.filter(issue_id=instance.issue_id, origin="intake").first()
    if not context or str(context.original_intake_id) != str(instance.pk):
        raise WorkflowError(Violation("WORKFLOW_INVALID_SOURCE", "Intake 缺少可信来源记录"))
    if context.accepted_at:
        return ModelSerializer.update(serializer, instance, values)
    if instance.status not in {-2, 0}:
        raise WorkflowError(Violation("WORKFLOW_INTAKE_NOT_PENDING", "只有待评审或稍后处理的 Intake 可以接收"))
    issue = Issue.objects.select_for_update().get(pk=instance.issue_id)
    try:
        validate_assignees(project, issue_values(issue)["assignees"])
        validate_fields(config, "requirement", config["intake_acceptance_state_id"], issue_values(issue))
    except Violation as exc:
        raise WorkflowError(exc) from exc
    issue.state_id = config["intake_acceptance_state_id"]
    issue.save(update_fields=["state"])
    context.accepted_at, context.accepted_by, context.accepted_revision = timezone.now(), actor, workflow.revision
    context.save(update_fields=["accepted_at", "accepted_by", "accepted_revision"])
    result = ModelSerializer.update(serializer, instance, values)
    result.issue = issue
    audit(project, actor, config, "intake.accepted", issue)
    return result

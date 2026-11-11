# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import transaction
from rest_framework.exceptions import APIException, PermissionDenied

from plane.db.models import (
    Project,
    ProjectWorkflow,
    WorkflowTransition,
    WorkflowStateRequirement,
    WorkflowAudit,
    WorkspaceMember,
    ProjectMember,
    Issue,
    IssueWorkflowContext,
    State,
)
from .rules import STATE_FIELDS, OPTIONAL_STATE_FIELDS, Violation, validate_configuration, validate_fields


class WorkflowError(APIException):
    def __init__(self, violation):
        self.status_code = violation.status
        super().__init__({"code": violation.code, "message": violation.message, "details": violation.details})


def workspace_admin(user, project):
    if not WorkspaceMember.objects.filter(
        workspace_id=project.workspace_id, member=user, is_active=True, role=20
    ).exists():
        raise PermissionDenied("只有工作空间管理员可以管理工作流配置")


def project_role(user, project):
    if not user or not user.is_authenticated:
        return None
    return (
        ProjectMember.objects.filter(
            project_id=project.pk, workspace_id=project.workspace_id, member=user, is_active=True
        )
        .values_list("role", flat=True)
        .first()
    )


def configuration(workflow):
    return {
        "enabled": workflow.enabled,
        "revision": workflow.revision,
        **{
            key: str(getattr(workflow, key)) if getattr(workflow, key) else None
            for key in (*STATE_FIELDS, *OPTIONAL_STATE_FIELDS)
        },
        "transitions": list(
            workflow.transitions.values("branch", "from_state_id", "to_state_id", "roles", "creates_test_task")
        ),
        "requirements": list(workflow.requirements.values("branch", "state_id", "fields")),
    }


def canonical(config):
    """JSON-compatible IDs, shared by persistence, previews and execution."""
    result = dict(config)
    for key in (*STATE_FIELDS, *OPTIONAL_STATE_FIELDS):
        result[key] = str(config[key]) if config.get(key) else None
    result["transitions"] = [
        {**t, "from_state_id": str(t["from_state_id"]), "to_state_id": str(t["to_state_id"])}
        for t in config.get("transitions", [])
    ]
    result["requirements"] = [{**r, "state_id": str(r["state_id"])} for r in config.get("requirements", [])]
    return result


def state_options(project):
    return list(
        State.all_state_objects.filter(project_id=project.pk, deleted_at__isnull=True).values(
            "id", "name", "group", "color"
        )
    )


def issue_values(issue):
    return {
        "assignees": list(issue.issue_assignee.filter(deleted_at__isnull=True).values_list("assignee_id", flat=True)),
        "start_date": issue.start_date,
        "target_date": issue.target_date,
    }


def validate_assignees(project, assignees):
    requested = {str(getattr(value, "pk", value)) for value in assignees or []}
    if not requested:
        return
    if not isinstance(project, Project):
        project = Project.objects.get(pk=project)
    project_members = set(
        map(
            str,
            ProjectMember.objects.filter(
                project=project, member_id__in=requested, is_active=True, role__gte=15, member__is_active=True
            ).values_list("member_id", flat=True),
        )
    )
    workspace_members = set(
        map(
            str,
            WorkspaceMember.objects.filter(
                workspace_id=project.workspace_id, member_id__in=requested, is_active=True
            ).values_list("member_id", flat=True),
        )
    )
    if requested - (project_members & workspace_members):
        raise Violation(
            "WORKFLOW_INVALID_ASSIGNEE", "负责人必须是当前工作空间和项目的有效成员", {"fields": ["assignees"]}
        )


def check_compatibility(project, config):
    if not config["enabled"]:
        return
    problems = []
    from .execution import snapshot, descendants

    nodes = snapshot(project.pk)
    groups = {str(row["id"]): row["group"] for row in state_options(project)}
    for issue in Issue.objects.filter(project_id=project.pk, is_draft=False).select_related("workflow_context"):
        try:
            context = issue.workflow_context
        except IssueWorkflowContext.DoesNotExist:
            problems.append({"issue_id": str(issue.pk), "code": "WORKFLOW_SOURCE_MISSING"})
            continue
        branch = "requirement" if context.origin == "intake" else "task"
        try:
            values = issue_values(issue)
            validate_assignees(project, values["assignees"])
            state_id = str(issue.state_id)
            if state_id not in groups:
                raise Violation("WORKFLOW_INVALID_STATE", "工作项缺少有效状态")
            if (
                context.origin == "intake"
                and not context.accepted_at
                and state_id != config["intake_submission_state_id"]
            ):
                raise Violation("WORKFLOW_INITIAL_STATE_REQUIRED", "待评审工作项不处于配置的提交状态")
            if groups[state_id] == "completed" and any(
                nodes[key]["state__group"] != "completed" for key in descendants(nodes, str(issue.pk))
            ):
                raise Violation("WORKFLOW_CHILDREN_INCOMPLETE", "已完成工作项仍有未完成后代")
            validate_fields(config, branch, str(issue.state_id), values)
            if context.accepted_at:
                for state_id in {config["requirement_completed_state_id"], config["requirement_reopen_state_id"]}:
                    validate_fields(config, "requirement", state_id, values)
        except Violation as exc:
            problems.append({"issue_id": str(issue.pk), "code": exc.code, "details": exc.details})
    if problems:
        raise Violation("WORKFLOW_INCOMPATIBLE_ISSUES", "现有工作项不满足工作流约束", {"issues": problems})


@transaction.atomic
def save_configuration(project, user, payload, *, preview=False):
    project = Project.objects.select_for_update().get(pk=project.pk)
    workspace_admin(user, project)
    current = ProjectWorkflow.objects.filter(project=project).first()
    revision = current.revision if current else 0
    config = canonical(payload)
    try:
        if config["revision"] != revision:
            raise Violation(
                "WORKFLOW_REVISION_CONFLICT", "配置已更新，请刷新后重试", {"workflow_revision": revision}, status=409
            )
        validate_configuration(config, {str(s["id"]): s["group"] for s in state_options(project)})
        check_compatibility(project, config)
    except Violation as exc:
        raise WorkflowError(exc) from exc
    if preview:
        return {"valid": True, "revision": revision}
    workflow = current or ProjectWorkflow(project=project)
    for key in (*STATE_FIELDS, *OPTIONAL_STATE_FIELDS):
        setattr(workflow, key, config[key])
    workflow.enabled = config["enabled"]
    workflow.revision = revision + 1
    workflow.save()
    # These rows are configuration children, not user content; replace the entire revision.
    workflow.transitions.all().delete(soft=False)
    workflow.requirements.all().delete(soft=False)
    WorkflowTransition.objects.bulk_create([WorkflowTransition(workflow=workflow, **t) for t in config["transitions"]])
    WorkflowStateRequirement.objects.bulk_create(
        [WorkflowStateRequirement(workflow=workflow, **r) for r in config["requirements"]]
    )
    saved = canonical(configuration(workflow))
    WorkflowAudit.objects.create(
        project=project,
        actor=user,
        operation="configuration.saved",
        revision=workflow.revision,
        details={"enabled": workflow.enabled, "previous_revision": revision, "configuration": saved},
    )
    return saved

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Normalization for existing serializers; no client-controlled bypass options."""

from plane.db.models import ProjectWorkflow, State, ProjectMember, IssueWorkflowContext
from .configuration import WorkflowError, canonical, configuration, issue_values, project_role, validate_assignees
from .execution import active_mutation, snapshot, descendants, unfinished_details
from .rules import Violation, validate_fields, evaluate


def preflight_request(view, request):
    from rest_framework.exceptions import PermissionDenied
    from .configuration import project_role

    mutation = active_mutation.get()
    if not mutation or not mutation["intake"] or request.method not in {"POST", "PATCH", "PUT"}:
        return
    if not isinstance(request.data, dict) or str(request.data.get("status")) != "1":
        return
    for project_id, project in mutation["projects"].items():
        if mutation["configs"][project_id] and project_role(request.user, project) != 20:
            raise PermissionDenied("只有当前项目的有效项目管理员可以接收 Intake")
    mutation["accepting"] = True


def submission_state(project_id):
    workflow = ProjectWorkflow.objects.filter(project_id=project_id, enabled=True).first()
    return workflow.intake_submission_state if workflow else State.triage_objects.filter(project_id=project_id).first()


def prepare_intake_input(project_id, payload):
    """Optional fields shared by public API and Space submissions."""
    from rest_framework import serializers
    from plane.db.models import Project

    workflow = ProjectWorkflow.objects.filter(project_id=project_id, enabled=True).first()
    if not workflow:
        return {}, []

    class Fields(serializers.Serializer):
        start_date = serializers.DateField(required=False, allow_null=True)
        target_date = serializers.DateField(required=False, allow_null=True)
        assignees = serializers.ListField(child=serializers.UUIDField(), required=False)

    data = {**payload}
    if "assignee_ids" in data:
        data["assignees"] = data["assignee_ids"]
    serializer = Fields(data=data)
    serializer.is_valid(raise_exception=True)
    values = dict(serializer.validated_data)
    assignees = values.pop("assignees", None)
    if assignees is None:
        default = Project.objects.get(pk=project_id).default_assignee_id
        assignees = (
            [default]
            if default
            and ProjectMember.objects.filter(
                project_id=project_id, member_id=default, is_active=True, role__gte=15
            ).exists()
            else []
        )
    valid = set(
        ProjectMember.objects.filter(
            project_id=project_id, member_id__in=assignees, is_active=True, role__gte=15
        ).values_list("member_id", flat=True)
    )
    if set(assignees) - valid:
        raise WorkflowError(Violation("WORKFLOW_INVALID_ASSIGNEE", "负责人必须是当前项目的有效成员"))
    config = canonical(configuration(workflow))
    try:
        validate_assignees(project_id, assignees)
        validate_fields(config, "requirement", config["intake_submission_state_id"], {**values, "assignees": assignees})
    except Violation as exc:
        raise WorkflowError(exc) from exc
    return values, assignees


def assign_submission(issue, assignees):
    from plane.db.models import IssueAssignee

    IssueAssignee.objects.bulk_create(
        [
            IssueAssignee(
                issue=issue, project_id=issue.project_id, workspace_id=issue.workspace_id, assignee_id=user_id
            )
            for user_id in set(assignees)
        ]
    )


def prepare_issue(serializer, attrs):
    project_id = serializer.context.get("project_id") or getattr(serializer.instance, "project_id", None)
    if not project_id:
        return attrs
    workflow = ProjectWorkflow.objects.filter(project_id=project_id, enabled=True).first()
    if not workflow:
        return attrs
    config = canonical(configuration(workflow))
    mutation = active_mutation.get()
    intake = bool(mutation and mutation["intake"])
    initial = serializer.instance is None
    if initial:
        target_id = config["intake_submission_state_id"] if intake else config["task_initial_state_id"]
        raw = serializer.initial_data
        supplied = raw.get("state_id", raw.get("state"))
        if supplied and str(supplied) != target_id:
            raise WorkflowError(
                Violation("WORKFLOW_INITIAL_STATE_REQUIRED", "请使用项目配置的初始状态", {"state_id": target_id})
            )
        attrs["state"] = State.all_state_objects.get(pk=target_id, project_id=project_id, deleted_at__isnull=True)
        values = {**attrs, "assignees": attrs.get("assignee_ids", attrs.get("assignees", []))}
        if raw.get("assignee_ids", raw.get("assignees")) == []:
            serializer.context["default_assignee_id"] = None
        default_assignee = serializer.context.get("default_assignee_id")
        if (
            not values["assignees"]
            and default_assignee
            and ProjectMember.objects.filter(
                project_id=project_id, member_id=default_assignee, is_active=True, role__gte=15
            ).exists()
        ):
            values["assignees"] = [default_assignee]
        try:
            validate_assignees(project_id, values.get("assignees", []))
            validate_fields(config, "requirement" if intake else "task", target_id, values)
        except Violation as exc:
            raise WorkflowError(exc) from exc
    else:
        from crum import get_current_user

        issue = serializer.instance
        context = IssueWorkflowContext.objects.filter(issue=issue).first()
        if not context:
            raise WorkflowError(Violation("WORKFLOW_SOURCE_MISSING", "工作项缺少可信来源记录"))
        values = {**issue_values(issue), **attrs}
        if "assignee_ids" in attrs or "assignees" in attrs:
            values["assignees"] = attrs.get("assignee_ids", attrs.get("assignees"))
        target = attrs.get("state", issue.state)
        target_id = str(target.pk) if target else None
        branch = "requirement" if context.origin == "intake" else "task"
        try:
            if context.origin == "intake" and not context.accepted_at:
                if mutation and mutation.get("accepting"):
                    validate_fields(config, branch, config["intake_acceptance_state_id"], values)
                elif target_id != str(issue.state_id):
                    raise Violation("WORKFLOW_INTAKE_NOT_ACCEPTED", "待评审需求不能通过状态更新进入执行流程")
                else:
                    validate_fields(config, branch, target_id, values)
            else:
                evaluate(
                    config,
                    branch,
                    str(issue.state_id),
                    target_id,
                    project_role(get_current_user(), issue.project),
                    values,
                )
            validate_assignees(issue.project, values.get("assignees", []))
            if (
                target
                and target.group == "completed"
                and not (mutation and str(issue.pk) in mutation.get("batch_issue_ids", ()))
            ):
                nodes = snapshot(issue.project_id)
                incomplete = [
                    key for key in descendants(nodes, str(issue.pk)) if nodes[key]["state__group"] != "completed"
                ]
                if incomplete:
                    raise Violation(
                        "WORKFLOW_CHILDREN_INCOMPLETE",
                        "还有未完成子工作项，不能完成该工作项",
                        unfinished_details(issue.project, get_current_user(), incomplete),
                    )
        except Violation as exc:
            exc.details["issue_id"] = str(issue.pk)
            raise WorkflowError(exc) from exc
    raw_assignees = serializer.initial_data.get("assignee_ids", serializer.initial_data.get("assignees"))
    if raw_assignees is not None:
        valid = set(
            str(pk)
            for pk in ProjectMember.objects.filter(
                project_id=project_id, member_id__in=raw_assignees, is_active=True, role__gte=15
            ).values_list("member_id", flat=True)
        )
        if set(map(str, raw_assignees)) - valid:
            raise WorkflowError(
                Violation("WORKFLOW_INVALID_ASSIGNEE", "负责人必须是当前项目的有效成员", {"fields": ["assignees"]})
            )
    return attrs

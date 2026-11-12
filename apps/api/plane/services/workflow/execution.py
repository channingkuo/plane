# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Transaction-bound workflow operations and parent aggregation.

Callers must hold the project lock before reading or writing related issues.
HTTP adapters use the same transaction boundary as explicit bulk/background callers.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from plane.db.models import (
    Project,
    ProjectWorkflow,
    Issue,
    IssueAssignee,
    IssueWorkflowContext,
    IntakeIssue,
    State,
    WorkflowAudit,
    DeployBoard,
)
from .configuration import canonical, configuration, project_role, issue_values, validate_assignees
from .rules import Violation, evaluate, validate_fields, validate_configuration


active_mutation = ContextVar("workflow_mutation", default=None)


def snapshot(project_id):
    rows = list(
        Issue.all_objects.filter(project_id=project_id, is_draft=False).values(
            "id",
            "state_id",
            "state__group",
            "parent_id",
            "start_date",
            "target_date",
            "deleted_at",
            "archived_at",
            "updated_at",
            "workflow_context__accepted_at",
        )
    )
    assignees = {}
    for issue_id, user_id in IssueAssignee.objects.filter(project_id=project_id).values_list("issue_id", "assignee_id"):
        assignees.setdefault(issue_id, []).append(str(user_id))
    return {str(row["id"]): {**row, "assignees": sorted(assignees.get(row["id"], []))} for row in rows}


def descendants(nodes, parent):
    """All live descendants, including archived nodes, with cycle detection."""
    children = {}
    for key, row in nodes.items():
        if row["parent_id"] and not row["deleted_at"]:
            children.setdefault(str(row["parent_id"]), []).append(key)
    result, pending, visited = [], list(children.get(parent, [])), {parent}
    while pending:
        key = pending.pop()
        if key in visited:
            raise Violation("WORKFLOW_PARENT_CYCLE", "父子关系不能形成循环")
        visited.add(key)
        result.append(key)
        pending.extend(children.get(key, []))
    return result


def completion_signature(nodes, ids):
    return sorted(
        (key, nodes[key]["state__group"] == "completed", str(nodes[key]["parent_id"]), str(nodes[key]["deleted_at"]))
        for key in ids
    )


def audit(project, actor, config, operation, issue=None, **details):
    WorkflowAudit.objects.create(
        project=project,
        actor=actor if actor and actor.is_authenticated else None,
        issue_id_snapshot=issue.pk if issue else None,
        operation=operation,
        revision=config["revision"],
        details=details,
    )
    if issue and operation in {"task.completed", "requirement.completed", "requirement.reopened"}:
        from plane.bgtasks.issue_activities_task import issue_activity
        from .tasks import enqueue_after_commit

        enqueue_after_commit(
            issue_activity,
            type="issue.activity.updated",
            requested_data=json.dumps({"state_id": str(issue.state_id), "workflow_source": "aggregation"}),
            current_instance=json.dumps({"state_id": details.get("from_state_id")}),
            actor_id=str(actor.pk) if actor and actor.is_authenticated else None,
            issue_id=str(issue.pk),
            project_id=str(project.pk),
            epoch=int(timezone.now().timestamp()),
            notification=True,
        )


def unfinished_details(project, actor, ids):
    visible = Issue.objects.filter(project=project, pk__in=ids)
    if project_role(actor, project) == 5 and not project.guest_view_all_features:
        visible = visible.filter(created_by=actor)
    return {
        "unfinished_count": len(ids),
        "unfinished_issues": [{"id": str(row["id"]), "name": row["name"]} for row in visible.values("id", "name")],
    }


def aggregate(project, actor, config, before, after):
    from .test_tasks import complete_test_sources

    groups = {
        str(key): group
        for key, group in State.all_state_objects.filter(project=project, deleted_at__isnull=True).values_list(
            "id", "group"
        )
    }
    # Update task rows in the shared snapshot before evaluating their requirements.
    complete_test_sources(project, actor, config, before, after, groups)
    contexts = IssueWorkflowContext.objects.filter(
        issue__project=project, issue__deleted_at__isnull=True, accepted_at__isnull=False
    ).select_related("issue")
    for context in sorted(contexts, key=lambda item: len(descendants(after, str(item.issue_id)))):
        issue = context.issue
        parent_id = str(issue.pk)
        row = after.get(parent_id)
        if not row or groups.get(str(row["state_id"])) == "cancelled":
            continue
        child_ids = descendants(after, parent_id)
        old_children = descendants(before, parent_id) if parent_id in before else []

        changed = completion_signature(before, old_children) != completion_signature(after, child_ids)
        if changed:
            context.children_revision += 1
        unfinished = [key for key in child_ids if groups.get(str(after[key]["state_id"])) != "completed"]
        complete = groups.get(str(row["state_id"])) == "completed"
        previous = before.get(parent_id)
        manual_reopen = (
            previous
            and previous["state_id"] != row["state_id"]
            and previous["state__group"] == "completed"
            and not complete
        )
        if manual_reopen:
            context.manual_reopen_revision = context.children_revision
            context.blocked_reason = {}
        if complete and unfinished:
            target = config["requirement_reopen_state_id"]
            validate_assignees(project, issue_values(issue)["assignees"])
            evaluate(config, "requirement", str(issue.state_id), target, None, issue_values(issue), system=True)
            issue.state_id = target
            issue.save(update_fields=["state"])
            context.blocked_reason = {}
            audit(
                project,
                actor,
                config,
                "requirement.reopened",
                issue,
                unfinished_count=len(unfinished),
                from_state_id=str(row["state_id"]),
            )
            row["state_id"], row["state__group"] = issue.state_id, groups[str(issue.state_id)]
        elif (
            child_ids and not unfinished and not complete and not manual_reopen and (changed or context.blocked_reason)
        ):
            if context.manual_reopen_revision is None or context.manual_reopen_revision != context.children_revision:
                try:
                    validate_assignees(project, issue_values(issue)["assignees"])
                    evaluate(
                        config,
                        "requirement",
                        str(issue.state_id),
                        config["requirement_completed_state_id"],
                        None,
                        issue_values(issue),
                        system=True,
                    )
                except Violation as exc:
                    reason = {"code": exc.code, "message": exc.message, "details": exc.details}
                    if context.blocked_reason != reason:
                        audit(project, actor, config, "requirement.completion_blocked", issue, **reason)
                    context.blocked_reason = reason
                else:
                    issue.state_id = config["requirement_completed_state_id"]
                    issue.save(update_fields=["state"])
                    context.blocked_reason = {}
                    audit(project, actor, config, "requirement.completed", issue, from_state_id=str(row["state_id"]))
                    row["state_id"], row["state__group"] = issue.state_id, groups[str(issue.state_id)]
        context.save(update_fields=["children_revision", "manual_reopen_revision", "blocked_reason"])


def validate_mutation(project, actor, config, before, *, intake_operation=False):
    after = snapshot(project.pk)
    groups = {
        str(key): group
        for key, group in State.all_state_objects.filter(project=project, deleted_at__isnull=True).values_list(
            "id", "group"
        )
    }
    if config:
        validate_configuration(config, groups)
    contexts = {str(c.issue_id): c for c in IssueWorkflowContext.objects.filter(issue__project=project)}
    intakes = {str(i.issue_id): i for i in IntakeIssue.objects.filter(project=project)}
    for key, row in after.items():
        old = before.get(key)
        intake = intakes.get(key)
        context = contexts.get(key)
        if old is None and not context:
            context = IssueWorkflowContext.objects.create(
                issue_id=key, origin="intake" if intake else "task", original_intake_id=intake.pk if intake else None
            )
            contexts[key] = context
        accepting = intake and intake.status == 1 and context and not context.accepted_at
        if not config:
            if accepting:
                context.accepted_at, context.accepted_by = timezone.now(), actor if actor.is_authenticated else None
                context.save(update_fields=["accepted_at", "accepted_by"])
            continue
        if not row["deleted_at"] and str(row["state_id"]) not in groups:
            raise Violation(
                "WORKFLOW_INVALID_STATE", "仍有工作项使用无效或已删除的状态", {"state_id": str(row["state_id"])}
            )
        if row["deleted_at"] or (row == old and not accepting):
            continue
        if not context:
            raise Violation("WORKFLOW_SOURCE_MISSING", "工作项缺少可信来源记录")
        branch = "requirement" if context.origin == "intake" else "task"
        state = str(row["state_id"])
        if state not in groups:
            raise Violation("WORKFLOW_INVALID_STATE", "工作项状态不属于当前项目或已删除")
        if row["parent_id"]:
            parent = after.get(str(row["parent_id"]))
            if not parent or parent["deleted_at"]:
                raise Violation("WORKFLOW_INVALID_PARENT", "父工作项必须属于同项目且未删除")
            parent_context = contexts.get(str(row["parent_id"]))
            if parent_context and parent_context.origin == "intake" and not parent_context.accepted_at:
                raise Violation("WORKFLOW_UNREVIEWED_PARENT", "待评审需求不能拆分执行子任务")
            descendants(after, key)
        accepted_now = context.accepted_at and old and not old["workflow_context__accepted_at"]
        if accepted_now:
            if not intake_operation or not intake or intake.status != 1 or project_role(actor, project) != 20:
                raise Violation("WORKFLOW_INVALID_SOURCE", "不能通过普通操作伪造 Intake 接收事实")
            if state != config["intake_acceptance_state_id"]:
                raise Violation("WORKFLOW_INVALID_STATE", "接收必须进入配置的接收状态")
            validate_fields(config, "requirement", state, row)
        elif context.origin == "intake" and not context.accepted_at:
            if intake and intake.status == 1:
                if not intake_operation or project_role(actor, project) != 20:
                    raise Violation("WORKFLOW_INTAKE_FORBIDDEN", "只有项目管理员可以接收 Intake", status=403)
                context.accepted_at, context.accepted_by = timezone.now(), actor
                context.accepted_revision = config["revision"]
                context.save(update_fields=["accepted_at", "accepted_by", "accepted_revision"])
                issue = Issue.objects.get(pk=key)
                issue.state_id = config["intake_acceptance_state_id"]
                validate_fields(config, branch, str(issue.state_id), issue_values(issue))
                issue.save(update_fields=["state"])
                row["state_id"] = issue.state_id
                audit(project, actor, config, "intake.accepted", issue)
            elif old is None:
                if not intake_operation or not intake:
                    raise Violation("WORKFLOW_INVALID_SOURCE", "不能伪造 Intake 来源")
                if state != config["intake_submission_state_id"]:
                    raise Violation("WORKFLOW_INITIAL_STATE_REQUIRED", "Intake 必须使用项目配置的提交状态")
                validate_fields(config, branch, state, row)
            elif row["state_id"] != old["state_id"]:
                raise Violation("WORKFLOW_INTAKE_NOT_ACCEPTED", "待评审需求不能通过普通状态修改进入执行流程")
            else:
                validate_fields(config, branch, state, row)
        elif old is None:
            if state != config["task_initial_state_id"]:
                raise Violation("WORKFLOW_INITIAL_STATE_REQUIRED", "新任务必须使用项目配置的初始状态")
            validate_fields(config, "task", state, row)
        else:
            if context.accepted_at and intake and intake.status != 1:
                raise Violation("WORKFLOW_ACCEPTANCE_IMMUTABLE", "不能清除已发生的评审接收事实")
            evaluate(config, branch, str(old["state_id"]), state, project_role(actor, project), row)
            if state != str(old["state_id"]):
                audit(
                    project,
                    actor,
                    config,
                    "issue.transitioned",
                    Issue.objects.get(pk=key),
                    from_state_id=str(old["state_id"]),
                    to_state_id=state,
                )
        validate_assignees(project, row["assignees"])
        if groups.get(str(row["state_id"])) == "completed":
            unfinished = [
                child for child in descendants(after, key) if groups.get(str(after[child]["state_id"])) != "completed"
            ]
            if unfinished:
                raise Violation(
                    "WORKFLOW_CHILDREN_INCOMPLETE",
                    "还有未完成子工作项，不能完成该工作项",
                    {**unfinished_details(project, actor, unfinished), "issue_id": key},
                )
        if old is None or row["state_id"] != old["state_id"] or row["state__group"] != old["state__group"]:
            Issue.objects.filter(pk=key).update(
                completed_at=timezone.now() if groups.get(str(row["state_id"])) == "completed" else None
            )
    if config:
        # Intake-only updates can change acceptance without touching the issue timestamp.
        for key, intake in intakes.items():
            context = contexts.get(key)
            if context and context.accepted_at and intake.status != 1:
                raise Violation("WORKFLOW_ACCEPTANCE_IMMUTABLE", "不能清除已发生的评审接收事实")
        from .test_tasks import create_test_tasks

        if create_test_tasks(project, actor, config, before, after):
            after = snapshot(project.pk)
        aggregate(project, actor, config, before, after)


WORKFLOW_MUTATION_VIEWS = {
    "IssueViewSet",
    "IssueListCreateAPIEndpoint",
    "IssueDetailAPIEndpoint",
    "IssueBulkUpdateDateEndpoint",
    "WorkflowBulkUpdateEndpoint",
    "BulkDeleteIssuesEndpoint",
    "SubIssuesEndpoint",
    "IssueArchiveViewSet",
    "BulkArchiveIssuesEndpoint",
    "WorkspaceDraftIssueViewSet",
    "IntakeIssueViewSet",
    "IntakeViewSet",
    "IntakeIssueListCreateAPIEndpoint",
    "IntakeIssueDetailAPIEndpoint",
    "IntakeIssuePublicViewSet",
    "StateViewSet",
    "StateListCreateAPIEndpoint",
    "StateDetailAPIEndpoint",
}


def _guarded_handler(handler, view, request, *args, **kwargs):
    from .adapters import preflight_request

    if active_mutation.get() is not None:
        return handler(request, *args, **kwargs)
    project_id = kwargs.get("project_id")
    if not project_id and kwargs.get("anchor"):
        project_id = DeployBoard.objects.filter(anchor=kwargs["anchor"]).values_list("project_id", flat=True).first()
    projects = (
        Project.objects.filter(pk=project_id)
        if project_id
        else Project.objects.filter(workspace__slug=kwargs["slug"])
        if kwargs.get("slug")
        else Project.objects.none()
    )
    # Classify the trusted handler, including legacy /inbox-issues/ aliases.
    intake_operation = any(
        base.__name__ in WORKFLOW_MUTATION_VIEWS and base.__name__.startswith("Intake") for base in type(view).__mro__
    )
    with transaction.atomic():
        locked = list(projects.select_for_update().order_by("id"))
        before = {project.pk: snapshot(project.pk) for project in locked}
        configs = {
            project.pk: canonical(configuration(workflow))
            if (workflow := ProjectWorkflow.objects.filter(project=project, enabled=True).first())
            else None
            for project in locked
        }
        token = active_mutation.set(
            {
                "projects": {project.pk: project for project in locked},
                "configs": configs,
                "intake": intake_operation,
            }
        )
        try:
            preflight_request(view, request)
            response = handler(request, *args, **kwargs)
            if response.status_code >= 400:
                transaction.set_rollback(True)
                return response
            for project in locked:
                validate_mutation(
                    project,
                    request.user,
                    configs[project.pk],
                    before[project.pk],
                    intake_operation=intake_operation,
                )
            if any(configs.values()):
                response["X-Plane-Workflow"] = "enabled"
            return response
        except Violation as exc:
            transaction.set_rollback(True)
            return Response({"code": exc.code, "message": exc.message, "details": exc.details}, status=exc.status)
        finally:
            active_mutation.reset(token)


def mutation_dispatch(method):
    """Install the project-first transaction after DRF authentication and permissions.

    Limit the adapter to the inventoried work-item/configuration mutations; links,
    reactions and other unrelated resources keep their existing execution path.
    """

    @wraps(method)
    def wrapped(view, request, *args, **kwargs):
        if (
            request.method in {"GET", "HEAD", "OPTIONS"}
            or request.path.endswith(("/preview/", "/validate/"))
            or not any(base.__name__ in WORKFLOW_MUTATION_VIEWS for base in type(view).__mro__)
        ):
            return method(view, request, *args, **kwargs)
        original_initial = view.initial

        def authenticated_initial(authenticated_request, *initial_args, **initial_kwargs):
            original_initial(authenticated_request, *initial_args, **initial_kwargs)
            method_name = authenticated_request.method.lower()
            original_handler = getattr(view, method_name)

            @wraps(original_handler)
            def handler(current_request, *handler_args, **handler_kwargs):
                return _guarded_handler(original_handler, view, current_request, *handler_args, **handler_kwargs)

            setattr(view, method_name, handler)

        view.initial = authenticated_initial
        return method(view, request, *args, **kwargs)

    return wrapped


@contextmanager
def project_mutation(project, actor):
    """Explicit adapter for background operations outside the HTTP stack."""
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.pk)
        before = snapshot(project.pk)
        workflow = ProjectWorkflow.objects.filter(project=project, enabled=True).first()
        config = canonical(configuration(workflow)) if workflow else None
        yield
        validate_mutation(project, actor, config, before)

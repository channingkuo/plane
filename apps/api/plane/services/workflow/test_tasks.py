# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Trusted test-task creation and completion of their development sources."""

from copy import deepcopy

from plane.db.models import Issue, IssueWorkflowContext, IssueAssignee, IssueLabel, CycleIssue, ModuleIssue
from .configuration import issue_values, validate_assignees
from .rules import Violation, evaluate, validate_fields


def create_test_tasks(project, actor, config, before, after):
    from .execution import audit

    state = config.get("test_task_initial_state_id")
    if not state:
        return False
    paths = {
        (row["from_state_id"], row["to_state_id"])
        for row in config["transitions"]
        if row["branch"] == "task" and row.get("creates_test_task")
    }
    created = False
    for key, current in after.items():
        previous = before.get(key)
        if not previous or current["deleted_at"] or previous["state_id"] == current["state_id"]:
            continue
        if (str(previous["state_id"]), str(current["state_id"])) not in paths:
            continue
        context = IssueWorkflowContext.objects.filter(issue_id=key, origin="task", test_source__isnull=True).first()
        if not context or IssueWorkflowContext.all_objects.filter(test_source_id=key).exists():
            continue
        source = Issue.objects.get(pk=key, project=project)
        values = issue_values(source)
        validate_assignees(project, values["assignees"])
        validate_fields(config, "task", state, values)
        fields = {
            field: deepcopy(getattr(source, field))
            for field in (
                "description_json",
                "description_html",
                "priority",
                "start_date",
                "target_date",
                "point",
                "estimate_point_id",
                "type_id",
            )
        }
        fields["description_binary"] = (
            bytes(source.description_binary) if source.description_binary is not None else None
        )
        child = Issue.objects.create(
            project=project,
            workspace_id=project.workspace_id,
            parent=source,
            state_id=state,
            name=f"【自动生成测试任务】{source.name}"[:255],
            created_by=actor,
            updated_by=actor,
            **fields,
        )
        IssueWorkflowContext.objects.create(issue=child, origin="task", test_source=source)
        for model, relation in (
            (IssueAssignee, "assignee_id"),
            (IssueLabel, "label_id"),
            (CycleIssue, "cycle_id"),
            (ModuleIssue, "module_id"),
        ):
            model.objects.bulk_create(
                [
                    model(
                        issue=child,
                        project=project,
                        workspace_id=project.workspace_id,
                        created_by=actor,
                        updated_by=actor,
                        **{relation: value},
                    )
                    for value in model.objects.filter(issue=source).values_list(relation, flat=True)
                ]
            )
        audit(project, actor, config, "test_task.created", child, source_issue_id=str(source.pk))
        created = True
    return created


def complete_test_sources(project, actor, config, before, after, groups):
    from .execution import audit, completion_signature, descendants

    if not config.get("test_task_initial_state_id"):
        return
    ready_states = {
        row["to_state_id"] for row in config["transitions"] if row["branch"] == "task" and row.get("creates_test_task")
    }
    tests = IssueWorkflowContext.objects.filter(
        origin="task",
        issue__project=project,
        issue__deleted_at__isnull=True,
        test_source__project=project,
        test_source__deleted_at__isnull=True,
        test_source__workflow_context__origin="task",
        test_source__workflow_context__deleted_at__isnull=True,
        test_source__workflow_context__test_source__isnull=True,
    ).select_related("test_source", "test_source__workflow_context")
    # A source may itself contain another development source. Finish the deepest
    # source first so every ancestor sees the final completion result immediately.
    for test in sorted(tests, key=lambda item: len(descendants(after, str(item.test_source_id)))):
        source = test.test_source
        source_id = str(source.pk)
        row, test_row = after.get(source_id), after.get(str(test.issue_id))
        context = source.workflow_context
        child_ids = descendants(after, source_id)
        eligible = (
            row
            and test_row
            and str(row["state_id"]) in ready_states
            and str(test_row["parent_id"]) == source_id
            and groups.get(str(test_row["state_id"])) == "completed"
            and all(groups.get(str(after[key]["state_id"])) == "completed" for key in child_ids)
        )
        if not eligible:
            if context.blocked_reason:
                context.blocked_reason = {}
                context.save(update_fields=["blocked_reason"])
            continue
        old_children = descendants(before, source_id) if source_id in before else []
        if not context.blocked_reason and completion_signature(before, old_children) == completion_signature(
            after, child_ids
        ):
            continue
        target = str(test_row["state_id"])
        try:
            values = issue_values(source)
            validate_assignees(project, values["assignees"])
            evaluate(config, "task", str(row["state_id"]), target, None, values, system=True)
        except Violation as exc:
            reason = {"code": exc.code, "message": exc.message, "details": exc.details}
            if context.blocked_reason != reason:
                audit(project, actor, config, "task.completion_blocked", source, **reason)
            context.blocked_reason = reason
        else:
            source.state_id = target
            source.save(update_fields=["state"])
            context.blocked_reason = {}
            audit(
                project,
                actor,
                config,
                "task.completed",
                source,
                from_state_id=str(row["state_id"]),
                test_issue_id=str(test.issue_id),
            )
            row["state_id"], row["state__group"] = source.state_id, groups[target]
        context.save(update_fields=["blocked_reason"])

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Trusted test-task creation, called only after a validated task transition."""

from copy import deepcopy

from plane.db.models import Issue, IssueWorkflowContext, IssueAssignee, IssueLabel, CycleIssue, ModuleIssue
from .configuration import issue_values, validate_assignees
from .rules import validate_fields


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
            name=f"测试：{source.name}"[:255],
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

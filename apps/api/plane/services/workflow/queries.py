# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from plane.db.models import IssueWorkflowContext, ProjectWorkflow, State, WorkspaceMember
from .configuration import canonical, configuration, project_role, issue_values, validate_assignees
from .execution import descendants, snapshot, unfinished_details
from .rules import Violation, evaluate


def capabilities(issue, actor, patch=None):
    workflow = ProjectWorkflow.objects.filter(project_id=issue.project_id, enabled=True).first()
    if not workflow:
        return {"enabled": False}
    config = canonical(configuration(workflow))
    context = IssueWorkflowContext.objects.filter(issue=issue).first()
    if not context:
        return {
            "enabled": True,
            "revision": config["revision"],
            "targets": [],
            "blocked_reason": {"code": "WORKFLOW_SOURCE_MISSING"},
        }
    branch = "requirement" if context.origin == "intake" else "task"
    values = {**issue_values(issue), **(patch or {})}
    role = project_role(actor, issue.project)
    workspace_admin_fallback = (
        role == 5
        and WorkspaceMember.objects.filter(
            workspace_id=issue.workspace_id, member=actor, is_active=True, role=20
        ).exists()
    )
    nodes = snapshot(issue.project_id)
    child_ids = descendants(nodes, str(issue.pk))
    completed_states = set(
        map(str, State.objects.filter(project_id=issue.project_id, group="completed").values_list("id", flat=True))
    )
    targets = []
    for transition in config["transitions"]:
        if transition["branch"] != branch or transition["from_state_id"] != str(issue.state_id):
            continue
        target = transition["to_state_id"]
        allowed, problem = True, None
        try:
            if context.origin == "intake" and not context.accepted_at:
                raise Violation("WORKFLOW_INTAKE_NOT_ACCEPTED", "请先通过 Intake 接收评审")
            if role is None or (role == 5 and issue.created_by_id != actor.pk and not workspace_admin_fallback):
                raise Violation("WORKFLOW_ROLE_FORBIDDEN", "当前角色没有工作项写入权限", status=403)
            evaluate(config, branch, str(issue.state_id), target, role, values)
            validate_assignees(issue.project, values.get("assignees", []))
            if target in completed_states:
                unfinished = [key for key in child_ids if str(nodes[key]["state_id"]) not in completed_states]
                if unfinished:
                    raise Violation(
                        "WORKFLOW_CHILDREN_INCOMPLETE",
                        "还有未完成子工作项",
                        unfinished_details(issue.project, actor, unfinished),
                    )
        except Violation as exc:
            allowed = False
            problem = {"code": exc.code, "message": exc.message, "details": exc.details}
        targets.append({"state_id": target, "allowed": allowed, "reason": problem})
    return {
        "descendant_count": len(child_ids),
        "completed_descendant_count": sum(str(nodes[key]["state_id"]) in completed_states for key in child_ids),
        "enabled": True,
        "revision": config["revision"],
        "branch": branch,
        "reviewed": bool(context.accepted_at),
        "completion_state_id": config["requirement_completed_state_id"],
        "reopen_state_id": config["requirement_reopen_state_id"],
        "targets": targets,
        "blocked_reason": context.blocked_reason,
        "can_accept": context.origin == "intake" and not context.accepted_at and role == 20,
        "can_change_requirement_state": branch != "requirement" or role == 20,
    }

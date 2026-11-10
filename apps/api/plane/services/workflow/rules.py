# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Side-effect-free workflow rules shared by previews and execution."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Violation(Exception):
    code: str
    message: str
    details: dict = field(default_factory=dict)
    status: int = 400


STATE_FIELDS = (
    "task_initial_state_id",
    "intake_submission_state_id",
    "intake_acceptance_state_id",
    "requirement_completed_state_id",
    "requirement_reopen_state_id",
)
REQUIRED_FIELDS = {"assignees", "start_date", "target_date"}
ROLES = {5, 15, 20}
BRANCHES = {"task", "requirement"}


def validate_configuration(config, states):
    """States maps canonical string IDs to their group; all must belong to the project."""
    for key in STATE_FIELDS:
        if config.get(key) not in states:
            raise Violation("WORKFLOW_INVALID_CONFIGURATION", "请选择当前项目的有效状态", {"field": key})
    task, pending, accepted, completed, reopened = [config[key] for key in STATE_FIELDS]
    if pending == accepted:
        raise Violation("WORKFLOW_INVALID_CONFIGURATION", "提交状态和接收状态必须不同")
    if any(states[s] in {"completed", "cancelled"} for s in (task, pending, accepted)):
        raise Violation("WORKFLOW_INVALID_CONFIGURATION", "初始及评审状态不能是完成或取消状态")
    if states[task] == "triage" or states[accepted] == "triage":
        raise Violation("WORKFLOW_INVALID_CONFIGURATION", "任务初始状态和接收状态不能属于 Triage")
    if states[completed] != "completed" or states[reopened] in {"completed", "cancelled", "triage"}:
        raise Violation("WORKFLOW_INVALID_CONFIGURATION", "父需求完成或退回状态分组不合法")
    seen = set()
    for transition in config.get("transitions", []):
        branch = transition.get("branch")
        source, target = transition.get("from_state_id"), transition.get("to_state_id")
        roles = transition.get("roles")
        key = (branch, source, target)
        if branch not in BRANCHES or source not in states or target not in states or key in seen:
            raise Violation("WORKFLOW_INVALID_CONFIGURATION", "流转状态、分支或唯一性不合法")
        if (
            not isinstance(roles, list)
            or not roles
            or any(type(role) is not int or role not in ROLES for role in roles)
        ):
            raise Violation("WORKFLOW_INVALID_CONFIGURATION", "请选择有效的项目角色")
        seen.add(key)
    for source, target in {(accepted, completed), (reopened, completed), (completed, reopened)}:
        if not any(
            t.get("branch") == "requirement"
            and t.get("from_state_id") == source
            and t.get("to_state_id") == target
            and 20 in t.get("roles", [])
            for t in config.get("transitions", [])
        ):
            raise Violation("WORKFLOW_INVALID_CONFIGURATION", "父需求完成及重新打开路径必须允许项目管理员")
    seen = set()
    for requirement in config.get("requirements", []):
        branch, state = requirement.get("branch"), requirement.get("state_id")
        fields = requirement.get("fields")
        if (
            branch not in BRANCHES
            or state not in states
            or (branch, state) in seen
            or not isinstance(fields, list)
            or any(f not in REQUIRED_FIELDS for f in fields)
        ):
            raise Violation("WORKFLOW_INVALID_CONFIGURATION", "必填字段配置不合法")
        seen.add((branch, state))


def validate_fields(config, branch, state, values):
    fields = next(
        (r["fields"] for r in config.get("requirements", []) if r["branch"] == branch and r["state_id"] == state), []
    )
    missing = [key for key in fields if not values.get(key)]
    if missing:
        raise Violation(
            "WORKFLOW_REQUIRED_FIELDS_MISSING",
            "请补齐当前状态的必填字段",
            {"fields": missing, "to_state_id": state, "workflow_revision": config["revision"]},
        )
    start, end = values.get("start_date"), values.get("target_date")
    try:
        start = date.fromisoformat(str(start)) if start else None
        end = date.fromisoformat(str(end)) if end else None
    except ValueError:
        raise Violation("WORKFLOW_INVALID_DATE", "请输入有效日期") from None
    if start and end and start > end:
        raise Violation("WORKFLOW_INVALID_DATE", "开始日期不得晚于截止日期", {"fields": ["start_date", "target_date"]})


def evaluate(config, branch, source, target, role, values, *, system=False):
    if not system and source != target:
        if branch == "requirement" and role != 20:
            raise Violation("WORKFLOW_ROLE_FORBIDDEN", "只有项目管理员可以修改父需求状态", status=403)
        transition = next(
            (
                t
                for t in config["transitions"]
                if t["branch"] == branch and t["from_state_id"] == source and t["to_state_id"] == target
            ),
            None,
        )
        if not transition:
            raise Violation("WORKFLOW_TRANSITION_NOT_ALLOWED", "项目未配置该状态流转")
        if role not in transition["roles"]:
            raise Violation("WORKFLOW_ROLE_FORBIDDEN", "当前项目角色无权执行该流转", status=403)
    validate_fields(config, branch, target, values)

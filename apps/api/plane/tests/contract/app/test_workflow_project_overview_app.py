# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connections
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueWorkflowContext,
    Intake,
    IntakeIssue,
    ProjectMember,
    WorkspaceMember,
    State,
    User,
    IssueLabel,
    Label,
    WorkflowAudit,
    ProjectWorkflow,
    Cycle,
    CycleIssue,
    Module,
    ModuleIssue,
)
from . import test_project_workflow_app as workflow_fixtures
from .test_project_workflow_app import enable, create_task, issue_url, url

workflow_project = workflow_fixtures.workflow_project
workflow_settings = workflow_fixtures.workflow_settings

pytestmark = [pytest.mark.django_db, pytest.mark.contract]


@pytest.fixture
def testing_config(workflow_project):
    project, config = workflow_project
    states = {}
    for name in ("Developing", "Development ready", "Testing"):
        states[name] = str(
            State.objects.create(
                project=project, workspace=project.workspace, name=name, group="started", color="#abc123"
            ).pk
        )
    config["test_task_initial_state_id"] = states["Testing"]
    config["transitions"] += [
        {
            "branch": "task",
            "from_state_id": config["task_initial_state_id"],
            "to_state_id": states["Developing"],
            "roles": [20, 15],
        },
        {
            "branch": "task",
            "from_state_id": states["Developing"],
            "to_state_id": states["Development ready"],
            "roles": [20, 15],
            "creates_test_task": True,
        },
        {
            "branch": "task",
            "from_state_id": states["Development ready"],
            "to_state_id": states["Developing"],
            "roles": [20, 15],
        },
        {"branch": "task", "from_state_id": states["Testing"], "to_state_id": states["Developing"], "roles": [20, 15]},
        {
            "branch": "task",
            "from_state_id": states["Testing"],
            "to_state_id": config["requirement_completed_state_id"],
            "roles": [20, 15],
        },
        {
            "branch": "task",
            "from_state_id": config["task_initial_state_id"],
            "to_state_id": config["requirement_completed_state_id"],
            "roles": [20, 15],
        },
    ]
    return project, config, states


def change(client, project, issue, state, expected=204):
    response = client.patch(issue_url(project, issue.pk), {"state_id": state}, format="json")
    assert response.status_code == expected, getattr(response, "data", None)
    return response


def ready_source(client, project, states, **data):
    source = create_task(client, project, **data)
    change(client, project, source, states["Developing"])
    change(client, project, source, states["Development ready"])
    return source, Issue.objects.get(parent=source, workflow_context__test_source=source)


@pytest.mark.parametrize("with_requirement", [False, True])
def test_test_completion_finishes_source_then_requirement(
    session_client, testing_config, create_user, with_requirement, django_capture_on_commit_callbacks, mocker
):
    project, config, states = testing_config
    # A custom test completion state need not be the requirement completion state.
    test_done = str(
        State.objects.create(project=project, workspace=project.workspace, name="QA passed", group="completed").pk
    )
    config["transitions"] += [
        {"branch": "task", "from_state_id": states["Testing"], "to_state_id": test_done, "roles": [15, 20]},
        {"branch": "task", "from_state_id": test_done, "to_state_id": states["Testing"], "roles": [15, 20]},
    ]
    enable(session_client, project, config)
    parent = workflow_fixtures.approved_parent(project, config, create_user) if with_requirement else None
    source, test = ready_source(session_client, project, states, **({"parent_id": str(parent.pk)} if parent else {}))
    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    activity = mocker.patch("plane.bgtasks.issue_activities_task.issue_activity.delay")
    mocker.patch("plane.app.views.issue.base.model_activity.delay")
    mocker.patch("plane.app.views.issue.base.issue_description_version_task.delay")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        change(session_client, project, test, test_done)
        assert not activity.called
    assert callbacks
    assert any(
        call.kwargs["issue_id"] == str(source.pk)
        and '"workflow_source": "aggregation"' in call.kwargs["requested_data"]
        for call in activity.call_args_list
    )
    source.refresh_from_db()
    assert str(source.state_id) == test_done
    assert source.completed_at is not None
    assert WorkflowAudit.objects.filter(project=project, operation="task.completed", actor=create_user).count() == 1
    if parent:
        parent.refresh_from_db()
        assert str(parent.state_id) == config["requirement_completed_state_id"]
        assert parent.completed_at is not None
    change(session_client, project, test, test_done)
    assert WorkflowAudit.objects.filter(project=project, operation="task.completed").count() == 1
    assert Issue.objects.filter(parent=source).count() == 1
    if parent:
        # Test reopening still propagates to the requirement through all descendants.
        change(session_client, project, test, states["Testing"])
        parent.refresh_from_db()
        assert str(parent.state_id) == config["requirement_reopen_state_id"]


@pytest.mark.parametrize("pending_kind", ["unstarted", "archived", "cancelled"])
def test_test_completion_waits_for_all_source_descendants(session_client, testing_config, pending_kind):
    project, config, states = testing_config
    cancelled = str(
        State.objects.create(project=project, workspace=project.workspace, name="Cancelled", group="cancelled").pk
    )
    config["transitions"] += [
        {"branch": "task", "from_state_id": config["task_initial_state_id"], "to_state_id": cancelled, "roles": [20]},
        {
            "branch": "task",
            "from_state_id": cancelled,
            "to_state_id": config["requirement_completed_state_id"],
            "roles": [20],
        },
    ]
    enable(session_client, project, config)
    source, test = ready_source(session_client, project, states)
    sibling = create_task(session_client, project, name="Another child", parent_id=str(source.pk))
    if pending_kind == "archived":
        Issue.objects.filter(pk=sibling.pk).update(archived_at=timezone.now())
    elif pending_kind == "cancelled":
        change(session_client, project, sibling, cancelled)
    change(session_client, project, test, config["requirement_completed_state_id"])
    source.refresh_from_db()
    assert str(source.state_id) == states["Development ready"]
    if pending_kind == "archived":
        Issue.objects.filter(pk=sibling.pk).update(archived_at=None)
    change(session_client, project, sibling, config["requirement_completed_state_id"])
    source.refresh_from_db()
    assert str(source.state_id) == config["requirement_completed_state_id"]


def test_test_completion_waits_for_other_requirement_branches(session_client, testing_config, create_user):
    project, config, states = testing_config
    enable(session_client, project, config)
    parent = workflow_fixtures.approved_parent(project, config, create_user)
    first, first_test = ready_source(session_client, project, states, name="First", parent_id=str(parent.pk))
    second, second_test = ready_source(session_client, project, states, name="Second", parent_id=str(parent.pk))
    change(session_client, project, first_test, config["requirement_completed_state_id"])
    first.refresh_from_db()
    parent.refresh_from_db()
    assert str(first.state_id) == config["requirement_completed_state_id"]
    assert str(parent.state_id) == config["intake_acceptance_state_id"]
    change(session_client, project, second_test, config["requirement_completed_state_id"])
    second.refresh_from_db()
    parent.refresh_from_db()
    assert str(second.state_id) == str(parent.state_id) == config["requirement_completed_state_id"]


def test_test_completion_aggregates_nested_sources_bottom_up(session_client, testing_config, create_user):
    project, config, states = testing_config
    enable(session_client, project, config)
    parent = workflow_fixtures.approved_parent(project, config, create_user)
    outer, outer_test = ready_source(session_client, project, states, name="Outer", parent_id=str(parent.pk))
    inner, inner_test = ready_source(session_client, project, states, name="Inner", parent_id=str(outer.pk))
    done = config["requirement_completed_state_id"]
    change(session_client, project, outer_test, done)
    change(session_client, project, inner_test, done)
    assert Issue.objects.filter(pk__in=[parent.pk, outer.pk, inner.pk], state_id=done).count() == 3


@pytest.mark.parametrize("branch", ["task", "requirement"])
def test_test_completion_blocked_fields_retry_without_repeating_test(
    session_client, testing_config, create_user, branch
):
    project, config, states = testing_config
    done = config["requirement_completed_state_id"]
    config["requirements"] = [{"branch": branch, "state_id": done, "fields": ["target_date"]}]
    enable(session_client, project, config)
    parent = workflow_fixtures.approved_parent(project, config, create_user)
    source, test = ready_source(session_client, project, states, parent_id=str(parent.pk))
    response = session_client.patch(
        issue_url(project, test.pk), {"state_id": done, "target_date": "2026-11-15"}, format="json"
    )
    assert response.status_code == 204, response.data
    blocked = source if branch == "task" else parent
    blocked.refresh_from_db()
    test.refresh_from_db()
    assert str(test.state_id) == done
    assert str(blocked.state_id) != done
    capabilities = session_client.get(issue_url(project, blocked.pk) + "workflow/").data
    assert capabilities["blocked_reason"]["code"] == "WORKFLOW_REQUIRED_FIELDS_MISSING"
    assert capabilities["blocked_reason"]["details"]["fields"] == ["target_date"]
    filled = session_client.patch(issue_url(project, blocked.pk), {"target_date": "2026-11-15"}, format="json")
    assert filled.status_code == 204, filled.data
    assert Issue.objects.filter(pk__in=[source.pk, parent.pk], state_id=done, completed_at__isnull=False).count() == 2
    assert not IssueWorkflowContext.objects.get(issue=blocked).blocked_reason


@pytest.mark.parametrize("case", ["ordinary", "detached", "deleted", "developing", "cancelled", "disabled"])
def test_test_completion_does_not_advance_unrelated_or_ineligible_source(session_client, testing_config, case):
    project, config, states = testing_config
    cancelled = State.objects.create(project=project, workspace=project.workspace, name="Cancelled", group="cancelled")
    config["transitions"].append(
        {
            "branch": "task",
            "from_state_id": states["Development ready"],
            "to_state_id": str(cancelled.pk),
            "roles": [20],
        }
    )
    enable(session_client, project, config)
    source, test = ready_source(session_client, project, states)
    if case in {"ordinary", "deleted"}:
        test.delete()
        test = create_task(session_client, project, parent_id=str(source.pk) if case == "ordinary" else None)
    elif case == "detached":
        response = session_client.patch(issue_url(project, test.pk), {"parent_id": None}, format="json")
        assert response.status_code == 204, response.data
    elif case == "developing":
        change(session_client, project, source, states["Developing"])
    elif case == "cancelled":
        change(session_client, project, source, str(cancelled.pk))
    elif case == "disabled":
        ProjectWorkflow.objects.filter(project=project).update(enabled=False)
    source.refresh_from_db()
    old_state = source.state_id
    change(session_client, project, test, config["requirement_completed_state_id"])
    source.refresh_from_db()
    assert source.state_id == old_state
    assert not WorkflowAudit.objects.filter(project=project, operation="task.completed").exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_test_completion_aggregates_once(session_client, testing_config, create_user, mocker):
    mocker.patch("plane.app.views.issue.base.issue_activity.delay")
    mocker.patch("plane.app.views.issue.base.model_activity.delay")
    mocker.patch("plane.app.views.issue.base.issue_description_version_task.delay")
    project, config, states = testing_config
    enable(session_client, project, config)
    parent = workflow_fixtures.approved_parent(project, config, create_user)
    source, test = ready_source(session_client, project, states, parent_id=str(parent.pk))
    barrier = Barrier(2)
    user_id, done = create_user.pk, config["requirement_completed_state_id"]

    def complete(_):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=User.objects.get(pk=user_id))
            barrier.wait(timeout=10)
            response = client.patch(issue_url(project, test.pk), {"state_id": done}, format="json")
            return response.status_code, getattr(response, "data", None)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, range(2)))
    assert all(status == 204 for status, _ in results), results
    assert Issue.objects.filter(pk__in=[parent.pk, source.pk, test.pk], state_id=done).count() == 3
    assert WorkflowAudit.objects.filter(project=project, operation="task.completed").count() == 1
    assert WorkflowAudit.objects.filter(project=project, operation="requirement.completed").count() == 1


@pytest.mark.parametrize("source_name", ["Feature", "开发工作项" * 51])
def test_test_task_copies_content_and_relations_once(session_client, testing_config, create_user, source_name):
    project, config, states = testing_config
    enable(session_client, project, config)
    source = create_task(
        session_client,
        project,
        name=source_name,
        description_html="<p>Detailed content</p>",
        priority="high",
        start_date="2026-11-11",
        target_date="2026-11-15",
        assignee_ids=[str(create_user.pk)],
    )
    label = Label.objects.create(project=project, workspace=project.workspace, name="Feature label")
    IssueLabel.objects.create(issue=source, label=label, project=project, workspace=project.workspace)
    cycle = Cycle.objects.create(project=project, workspace=project.workspace, name="Release", owned_by=create_user)
    module = Module.objects.create(project=project, workspace=project.workspace, name="Feature area")
    CycleIssue.objects.create(issue=source, cycle=cycle, project=project, workspace=project.workspace)
    ModuleIssue.objects.create(issue=source, module=module, project=project, workspace=project.workspace)
    source.description_binary = b"document"
    source.description_json = {"text": "Details"}
    source.save()
    change(session_client, project, source, states["Developing"])
    # State names are presentation only.
    State.objects.filter(pk=states["Development ready"]).update(name="Ready for QA")
    change(session_client, project, source, states["Development ready"])
    child = Issue.objects.get(parent=source)
    assert child.name == f"【自动生成测试任务】{source_name}"[:255]
    assert str(child.state_id) == config["test_task_initial_state_id"]
    assert child.description_html == source.description_html
    assert child.description_json == source.description_json
    assert bytes(child.description_binary) == b"document"
    assert (child.priority, child.start_date, child.target_date) == (
        source.priority,
        source.start_date,
        source.target_date,
    )
    assert set(child.assignees.values_list("pk", flat=True)) == {create_user.pk}
    assert set(child.labels.values_list("pk", flat=True)) == {label.pk}
    assert set(CycleIssue.objects.filter(issue=child).values_list("cycle_id", flat=True)) == {cycle.pk}
    assert set(ModuleIssue.objects.filter(issue=child).values_list("module_id", flat=True)) == {module.pk}
    assert child.workflow_context.test_source_id == source.pk
    change(session_client, project, source, states["Development ready"])
    change(session_client, project, source, states["Developing"])
    change(session_client, project, source, states["Development ready"])
    change(session_client, project, child, states["Developing"])
    change(session_client, project, child, states["Development ready"])
    assert Issue.objects.filter(parent=source).count() == 1
    assert not Issue.objects.filter(parent=child).exists()
    assert WorkflowAudit.objects.filter(project=project, operation="test_task.created").count() == 1
    denied = session_client.post(
        issue_url(project),
        {"name": "Forged test", "state_id": states["Testing"], "test_source": str(source.pk)},
        format="json",
    )
    assert denied.status_code == 400
    # Soft deletion must not erase the durable one-per-source creation fact.
    child.delete()
    change(session_client, project, source, states["Developing"])
    change(session_client, project, source, states["Development ready"])
    assert IssueWorkflowContext.all_objects.filter(test_source=source).count() == 1
    assert not Issue.objects.filter(parent=source).exists()
    # A hard cascade may remove both items without a protective FK dead end.
    Issue.objects.filter(pk=source.pk).delete(soft=False)


def test_test_task_missing_fields_rolls_back_transition(session_client, testing_config):
    project, config, states = testing_config
    config["requirements"] = [{"branch": "task", "state_id": states["Testing"], "fields": ["target_date"]}]
    enable(session_client, project, config)
    source = create_task(session_client, project)
    change(session_client, project, source, states["Developing"])
    response = change(session_client, project, source, states["Development ready"], 400)
    assert response.data["code"] == "WORKFLOW_REQUIRED_FIELDS_MISSING"
    source.refresh_from_db()
    assert str(source.state_id) == states["Developing"]
    assert not Issue.objects.filter(parent=source).exists()
    assert not WorkflowAudit.objects.filter(project=project, operation="test_task.created").exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_transitions_create_one_test_task(session_client, testing_config, create_user):
    project, config, states = testing_config
    enable(session_client, project, config)
    source = create_task(session_client, project)
    change(session_client, project, source, states["Developing"])
    barrier = Barrier(2)
    user_id = create_user.pk

    def transition(_):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=User.objects.get(pk=user_id))
            barrier.wait(timeout=10)
            response = client.patch(
                issue_url(project, source.pk), {"state_id": states["Development ready"]}, format="json"
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(transition, range(2))) == [204, 204]
    assert Issue.objects.filter(parent=source).count() == 1
    assert IssueWorkflowContext.objects.filter(test_source=source).count() == 1


def test_review_scope_summary_and_pending_write_guard(session_client, workflow_project, create_user):
    project, config = workflow_project
    enable(session_client, project, config)
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    response = session_client.post(
        url(project).replace("workflow/", "intake-issues/"), {"issue": {"name": "Pending review"}}, format="json"
    )
    assert response.status_code == 200, response.data
    pending = Issue.objects.get(project=project, name="Pending review")
    canonical_url = f"/api/workspaces/{project.workspace.slug}/work-items/{project.identifier}-{pending.sequence_id}/"
    assert session_client.get(canonical_url).status_code == 200
    mine = create_task(session_client, project, name="My task", assignee_ids=[str(create_user.pk)])
    other = create_task(session_client, project, name="Other task")
    reviewed = Issue.objects.create(
        project=project, workspace=project.workspace, name="Reviewed", state_id=config["intake_acceptance_state_id"]
    )
    IssueWorkflowContext.objects.create(issue=reviewed, origin="intake", accepted_at=timezone.now())
    archived = create_task(session_client, project, name="Archived")
    Issue.objects.filter(pk=archived.pk).update(archived_at=timezone.now().date())
    assert session_client.get(issue_url(project, archived.pk)).status_code == 200
    # Workspace admin in this project is still a project member for the default scope.
    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    for suffix in ("issues/", "v2/issues/", "issues-detail/?expand=issue_relation"):
        response = session_client.get(url(project).replace("workflow/", suffix))
        assert response.status_code == 200, response.data
        import json

        body = json.dumps(response.data, default=str)
        assert str(mine.pk) in body and str(pending.pk) in body and str(reviewed.pk) in body
        assert str(other.pk) not in body and str(archived.pk) not in body
    all_items = session_client.get(issue_url(project), {"work_item_scope": "all"})
    assert all_items.status_code == 200
    assert all_items.data["total_count"] == 4
    overview = session_client.get(url(project).replace("workflow/", "overview/"))
    assert overview.status_code == 200, overview.data
    assert overview.data["total"] == 3
    assert overview.data["undated_count"] == 3
    assert sum(state["count"] for state in overview.data["states"]) == 3
    assert (
        next(state for state in overview.data["states"] if str(state["id"]) == config["intake_submission_state_id"])[
            "count"
        ]
        == 1
    )
    assert any(state["count"] == 0 for state in overview.data["states"])
    grouped = session_client.get(issue_url(project), {"group_by": "state_id"})
    assert grouped.status_code == 200, grouped.data
    assert grouped.data["total_count"] == 3
    blocked = session_client.patch(
        issue_url(project, pending.pk), {"state_id": config["task_initial_state_id"]}, format="json"
    )
    assert blocked.status_code == 400
    assert blocked.data["code"] == "WORKFLOW_INTAKE_NOT_ACCEPTED"
    intake = IntakeIssue.objects.get(issue=pending)
    intake.status = -1
    intake.save()
    assert not Issue.project_objects.filter(pk=pending.pk).exists()


def test_hierarchy_includes_filtered_ancestors_and_respects_guest_scope(session_client, workflow_project, create_user):
    project, _ = workflow_project
    parent = Issue.objects.create(project=project, workspace=project.workspace, name="Parent")
    middle = Issue.objects.create(project=project, workspace=project.workspace, name="Middle", parent=parent)
    child = Issue.objects.create(
        project=project, workspace=project.workspace, name="Child", parent=middle, created_by=create_user
    )
    Issue.objects.filter(pk=child.pk).update(created_by=create_user)
    endpoint = url(project).replace("workflow/", "issue-hierarchy/")
    data = session_client.get(endpoint).data
    assert data[str(child.pk)]["parent_id"] == middle.pk
    assert data[str(middle.pk)]["parent_id"] == parent.pk
    ProjectMember.objects.filter(project=project, member=create_user).update(role=5)
    WorkspaceMember.objects.filter(workspace=project.workspace, member=create_user).update(role=15)
    data = session_client.get(endpoint).data
    assert str(child.pk) in data
    assert str(parent.pk) not in data and str(middle.pk) not in data
    assert session_client.get(url(project).replace("workflow/", "overview/")).status_code == 403
    ProjectMember.objects.filter(project=project, member=create_user).update(is_active=False)
    assert session_client.get(endpoint).status_code == 403


@pytest.mark.parametrize(
    "patch",
    [
        {"test_task_initial_state_id": "completed"},
        {"test_task_initial_state_id": None},
        {"trigger_branch": "requirement"},
    ],
)
def test_invalid_testing_configuration_is_rejected(session_client, testing_config, patch):
    project, config, _ = testing_config
    config = deepcopy(config)
    if "test_task_initial_state_id" in patch:
        config["test_task_initial_state_id"] = (
            config["requirement_completed_state_id"] if patch["test_task_initial_state_id"] else None
        )
    else:
        next(row for row in config["transitions"] if row.get("creates_test_task"))["branch"] = patch["trigger_branch"]
    assert session_client.put(url(project), config, format="json").status_code == 400
    assert not ProjectWorkflow.objects.filter(project=project).exists()

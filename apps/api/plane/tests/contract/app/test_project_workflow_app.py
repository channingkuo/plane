# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from copy import deepcopy

import pytest

from plane.db.models import (
    Project,
    State,
    ProjectMember,
    WorkspaceMember,
    WorkflowAudit,
    Issue,
    IssueWorkflowContext,
    Intake,
    IntakeIssue,
)

pytestmark = [pytest.mark.django_db, pytest.mark.contract]


@pytest.fixture(autouse=True)
def workflow_settings(settings):
    settings.APP_BASE_URL = "http://testserver"
    settings.WEB_URL = "http://testserver"


@pytest.fixture
def workflow_project(workspace, create_user):
    project = Project.objects.create(name="Workflow project", identifier="WFL", workspace=workspace)
    ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20)
    WorkspaceMember.objects.filter(workspace=workspace, member=create_user).update(role=20)
    states = {}
    for name, group in [("Todo", "unstarted"), ("Review", "triage"), ("Reviewed", "started"), ("Done", "completed")]:
        states[name] = str(
            State.objects.create(name=name, group=group, project=project, workspace=workspace, color="#123456").pk
        )
    return project, {
        "enabled": True,
        "revision": 0,
        "task_initial_state_id": states["Todo"],
        "intake_submission_state_id": states["Review"],
        "intake_acceptance_state_id": states["Reviewed"],
        "requirement_completed_state_id": states["Done"],
        "requirement_reopen_state_id": states["Reviewed"],
        "transitions": [
            {
                "branch": "requirement",
                "from_state_id": states["Reviewed"],
                "to_state_id": states["Done"],
                "roles": [20],
            },
            {
                "branch": "requirement",
                "from_state_id": states["Done"],
                "to_state_id": states["Reviewed"],
                "roles": [20],
            },
        ],
        "requirements": [],
    }


def url(project):
    return f"/api/workspaces/{project.workspace.slug}/projects/{project.pk}/workflow/"


@pytest.mark.parametrize("route", ["intake-issues", "inbox-issues"])
def test_accept_intake_with_form_edits_atomically(session_client, workflow_project, create_user, route):
    project, payload = workflow_project
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    payload["intake_submission_state_id"] = payload["task_initial_state_id"]
    payload["requirements"] = [
        {"branch": "requirement", "state_id": payload["intake_acceptance_state_id"], "fields": ["assignees"]}
    ]
    assert session_client.put(url(project), payload, format="json").status_code == 200
    context = session_client.get(url(project) + "creation/").data
    assert str(context["intake"]["acceptance"]["state"]["id"]) == payload["intake_acceptance_state_id"]
    assert context["intake"]["acceptance"]["fields"] == ["assignees"]
    endpoint = url(project).replace("workflow/", f"{route}/")
    created = session_client.post(endpoint, {"issue": {"name": "Original"}}, format="json")
    assert created.status_code == 200, created.data
    issue = Issue.objects.get(pk=created.data["issue"]["id"])
    detail = endpoint + str(issue.pk) + "/"
    rejected = session_client.patch(detail, {"status": 1, "issue": {"name": "Rejected edit"}}, format="json")
    assert rejected.status_code == 400, rejected.data
    issue.refresh_from_db()
    assert issue.name == "Original"
    assert IntakeIssue.objects.get(issue=issue).status == -2
    assert issue.workflow_context.accepted_at is None
    assert WorkflowAudit.objects.filter(project=project, operation="intake.accepted").count() == 0

    accepted = session_client.patch(
        detail,
        {
            "status": 1,
            "issue": {
                "name": "Reviewed edit",
                "state_id": payload["intake_acceptance_state_id"],
                "assignee_ids": [str(create_user.pk)],
            },
        },
        format="json",
    )
    assert accepted.status_code == 200, accepted.data
    assert str(accepted.data["issue"]["state_id"]) == payload["intake_acceptance_state_id"]
    issue.refresh_from_db()
    assert issue.name == "Reviewed edit"
    assert str(issue.state_id) == payload["intake_acceptance_state_id"]
    assert IntakeIssue.objects.get(issue=issue).status == 1
    assert IssueWorkflowContext.objects.get(issue=issue).accepted_by == create_user
    assert WorkflowAudit.objects.filter(project=project, operation="intake.accepted").count() == 1


def test_configuration_round_trip_and_stale_revision(session_client, workflow_project):
    project, payload = workflow_project
    result = session_client.put(url(project), payload, format="json")
    assert result.status_code == 200, result.data
    assert result.data["revision"] == 1
    fetched = session_client.get(url(project))
    assert fetched.status_code == 200
    assert fetched.data["task_initial_state_id"] == payload["task_initial_state_id"]
    assert session_client.put(url(project), payload, format="json").status_code == 409
    assert WorkflowAudit.objects.filter(project=project).count() == 1


@pytest.mark.parametrize("route", ["intake-issues", "inbox-issues"])
def test_accept_intake_preserves_form_edits_without_workflow(session_client, workflow_project, route):
    project, _ = workflow_project
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    default = State.objects.get(project=project, name="Todo")
    default.default = True
    default.save()
    endpoint = url(project).replace("workflow/", f"{route}/")
    created = session_client.post(endpoint, {"issue": {"name": "Original"}}, format="json")
    assert created.status_code == 200, created.data
    issue_id = created.data["issue"]["id"]
    accepted = session_client.patch(
        endpoint + str(issue_id) + "/",
        {
            "status": 1,
            "issue": {"name": "Edited", "description_html": "<p>Edited description</p>"},
        },
        format="json",
    )
    assert accepted.status_code == 200, accepted.data
    issue = Issue.objects.get(pk=issue_id)
    assert issue.name == "Edited"
    assert issue.description_html == "<p>Edited description</p>"
    assert issue.state_id == default.pk


def test_project_admin_cannot_edit_configuration(session_client, workflow_project, create_user):
    project, payload = workflow_project
    WorkspaceMember.objects.filter(workspace=project.workspace, member=create_user).update(role=15)
    assert session_client.put(url(project), payload, format="json").status_code == 403


def test_workspace_admin_need_not_join_project(session_client, workflow_project, create_user):
    project, payload = workflow_project
    ProjectMember.objects.filter(project=project, member=create_user).delete(soft=False)
    result = session_client.put(url(project), payload, format="json")
    assert result.status_code == 200, result.data


def test_configuration_preview_does_not_save(session_client, workflow_project):
    project, payload = workflow_project
    result = session_client.post(url(project) + "validate/", payload, format="json")
    assert result.status_code == 200, result.data
    assert not WorkflowAudit.objects.filter(project=project).exists()
    assert session_client.get(url(project)).data["revision"] == 0


def test_cross_project_state_reference_rejected(session_client, workflow_project, workspace):
    project, payload = workflow_project
    other = Project.objects.create(name="Other", identifier="OTH", workspace=workspace)
    state = State.objects.create(project=other, workspace=workspace, name="Todo", group="unstarted", color="#000000")
    invalid = deepcopy(payload)
    invalid["task_initial_state_id"] = str(state.pk)
    result = session_client.put(url(project), invalid, format="json")
    assert result.status_code == 400, result.data


def enable(client, project, payload):
    result = client.put(url(project), payload, format="json")
    assert result.status_code == 200, result.data


def issue_url(project, issue_id=None):
    return url(project).replace("workflow/", "issues/") + (str(issue_id) + "/" if issue_id else "")


def create_task(client, project, **data):
    result = client.post(issue_url(project), {"name": "Task", **data}, format="json")
    assert result.status_code in (200, 201), result.data
    return Issue.objects.filter(project=project, name=data.get("name", "Task")).latest("created_at")


def test_initial_state_and_durable_source(session_client, workflow_project):
    project, payload = workflow_project
    enable(session_client, project, payload)
    task = create_task(session_client, project)
    assert str(task.state_id) == payload["task_initial_state_id"]
    assert task.workflow_context.origin == "task"
    conflict = session_client.post(
        issue_url(project), {"name": "Invalid", "state_id": payload["requirement_completed_state_id"]}, format="json"
    )
    assert conflict.status_code == 400
    assert not Issue.objects.filter(project=project, name="Invalid").exists()


def test_final_fields_and_failed_m2m_update_roll_back(session_client, workflow_project, create_user):
    project, payload = workflow_project
    target = payload["intake_acceptance_state_id"]
    payload["transitions"].append(
        {"branch": "task", "from_state_id": payload["task_initial_state_id"], "to_state_id": target, "roles": [20]}
    )
    payload["requirements"] = [
        {"branch": "task", "state_id": target, "fields": ["assignees", "start_date", "target_date"]}
    ]
    enable(session_client, project, payload)
    task = create_task(session_client, project)
    failure = session_client.patch(issue_url(project, task.pk), {"state_id": target}, format="json")
    assert failure.status_code == 400, failure.data
    task.refresh_from_db()
    assert str(task.state_id) == payload["task_initial_state_id"]
    good = session_client.patch(
        issue_url(project, task.pk),
        {
            "state_id": target,
            "assignee_ids": [str(create_user.pk)],
            "start_date": "2026-11-10",
            "target_date": "2026-11-12",
        },
        format="json",
    )
    assert good.status_code in (200, 204), good.data
    bad = session_client.patch(
        issue_url(project, task.pk), {"assignee_ids": [], "target_date": "2026-11-01"}, format="json"
    )
    assert bad.status_code == 400, bad.data
    task.refresh_from_db()
    assert str(task.target_date) == "2026-11-12"
    assert task.issue_assignee.filter(deleted_at__isnull=True).count() == 1


@pytest.mark.parametrize("route", ["intake-issues", "inbox-issues"])
def test_intake_acceptance_on_same_issue_and_admin_boundary(session_client, workflow_project, create_user, route):
    project, payload = workflow_project
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    enable(session_client, project, payload)
    intake_url = url(project).replace("workflow/", f"{route}/")
    result = session_client.post(intake_url, {"issue": {"name": "Requirement"}}, format="json")
    assert result.status_code in (200, 201), result.data
    issue = Issue.objects.get(project=project, name="Requirement")
    assert issue.workflow_context.origin == "intake"
    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    denied = session_client.patch(intake_url + str(issue.pk) + "/", {"status": 1}, format="json")
    assert denied.status_code == 403, denied.data
    ProjectMember.objects.filter(project=project, member=create_user).update(role=20)
    accepted = session_client.patch(intake_url + str(issue.pk) + "/", {"status": 1}, format="json")
    assert accepted.status_code == 200, accepted.data
    issue.refresh_from_db()
    assert str(issue.state_id) == payload["intake_acceptance_state_id"]
    assert IssueWorkflowContext.objects.get(issue=issue).accepted_by == create_user
    assert Issue.objects.filter(project=project).count() == 1


def test_parent_completion_reopening_and_nested_descendants(session_client, workflow_project, create_user):
    from django.utils import timezone

    project, payload = workflow_project
    todo, done = payload["task_initial_state_id"], payload["requirement_completed_state_id"]
    payload["transitions"] += [
        {"branch": "task", "from_state_id": todo, "to_state_id": done, "roles": [20]},
        {"branch": "task", "from_state_id": done, "to_state_id": todo, "roles": [20]},
    ]
    enable(session_client, project, payload)
    parent = Issue.objects.create(
        project=project, workspace=project.workspace, name="Parent", state_id=payload["intake_acceptance_state_id"]
    )
    IssueWorkflowContext.objects.create(
        issue=parent, origin="intake", accepted_at=timezone.now(), accepted_by=create_user
    )
    child = create_task(session_client, project, name="Child", parent_id=str(parent.pk))
    nested = create_task(session_client, project, name="Nested", parent_id=str(child.pk))
    premature = session_client.patch(issue_url(project, child.pk), {"state_id": done}, format="json")
    assert premature.status_code == 400, premature.data
    assert session_client.patch(issue_url(project, nested.pk), {"state_id": done}, format="json").status_code == 204
    assert session_client.patch(issue_url(project, child.pk), {"state_id": done}, format="json").status_code == 204
    parent.refresh_from_db()
    assert str(parent.state_id) == done
    assert WorkflowAudit.objects.filter(project=project, operation="requirement.completed").count() == 1
    assert session_client.patch(issue_url(project, nested.pk), {"state_id": todo}, format="json").status_code == 204
    parent.refresh_from_db()
    child.refresh_from_db()
    assert str(parent.state_id) == payload["requirement_reopen_state_id"]
    assert str(child.state_id) == done


def test_manual_reopen_survives_unrelated_edit(session_client, workflow_project, create_user):
    from django.utils import timezone

    project, payload = workflow_project
    todo, done = payload["task_initial_state_id"], payload["requirement_completed_state_id"]
    payload["transitions"].append({"branch": "task", "from_state_id": todo, "to_state_id": done, "roles": [20]})
    enable(session_client, project, payload)
    parent = Issue.objects.create(
        project=project, workspace=project.workspace, name="Parent", state_id=payload["intake_acceptance_state_id"]
    )
    IssueWorkflowContext.objects.create(
        issue=parent, origin="intake", accepted_at=timezone.now(), accepted_by=create_user
    )
    child = create_task(session_client, project, parent_id=str(parent.pk))
    assert session_client.patch(issue_url(project, child.pk), {"state_id": done}, format="json").status_code == 204
    reopened = session_client.patch(
        issue_url(project, parent.pk), {"state_id": payload["requirement_reopen_state_id"]}, format="json"
    )
    assert reopened.status_code == 204, reopened.data
    assert (
        session_client.patch(issue_url(project, parent.pk), {"name": "Manually reopened"}, format="json").status_code
        == 204
    )
    parent.refresh_from_db()
    assert str(parent.state_id) == payload["requirement_reopen_state_id"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_last_children_complete_only_once(session_client, workflow_project, create_user, mocker):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections, connections
    from django.utils import timezone
    from rest_framework.test import APIClient
    from plane.db.models import User

    mocker.patch("plane.app.views.issue.base.issue_activity.delay")
    mocker.patch("plane.app.views.issue.base.model_activity.delay")
    mocker.patch("plane.app.views.issue.base.issue_description_version_task.delay")
    project, payload = workflow_project
    todo, done = payload["task_initial_state_id"], payload["requirement_completed_state_id"]
    payload["transitions"].append({"branch": "task", "from_state_id": todo, "to_state_id": done, "roles": [20]})
    enable(session_client, project, payload)
    parent = Issue.objects.create(
        project=project, workspace=project.workspace, name="Parent", state_id=payload["intake_acceptance_state_id"]
    )
    IssueWorkflowContext.objects.create(
        issue=parent, origin="intake", accepted_at=timezone.now(), accepted_by=create_user
    )
    children = [create_task(session_client, project, name=f"Child {i}", parent_id=str(parent.pk)) for i in range(2)]
    barrier = Barrier(2)
    user_id = create_user.pk

    def complete(issue_id):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=User.objects.get(pk=user_id))
            barrier.wait(timeout=10)
            response = client.patch(issue_url(project, issue_id), {"state_id": done}, format="json")
            return response.status_code, getattr(response, "data", None)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, [child.pk for child in children]))
    assert all(status == 204 for status, _ in results), results
    parent.refresh_from_db()
    assert str(parent.state_id) == done
    assert WorkflowAudit.objects.filter(project=project, operation="requirement.completed").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_configuration_save_rejects_stale_writer(session_client, workflow_project, create_user):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections, connections
    from rest_framework.test import APIClient
    from plane.db.models import User

    project, payload = workflow_project
    barrier = Barrier(2)
    user_id = create_user.pk

    def save(_):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=User.objects.get(pk=user_id))
            barrier.wait(timeout=10)
            return client.put(url(project), deepcopy(payload), format="json").status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sorted(results) == [200, 409]
    assert WorkflowAudit.objects.filter(project=project, operation="configuration.saved").count() == 1


def approved_parent(project, payload, actor, *, state=None):
    from django.utils import timezone

    parent = Issue.objects.create(
        project=project,
        workspace=project.workspace,
        name="Parent",
        state_id=state or payload["intake_acceptance_state_id"],
    )
    IssueWorkflowContext.objects.create(issue=parent, origin="intake", accepted_at=timezone.now(), accepted_by=actor)
    return parent


def task_paths(payload):
    todo, done = payload["task_initial_state_id"], payload["requirement_completed_state_id"]
    payload["transitions"] += [
        {"branch": "task", "from_state_id": todo, "to_state_id": done, "roles": [20, 15]},
        {"branch": "task", "from_state_id": done, "to_state_id": todo, "roles": [20, 15]},
    ]
    return todo, done


def test_automatic_completion_blocked_until_parent_fields_filled(session_client, workflow_project, create_user):
    project, payload = workflow_project
    _, done = task_paths(payload)
    payload["requirements"] = [{"branch": "requirement", "state_id": done, "fields": ["target_date"]}]
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user)
    child = create_task(session_client, project, parent_id=str(parent.pk))
    assert session_client.patch(issue_url(project, child.pk), {"state_id": done}, format="json").status_code == 204
    parent.refresh_from_db()
    assert str(parent.state_id) != done
    assert IssueWorkflowContext.objects.get(issue=parent).blocked_reason["code"] == "WORKFLOW_REQUIRED_FIELDS_MISSING"
    result = session_client.patch(issue_url(project, parent.pk), {"target_date": "2026-12-01"}, format="json")
    assert result.status_code == 204, result.data
    parent.refresh_from_db()
    assert str(parent.state_id) == done
    assert not IssueWorkflowContext.objects.get(issue=parent).blocked_reason


def test_automatic_reopen_missing_fields_rolls_back_child(session_client, workflow_project, create_user):
    project, payload = workflow_project
    todo, done = task_paths(payload)
    payload["requirements"] = [
        {"branch": "requirement", "state_id": payload["requirement_reopen_state_id"], "fields": ["target_date"]}
    ]
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user, state=done)
    child = Issue.objects.create(
        project=project, workspace=project.workspace, name="Completed child", state_id=done, parent=parent
    )
    IssueWorkflowContext.objects.create(issue=child, origin="task")
    result = session_client.patch(issue_url(project, child.pk), {"state_id": todo}, format="json")
    assert result.status_code == 400, result.data
    child.refresh_from_db()
    parent.refresh_from_db()
    assert str(child.state_id) == str(parent.state_id) == done


def test_member_can_trigger_parent_reopen_without_parent_manual_permission(
    session_client, workflow_project, create_user
):
    project, payload = workflow_project
    todo, done = task_paths(payload)
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user, state=done)
    child = Issue.objects.create(
        project=project, workspace=project.workspace, name="Completed child", state_id=done, parent=parent
    )
    IssueWorkflowContext.objects.create(issue=child, origin="task")
    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    denied = session_client.patch(
        issue_url(project, parent.pk), {"state_id": payload["requirement_reopen_state_id"]}, format="json"
    )
    assert denied.status_code == 403
    changed = session_client.patch(issue_url(project, child.pk), {"state_id": todo}, format="json")
    assert changed.status_code == 204, changed.data
    parent.refresh_from_db()
    assert str(parent.state_id) == payload["requirement_reopen_state_id"]


def test_archived_cancelled_descendant_does_not_count_as_completed(session_client, workflow_project, create_user):
    from datetime import date

    project, payload = workflow_project
    _, done = task_paths(payload)
    cancelled = State.objects.create(
        project=project, workspace=project.workspace, name="Cancelled", group="cancelled", color="#000000"
    )
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user)
    child = Issue.objects.create(
        project=project,
        workspace=project.workspace,
        name="Archived child",
        state=cancelled,
        parent=parent,
        archived_at=date(2026, 11, 10),
    )
    IssueWorkflowContext.objects.create(issue=child, origin="task")
    blocked = session_client.patch(issue_url(project, parent.pk), {"state_id": done}, format="json")
    assert blocked.status_code == 400, blocked.data
    assert blocked.data["details"]["unfinished_count"] in (1, "1")
    assert blocked.data["details"]["unfinished_issues"][0]["id"] == str(child.pk)


def test_batch_dates_failure_is_atomic(session_client, workflow_project):
    project, payload = workflow_project
    payload["requirements"] = [
        {"branch": "task", "state_id": payload["task_initial_state_id"], "fields": ["start_date", "target_date"]}
    ]
    enable(session_client, project, payload)
    first = create_task(session_client, project, name="First", start_date="2026-11-10", target_date="2026-11-12")
    second = create_task(session_client, project, name="Second", start_date="2026-11-10", target_date="2026-11-12")
    result = session_client.post(
        url(project).replace("workflow/", "issue-dates/"),
        {
            "updates": [
                {"id": str(first.pk), "target_date": "2026-11-13"},
                {"id": str(second.pk), "target_date": "2026-11-01"},
            ]
        },
        format="json",
    )
    assert result.status_code == 400, result.data
    first.refresh_from_db()
    second.refresh_from_db()
    assert str(first.target_date) == str(second.target_date) == "2026-11-12"
    cleared = session_client.post(
        url(project).replace("workflow/", "issue-dates/"),
        {
            "updates": [
                {"id": str(first.pk), "target_date": "2026-11-13"},
                {"id": str(second.pk), "target_date": None},
            ]
        },
        format="json",
    )
    assert cleared.status_code == 400, cleared.data
    assert cleared.data["details"]["issue_id"] == str(second.pk)
    first.refresh_from_db()
    second.refresh_from_db()
    assert str(first.target_date) == str(second.target_date) == "2026-11-12"


def test_non_triage_intake_submission_visible_in_project_layouts_only(session_client, workflow_project):
    project, payload = workflow_project
    state = State.objects.create(
        project=project, workspace=project.workspace, name="Awaiting review", group="started", color="#000000"
    )
    payload["intake_submission_state_id"] = str(state.pk)
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    enable(session_client, project, payload)
    submitted = session_client.post(
        url(project).replace("workflow/", "intake-issues/"), {"issue": {"name": "Pending"}}, format="json"
    )
    assert submitted.status_code == 200, submitted.data
    issue = Issue.objects.get(project=project, name="Pending")
    assert not Issue.formal_objects.filter(pk=issue.pk).exists()
    assert not Issue.issue_objects.filter(pk=issue.pk).exists()
    assert str(issue.state_id) == str(state.pk)
    from plane.db.models import IssueRelation
    import json

    task = create_task(session_client, project, name="Formal task")
    IssueRelation.objects.create(
        project=project, workspace=project.workspace, issue=task, related_issue=issue, relation_type="relates_to"
    )
    for suffix in ("issues/", "v2/issues/", "issues-detail/?expand=issue_relation"):
        response = session_client.get(url(project).replace("workflow/", suffix))
        assert response.status_code == 200, response.data
        assert "Pending" in json.dumps(response.data, default=str), response.data
    assert session_client.get(issue_url(project, issue.pk)).status_code == 200
    assert session_client.get(issue_url(project, issue.pk) + "issue-relation/").status_code == 404


def test_public_api_uses_initial_state_and_shared_transition_rules(session_client, api_key_client, workflow_project):
    project, payload = workflow_project
    enable(session_client, project, payload)
    endpoint = f"/api/v1/workspaces/{project.workspace.slug}/projects/{project.pk}/work-items/"
    created = api_key_client.post(endpoint, {"name": "Public task"}, format="json")
    assert created.status_code in (200, 201), created.data
    task = Issue.objects.get(project=project, name="Public task")
    assert str(task.state_id) == payload["task_initial_state_id"]
    denied = api_key_client.patch(
        endpoint + str(task.pk) + "/", {"state": payload["requirement_completed_state_id"]}, format="json"
    )
    assert denied.status_code == 400, denied.data


def test_state_rename_preserves_binding_and_group_change_is_guarded(session_client, workflow_project):
    project, payload = workflow_project
    enable(session_client, project, payload)
    state_id = payload["requirement_completed_state_id"]
    endpoint = url(project).replace("workflow/", f"states/{state_id}/")
    renamed = session_client.patch(endpoint, {"name": "Released"}, format="json")
    assert renamed.status_code == 200, renamed.data
    assert session_client.get(url(project)).data["requirement_completed_state_id"] == state_id
    blocked = session_client.patch(endpoint, {"group": "started"}, format="json")
    assert blocked.status_code == 400, blocked.data
    assert State.objects.get(pk=state_id).group == "completed"


def test_space_submission_uses_configured_state_and_fields(session_client, workflow_project):
    from plane.db.models import DeployBoard

    project, payload = workflow_project
    intake = Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    board = DeployBoard.objects.create(
        project=project, workspace=project.workspace, entity_name="project", entity_identifier=project.pk, intake=intake
    )
    payload["requirements"] = [
        {
            "branch": "requirement",
            "state_id": payload["intake_submission_state_id"],
            "fields": ["start_date", "target_date"],
        }
    ]
    enable(session_client, project, payload)
    endpoint = f"/api/public/anchor/{board.anchor}/intakes/{intake.pk}/intake-issues/"
    denied = session_client.post(endpoint, {"issue": {"name": "Missing dates"}}, format="json")
    assert denied.status_code == 400, denied.data
    assert not Issue.objects.filter(project=project, name="Missing dates").exists()
    created = session_client.post(
        endpoint,
        {"issue": {"name": "Space requirement", "start_date": "2026-11-10", "target_date": "2026-11-12"}},
        format="json",
    )
    assert created.status_code in (200, 201), created.data
    issue = Issue.objects.get(project=project, name="Space requirement")
    assert str(issue.state_id) == payload["intake_submission_state_id"]
    assert str(issue.start_date) == "2026-11-10"
    assert issue.workflow_context.origin == "intake"


def test_shared_state_does_not_convert_task_into_reviewed_requirement(session_client, workflow_project):
    project, payload = workflow_project
    payload["intake_submission_state_id"] = payload["task_initial_state_id"]
    todo, done = task_paths(payload)
    enable(session_client, project, payload)
    task = create_task(session_client, project, source="intake", accepted_at="2026-11-10")
    assert task.workflow_context.origin == "task"
    assert not task.workflow_context.accepted_at
    assert not IntakeIssue.objects.filter(issue=task).exists()
    assert session_client.patch(issue_url(project, task.pk), {"state_id": done}, format="json").status_code == 204
    assert session_client.patch(issue_url(project, task.pk), {"state_id": todo}, format="json").status_code == 204
    assert Issue.formal_objects.filter(pk=task.pk).exists()


def test_pending_parent_and_parent_cycle_are_rejected(session_client, workflow_project):
    project, payload = workflow_project
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    enable(session_client, project, payload)
    created = session_client.post(
        url(project).replace("workflow/", "intake-issues/"), {"issue": {"name": "Pending parent"}}, format="json"
    )
    assert created.status_code == 200
    pending = Issue.objects.get(project=project, name="Pending parent")
    denied = session_client.post(
        issue_url(project), {"name": "Premature child", "parent_id": str(pending.pk)}, format="json"
    )
    assert denied.status_code == 400, denied.data
    assert not Issue.objects.filter(project=project, name="Premature child").exists()
    first = create_task(session_client, project, name="First")
    second = create_task(session_client, project, name="Second", parent_id=str(first.pk))
    cycle = session_client.patch(issue_url(project, first.pk), {"parent_id": str(second.pk)}, format="json")
    assert cycle.status_code == 400, cycle.data
    first.refresh_from_db()
    assert first.parent_id is None


def test_workflow_disable_keeps_provenance_and_restore_requires_validation(session_client, workflow_project):
    project, payload = workflow_project
    enable(session_client, project, payload)
    task = create_task(session_client, project)
    saved = session_client.get(url(project)).data
    disabled = session_client.put(url(project), {**saved, "enabled": False}, format="json")
    assert disabled.status_code == 200
    assert task.workflow_context.origin == "task"
    # The disabled project keeps its legacy behavior even without a configured transition.
    moved = session_client.patch(
        issue_url(project, task.pk), {"state_id": payload["requirement_completed_state_id"]}, format="json"
    )
    assert moved.status_code == 204, moved.data
    latest = session_client.get(url(project)).data
    latest["enabled"] = True
    latest["requirements"] = [
        {"branch": "task", "state_id": payload["requirement_completed_state_id"], "fields": ["target_date"]}
    ]
    incompatible = session_client.put(url(project), latest, format="json")
    assert incompatible.status_code == 400, incompatible.data


def test_manual_reopen_is_not_reversed_by_switching_between_completed_states(
    session_client, workflow_project, create_user
):
    project, payload = workflow_project
    _, done = task_paths(payload)
    alternate = State.objects.create(
        project=project, workspace=project.workspace, name="Other completed", group="completed", color="#000000"
    )
    payload["transitions"].append(
        {"branch": "task", "from_state_id": done, "to_state_id": str(alternate.pk), "roles": [20]}
    )
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user)
    child = create_task(session_client, project, parent_id=str(parent.pk))
    assert session_client.patch(issue_url(project, child.pk), {"state_id": done}, format="json").status_code == 204
    assert (
        session_client.patch(
            issue_url(project, parent.pk), {"state_id": payload["requirement_reopen_state_id"]}, format="json"
        ).status_code
        == 204
    )
    changed = session_client.patch(issue_url(project, child.pk), {"state_id": str(alternate.pk)}, format="json")
    assert changed.status_code == 204, changed.data
    parent.refresh_from_db()
    assert str(parent.state_id) == payload["requirement_reopen_state_id"]


def test_reparent_recalculates_both_parents_and_delete_does_not_complete_empty_parent(
    session_client, workflow_project, create_user
):
    project, payload = workflow_project
    _, done = task_paths(payload)
    enable(session_client, project, payload)
    old_parent = approved_parent(project, payload, create_user)
    new_parent = approved_parent(project, payload, create_user, state=done)
    child = create_task(session_client, project, parent_id=str(old_parent.pk))
    reparented = session_client.post(
        issue_url(project, new_parent.pk) + "sub-issues/", {"sub_issue_ids": [str(child.pk)]}, format="json"
    )
    assert reparented.status_code == 200, reparented.data
    old_parent.refresh_from_db()
    new_parent.refresh_from_db()
    child.refresh_from_db()
    assert child.parent_id == new_parent.pk
    assert str(old_parent.state_id) == str(new_parent.state_id) == payload["requirement_reopen_state_id"]
    deleted = session_client.delete(issue_url(project, child.pk))
    assert deleted.status_code == 204, deleted.data
    new_parent.refresh_from_db()
    assert str(new_parent.state_id) == payload["requirement_reopen_state_id"]


def test_automation_obeys_real_actor_role_and_configured_paths(session_client, workflow_project, create_user):
    from plane.bgtasks.issue_automation_task import controlled_automation

    project, payload = workflow_project
    done = payload["requirement_completed_state_id"]
    payload["transitions"].append(
        {"branch": "task", "from_state_id": payload["task_initial_state_id"], "to_state_id": done, "roles": [15]}
    )
    enable(session_client, project, payload)
    task = create_task(session_client, project)
    Project.objects.filter(pk=project.pk).update(created_by=create_user)
    project.refresh_from_db()
    controlled_automation(project, [task], state=State.objects.get(pk=done))
    task.refresh_from_db()
    assert str(task.state_id) == payload["task_initial_state_id"]
    assert WorkflowAudit.objects.filter(project=project, operation="automation.skipped").exists()
    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    controlled_automation(project, [task], state=State.objects.get(pk=done))
    task.refresh_from_db()
    assert str(task.state_id) == done
    assert task.completed_at is not None


def test_cancelled_requirement_does_not_automatically_restore(session_client, workflow_project, create_user):
    project, payload = workflow_project
    _, done = task_paths(payload)
    cancelled = State.objects.create(
        project=project, workspace=project.workspace, name="Cancelled", group="cancelled", color="#000000"
    )
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user, state=str(cancelled.pk))
    child = create_task(session_client, project, parent_id=str(parent.pk))
    assert session_client.patch(issue_url(project, child.pk), {"state_id": done}, format="json").status_code == 204
    parent.refresh_from_db()
    assert parent.state_id == cancelled.pk


def test_suspended_assignee_must_be_repaired_before_transition(session_client, workflow_project, create_user):
    from plane.tests.factories import UserFactory
    from plane.db.models import IssueAssignee

    project, payload = workflow_project
    _, done = task_paths(payload)
    enable(session_client, project, payload)
    assigned = UserFactory()
    WorkspaceMember.objects.create(workspace=project.workspace, member=assigned, role=15)
    membership = ProjectMember.objects.create(project=project, workspace=project.workspace, member=assigned, role=15)
    task = create_task(session_client, project, assignee_ids=[str(assigned.pk)])
    membership.is_active = False
    membership.save()
    blocked = session_client.patch(issue_url(project, task.pk), {"state_id": done}, format="json")
    assert blocked.status_code == 400, blocked.data
    assert blocked.data["code"] == "WORKFLOW_INVALID_ASSIGNEE"
    preview = session_client.post(
        issue_url(project, task.pk) + "workflow/preview/", {"assignee_ids": [str(create_user.pk)]}, format="json"
    )
    assert preview.status_code == 200, preview.data
    assert any(target["state_id"] == done and target["allowed"] for target in preview.data["targets"])
    repaired = session_client.patch(
        issue_url(project, task.pk), {"state_id": done, "assignee_ids": [str(create_user.pk)]}, format="json"
    )
    assert repaired.status_code == 204, repaired.data
    assert not IssueAssignee.objects.filter(issue=task, assignee=assigned).exists()


def test_preview_is_read_only_and_rejects_malformed_field_values(session_client, workflow_project):
    project, payload = workflow_project
    task_paths(payload)
    enable(session_client, project, payload)
    task = create_task(session_client, project)
    endpoint = issue_url(project, task.pk) + "workflow/preview/"
    before = WorkflowAudit.objects.filter(project=project).count()
    result = session_client.post(endpoint, {"start_date": "2026-11-10", "target_date": "2026-11-12"}, format="json")
    assert result.status_code == 200, result.data
    task.refresh_from_db()
    assert task.start_date is None and task.target_date is None
    assert WorkflowAudit.objects.filter(project=project).count() == before
    assert session_client.post(endpoint, {"assignees": ["not-a-user-id"]}, format="json").status_code == 400


def test_draft_publication_preserves_failed_draft(session_client, workflow_project, create_user):
    from plane.db.models import DraftIssue

    project, payload = workflow_project
    enable(session_client, project, payload)
    draft = DraftIssue.objects.create(
        project=project, workspace=project.workspace, name="Draft", created_by=create_user
    )
    DraftIssue.objects.filter(pk=draft.pk).update(created_by=create_user)
    endpoint = f"/api/workspaces/{project.workspace.slug}/draft-to-issue/{draft.pk}/"
    denied = session_client.post(
        endpoint, {"name": "Published", "state_id": payload["requirement_completed_state_id"]}, format="json"
    )
    assert denied.status_code == 400, denied.data
    assert DraftIssue.objects.filter(pk=draft.pk).exists()
    assert not Issue.objects.filter(project=project, name="Published").exists()
    published = session_client.post(endpoint, {"name": "Published"}, format="json")
    assert published.status_code in (200, 201), published.data
    issue = Issue.objects.get(project=project, name="Published")
    assert str(issue.state_id) == payload["task_initial_state_id"]
    assert issue.workflow_context.origin == "task"
    assert not DraftIssue.objects.filter(pk=draft.pk).exists()


def test_legal_group_change_recomputes_parent(session_client, workflow_project, create_user):
    project, payload = workflow_project
    todo, done = task_paths(payload)
    extra = State.objects.create(
        project=project, workspace=project.workspace, name="Alternate", group="started", color="#123456"
    )
    payload["transitions"].append(
        {"branch": "task", "from_state_id": todo, "to_state_id": str(extra.pk), "roles": [20]}
    )
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user)
    child = create_task(session_client, project, parent_id=str(parent.pk))
    assert (
        session_client.patch(issue_url(project, child.pk), {"state_id": str(extra.pk)}, format="json").status_code
        == 204
    )
    endpoint = url(project).replace("workflow/", f"states/{extra.pk}/")
    result = session_client.patch(endpoint, {"group": "completed"}, format="json")
    assert result.status_code == 200, result.data
    parent.refresh_from_db()
    assert str(parent.state_id) == done
    child.refresh_from_db()
    assert child.completed_at is not None


def test_empty_requirement_needs_explicit_admin_transition(session_client, workflow_project, create_user):
    project, payload = workflow_project
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user)
    assert session_client.patch(issue_url(project, parent.pk), {"name": "Empty"}, format="json").status_code == 204
    parent.refresh_from_db()
    assert str(parent.state_id) == payload["intake_acceptance_state_id"]
    done = payload["requirement_completed_state_id"]
    assert session_client.patch(issue_url(project, parent.pk), {"state_id": done}, format="json").status_code == 204
    parent.refresh_from_db()
    assert str(parent.state_id) == done


@pytest.mark.django_db(transaction=True)
def test_concurrent_acceptance_keeps_one_receipt(session_client, workflow_project, create_user, mocker):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections, connections
    from rest_framework.test import APIClient
    from plane.db.models import User

    mocker.patch("plane.app.views.intake.base.issue_activity.delay")
    project, payload = workflow_project
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    enable(session_client, project, payload)
    endpoint = url(project).replace("workflow/", "intake-issues/")
    created = session_client.post(endpoint, {"issue": {"name": "Concurrent review"}}, format="json")
    assert created.status_code in (200, 201), created.data
    issue = Issue.objects.get(project=project, name="Concurrent review")
    barrier = Barrier(2)
    user_id = create_user.pk

    def accept(_):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=User.objects.get(pk=user_id))
            barrier.wait(timeout=10)
            response = client.patch(endpoint + str(issue.pk) + "/", {"status": 1}, format="json")
            return response.status_code, getattr(response, "data", None)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, range(2)))
    assert all(status in (200, 204) for status, _ in results), results
    assert IssueWorkflowContext.objects.get(issue=issue).accepted_at is not None
    assert WorkflowAudit.objects.filter(project=project, operation="intake.accepted").count() == 1


def test_batch_state_updates_validate_all_items_and_commit_final_tree(
    session_client, workflow_project, create_user, django_capture_on_commit_callbacks, mocker
):
    project, payload = workflow_project
    todo, done = task_paths(payload)
    payload["requirements"] = [{"branch": "task", "state_id": done, "fields": ["target_date"]}]
    enable(session_client, project, payload)
    parent = approved_parent(project, payload, create_user)
    first = create_task(session_client, project, name="Batch first", parent_id=str(parent.pk), target_date="2026-11-12")
    second = create_task(session_client, project, name="Batch second", parent_id=str(parent.pk))
    endpoint = url(project).replace("workflow/", "bulk-operation-issues/")
    rejected = session_client.post(
        endpoint, {"issue_ids": [str(first.pk), str(second.pk)], "properties": {"state_id": done}}, format="json"
    )
    assert rejected.status_code == 400, rejected.data
    assert str(rejected.data["details"]["issue_id"]) == str(second.pk)
    first.refresh_from_db()
    assert str(first.state_id) == todo
    activity = mocker.patch("plane.bgtasks.issue_activities_task.issue_activity.delay")
    with django_capture_on_commit_callbacks(execute=True):
        saved = session_client.post(
            endpoint,
            {
                "issue_ids": [str(parent.pk), str(first.pk), str(second.pk)],
                "properties": {"state_id": done, "target_date": "2026-11-12"},
            },
            format="json",
        )
    from inspect import signature
    from plane.bgtasks.issue_activities_task import issue_activity

    assert activity.call_count == 3
    for call in activity.call_args_list:
        signature(issue_activity.run).bind(**call.kwargs)
    assert saved.status_code == 200, saved.data
    assert (
        Issue.objects.filter(pk__in=[parent.pk, first.pk, second.pk], state_id=done, completed_at__isnull=False).count()
        == 3
    )


def test_batch_unsupported_and_foreign_ids_are_rejected_without_writes(session_client, workflow_project):
    project, payload = workflow_project
    enable(session_client, project, payload)
    task = create_task(session_client, project)
    endpoint = url(project).replace("workflow/", "bulk-operation-issues/")
    rejected = session_client.post(
        endpoint, {"issue_ids": [str(task.pk)], "properties": {"cycle_id": None}}, format="json"
    )
    assert rejected.status_code == 400, rejected.data
    assert rejected.data["code"] == "WORKFLOW_ENTRY_UNSUPPORTED"
    foreign = session_client.post(
        endpoint,
        {"issue_ids": [str(task.pk), "00000000-0000-0000-0000-000000000001"], "properties": {"priority": "high"}},
        format="json",
    )
    assert foreign.status_code == 400, foreign.data
    task.refresh_from_db()
    assert task.priority == "none"


@pytest.mark.parametrize("route", ["intake-issues", "inbox-issues"])
def test_intake_submission_fields_and_creation_context(session_client, workflow_project, create_user, route):
    project, payload = workflow_project
    pending = State.objects.create(
        name="Pending review", group="backlog", project=project, workspace=project.workspace, color="#456789"
    )
    payload["intake_submission_state_id"] = str(pending.pk)
    payload["requirements"] = [
        {"branch": "requirement", "state_id": str(pending.pk), "fields": ["assignees", "start_date", "target_date"]},
        {"branch": "task", "state_id": payload["task_initial_state_id"], "fields": ["assignees"]},
    ]
    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    enable(session_client, project, payload)
    context = session_client.get(url(project) + "creation/")
    assert context.status_code == 200, context.data
    assert str(context.data["intake"]["state"]["id"]) == str(pending.pk)
    assert context.data["intake"]["state"]["name"] == "Pending review"
    assert context.data["intake"]["fields"] == ["assignees", "start_date", "target_date"]
    assert context.data["fields"] == ["assignees"]
    assert "transitions" not in context.data
    intake_url = url(project).replace("workflow/", f"{route}/")
    failure = session_client.post(intake_url, {"issue": {"name": "Missing fields"}}, format="json")
    assert failure.status_code == 400, failure.data
    assert failure.data["code"] == "WORKFLOW_REQUIRED_FIELDS_MISSING"
    assert set(failure.data["details"]["fields"]) == {"assignees", "start_date", "target_date"}
    assert not Issue.objects.filter(project=project).exists()
    assert not IntakeIssue.objects.filter(project=project).exists()
    result = session_client.post(
        intake_url,
        {
            "issue": {
                "name": "Valid submission",
                "state_id": payload["task_initial_state_id"],
                "assignee_ids": [str(create_user.pk)],
                "start_date": "2026-11-10",
                "target_date": "2026-11-12",
            }
        },
        format="json",
    )
    assert result.status_code in (200, 201), result.data
    issue = Issue.objects.get(project=project, name="Valid submission")
    assert issue.state_id == pending.pk
    assert str(issue.start_date) == "2026-11-10"
    assert issue.workflow_context.origin == "intake"
    assert issue.workflow_context.accepted_at is None
    assert IntakeIssue.objects.get(issue=issue).status == -2

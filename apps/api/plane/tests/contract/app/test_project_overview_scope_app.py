# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.utils import timezone

from plane.db.models import (
    Intake,
    Issue,
    IssueAssignee,
    IssueWorkflowContext,
    ProjectMember,
    User,
    WorkspaceMember,
)
from . import test_project_workflow_app as workflow_fixtures
from . import test_workflow_project_overview_app as overview_fixtures
from .test_project_workflow_app import enable, issue_url, url

workflow_project = workflow_fixtures.workflow_project
workflow_settings = workflow_fixtures.workflow_settings
testing_config = overview_fixtures.testing_config
pytestmark = [pytest.mark.django_db, pytest.mark.contract]


@pytest.mark.parametrize("workspace_role", [15, 20])
def test_overview_counts_and_timeline_use_the_same_member_scope(
    session_client, testing_config, create_user, workspace_role
):
    project, config, states = testing_config
    enable(session_client, project, config)
    other = User.objects.create(
        email="other-overview-member@plane.test", username="other-overview-member", first_name="Other"
    )
    WorkspaceMember.objects.create(workspace=project.workspace, member=other, role=15)
    ProjectMember.objects.create(project=project, workspace=project.workspace, member=other, role=15)

    def item(name, *, state=None, parent=None, assignees=(), requirement=False, source=None, dated=False):
        issue = Issue.objects.create(
            project=project,
            workspace=project.workspace,
            name=name,
            parent=parent,
            state_id=state or config["task_initial_state_id"],
            start_date="2026-11-12" if dated else None,
            target_date="2026-11-15" if dated else None,
        )
        IssueWorkflowContext.objects.create(
            issue=issue,
            origin="intake" if requirement else "task",
            accepted_at=timezone.now() if requirement else None,
            test_source=source,
        )
        for assignee in assignees:
            IssueAssignee.objects.create(issue=issue, project=project, workspace=project.workspace, assignee=assignee)
        return issue

    Intake.objects.create(project=project, workspace=project.workspace, name="Intake", is_default=True)
    response = session_client.post(
        url(project).replace("workflow/", "intake-issues/"), {"issue": {"name": "Pending requirement"}}, format="json"
    )
    assert response.status_code == 200, response.data
    pending = Issue.project_objects.get(project=project, name="Pending requirement")
    reviewed = item("Reviewed requirement", state=config["intake_acceptance_state_id"], requirement=True, dated=True)
    completed = item("Completed requirement", state=config["requirement_completed_state_id"], requirement=True)
    mine = item("My development", parent=reviewed, assignees=[create_user], dated=True)
    shared = item("Shared development", parent=reviewed, assignees=[create_user, other])
    others = item("Other development", parent=reviewed, assignees=[other])
    mine_test = item(
        "My test", parent=others, assignees=[create_user], source=others, state=states["Testing"], dated=True
    )
    other_test = item("Other test", parent=mine, assignees=[other], source=mine, state=states["Testing"])
    unassigned = item("Unassigned development")
    lookalike = item("Development in reviewed state", state=config["intake_acceptance_state_id"])
    former = item("No longer assigned to me", assignees=[create_user])
    IssueAssignee.objects.filter(issue=former).update(deleted_at=timezone.now())
    for field in ["archived_at", "deleted_at", "is_draft"]:
        hidden = item(field, assignees=[create_user])
        Issue.all_objects.filter(pk=hidden.pk).update(**{field: True if field == "is_draft" else timezone.now()})

    endpoint = url(project).replace("workflow/", "overview/")
    administrator = session_client.get(endpoint)
    assert administrator.status_code == 200, administrator.data
    assert administrator.data["total"] == 11
    admin_ids = {
        str(row.pk)
        for row in [
            pending,
            reviewed,
            completed,
            mine,
            shared,
            others,
            mine_test,
            other_test,
            unassigned,
            lookalike,
            former,
        ]
    }

    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    WorkspaceMember.objects.filter(workspace=project.workspace, member=create_user).update(role=workspace_role)
    summary = session_client.get(endpoint)
    assert summary.status_code == 200, summary.data
    assert summary.data["total"] == 6
    assert summary.data["undated_count"] == 3
    counts = {str(row["id"]): row["count"] for row in summary.data["states"]}
    assert counts[config["intake_submission_state_id"]] == 1
    assert counts[config["intake_acceptance_state_id"]] == 1
    assert counts[config["requirement_completed_state_id"]] == 1
    assert counts[config["task_initial_state_id"]] == 2
    assert counts[states["Testing"]] == 1
    assert sum(counts.values()) == 6
    # Overview ignores the work-item page's explicit full-project selection.
    assert session_client.get(endpoint, {"work_item_scope": "all"}).data == summary.data
    assert session_client.get(issue_url(project), {"work_item_scope": "all"}).data["total_count"] == 11

    rows = []
    params = {"work_item_scope": "overview", "order_by": "workflow_order", "per_page": 2}
    while True:
        page = session_client.get(issue_url(project), params)
        assert page.status_code == 200, page.data
        assert page.data["total_count"] == summary.data["total"]
        rows.extend(page.data["results"])
        if not page.data["next_page_results"]:
            break
        params["cursor"] = page.data["next_cursor"]
    assert len(rows) == 6
    assert {str(row["id"]) for row in rows} == {
        str(issue.pk) for issue in [pending, reviewed, completed, mine, shared, mine_test]
    }
    assert sum(row["start_date"] is None or row["target_date"] is None for row in rows) == summary.data["undated_count"]

    ProjectMember.objects.filter(project=project, member=create_user).update(role=20)
    assert session_client.get(endpoint).data["total"] == 11
    all_rows = session_client.get(issue_url(project), {"work_item_scope": "overview", "order_by": "workflow_order"})
    assert {str(row["id"]) for row in all_rows.data["results"]} == admin_ids

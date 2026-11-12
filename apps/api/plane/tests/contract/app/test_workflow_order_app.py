# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import date

import pytest
from django.utils import timezone

from plane.db.models import Issue, IssueWorkflowContext, ProjectWorkflow, State
from . import test_project_workflow_app as fixtures
from .test_project_workflow_app import issue_url

workflow_project = fixtures.workflow_project
workflow_settings = fixtures.workflow_settings
pytestmark = [pytest.mark.django_db, pytest.mark.contract]


def test_workflow_order_is_global_paginated_and_preserves_filters(session_client, workflow_project):
    project, config = workflow_project

    def create(name, parent=None, start=None, reviewed=False, source=None):
        issue = Issue.objects.create(
            project=project,
            workspace=project.workspace,
            name=name,
            state_id=config["task_initial_state_id"],
            parent=parent,
            start_date=start,
        )
        IssueWorkflowContext.objects.create(
            issue=issue,
            origin="intake" if reviewed else "task",
            accepted_at=timezone.now() if reviewed else None,
            test_source=source,
        )
        return issue

    late = create("Late requirement", start=date(2026, 11, 20), reviewed=True)
    test_state = State.objects.create(
        project=project, workspace=project.workspace, name="Manual testing", group="started"
    )
    ProjectWorkflow.objects.create(project=project, test_task_initial_state=test_state)
    manual_test = create("Independent testing")
    Issue.objects.filter(pk=manual_test.pk).update(state=test_state)
    standalone = create("Independent work")
    early = create("Early requirement", start=date(2026, 11, 12), reviewed=True)
    development = create("Development", parent=early)
    testing = create("Testing", parent=development, source=development)
    nested = create("Nested development", parent=development)
    late_child = create("Late development", parent=late)
    expected = [early, development, nested, testing, late, late_child, standalone, manual_test]
    params = {"order_by": "workflow_order", "work_item_scope": "all", "per_page": 2}
    result = []
    ranks = []
    while True:
        response = session_client.get(issue_url(project), params)
        assert response.status_code == 200, response.data
        result.extend(str(row["id"]) for row in response.data["results"])
        ranks.extend(row["workflow_order"] for row in response.data["results"])
        if not response.data["next_page_results"]:
            break
        params["cursor"] = response.data["next_cursor"]
    assert result == [str(issue.pk) for issue in expected]
    assert ranks == sorted(set(ranks))

    # Excluding the requirement doesn't detach its descendants from their family.
    Issue.objects.filter(pk=development.pk).update(priority="high")
    Issue.objects.filter(pk=testing.pk).update(priority="high")
    Issue.objects.filter(pk=late_child.pk).update(priority="high")
    response = session_client.get(
        issue_url(project),
        {
            "order_by": "workflow_order",
            "priority": "high",
            "work_item_scope": "all",
        },
    )
    assert response.status_code == 200, response.data
    assert [str(row["id"]) for row in response.data["results"]] == [
        str(issue.pk) for issue in [development, testing, late_child]
    ]

    # A date edit moves the entire family on the next request.
    Issue.objects.filter(pk=late.pk).update(start_date=date(2026, 11, 1))
    response = session_client.get(issue_url(project), {"order_by": "workflow_order", "work_item_scope": "all"})
    assert [str(row["id"]) for row in response.data["results"]][:2] == [str(late.pk), str(late_child.pk)]

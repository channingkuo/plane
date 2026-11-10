# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Provenance for server-created example projects, before they become visible.

This is deliberately only called by the two new-project seed transactions, never
by workflow activation or by an ordinary work-item input serializer.
"""

from plane.db.models import Issue, IntakeIssue, IssueWorkflowContext


def record_new_project_sources(project, actor):
    intakes = {record.issue_id: record for record in IntakeIssue.objects.filter(project=project)}
    contexts = []
    for issue in Issue.objects.filter(project=project):
        intake = intakes.get(issue.pk)
        accepted = intake is not None and intake.status == 1
        contexts.append(
            IssueWorkflowContext(
                issue=issue,
                origin="intake" if intake else "task",
                original_intake_id=intake.pk if intake else None,
                accepted_by=actor if accepted else None,
                accepted_at=intake.created_at if accepted else None,
            )
        )
    IssueWorkflowContext.objects.bulk_create(contexts)

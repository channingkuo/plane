# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Project workflow configuration and durable execution facts."""

from django.db import models

from .base import BaseModel


class WorkflowBranch(models.TextChoices):
    TASK = "task", "Task"
    REQUIREMENT = "requirement", "Requirement"


class ProjectWorkflow(BaseModel):
    project = models.OneToOneField("db.Project", on_delete=models.CASCADE, related_name="workflow")
    enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    task_initial_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+", null=True)
    test_task_initial_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+", null=True)
    intake_submission_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+", null=True)
    intake_acceptance_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+", null=True)
    requirement_completed_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+", null=True)
    requirement_reopen_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+", null=True)

    class Meta:
        db_table = "project_workflows"


class WorkflowTransition(BaseModel):
    workflow = models.ForeignKey(ProjectWorkflow, on_delete=models.CASCADE, related_name="transitions")
    branch = models.CharField(max_length=20, choices=WorkflowBranch.choices)
    from_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+")
    to_state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+")
    roles = models.JSONField(default=list)
    creates_test_task = models.BooleanField(default=False)

    class Meta:
        db_table = "workflow_transitions"
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "branch", "from_state", "to_state"], name="workflow_transition_unique"
            )
        ]


class WorkflowStateRequirement(BaseModel):
    workflow = models.ForeignKey(ProjectWorkflow, on_delete=models.CASCADE, related_name="requirements")
    branch = models.CharField(max_length=20, choices=WorkflowBranch.choices)
    state = models.ForeignKey("db.State", on_delete=models.PROTECT, related_name="+")
    fields = models.JSONField(default=list)

    class Meta:
        db_table = "workflow_state_requirements"
        constraints = [
            models.UniqueConstraint(fields=["workflow", "branch", "state"], name="workflow_requirement_unique")
        ]


class IssueWorkflowContext(BaseModel):
    issue = models.OneToOneField("db.Issue", on_delete=models.CASCADE, related_name="workflow_context")
    origin = models.CharField(max_length=12, choices=[("task", "Task"), ("intake", "Intake")])
    original_intake_id = models.UUIDField(null=True)
    accepted_by = models.ForeignKey("db.User", on_delete=models.SET_NULL, null=True, related_name="+")
    accepted_at = models.DateTimeField(null=True)
    accepted_revision = models.PositiveIntegerField(null=True)
    children_revision = models.PositiveIntegerField(default=0)
    manual_reopen_revision = models.PositiveIntegerField(null=True)
    blocked_reason = models.JSONField(default=dict)
    test_source = models.OneToOneField(
        "db.Issue", on_delete=models.SET_NULL, null=True, related_name="generated_test_context"
    )

    class Meta:
        db_table = "issue_workflow_contexts"

    @property
    def branch(self):
        return WorkflowBranch.REQUIREMENT if self.origin == "intake" and self.accepted_at else WorkflowBranch.TASK


class WorkflowAudit(BaseModel):
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="workflow_audits")
    issue_id_snapshot = models.UUIDField(null=True)
    actor = models.ForeignKey("db.User", on_delete=models.SET_NULL, null=True, related_name="+")
    operation = models.CharField(max_length=40)
    revision = models.PositiveIntegerField()
    details = models.JSONField(default=dict)

    class Meta:
        db_table = "workflow_audits"
        indexes = [models.Index(fields=["project", "issue_id_snapshot", "created_at"], name="workflow_audit_issue_idx")]

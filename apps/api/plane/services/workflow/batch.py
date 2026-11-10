# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from rest_framework import serializers

from plane.db.models import Issue, IssueWorkflowContext
from plane.bgtasks.issue_activities_task import issue_activity
from .configuration import issue_values, validate_assignees
from .execution import active_mutation
from .rules import Violation, validate_fields
from .tasks import enqueue_after_commit


def controlled_batch_dates(project_id, actor, updates):
    """Validate the whole batch before its first write; preserve explicit nulls."""
    mutation = active_mutation.get()
    if not mutation:
        return False
    project = next((project for key, project in mutation["projects"].items() if str(key) == str(project_id)), None)
    if not project or not (config := mutation["configs"].get(project.pk)):
        return False

    class DateUpdate(serializers.Serializer):
        id = serializers.UUIDField()
        start_date = serializers.DateField(required=False, allow_null=True)
        target_date = serializers.DateField(required=False, allow_null=True)

    serializer = DateUpdate(data=updates, many=True)
    serializer.is_valid(raise_exception=True)
    patches = {}
    for item in serializer.validated_data:
        item = dict(item)
        key = item.pop("id")
        patches.setdefault(key, {}).update(item)
    issues = list(
        Issue.formal_objects.select_for_update(of=("self",)).filter(project=project, pk__in=patches).order_by("id")
    )
    # Match the existing resource-scoping semantics: foreign IDs cannot be edited.
    originals = {}
    for issue in issues:
        context = IssueWorkflowContext.objects.filter(issue=issue).first()
        if not context:
            raise Violation("WORKFLOW_SOURCE_MISSING", "工作项缺少可信来源记录", {"issue_id": str(issue.pk)})
        originals[issue.pk] = {field: getattr(issue, field) for field in patches[issue.pk]}
        try:
            validate_assignees(project, issue_values(issue)["assignees"])
            validate_fields(
                config,
                "requirement" if context.origin == "intake" else "task",
                str(issue.state_id),
                {**issue_values(issue), **patches[issue.pk]},
            )
        except Violation as exc:
            exc.details["issue_id"] = str(issue.pk)
            raise
    for issue in issues:
        for field, value in patches[issue.pk].items():
            setattr(issue, field, value)
    Issue.formal_objects.bulk_update(issues, ["start_date", "target_date"])
    for issue in issues:
        enqueue_after_commit(
            issue_activity,
            type="issue.activity.updated",
            requested_data=json.dumps(patches[issue.pk], cls=DjangoJSONEncoder),
            current_instance=json.dumps(originals[issue.pk], cls=DjangoJSONEncoder),
            actor_id=str(actor.pk),
            issue_id=str(issue.pk),
            project_id=str(project.pk),
            epoch=int(timezone.now().timestamp()),
        )
    return True


def controlled_batch_properties(project_id, request):
    """Validate all property patches, then persist and validate the final tree atomically."""
    import json
    from rest_framework import serializers
    from rest_framework.exceptions import NotFound
    from plane.app.serializers import IssueCreateSerializer, IssueSerializer
    from plane.db.models import IssueAssignee, IssueLabel
    from plane.bgtasks.issue_activities_task import issue_activity
    from plane.services.workflow.execution import active_mutation
    from plane.services.workflow.rules import Violation
    from plane.services.workflow.tasks import enqueue_after_commit
    from plane.services.workflow.configuration import WorkflowError
    from rest_framework.exceptions import APIException

    mutation = active_mutation.get()
    project = mutation["projects"].get(project_id)
    if not project or not mutation["configs"].get(project_id):
        raise NotFound()

    class Batch(serializers.Serializer):
        issue_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=500)
        properties = serializers.DictField(allow_empty=False)

    batch = Batch(data=request.data)
    batch.is_valid(raise_exception=True)
    ids = set(batch.validated_data["issue_ids"])
    properties = batch.validated_data["properties"]
    supported = {"state_id", "priority", "assignee_ids", "label_ids", "start_date", "target_date", "estimate_point"}
    if set(properties) - supported:
        raise Violation(
            "WORKFLOW_ENTRY_UNSUPPORTED",
            "此批量入口不支持所选属性，请分别修改模块或周期",
            {"fields": sorted(set(properties) - supported)},
        )
    issues = list(Issue.formal_objects.filter(project=project, pk__in=ids).order_by("id"))
    if {issue.pk for issue in issues} != ids:
        raise Violation("WORKFLOW_INVALID_ISSUE", "批量工作项必须属于当前项目且可访问")
    prepared = []
    # Prechecks defer descendant completion to the transaction's final tree.
    mutation["batch_issue_ids"] = frozenset(str(issue.pk) for issue in issues)
    for issue in issues:
        patch = dict(properties)
        for field, model, fk in [
            ("assignee_ids", IssueAssignee, "assignee_id"),
            ("label_ids", IssueLabel, "label_id"),
        ]:
            if field in patch:
                if not isinstance(patch[field], list):
                    raise Violation(
                        "WORKFLOW_INVALID_FIELDS",
                        "批量关系属性必须使用列表",
                        {"issue_id": str(issue.pk), "fields": [field]},
                    )
                patch[field] = sorted(
                    set(map(str, patch[field]))
                    | set(map(str, model.objects.filter(issue=issue).values_list(fk, flat=True)))
                )
        serializer = IssueCreateSerializer(
            issue,
            data=patch,
            partial=True,
            context={"project_id": project.pk, "workspace_id": project.workspace_id, "request": request},
        )
        try:
            serializer.is_valid(raise_exception=True)
        except APIException as exc:
            if isinstance(exc.detail, dict) and "code" in exc.detail:
                exc.detail.setdefault("details", {})["issue_id"] = str(issue.pk)
                raise
            raise WorkflowError(
                Violation(
                    "WORKFLOW_INVALID_FIELDS",
                    "批量工作项属性不合法",
                    {"issue_id": str(issue.pk), "errors": exc.detail},
                    status=exc.status_code,
                )
            ) from exc
        prepared.append((serializer, IssueSerializer(issue).data, patch))
    for serializer, original, patch in prepared:
        issue = serializer.save()
        enqueue_after_commit(
            issue_activity,
            type="issue.activity.updated",
            requested_data=json.dumps(patch, default=str),
            current_instance=json.dumps(original, default=str),
            actor_id=str(request.user.pk),
            issue_id=str(issue.pk),
            project_id=str(project.pk),
            notification=True,
            epoch=int(timezone.now().timestamp()),
        )
    return {"issue_ids": [str(issue.pk) for issue in issues]}

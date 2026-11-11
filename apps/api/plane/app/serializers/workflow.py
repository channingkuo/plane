# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import serializers


class TransitionSerializer(serializers.Serializer):
    branch = serializers.ChoiceField(choices=["task", "requirement"])
    from_state_id = serializers.UUIDField()
    to_state_id = serializers.UUIDField()
    roles = serializers.ListField(child=serializers.ChoiceField(choices=[5, 15, 20]), allow_empty=False)
    creates_test_task = serializers.BooleanField(required=False, default=False)


class RequirementSerializer(serializers.Serializer):
    branch = serializers.ChoiceField(choices=["task", "requirement"])
    state_id = serializers.UUIDField()
    fields = serializers.ListField(child=serializers.ChoiceField(choices=["assignees", "start_date", "target_date"]))


class WorkflowConfigurationSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    revision = serializers.IntegerField(min_value=0)
    task_initial_state_id = serializers.UUIDField()
    test_task_initial_state_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    intake_submission_state_id = serializers.UUIDField()
    intake_acceptance_state_id = serializers.UUIDField()
    requirement_completed_state_id = serializers.UUIDField()
    requirement_reopen_state_id = serializers.UUIDField()
    transitions = TransitionSerializer(many=True)
    requirements = RequirementSerializer(many=True)


class WorkflowPreviewSerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False, allow_null=True)
    target_date = serializers.DateField(required=False, allow_null=True)
    assignees = serializers.ListField(child=serializers.UUIDField(), required=False)
    assignee_ids = serializers.ListField(child=serializers.UUIDField(), required=False, source="assignees")

    def validate(self, attrs):
        if "assignees" in self.initial_data and "assignee_ids" in self.initial_data:
            if set(map(str, self.initial_data["assignees"])) != set(map(str, self.initial_data["assignee_ids"])):
                raise serializers.ValidationError("负责人字段不能提供相互冲突的值")
        return attrs

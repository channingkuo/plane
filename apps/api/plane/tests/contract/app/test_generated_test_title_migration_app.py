# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.utils import timezone

from plane.db.models import Issue, IssueWorkflowContext
from . import test_project_workflow_app as fixtures

workflow_project = fixtures.workflow_project
pytestmark = [pytest.mark.django_db, pytest.mark.contract]


def test_existing_generated_titles_are_renamed_safely_and_idempotently(workflow_project):
    project, config = workflow_project

    def make(name, automatic=True):
        source = (
            Issue.objects.create(
                project=project,
                workspace=project.workspace,
                name="Renamed source",
                state_id=config["task_initial_state_id"],
            )
            if automatic
            else None
        )
        issue = Issue.objects.create(
            project=project,
            workspace=project.workspace,
            name=name,
            parent=source,
            state_id=config["task_initial_state_id"],
        )
        IssueWorkflowContext.objects.create(issue=issue, origin="task", test_source=source)
        return issue

    legacy = make("测试：旧原标题")
    long_title = make("测试：" + "开" * 252)
    custom = make("已人工修改的标题")
    current = make("【自动生成测试任务】新原标题")
    manual = make("测试：手动创建", automatic=False)
    archived = make("测试：已归档")
    deleted = make("测试：已删除")
    Issue.all_objects.filter(pk=archived.pk).update(archived_at=timezone.now())
    Issue.all_objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())

    migration = import_module("plane.db.migrations.0128_generated_test_task_title_prefix")
    # The suite normally uses --nomigrations; load historical model definitions
    # without changing the test database's schema or migration history.
    with override_settings(MIGRATION_MODULES={}):
        historical_apps = MigrationExecutor(connection).loader.project_state([("db", "0127_workflow_test_tasks")]).apps
    expected = {
        legacy.pk: "【自动生成测试任务】旧原标题",
        long_title.pk: ("【自动生成测试任务】" + "开" * 252)[:255],
        custom.pk: custom.name,
        current.pk: current.name,
        manual.pk: manual.name,
        archived.pk: "【自动生成测试任务】已归档",
        deleted.pk: deleted.name,
    }
    for _ in range(2):
        migration.rename_generated_test_titles(historical_apps, connection.schema_editor())
        assert dict(Issue.all_objects.filter(pk__in=expected).values_list("pk", "name")) == expected

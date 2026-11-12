# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Concat, Left, Substr


def rename_generated_test_titles(apps, schema_editor):
    issue = apps.get_model("db", "Issue")
    context = apps.get_model("db", "IssueWorkflowContext")
    database = schema_editor.connection.alias
    generated_ids = (
        context._base_manager.using(database)
        .filter(test_source_id__isnull=False, deleted_at__isnull=True)
        .values("issue_id")
    )
    # Preserve manually named tasks and the stored original title, even when
    # the development source has since been renamed. Match durable provenance.
    issue._base_manager.using(database).filter(
        pk__in=generated_ids, deleted_at__isnull=True, name__startswith="测试："
    ).update(name=Left(Concat(Value("【自动生成测试任务】"), Substr("name", len("测试：") + 1)), 255))


class Migration(migrations.Migration):
    dependencies = [("db", "0127_workflow_test_tasks")]

    operations = [migrations.RunPython(rename_generated_test_titles, migrations.RunPython.noop)]

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("db", "0126_project_workflows")]

    operations = [
        migrations.AddField(
            model_name="projectworkflow",
            name="test_task_initial_state",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="db.state"
            ),
        ),
        migrations.AddField(
            model_name="workflowtransition",
            name="creates_test_task",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="issueworkflowcontext",
            name="test_source",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="generated_test_context",
                to="db.issue",
            ),
        ),
    ]

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Seed an isolated workflow browser-test database; never run on production data."""

# Django must initialize before importing its models.
# ruff: noqa: E402
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

if os.environ.get("WORKFLOW_E2E_ALLOW_SEED") != "1":
    raise SystemExit("Set WORKFLOW_E2E_ALLOW_SEED=1 with an isolated DATABASE_URL")

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps/api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.test")

import django

django.setup()

from django.test import Client
from django.utils import timezone
from plane.db.models import (
    User,
    Profile,
    Workspace,
    WorkspaceMember,
    Project,
    ProjectMember,
    State,
    Intake,
)
from plane.license.models import Instance

token = uuid4().hex[:10]
user = User.objects.create(
    email=f"workflow-{token}@example.invalid",
    username=f"workflow-{token}",
    first_name="Workflow",
    is_email_verified=True,
)
workspace = Workspace.objects.create(
    name="Workflow acceptance", slug=f"workflow-{token}", owner=user
)
WorkspaceMember.objects.create(workspace=workspace, member=user, role=20)
Profile.objects.update_or_create(
    user=user,
    defaults={
        "is_onboarded": True,
        "is_tour_completed": True,
        "is_navigation_tour_completed": True,
        "last_workspace_id": workspace.pk,
        "language": "en",
    },
)
project = Project.objects.create(
    workspace=workspace,
    name="Workflow acceptance",
    identifier=f"WF{token[:6].upper()}",
    intake_view=True,
)
ProjectMember.objects.create(workspace=workspace, project=project, member=user, role=20)
Intake.objects.create(
    workspace=workspace, project=project, name="Intake", is_default=True
)
states = {}
for name, group in [
    ("Ready", "unstarted"),
    ("Review", "triage"),
    ("Reviewed", "started"),
    ("Live", "completed"),
]:
    states[name] = str(
        State.objects.create(
            workspace=workspace,
            project=project,
            name=name,
            group=group,
            color="#234567",
            default=name == "Ready",
        ).pk
    )
Instance.objects.get_or_create(
    instance_id="workflow-browser-tests",
    defaults={
        "instance_name": "Workflow test",
        "current_version": "1.5.0",
        "is_setup_done": True,
        "is_signup_screen_visited": True,
        "last_checked_at": timezone.now(),
    },
)
client = Client()
client.force_login(user)
fixture = {
    "workspaceSlug": workspace.slug,
    "projectId": str(project.pk),
    "userId": str(user.pk),
    "states": states,
    "cookieName": "session-id",
    "cookie": client.cookies["session-id"].value,
}
Path(
    os.environ.get("WORKFLOW_E2E_FIXTURE", "/tmp/plane-workflow-browser-fixture.json")
).write_text(json.dumps(fixture))
print("Seeded isolated workflow browser fixture")

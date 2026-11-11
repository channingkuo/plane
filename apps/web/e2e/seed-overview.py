# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Extend the isolated workflow browser seed with a project member session."""

# ruff: noqa: E402
import os
import json
from pathlib import Path

os.environ.setdefault(
    "WORKFLOW_E2E_FIXTURE", "/tmp/plane-overview-browser-fixture.json"
)
from seed import (
    User,
    Profile,
    WorkspaceMember,
    ProjectMember,
    Client,
    token,
    workspace,
    project,
    fixture,
)

member = User.objects.create(
    email=f"member-{token}@example.invalid",
    username=f"member-{token}",
    first_name="Project member",
    is_email_verified=True,
)
WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)
ProjectMember.objects.create(
    workspace=workspace, project=project, member=member, role=15
)
Profile.objects.update_or_create(
    user=member,
    defaults={
        "is_onboarded": True,
        "is_tour_completed": True,
        "is_navigation_tour_completed": True,
        "last_workspace_id": workspace.pk,
        "language": "en",
    },
)
client = Client()
client.force_login(member)
fixture.update(memberId=str(member.pk), memberCookie=client.cookies["session-id"].value)
Path(os.environ["WORKFLOW_E2E_FIXTURE"]).write_text(json.dumps(fixture))
print("Seeded isolated overview browser fixture")

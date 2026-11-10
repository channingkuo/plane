# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from copy import deepcopy

import pytest

from plane.services.workflow.rules import Violation, evaluate, validate_configuration


@pytest.fixture
def config():
    return {
        "revision": 1,
        "enabled": True,
        "task_initial_state_id": "todo",
        "intake_submission_state_id": "pending",
        "intake_acceptance_state_id": "accepted",
        "requirement_completed_state_id": "done",
        "requirement_reopen_state_id": "accepted",
        "transitions": [
            {"branch": "requirement", "from_state_id": "accepted", "to_state_id": "done", "roles": [20]},
            {"branch": "requirement", "from_state_id": "done", "to_state_id": "accepted", "roles": [20]},
            {"branch": "task", "from_state_id": "todo", "to_state_id": "doing", "roles": [15]},
        ],
        "requirements": [{"branch": "task", "state_id": "doing", "fields": ["assignees", "start_date", "target_date"]}],
    }


STATES = {"todo": "unstarted", "doing": "started", "pending": "triage", "accepted": "started", "done": "completed"}
VALUES = {"assignees": ["user"], "start_date": "2026-11-10", "target_date": "2026-11-12"}
pytestmark = pytest.mark.unit


def test_valid_config_and_combined_update(config):
    validate_configuration(config, STATES)
    evaluate(config, "task", "todo", "doing", 15, VALUES)


@pytest.mark.parametrize("role", [None, 5, 20])
def test_roles_do_not_gain_an_admin_bypass(config, role):
    with pytest.raises(Violation) as failure:
        evaluate(config, "task", "todo", "doing", role, VALUES)
    assert failure.value.status == 403


def test_no_path_denies(config):
    with pytest.raises(Violation, match="未配置"):
        evaluate(config, "task", "todo", "done", 20, VALUES)


@pytest.mark.parametrize("missing", ["assignees", "start_date", "target_date"])
def test_same_state_updates_still_validate_fields(config, missing):
    values = {**VALUES, missing: None}
    with pytest.raises(Violation) as failure:
        evaluate(config, "task", "doing", "doing", 15, values)
    assert missing in failure.value.details["fields"]


def test_final_date_order(config):
    with pytest.raises(Violation) as failure:
        evaluate(config, "task", "doing", "doing", 15, {**VALUES, "target_date": "2026-11-01"})
    assert failure.value.code == "WORKFLOW_INVALID_DATE"


def test_requirement_and_task_paths_are_independent(config):
    with pytest.raises(Violation):
        evaluate(config, "requirement", "todo", "doing", 20, VALUES)


def test_requirement_manual_changes_require_real_project_admin(config):
    with pytest.raises(Violation) as failure:
        evaluate(config, "requirement", "accepted", "done", 15, VALUES)
    assert failure.value.status == 403


@pytest.mark.parametrize(
    "mutation",
    [
        {"task_initial_state_id": "pending"},
        {"intake_acceptance_state_id": "pending"},
        {"requirement_completed_state_id": "doing"},
        {"requirement_reopen_state_id": "done"},
        {"transitions": []},
    ],
)
def test_invalid_special_paths_rejected(config, mutation):
    with pytest.raises(Violation):
        validate_configuration({**config, **mutation}, STATES)


def test_duplicates_and_unknown_fields_rejected(config):
    duplicate = deepcopy(config)
    duplicate["transitions"].append(duplicate["transitions"][0])
    with pytest.raises(Violation):
        validate_configuration(duplicate, STATES)
    invalid = deepcopy(config)
    invalid["requirements"][0]["fields"] = ["description"]
    with pytest.raises(Violation):
        validate_configuration(invalid, STATES)


def test_system_paths_still_validate_fields(config):
    with pytest.raises(Violation):
        evaluate(config, "task", "todo", "doing", None, {}, system=True)

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import date

import pytest

from plane.utils.workflow_order import workflow_order_ids

pytestmark = pytest.mark.unit


def item(key, sequence, parent=None, start=None, **facts):
    return {"id": key, "sequence_id": sequence, "parent_id": parent, "start_date": start, **facts}


def test_requirement_families_order_by_start_then_development_then_tests():
    rows = [
        item("late", 1, start=date(2026, 11, 15), origin="intake", reviewed=True),
        item("early", 2, start=date(2026, 11, 12), origin="intake", reviewed=True),
        item("test", 3, "development", is_test=True),
        item("development", 4, "early"),
        item("nested", 5, "development"),
        item("late-development", 6, "late"),
        item("other-development", 7, "early"),
        item("other-test", 8, "other-development", is_test=True),
        item("independent-test", 9, is_test=True),
        item("independent", 10),
    ]
    expected = [
        "early",
        "development",
        "nested",
        "other-development",
        "test",
        "other-test",
        "late",
        "late-development",
        "independent",
        "independent-test",
    ]
    assert workflow_order_ids(rows) == expected
    assert workflow_order_ids(reversed(rows)) == expected


def test_undated_ties_unreviewed_requirements_and_orphans_are_retained():
    rows = [
        item("undated", 1, origin="intake", reviewed=True),
        item("second", 3, start=date(2026, 11, 12), origin="intake", reviewed=True),
        item("first", 2, start=date(2026, 11, 12), origin="intake", reviewed=True),
        item("pending", 4, start=date(2026, 11, 1), origin="intake", reviewed=False),
        item("orphan", 5, "deleted-parent"),
        item("ordinary", 6),
    ]
    assert workflow_order_ids(rows) == ["first", "second", "undated", "pending", "orphan", "ordinary"]


def test_test_descendants_stay_after_all_development_and_cycles_terminate():
    rows = [
        item("requirement", 1, origin="intake", reviewed=True),
        item("test", 2, "requirement", is_test=True),
        item("test-child", 3, "test"),
        item("development", 4, "requirement"),
        item("cycle-a", 5, "cycle-b"),
        item("cycle-b", 6, "cycle-a"),
    ]
    result = workflow_order_ids(rows)
    assert result[:4] == ["requirement", "development", "test", "test-child"]
    assert set(result) == {row["id"] for row in rows}
    assert workflow_order_ids([]) == []

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Stable requirement/development/test ordering, before filtering and pagination."""

from datetime import date


def workflow_order_ids(rows):
    nodes = {row["id"]: row for row in rows}
    families = {}
    paths = {}
    testing = {}

    def sibling_key(key):
        row = nodes[key]
        return (row["sequence_id"], str(key))

    # Resolve parents once, iteratively, including ancestors outside the current
    # layout's filter. Guard old/corrupt parent cycles without dropping any rows.
    for key in nodes:
        chain, seen = [], set()
        current = key
        while current in nodes and current not in families and current not in seen:
            chain.append(current)
            seen.add(current)
            current = nodes[current]["parent_id"]
        for item in reversed(chain):
            row = nodes[item]
            parent = row["parent_id"]
            is_requirement = row.get("origin") == "intake"
            if is_requirement:
                families[item] = item
                paths[item] = ()
                testing[item] = False
            else:
                families[item] = families.get(parent)
                paths[item] = paths.get(parent, ()) + (sibling_key(item),)
                testing[item] = bool(row.get("is_test") or testing.get(parent, False))

    def key(item):
        root = families[item]
        if root is None:
            # Independent development items, then independent test items.
            return (2, date.max, 0, "", int(testing[item]), paths[item])
        requirement = nodes[root]
        return (
            0 if requirement.get("reviewed") else 1,
            requirement["start_date"] or date.max,
            requirement["sequence_id"],
            str(root),
            0 if item == root else (2 if testing[item] else 1),
            paths[item],
        )

    return sorted(nodes, key=key)


def order_workflow_queryset(queryset):
    from django.db.models import BooleanField, Case, IntegerField, Value, When
    from plane.db.models import Issue

    # Use durable Intake acceptance/test-source facts, rather than translated
    # state or work-item names. Keep archived ancestors available for grouping.
    rows = Issue.all_objects.filter(
        project_id__in=queryset.order_by().values("project_id"), deleted_at__isnull=True, is_draft=False
    ).values(
        "id",
        "parent_id",
        "sequence_id",
        "start_date",
        "state_id",
        "workflow_context__origin",
        "workflow_context__accepted_at",
        "workflow_context__test_source_id",
        "project__workflow__test_task_initial_state_id",
    )
    ordered_ids = workflow_order_ids(
        [
            {
                **row,
                "origin": row["workflow_context__origin"],
                "reviewed": row["workflow_context__accepted_at"] is not None,
                "is_test": row["workflow_context__test_source_id"] is not None
                or (
                    row["project__workflow__test_task_initial_state_id"] is not None
                    and row["state_id"] == row["project__workflow__test_task_initial_state_id"]
                ),
            }
            for row in rows
        ]
    )
    return queryset.annotate(
        is_workflow_requirement=Case(
            When(workflow_context__origin="intake", workflow_context__accepted_at__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        workflow_order=Case(
            *[When(pk=key, then=Value(index)) for index, key in enumerate(ordered_ids)],
            default=Value(len(ordered_ids)),
            output_field=IntegerField(),
        ),
    ).order_by("workflow_order", "id")

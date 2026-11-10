# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from copy import deepcopy

from django.db import transaction, connection


def enqueue_after_commit(task, *args, **kwargs):
    """Freeze the payload and never publish events for a transaction that rolls back."""
    task_args, task_kwargs = deepcopy(args), deepcopy(kwargs)
    if not connection.in_atomic_block and (connection.connection is None or connection.autocommit):
        return task.delay(*task_args, **task_kwargs)
    transaction.on_commit(lambda: task.delay(*task_args, **task_kwargs))

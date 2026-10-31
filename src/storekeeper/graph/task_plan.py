"""Deterministic validation for the classifier's flat task plan."""

import re

from storekeeper.domain import Task

PLAIN_ORDER_REFERENCE_PATTERN = re.compile(r"^#?(\d{1,10})$")


def find_task_plan_conflict(tasks: list[Task]) -> str | None:
    """Return why a flat task plan is unsafe to dispatch in parallel."""
    write_task_ids_by_order: dict[str, list[str]] = {}
    for task in tasks:
        if task["requested_action"] is None or task["order_reference"] is None:
            continue

        normalized_order_reference = _normalize_plain_order_reference(
            task["order_reference"]
        )
        if normalized_order_reference is None:
            continue
        write_task_ids_by_order.setdefault(normalized_order_reference, []).append(
            task["task_id"]
        )

    conflicting_orders = [
        order_reference
        for order_reference, task_ids in write_task_ids_by_order.items()
        if len(task_ids) > 1
    ]
    if not conflicting_orders:
        return None

    joined_order_references = ", ".join(sorted(conflicting_orders))
    return (
        "The ticket requests multiple write actions for the same order "
        f"({joined_order_references}); v2 will not run those actions in parallel."
    )


def _normalize_plain_order_reference(order_reference: str) -> str | None:
    reference_match = PLAIN_ORDER_REFERENCE_PATTERN.fullmatch(order_reference.strip())
    if reference_match is None:
        return None
    return f"#{reference_match.group(1)}"

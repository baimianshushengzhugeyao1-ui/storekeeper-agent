"""A narrow, structured clarification before a missing-order cancellation."""

import re

from langgraph.types import interrupt

from storekeeper.graph.state import TicketState

CLARIFICATION_ORDER_REFERENCE_PATTERN = re.compile(r"#?[0-9]{1,10}")


def normalize_clarification_order_reference(order_reference: str) -> str:
    """Accept one ASCII order number and return its canonical '#'-prefixed form."""
    if not isinstance(order_reference, str):
        raise ValueError("Order reference must be text.")
    cleaned_reference = order_reference.strip()
    if not CLARIFICATION_ORDER_REFERENCE_PATTERN.fullmatch(cleaned_reference):
        raise ValueError(
            "Enter one order number using 1 to 10 ASCII digits, optionally "
            "prefixed with '#', for example #1036."
        )
    return f"#{cleaned_reference.removeprefix('#')}"


def is_clarification_payload(value: object) -> bool:
    """Distinguish customer questions from legacy, untagged approval interrupts."""
    return isinstance(value, dict) and value.get("kind") == "clarification"


def clarify_missing_order_reference_node(state: TicketState) -> dict:
    """Pause only a single cancellation whose order number was omitted."""
    tasks = state["tasks"]
    if len(tasks) != 1:
        return {}
    task = tasks[0]
    if (
        task["intent"] != "cancel_order"
        or task["requested_action"] != "cancel_order"
        or task["order_reference"] is not None
    ):
        return {}

    task_id = task.get("task_id") or "task-1"
    clarification_response = interrupt(
        {
            "kind": "clarification",
            "task_id": task_id,
            "field": "order_reference",
            "question": "What is the order number for the order you want to cancel?",
        }
    )
    if (
        not isinstance(clarification_response, dict)
        or set(clarification_response) != {"order_reference"}
    ):
        raise ValueError("Clarification must contain only an order_reference field.")
    order_reference = normalize_clarification_order_reference(
        clarification_response["order_reference"]
    )

    # Resume replays this node. Return only new values: task_results has an
    # append reducer, whereas this history is a normal replacement channel.
    return {
        "tasks": [{**task, "order_reference": order_reference}],
        "clarification_history": [
            *state.get("clarification_history", []),
            {
                "task_id": task_id,
                "field": "order_reference",
                "value": order_reference,
            },
        ],
    }

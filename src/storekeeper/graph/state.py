"""State schemas for the ticket-handling graph."""

import operator
from typing import Annotated, Literal, TypedDict

from storekeeper.domain import GateVerdict, ShopifyOrder, Task, TaskResult

TicketOutcome = Literal["resolved", "escalated_to_human"]


class ClarificationHistoryRecord(TypedDict):
    task_id: str
    field: Literal["order_reference"]
    value: str


class TaskState(TypedDict):
    """State for the per-task pipeline: lookup -> gate -> approval -> execute."""

    task: Task
    shopify_order: ShopifyOrder | None
    gate_verdict: GateVerdict | None
    task_result: TaskResult | None


class TicketState(TypedDict):
    """State for one customer ticket; wraps the per-task pipeline."""

    ticket_text: str
    tasks: list[Task]
    clarification_history: list[ClarificationHistoryRecord]
    # Appends instead of overwriting so the v2 planner can fan tasks out in
    # parallel without changing this schema.
    task_results: Annotated[list[TaskResult], operator.add]
    reply_draft: str | None
    ticket_outcome: TicketOutcome | None
    escalation_reason: str | None
    plan_conflict_reason: str | None


class TicketTaskWorkerState(TypedDict):
    """Isolated input for one task dispatched from the ticket graph."""

    ticket_text: str
    task: Task
    task_results: Annotated[list[TaskResult], operator.add]

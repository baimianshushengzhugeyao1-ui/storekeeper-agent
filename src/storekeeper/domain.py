"""Shared business data contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, TypedDict

Intent = Literal[
    "cancel_order",
    "refund_request",
    "address_change",
    "policy_question",
    "other",
]

RequestedAction = Literal[
    "cancel_order",
    "issue_refund",
    "update_shipping_address",
]

TaskOutcome = Literal[
    "executed",
    "submitted",
    "rejected_by_human",
    "denied_by_policy",
    "answered",
    "escalated_to_human",
    "failed",
]


class Task(TypedDict):
    # Assigned in code after classification so parallel task results can be
    # restored to the customer's original request order.
    task_id: str
    intent: Intent
    # The order reference as the customer wrote it (e.g. "#1036"), which is a
    # Shopify order *name* — not the Shopify GraphQL id (ShopifyOrder["id"]).
    order_reference: str | None
    requested_action: RequestedAction | None
    new_shipping_address: "ShippingAddress | None"
    confidence: float


class ShippingAddress(TypedDict):
    first_name: str | None
    last_name: str | None
    company: str | None
    address1: str | None
    address2: str | None
    city: str | None
    province: str | None
    zip: str | None
    country: str | None
    phone: str | None


class OrderFacts(TypedDict):
    """Only the Shopify order fields that policy rules need."""

    processed_at: datetime
    fulfilled: bool
    total_amount: Decimal
    currency_code: str


class ShopifyOrder(TypedDict):
    id: str
    name: str
    facts: OrderFacts
    shipping_address: ShippingAddress | None


class GateVerdict(TypedDict):
    passed: bool
    rule: str
    reason: str
    flags: list[str]


class TaskResult(TypedDict):
    task_id: str
    task: Task
    outcome: TaskOutcome
    gate_verdict: GateVerdict | None
    action_result: dict | None
    policy_citations: list[str]

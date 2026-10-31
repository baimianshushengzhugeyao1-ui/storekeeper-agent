"""Deterministic eligibility checks for Shopify order actions."""

from datetime import datetime, timedelta
from decimal import Decimal

from storekeeper.domain import GateVerdict, OrderFacts, RequestedAction

REFUND_WINDOW_DAYS = 30
HIGH_VALUE_REFUND_THRESHOLD = Decimal("100.00")


def policy_gate(
    requested_action: RequestedAction,
    order_facts: OrderFacts,
    evaluated_at: datetime,
) -> GateVerdict:
    """Return whether an order action is eligible for human approval."""
    _require_timezone_aware(order_facts["processed_at"], "order_facts.processed_at")
    _require_timezone_aware(evaluated_at, "evaluated_at")

    if requested_action == "cancel_order":
        return _check_fulfillment_rule(
            fulfilled=order_facts["fulfilled"],
            passing_rule="cancel_order_unfulfilled",
            failing_rule="cancel_order_fulfilled",
            passing_reason="The order is unfulfilled and can be cancelled.",
            failing_reason="The order is already fulfilled and cannot be cancelled.",
        )

    if requested_action == "update_shipping_address":
        return _check_fulfillment_rule(
            fulfilled=order_facts["fulfilled"],
            passing_rule="address_change_unfulfilled",
            failing_rule="address_change_fulfilled",
            passing_reason="The order is unfulfilled and its shipping address can be changed.",
            failing_reason="The order is already fulfilled, so its shipping address cannot be changed.",
        )

    if requested_action == "issue_refund":
        return _check_refund_rule(order_facts, evaluated_at)

    raise ValueError(f"Unsupported requested action: {requested_action}")


def _check_fulfillment_rule(
    *,
    fulfilled: bool,
    passing_rule: str,
    failing_rule: str,
    passing_reason: str,
    failing_reason: str,
) -> GateVerdict:
    if fulfilled:
        return {
            "passed": False,
            "rule": failing_rule,
            "reason": failing_reason,
            "flags": [],
        }

    return {
        "passed": True,
        "rule": passing_rule,
        "reason": passing_reason,
        "flags": [],
    }


def _check_refund_rule(order_facts: OrderFacts, evaluated_at: datetime) -> GateVerdict:
    refund_deadline = order_facts["processed_at"] + timedelta(days=REFUND_WINDOW_DAYS)
    if evaluated_at > refund_deadline:
        return {
            "passed": False,
            "rule": "refund_outside_return_window",
            "reason": f"The order is outside the {REFUND_WINDOW_DAYS}-day refund window.",
            "flags": [],
        }

    flags = []
    if order_facts["total_amount"] > HIGH_VALUE_REFUND_THRESHOLD:
        flags.append("high_value")

    return {
        "passed": True,
        "rule": "refund_within_return_window",
        "reason": f"The order is within the {REFUND_WINDOW_DAYS}-day refund window.",
        "flags": flags,
    }


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")

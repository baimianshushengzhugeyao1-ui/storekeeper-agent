"""Fetch one live Shopify order and evaluate a policy action."""

import argparse
from datetime import datetime, timezone

from storekeeper.domain import RequestedAction
from storekeeper.policy.gate import policy_gate
from storekeeper.shopify.operations import lookup_order

ACTION_CHOICES: tuple[RequestedAction, ...] = (
    "cancel_order",
    "issue_refund",
    "update_shipping_address",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("order_reference", help="Shopify order name, for example #1001")
    parser.add_argument("action", choices=ACTION_CHOICES)
    arguments = parser.parse_args()

    order = lookup_order(arguments.order_reference)
    verdict = policy_gate(
        arguments.action,
        order["facts"],
        evaluated_at=datetime.now(timezone.utc),
    )

    print(f"Order: {order['name']}")
    print(f"Action: {arguments.action}")
    print(f"Eligible: {'yes' if verdict['passed'] else 'no'}")
    print(f"Rule: {verdict['rule']}")
    print(f"Reason: {verdict['reason']}")
    if verdict["flags"]:
        print(f"Flags: {', '.join(verdict['flags'])}")


if __name__ == "__main__":
    main()

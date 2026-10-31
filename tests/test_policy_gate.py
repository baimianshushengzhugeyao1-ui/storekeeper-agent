import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from storekeeper.domain import OrderFacts
from storekeeper.policy.gate import policy_gate

EVALUATED_AT = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def make_order_facts(
    *,
    fulfilled: bool = False,
    age: timedelta = timedelta(days=10),
    total_amount: Decimal = Decimal("50.00"),
) -> OrderFacts:
    return {
        "processed_at": EVALUATED_AT - age,
        "fulfilled": fulfilled,
        "total_amount": total_amount,
        "currency_code": "USD",
    }


class PolicyGateTests(unittest.TestCase):
    def test_unfulfilled_order_can_be_cancelled(self) -> None:
        verdict = policy_gate("cancel_order", make_order_facts(), EVALUATED_AT)

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["rule"], "cancel_order_unfulfilled")

    def test_fulfilled_order_cannot_be_cancelled(self) -> None:
        verdict = policy_gate(
            "cancel_order",
            make_order_facts(fulfilled=True),
            EVALUATED_AT,
        )

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["rule"], "cancel_order_fulfilled")

    def test_refund_at_exactly_thirty_days_passes(self) -> None:
        verdict = policy_gate(
            "issue_refund",
            make_order_facts(age=timedelta(days=30)),
            EVALUATED_AT,
        )

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["flags"], [])

    def test_refund_after_thirty_days_fails(self) -> None:
        verdict = policy_gate(
            "issue_refund",
            make_order_facts(age=timedelta(days=30, seconds=1)),
            EVALUATED_AT,
        )

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["rule"], "refund_outside_return_window")

    def test_refund_over_one_hundred_is_flagged(self) -> None:
        verdict = policy_gate(
            "issue_refund",
            make_order_facts(total_amount=Decimal("100.01")),
            EVALUATED_AT,
        )

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["flags"], ["high_value"])

    def test_refund_at_one_hundred_is_not_flagged(self) -> None:
        verdict = policy_gate(
            "issue_refund",
            make_order_facts(total_amount=Decimal("100.00")),
            EVALUATED_AT,
        )

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["flags"], [])

    def test_unfulfilled_order_address_can_be_changed(self) -> None:
        verdict = policy_gate(
            "update_shipping_address",
            make_order_facts(),
            EVALUATED_AT,
        )

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["rule"], "address_change_unfulfilled")

    def test_fulfilled_order_address_cannot_be_changed(self) -> None:
        verdict = policy_gate(
            "update_shipping_address",
            make_order_facts(fulfilled=True),
            EVALUATED_AT,
        )

        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["rule"], "address_change_fulfilled")

    def test_timezone_naive_dates_are_rejected(self) -> None:
        order_facts = make_order_facts()
        order_facts["processed_at"] = order_facts["processed_at"].replace(tzinfo=None)

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            policy_gate("issue_refund", order_facts, EVALUATED_AT)


if __name__ == "__main__":
    unittest.main()

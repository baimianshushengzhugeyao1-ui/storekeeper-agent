import unittest

from storekeeper.shopify.writes import (
    ShopifyWriteError,
    build_updated_shipping_address,
    cancel_order,
    issue_full_refund,
    requested_shipping_address_is_complete,
    update_shipping_address,
)


class FakeWriteClient:
    """Returns queued responses and records every GraphQL call."""

    def __init__(self, queued_responses: list[dict]):
        self.queued_responses = list(queued_responses)
        self.calls: list[tuple[str, dict | None]] = []

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        return self.queued_responses.pop(0)


def make_cancel_success_response() -> dict:
    return {
        "orderCancel": {
            "job": {"id": "gid://shopify/Job/9", "done": False},
            "orderCancelUserErrors": [],
        }
    }


def make_refund_facts_response(refundable_quantity: int = 2) -> dict:
    return {
        "order": {
            "id": "gid://shopify/Order/123",
            "lineItems": {
                "nodes": [
                    {"id": "gid://shopify/LineItem/11", "refundableQuantity": refundable_quantity}
                ]
            },
        }
    }


def make_suggested_refund_response(with_payment_transaction: bool = True) -> dict:
    suggested_transactions = []
    if with_payment_transaction:
        suggested_transactions.append(
            {
                "amountSet": {"shopMoney": {"amount": "42.00", "currencyCode": "USD"}},
                "gateway": "manual",
                "parentTransaction": {"id": "gid://shopify/OrderTransaction/7"},
            }
        )
    return {
        "order": {
            "suggestedRefund": {
                "amountSet": {"shopMoney": {"amount": "42.00", "currencyCode": "USD"}},
                "suggestedTransactions": suggested_transactions,
            }
        }
    }


def make_refund_success_response() -> dict:
    return {
        "refundCreate": {
            "refund": {
                "id": "gid://shopify/Refund/5",
                "totalRefundedSet": {"shopMoney": {"amount": "42.00", "currencyCode": "USD"}},
            },
            "userErrors": [],
        }
    }


def make_current_shipping_address() -> dict:
    return {
        "first_name": "Rahat",
        "last_name": "Kabir",
        "company": "Storekeeper Demo",
        "address1": "10 Old Road",
        "address2": "Unit 2",
        "city": "Austin",
        "province": "Texas",
        "zip": "78701",
        "country": "United States",
        "phone": "+1 555 0100",
    }


def make_requested_shipping_address() -> dict:
    return {
        "first_name": None,
        "last_name": None,
        "company": None,
        "address1": " 20 Lake Road ",
        "address2": None,
        "city": " Dhaka ",
        "province": "Dhaka",
        "zip": "1205",
        "country": "Bangladesh",
        "phone": None,
    }


def make_order_update_success_response() -> dict:
    return {
        "orderUpdate": {
            "order": {"id": "gid://shopify/Order/123"},
            "userErrors": [],
        }
    }


class CancelOrderTests(unittest.TestCase):
    def test_cancel_sends_refund_and_restock_and_returns_job(self) -> None:
        fake_client = FakeWriteClient([make_cancel_success_response()])

        action_result = cancel_order("gid://shopify/Order/123", client=fake_client)

        _, sent_variables = fake_client.calls[0]
        assert sent_variables is not None
        self.assertEqual(sent_variables["orderId"], "gid://shopify/Order/123")
        self.assertEqual(sent_variables["reason"], "CUSTOMER")
        self.assertTrue(sent_variables["restock"])
        self.assertEqual(sent_variables["refundMethod"], {"originalPaymentMethodsRefund": True})
        self.assertFalse(sent_variables["notifyCustomer"])
        self.assertEqual(action_result["shopify_job_id"], "gid://shopify/Job/9")

    def test_cancel_user_errors_raise(self) -> None:
        fake_client = FakeWriteClient(
            [
                {
                    "orderCancel": {
                        "job": None,
                        "orderCancelUserErrors": [{"field": None, "message": "Order is already cancelled", "code": "INVALID"}],
                    }
                }
            ]
        )

        with self.assertRaisesRegex(ShopifyWriteError, "already cancelled"):
            cancel_order("gid://shopify/Order/123", client=fake_client)


class IssueFullRefundTests(unittest.TestCase):
    def test_refund_reverses_the_suggested_payment_transaction(self) -> None:
        fake_client = FakeWriteClient(
            [
                make_refund_facts_response(),
                make_suggested_refund_response(with_payment_transaction=True),
                make_refund_success_response(),
            ]
        )

        action_result = issue_full_refund("gid://shopify/Order/123", client=fake_client)

        _, refund_variables = fake_client.calls[2]
        assert refund_variables is not None
        sent_transaction = refund_variables["input"]["transactions"][0]
        self.assertEqual(sent_transaction["parentId"], "gid://shopify/OrderTransaction/7")
        self.assertEqual(sent_transaction["amount"], "42.00")
        self.assertEqual(sent_transaction["kind"], "REFUND")
        self.assertEqual(sent_transaction["gateway"], "manual")
        self.assertEqual(
            refund_variables["input"]["refundLineItems"],
            [{"lineItemId": "gid://shopify/LineItem/11", "quantity": 2}],
        )
        self.assertFalse(refund_variables["input"]["notify"])
        self.assertEqual(action_result["refunded_amount"], "42.00")
        self.assertEqual(action_result["refund_id"], "gid://shopify/Refund/5")
        self.assertIn("original payment method", action_result["summary"])

    def test_orders_without_payment_refund_line_items_only(self) -> None:
        fake_client = FakeWriteClient(
            [
                make_refund_facts_response(),
                make_suggested_refund_response(with_payment_transaction=False),
                make_refund_success_response(),
            ]
        )

        action_result = issue_full_refund("gid://shopify/Order/123", client=fake_client)

        _, refund_variables = fake_client.calls[2]
        assert refund_variables is not None
        self.assertNotIn("transactions", refund_variables["input"])
        self.assertEqual(
            refund_variables["input"]["refundLineItems"],
            [{"lineItemId": "gid://shopify/LineItem/11", "quantity": 2}],
        )
        self.assertIn("no charge to reverse", action_result["summary"].lower())

    def test_nothing_refundable_raises_before_any_write(self) -> None:
        fake_client = FakeWriteClient([make_refund_facts_response(refundable_quantity=0)])

        with self.assertRaisesRegex(ShopifyWriteError, "Nothing left to refund"):
            issue_full_refund("gid://shopify/Order/123", client=fake_client)

        self.assertEqual(len(fake_client.calls), 1)

    def test_refund_user_errors_raise(self) -> None:
        fake_client = FakeWriteClient(
            [
                make_refund_facts_response(),
                make_suggested_refund_response(with_payment_transaction=True),
                {
                    "refundCreate": {
                        "refund": None,
                        "userErrors": [{"field": None, "message": "Cannot refund more than available"}],
                    }
                },
            ]
        )

        with self.assertRaisesRegex(ShopifyWriteError, "Cannot refund"):
            issue_full_refund("gid://shopify/Order/123", client=fake_client)


class UpdateShippingAddressTests(unittest.TestCase):
    def test_complete_address_requires_every_location_field(self) -> None:
        requested_shipping_address = make_requested_shipping_address()
        self.assertTrue(requested_shipping_address_is_complete(requested_shipping_address))

        requested_shipping_address["zip"] = "   "
        self.assertFalse(requested_shipping_address_is_complete(requested_shipping_address))

    def test_build_updated_address_preserves_recipient_details(self) -> None:
        updated_shipping_address = build_updated_shipping_address(
            make_current_shipping_address(),
            make_requested_shipping_address(),
        )

        self.assertEqual(updated_shipping_address["first_name"], "Rahat")
        self.assertEqual(updated_shipping_address["last_name"], "Kabir")
        self.assertEqual(updated_shipping_address["phone"], "+1 555 0100")
        self.assertEqual(updated_shipping_address["address1"], "20 Lake Road")
        self.assertIsNone(updated_shipping_address["address2"])
        self.assertEqual(updated_shipping_address["city"], "Dhaka")

    def test_order_update_sends_the_complete_shipping_address(self) -> None:
        fake_client = FakeWriteClient([make_order_update_success_response()])
        updated_shipping_address = build_updated_shipping_address(
            make_current_shipping_address(),
            make_requested_shipping_address(),
        )

        action_result = update_shipping_address(
            "gid://shopify/Order/123",
            updated_shipping_address,
            client=fake_client,
        )

        _, sent_variables = fake_client.calls[0]
        assert sent_variables is not None
        self.assertEqual(sent_variables["input"]["id"], "gid://shopify/Order/123")
        self.assertEqual(
            sent_variables["input"]["shippingAddress"]["firstName"], "Rahat"
        )
        self.assertEqual(
            sent_variables["input"]["shippingAddress"]["address1"], "20 Lake Road"
        )
        self.assertEqual(
            sent_variables["input"]["shippingAddress"]["country"], "Bangladesh"
        )
        self.assertEqual(action_result["action"], "update_shipping_address")
        self.assertIn("20 Lake Road", action_result["summary"])

    def test_incomplete_address_is_rejected_before_shopify(self) -> None:
        fake_client = FakeWriteClient([])
        incomplete_shipping_address = make_requested_shipping_address()
        incomplete_shipping_address["country"] = None

        with self.assertRaisesRegex(ValueError, "incomplete"):
            update_shipping_address(
                "gid://shopify/Order/123",
                incomplete_shipping_address,
                client=fake_client,
            )

        self.assertEqual(fake_client.calls, [])

    def test_order_update_user_errors_raise(self) -> None:
        fake_client = FakeWriteClient(
            [
                {
                    "orderUpdate": {
                        "order": None,
                        "userErrors": [
                            {"field": ["input", "shippingAddress"], "message": "Invalid address"}
                        ],
                    }
                }
            ]
        )
        updated_shipping_address = build_updated_shipping_address(
            make_current_shipping_address(),
            make_requested_shipping_address(),
        )

        with self.assertRaisesRegex(ShopifyWriteError, "Invalid address"):
            update_shipping_address(
                "gid://shopify/Order/123",
                updated_shipping_address,
                client=fake_client,
            )


if __name__ == "__main__":
    unittest.main()

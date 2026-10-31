import unittest
from datetime import datetime, timezone
from decimal import Decimal

from storekeeper.shopify.operations import (
    InvalidOrderReferenceError,
    OrderNotFoundError,
    lookup_order,
)


class FakeShopifyClient:
    def __init__(self, orders: list[dict]):
        self.orders = orders
        self.received_variables: dict | None = None

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        self.received_variables = variables
        return {"orders": {"nodes": self.orders}}


def make_shopify_order(fulfillment_status: str = "UNFULFILLED") -> dict:
    return {
        "id": "gid://shopify/Order/123",
        "name": "#1001",
        "processedAt": "2026-07-10T12:00:00+00:00",
        "displayFulfillmentStatus": fulfillment_status,
        "shippingAddress": {
            "firstName": "Rahat",
            "lastName": "Kabir",
            "company": None,
            "address1": "10 Old Road",
            "address2": "Unit 2",
            "city": "Austin",
            "province": "Texas",
            "zip": "78701",
            "country": "United States",
            "phone": "+1 555 0100",
        },
        "totalPriceSet": {
            "shopMoney": {"amount": "125.50", "currencyCode": "USD"}
        },
    }


class ShopifyOperationsTests(unittest.TestCase):
    def test_lookup_normalizes_order_for_policy_gate(self) -> None:
        client = FakeShopifyClient([make_shopify_order()])

        order = lookup_order(" #1001 ", client=client)

        self.assertEqual(client.received_variables, {"searchQuery": "name:#1001"})
        self.assertEqual(order["id"], "gid://shopify/Order/123")
        self.assertEqual(order["facts"]["processed_at"], datetime(2026, 7, 10, 12, tzinfo=timezone.utc))
        self.assertFalse(order["facts"]["fulfilled"])
        self.assertEqual(order["facts"]["total_amount"], Decimal("125.50"))
        self.assertEqual(order["facts"]["currency_code"], "USD")
        assert order["shipping_address"] is not None
        self.assertEqual(order["shipping_address"]["first_name"], "Rahat")
        self.assertEqual(order["shipping_address"]["address1"], "10 Old Road")

    def test_partial_fulfillment_is_treated_as_fulfilled_for_safety(self) -> None:
        client = FakeShopifyClient([make_shopify_order("PARTIALLY_FULFILLED")])

        order = lookup_order("#1001", client=client)

        self.assertTrue(order["facts"]["fulfilled"])

    def test_missing_order_raises_clear_error(self) -> None:
        client = FakeShopifyClient([])

        with self.assertRaisesRegex(OrderNotFoundError, "#9999"):
            lookup_order("#9999", client=client)

    def test_empty_order_reference_is_rejected(self) -> None:
        client = FakeShopifyClient([])

        with self.assertRaisesRegex(InvalidOrderReferenceError, "Not a valid order reference"):
            lookup_order("   ", client=client)

    def test_bare_digits_are_normalized_to_an_order_name(self) -> None:
        client = FakeShopifyClient([make_shopify_order()])

        lookup_order("1001", client=client)

        self.assertEqual(client.received_variables, {"searchQuery": "name:#1001"})

    def test_query_operators_are_rejected_before_any_shopify_call(self) -> None:
        hostile_references = [
            "1001 OR status:any",
            "name:*",
            "#12 34",
            "abc",
            "#1001*",
            None,
        ]
        for hostile_reference in hostile_references:
            with self.subTest(reference=hostile_reference):
                client = FakeShopifyClient([])

                with self.assertRaises(InvalidOrderReferenceError):
                    lookup_order(hostile_reference, client=client)

                # The reference never reached a Shopify query.
                self.assertIsNone(client.received_variables)

    def test_mismatched_order_name_is_treated_as_not_found(self) -> None:
        # Shopify returned some order, but not the one the customer named.
        client = FakeShopifyClient([make_shopify_order()])  # named #1001

        with self.assertRaisesRegex(OrderNotFoundError, "returned order #1001"):
            lookup_order("#1009", client=client)


if __name__ == "__main__":
    unittest.main()

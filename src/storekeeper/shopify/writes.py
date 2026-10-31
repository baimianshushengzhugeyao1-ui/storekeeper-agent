"""Shopify write operations.

These run only after the policy gate passed AND a human approved the action.
Nothing in this module decides whether a write should happen.
"""

from storekeeper.domain import ShippingAddress
from storekeeper.shopify.client import ShopifyClient

ORDER_CANCEL_MUTATION = """
mutation StorekeeperOrderCancel(
  $orderId: ID!
  $reason: OrderCancelReason!
  $restock: Boolean!
  $refundMethod: OrderCancelRefundMethodInput
  $notifyCustomer: Boolean
  $staffNote: String
) {
  orderCancel(
    orderId: $orderId
    reason: $reason
    restock: $restock
    refundMethod: $refundMethod
    notifyCustomer: $notifyCustomer
    staffNote: $staffNote
  ) {
    job { id done }
    orderCancelUserErrors { field message code }
  }
}
"""

REFUND_FACTS_QUERY = """
query StorekeeperRefundFacts($orderId: ID!) {
  order(id: $orderId) {
    id
    lineItems(first: 100) {
      nodes {
        id
        refundableQuantity
      }
    }
  }
}
"""

SUGGESTED_REFUND_QUERY = """
query StorekeeperSuggestedRefund($orderId: ID!, $refundLineItems: [RefundLineItemInput!]) {
  order(id: $orderId) {
    suggestedRefund(refundLineItems: $refundLineItems) {
      amountSet { shopMoney { amount currencyCode } }
      suggestedTransactions {
        amountSet { shopMoney { amount currencyCode } }
        gateway
        parentTransaction { id }
      }
    }
  }
}
"""

REFUND_CREATE_MUTATION = """
mutation StorekeeperRefundCreate($input: RefundInput!, $idempotencyKey: String!) {
  refundCreate(input: $input) @idempotent(key: $idempotencyKey) {
    refund {
      id
      totalRefundedSet { shopMoney { amount currencyCode } }
    }
    userErrors { field message }
  }
}
"""

ORDER_UPDATE_MUTATION = """
mutation StorekeeperOrderUpdate($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id }
    userErrors { field message }
  }
}
"""

REQUIRED_SHIPPING_ADDRESS_FIELDS = ("address1", "city", "province", "zip", "country")
RECIPIENT_IDENTITY_FIELDS = ("first_name", "last_name", "company", "phone")
SHOPIFY_ADDRESS_FIELD_NAMES = {
    "first_name": "firstName",
    "last_name": "lastName",
    "company": "company",
    "address1": "address1",
    "address2": "address2",
    "city": "city",
    "province": "province",
    "zip": "zip",
    "country": "country",
    "phone": "phone",
}


class ShopifyWriteError(RuntimeError):
    """Raised when Shopify rejects a write we expected to succeed."""


def cancel_order(shopify_order_id: str, client: ShopifyClient | None = None) -> dict:
    """Cancel an order and refund the original payment. Shopify processes the
    cancellation as an async job; a returned job id means Shopify accepted it."""
    shopify_client = client if client is not None else ShopifyClient()
    data = shopify_client.graphql(
        ORDER_CANCEL_MUTATION,
        {
            "orderId": shopify_order_id,
            "reason": "CUSTOMER",
            "restock": True,
            "refundMethod": {"originalPaymentMethodsRefund": True},
            "notifyCustomer": False,
            "staffNote": "Cancelled by storekeeper after human approval.",
        },
    )
    payload = data["orderCancel"]
    if payload["orderCancelUserErrors"]:
        raise ShopifyWriteError(f"orderCancel failed: {payload['orderCancelUserErrors']}")
    job = payload.get("job")
    if not isinstance(job, dict) or not job.get("id"):
        raise ShopifyWriteError("Cancellation response has no job receipt; completion is unknown.")
    return {
        "action": "cancel_order",
        "shopify_job_id": job["id"],
        "operation_status": "submitted",
        "simulated": bool(getattr(shopify_client, "is_simulated", False)),
        "job_reported_done": job.get("done") is True,
        "refund_status": "unverified",
        "summary": "Shopify accepted the cancellation request. Cancellation completion and payment settlement have not been verified.",
    }


def issue_full_refund(shopify_order_id: str, client: ShopifyClient | None = None) -> dict:
    """Refund the whole order.

    Shopify's suggestedRefund computes which payment transactions to reverse.
    Orders without captured payments (imported orders; some seed data) suggest
    no transactions — the refund then only marks the items refunded.
    """
    shopify_client = client if client is not None else ShopifyClient()

    line_items = shopify_client.graphql(
        REFUND_FACTS_QUERY, {"orderId": shopify_order_id}
    )["order"]["lineItems"]["nodes"]
    refundable_line_items = [
        {"lineItemId": line_item["id"], "quantity": line_item["refundableQuantity"]}
        for line_item in line_items
        if line_item["refundableQuantity"] > 0
    ]
    if not refundable_line_items:
        raise ShopifyWriteError(f"Nothing left to refund on {shopify_order_id}.")

    suggested_refund = shopify_client.graphql(
        SUGGESTED_REFUND_QUERY,
        {"orderId": shopify_order_id, "refundLineItems": refundable_line_items},
    )["order"]["suggestedRefund"]
    refund_transactions = [
        {
            "orderId": shopify_order_id,
            "parentId": suggested_transaction["parentTransaction"]["id"],
            "amount": suggested_transaction["amountSet"]["shopMoney"]["amount"],
            "kind": "REFUND",
            "gateway": suggested_transaction["gateway"],
        }
        for suggested_transaction in suggested_refund["suggestedTransactions"]
    ]

    refund_input: dict = {
        "orderId": shopify_order_id,
        "notify": False,
        "note": "Refunded by storekeeper after human approval.",
        "refundLineItems": refundable_line_items,
    }
    if refund_transactions:
        refund_input["transactions"] = refund_transactions

    payload = _create_refund(shopify_client, refund_input)
    refunded_money = payload["refund"]["totalRefundedSet"]["shopMoney"]
    if refund_transactions:
        summary = (
            f"Refunded {refunded_money['amount']} {refunded_money['currencyCode']} "
            "to the original payment method."
        )
    else:
        summary = (
            "Marked every item in the order refunded. No payment was captured "
            "on this order, so there was no charge to reverse."
        )
    return {
        "action": "issue_refund",
        "refund_id": payload["refund"]["id"],
        "refunded_amount": refunded_money["amount"],
        "currency": refunded_money["currencyCode"],
        "summary": summary,
    }


def requested_shipping_address_is_complete(
    requested_shipping_address: ShippingAddress | None,
) -> bool:
    if requested_shipping_address is None:
        return False
    return all(
        _clean_address_value(requested_shipping_address[field_name]) is not None
        for field_name in REQUIRED_SHIPPING_ADDRESS_FIELDS
    )


def build_updated_shipping_address(
    current_shipping_address: ShippingAddress | None,
    requested_shipping_address: ShippingAddress,
) -> ShippingAddress:
    if not requested_shipping_address_is_complete(requested_shipping_address):
        raise ValueError(
            "A shipping-address update requires street, city, state/province, "
            "postal code, and country."
        )

    updated_shipping_address = ShippingAddress(
        first_name=None,
        last_name=None,
        company=None,
        address1=_clean_address_value(requested_shipping_address["address1"]),
        address2=_clean_address_value(requested_shipping_address["address2"]),
        city=_clean_address_value(requested_shipping_address["city"]),
        province=_clean_address_value(requested_shipping_address["province"]),
        zip=_clean_address_value(requested_shipping_address["zip"]),
        country=_clean_address_value(requested_shipping_address["country"]),
        phone=None,
    )
    for field_name in RECIPIENT_IDENTITY_FIELDS:
        requested_value = _clean_address_value(requested_shipping_address[field_name])
        current_value = (
            _clean_address_value(current_shipping_address[field_name])
            if current_shipping_address is not None
            else None
        )
        updated_shipping_address[field_name] = requested_value or current_value
    return updated_shipping_address


def update_shipping_address(
    shopify_order_id: str,
    updated_shipping_address: ShippingAddress,
    client: ShopifyClient | None = None,
) -> dict:
    if not requested_shipping_address_is_complete(updated_shipping_address):
        raise ValueError("Cannot send an incomplete shipping address to Shopify.")

    shopify_shipping_address = {
        shopify_field_name: updated_shipping_address[domain_field_name]
        for domain_field_name, shopify_field_name in SHOPIFY_ADDRESS_FIELD_NAMES.items()
    }
    shopify_client = client if client is not None else ShopifyClient()
    data = shopify_client.graphql(
        ORDER_UPDATE_MUTATION,
        {
            "input": {
                "id": shopify_order_id,
                "shippingAddress": shopify_shipping_address,
            }
        },
    )
    payload = data["orderUpdate"]
    if payload["userErrors"]:
        raise ShopifyWriteError(f"orderUpdate failed: {payload['userErrors']}")

    return {
        "action": "update_shipping_address",
        "order_id": payload["order"]["id"],
        "shipping_address": updated_shipping_address,
        "summary": (
            f"Updated the shipping address to {updated_shipping_address['address1']}, "
            f"{updated_shipping_address['city']}, {updated_shipping_address['province']} "
            f"{updated_shipping_address['zip']}, {updated_shipping_address['country']}."
        ),
    }


def _clean_address_value(address_value: str | None) -> str | None:
    if address_value is None:
        return None
    cleaned_address_value = address_value.strip()
    return cleaned_address_value or None


def _create_refund(shopify_client: ShopifyClient, refund_input: dict) -> dict:
    # The API requires an idempotency key on refundCreate. Deriving it from the
    # order id means a re-run of this write (interrupt resume, retry) cannot
    # refund the same order twice; plan validation permits at most one write per order.
    idempotency_key = f"storekeeper-full-refund-{refund_input['orderId']}"
    data = shopify_client.graphql(
        REFUND_CREATE_MUTATION,
        {"input": refund_input, "idempotencyKey": idempotency_key},
    )
    payload = data["refundCreate"]
    if payload["userErrors"]:
        raise ShopifyWriteError(f"refundCreate failed: {payload['userErrors']}")
    return payload

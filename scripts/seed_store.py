"""Seed the dev store with fake orders covering the policy gate's edge cases.

Segments:
    happy       30  recent, unfulfilled, PAID       -> cancel / refund / address change all pass
    fulfilled    8  recent, delivered               -> refund passes, cancel and address change fail
    old          8  processed 40-90 days ago        -> refund fails (outside return window)
    high_value   4  recent, unfulfilled, > $100     -> refund passes with the high_value flag

Every order is tagged 'storekeeper-seed'. Reruns count existing segment tags
and create only the missing orders.

Run: uv run python scripts/seed_store.py [--plan]
"""

import random
import sys
import time
from datetime import datetime, timedelta, timezone

from storekeeper.shopify.client import ShopifyClient, ShopifyGraphQLError

SEED_TAG = "storekeeper-seed"

# Deterministic data so reruns (with --force on a cleaned store) produce the same orders.
random.seed(42)

FAKE_CUSTOMERS = [
    ("Ava", "Thompson"), ("Liam", "Rodriguez"), ("Maya", "Chen"),
    ("Noah", "Patel"), ("Sofia", "Kim"), ("Ethan", "Nakamura"),
    ("Zara", "Hussain"), ("Lucas", "Meyer"), ("Ines", "Fernandez"),
    ("Omar", "Rahman"), ("Freya", "Lindqvist"), ("Daniel", "Okafor"),
    ("Hana", "Suzuki"), ("Mateo", "Silva"), ("Clara", "Novak"),
]

# (title, price) — everyday items stay under $100 so they never trip the
# high-value flag by accident; the last group exists to trip it on purpose.
EVERYDAY_CATALOG = [
    ("Ceramic Pour-Over Coffee Set", 42.00),
    ("Walnut Serving Board", 38.50),
    ("Linen Apron", 28.00),
    ("Stoneware Mug Duo", 24.00),
    ("Copper French Press", 54.00),
    ("Bamboo Utensil Set", 18.00),
    ("Organic Cotton Throw Blanket", 62.00),
    ("Scented Soy Candle Trio", 32.00),
    ("Insulated Travel Tumbler", 26.00),
]
HIGH_VALUE_CATALOG = [
    ("Compact Espresso Machine", 249.00),
    ("Enameled Dutch Oven 7qt", 189.00),
    ("Chef Knife Set with Block", 159.00),
    ("Cast Iron Cookware Bundle", 205.00),
]

FAKE_ADDRESSES = [
    ("112 Maple Street", "Portland", "OR", "97201"),
    ("48 Willow Avenue", "Austin", "TX", "78701"),
    ("905 Birch Lane", "Chicago", "IL", "60601"),
    ("77 Cedar Court", "Seattle", "WA", "98101"),
    ("230 Elm Drive", "Denver", "CO", "80201"),
    ("15 Juniper Road", "Raleigh", "NC", "27601"),
]

ORDER_CREATE_MUTATION = """
mutation SeedOrderCreate($order: OrderCreateOrderInput!) {
  orderCreate(order: $order) {
    order {
      id
      name
      processedAt
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
    }
    userErrors { field message }
  }
}
"""


def fetch_primary_location_id(client: ShopifyClient) -> str | None:
    """Fulfillment-at-creation may need a location id. The locations query
    needs the read_locations scope; if it's not granted we return None and
    create fulfillments without a location, which Shopify may accept."""
    try:
        data = client.graphql("query SeedLocation { locations(first: 1) { nodes { id } } }")
        location_nodes = data["locations"]["nodes"]
        return location_nodes[0]["id"] if location_nodes else None
    except ShopifyGraphQLError as error:
        print(f"  (locations query unavailable — {str(error)[:120]} — trying fulfillment without locationId)")
        return None


def count_seeded_orders_by_segment(client: ShopifyClient) -> dict[str, int]:
    data = client.graphql(
        'query SeedCheck { orders(first: 250, query: "tag:' + SEED_TAG + '") '
        '{ nodes { tags displayFulfillmentStatus } } }'
    )
    orders = data["orders"]["nodes"]
    segment_names = ["happy", "fulfilled", "old", "high_value"]
    return {
        segment_name: sum(
            f"seed-{segment_name}" in order["tags"]
            and (
                segment_name != "fulfilled"
                or order["displayFulfillmentStatus"] == "FULFILLED"
            )
            for order in orders
        )
        for segment_name in segment_names
    }


def pick_line_items(max_total: float = 90.0) -> list[dict]:
    """One or two everyday items whose combined price stays under max_total."""
    first_title, first_price = random.choice(EVERYDAY_CATALOG)
    line_items = [_line_item(first_title, first_price)]
    if random.random() < 0.5:
        second_title, second_price = random.choice(EVERYDAY_CATALOG)
        if first_price + second_price <= max_total:
            line_items.append(_line_item(second_title, second_price))
    return line_items


def _line_item(title: str, price: float) -> dict:
    return {
        "title": title,
        "quantity": 1,
        "priceSet": {"shopMoney": {"amount": price, "currencyCode": "USD"}},
    }


def processed_at_days_ago(day_range: tuple[int, int]) -> str:
    days_ago = random.randint(*day_range)
    hours_ago = random.randint(0, 23)
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)
    return moment.isoformat(timespec="seconds")


def build_order_input(
    segment: str,
    location_id: str | None,
) -> dict:
    first_name, last_name = random.choice(FAKE_CUSTOMERS)
    email = f"{first_name}.{last_name}@example.com".lower()
    address1, city, province_code, zip_code = random.choice(FAKE_ADDRESSES)

    if segment == "high_value":
        line_items = [_line_item(*random.choice(HIGH_VALUE_CATALOG))]
    else:
        line_items = pick_line_items()

    # Shopify exposes only the latest 60 days without read_all_orders.
    day_ranges = {"happy": (0, 20), "fulfilled": (2, 15), "old": (40, 59), "high_value": (1, 10)}

    order_total = sum(
        line_item["priceSet"]["shopMoney"]["amount"] for line_item in line_items
    )

    order_input: dict = {
        "lineItems": line_items,
        "customer": {
            "toUpsert": {"email": email, "firstName": first_name, "lastName": last_name}
        },
        "email": email,
        "financialStatus": "PAID",
        # A captured SALE transaction makes the order behave like a really-paid
        # order, so transaction-based refunds work on it.
        "transactions": [
            {
                "amountSet": {"shopMoney": {"amount": order_total, "currencyCode": "USD"}},
                "kind": "SALE",
                "status": "SUCCESS",
                "gateway": "manual",
            }
        ],
        "processedAt": processed_at_days_ago(day_ranges[segment]),
        "tags": [SEED_TAG, f"seed-{segment}"],
        "shippingAddress": {
            "firstName": first_name,
            "lastName": last_name,
            "address1": address1,
            "city": city,
            "provinceCode": province_code,
            "countryCode": "US",
            "zip": zip_code,
        },
    }

    fulfilled_segments = {"fulfilled"}
    # Half the old orders were delivered long ago — old + fulfilled and
    # old + unfulfilled both matter to the gate's refund rule.
    if segment == "old" and random.random() < 0.5:
        fulfilled_segments = {"fulfilled", "old"}

    if segment in fulfilled_segments:
        order_input["fulfillmentStatus"] = "FULFILLED"
        fulfillment_input: dict = {
            "shipmentStatus": "DELIVERED",
            "notifyCustomer": False,
            "trackingCompany": "UPS",
            "trackingNumber": f"1Z{random.randint(10**8, 10**9 - 1)}",
        }
        if location_id is not None:
            fulfillment_input["locationId"] = location_id
        order_input["fulfillment"] = fulfillment_input

    return order_input


# Dev stores cap order creation at roughly 5 per minute; when we trip that cap
# the mutation succeeds but returns a 'Too many attempts' userError.
ORDER_CREATION_RATE_LIMIT_SLEEP_SECONDS = 65


def create_order_with_retry(client: ShopifyClient, order_input: dict, max_attempts: int = 8) -> dict:
    result: dict = {}
    for attempt_number in range(1, max_attempts + 1):
        try:
            result = client.graphql(ORDER_CREATE_MUTATION, {"order": order_input})
        except ShopifyGraphQLError as error:
            request_was_throttled = "THROTTLED" in str(error)
            if request_was_throttled and attempt_number < max_attempts:
                time.sleep(2.0)
                continue
            raise

        user_errors = result["orderCreate"]["userErrors"]
        hit_creation_rate_limit = "Too many attempts" in str(user_errors)
        if hit_creation_rate_limit and attempt_number < max_attempts:
            print(f"    order-creation rate limit hit — sleeping {ORDER_CREATION_RATE_LIMIT_SLEEP_SECONDS}s")
            time.sleep(ORDER_CREATION_RATE_LIMIT_SLEEP_SECONDS)
            continue
        return result
    return result


def main() -> None:
    plan_only = "--plan" in sys.argv
    client = ShopifyClient()

    segment_targets = [("happy", 30), ("fulfilled", 8), ("old", 8), ("high_value", 4)]
    existing_counts = count_seeded_orders_by_segment(client)
    segments_to_create = [
        (segment_name, max(target_count - existing_counts[segment_name], 0))
        for segment_name, target_count in segment_targets
    ]
    total_planned = sum(count for _, count in segments_to_create)

    print("Seed plan:")
    for segment_name, target_count in segment_targets:
        existing_count = existing_counts[segment_name]
        missing_count = max(target_count - existing_count, 0)
        print(f"  {segment_name:<11} {existing_count}/{target_count} existing, {missing_count} to create")

    if total_planned == 0:
        print("\nAll 50 seed orders already exist.")
        return

    if plan_only:
        print(f"\nPlan only: {total_planned} orders would be created.")
        return

    location_id = fetch_primary_location_id(client)

    created: list[tuple[str, str, str]] = []  # (segment, order name, total)
    failures: list[tuple[str, str]] = []      # (segment, error message)
    order_number = 0

    for segment, count in segments_to_create:
        for _ in range(count):
            order_number += 1
            order_input = build_order_input(segment, location_id)
            try:
                result = create_order_with_retry(client, order_input)
            except ShopifyGraphQLError as error:
                failures.append((segment, str(error)[:200]))
                print(f"[{order_number}/{total_planned}] {segment:<11} FAILED: {str(error)[:120]}")
                continue

            user_errors = result["orderCreate"]["userErrors"]
            if user_errors:
                failures.append((segment, str(user_errors)[:200]))
                print(f"[{order_number}/{total_planned}] {segment:<11} userErrors: {user_errors}")
                continue

            order = result["orderCreate"]["order"]
            total = order["totalPriceSet"]["shopMoney"]["amount"]
            created.append((segment, order["name"], total))
            print(
                f"[{order_number}/{total_planned}] {segment:<11} {order['name']}  "
                f"${total}  {order['displayFulfillmentStatus']}"
            )
            # Stay comfortably under the Admin API rate limit.
            time.sleep(0.4)

    print()
    print(f"Created {len(created)}/{total_planned} orders (tag '{SEED_TAG}'):")
    for segment, target_count in segment_targets:
        created_in_segment = sum(1 for created_segment, _, _ in created if created_segment == segment)
        final_count = existing_counts[segment] + created_in_segment
        print(f"  {segment:<11} {final_count}/{target_count}")

    if created:
        sample_name = created[0][1]
        print(f"\nSpot-check in admin: search for order {sample_name} or tag '{SEED_TAG}'.")

    if failures:
        print(f"\n{len(failures)} order(s) FAILED:")
        for segment, error_message in failures:
            print(f"  [{segment}] {error_message}")
        sys.exit(1)


if __name__ == "__main__":
    main()

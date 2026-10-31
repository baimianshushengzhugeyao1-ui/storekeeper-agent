"""Smoke test for the Shopify client: proves auth and the GraphQL path.

Run after `pip install -e .` with credentials in .env:

    python scripts/smoke_shopify.py

Success looks like the store's name, domain, and currency printed.
"""

from storekeeper.shopify.client import ShopifyClient

SHOP_INFO_QUERY = """
query SmokeTestShopInfo {
  shop {
    name
    myshopifyDomain
    currencyCode
  }
}
"""


def main() -> None:
    client = ShopifyClient()
    shop_info = client.graphql(SHOP_INFO_QUERY)["shop"]
    print(f"Connected to: {shop_info['name']} ({shop_info['myshopifyDomain']})")
    print(f"Currency: {shop_info['currencyCode']}")


if __name__ == "__main__":
    main()

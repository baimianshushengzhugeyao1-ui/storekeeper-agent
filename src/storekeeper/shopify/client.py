"""Shopify Admin API client.

The single place access tokens are handled: every GraphQL call in the project —
graph nodes and scripts alike — goes through ShopifyClient.graphql().

Auth is the OAuth client credentials grant (Dev Dashboard apps): the client id
and secret are exchanged for an access token that lives ~24 hours, cached in
memory, and refreshed shortly before it expires.
"""

import time

import requests

from storekeeper.config import ShopifySettings, load_shopify_settings

SHOPIFY_API_VERSION = "2026-04"

REQUEST_TIMEOUT_SECONDS = 30

# Refresh this many seconds before the token's real expiry so a request never
# leaves with a token that dies mid-flight.
TOKEN_REFRESH_SAFETY_MARGIN_SECONDS = 60


class ShopifyAuthError(RuntimeError):
    """Raised when the token endpoint rejects our credentials."""


class ShopifyGraphQLError(RuntimeError):
    """Raised when the Admin API returns an HTTP or GraphQL-level error."""


class ShopifyClient:
    def __init__(self, settings: ShopifySettings | None = None):
        self.settings = settings if settings is not None else load_shopify_settings()
        self._access_token: str | None = None
        self._token_expires_at_epoch_seconds: float = 0.0
        # Scopes the store actually granted, from the last token response.
        # Useful for diagnosing ACCESS_DENIED errors (granted can lag configured).
        self.granted_scopes: str | None = None

    @property
    def _token_url(self) -> str:
        return f"https://{self.settings.shop_domain}/admin/oauth/access_token"

    @property
    def _graphql_url(self) -> str:
        return f"https://{self.settings.shop_domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    def get_token(self) -> str:
        token_is_missing_or_stale = (
            self._access_token is None
            or time.time() >= self._token_expires_at_epoch_seconds
        )
        if token_is_missing_or_stale:
            self._fetch_new_token()
        assert self._access_token is not None
        return self._access_token

    def _fetch_new_token(self) -> None:
        response = requests.post(
            self._token_url,
            json={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise ShopifyAuthError(
                f"Token request to {self.settings.shop_domain} failed with "
                f"HTTP {response.status_code}: {response.text[:500]}"
            )

        token_payload = response.json()
        self._access_token = token_payload["access_token"]
        self.granted_scopes = token_payload.get("scope")
        expires_in_seconds = token_payload.get("expires_in", 86_399)
        self._token_expires_at_epoch_seconds = (
            time.time() + expires_in_seconds - TOKEN_REFRESH_SAFETY_MARGIN_SECONDS
        )

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query or mutation and return its `data` payload.

        Raises ShopifyGraphQLError on HTTP or GraphQL-level errors. Mutation
        userErrors are per-operation and are the caller's job to check.
        """
        response = self._post_graphql(query, variables)
        if response.status_code == 401:
            # The cached token was revoked earlier than its timestamp promised.
            # Refresh once and retry; a second 401 is a real auth problem.
            self._fetch_new_token()
            response = self._post_graphql(query, variables)

        if response.status_code != 200:
            raise ShopifyGraphQLError(
                f"Admin API returned HTTP {response.status_code}: {response.text[:500]}"
            )

        response_payload = response.json()
        if response_payload.get("errors"):
            raise ShopifyGraphQLError(f"GraphQL errors: {response_payload['errors']}")
        return response_payload["data"]

    def _post_graphql(self, query: str, variables: dict | None) -> requests.Response:
        return requests.post(
            self._graphql_url,
            json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": self.get_token()},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

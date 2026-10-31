"""Environment configuration for storekeeper."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class ShopifySettings:
    shop_domain: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class ClassifierSettings:
    # OpenRouter model slug, for example "openai/gpt-5.5". The API key stays
    # env-only because ChatOpenRouter reads OPENROUTER_API_KEY itself.
    openrouter_model: str


def load_classifier_settings() -> ClassifierSettings:
    load_dotenv()

    required_variable_names = ["OPENROUTER_API_KEY", "OPENROUTER_MODEL"]
    missing_variable_names = [name for name in required_variable_names if not os.getenv(name)]
    if missing_variable_names:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing_variable_names)}. "
            "Copy .env.example to .env and fill them in."
        )

    return ClassifierSettings(openrouter_model=os.environ["OPENROUTER_MODEL"].strip())


def load_shopify_settings() -> ShopifySettings:
    load_dotenv()

    required_variable_names = ["SHOPIFY_SHOP", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"]
    missing_variable_names = [name for name in required_variable_names if not os.getenv(name)]
    if missing_variable_names:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing_variable_names)}. "
            "Copy .env.example to .env and fill them in."
        )

    shop_domain = os.environ["SHOPIFY_SHOP"].strip()
    # Accept either the bare store handle or the full domain.
    if not shop_domain.endswith(".myshopify.com"):
        shop_domain = f"{shop_domain}.myshopify.com"

    return ShopifySettings(
        shop_domain=shop_domain,
        client_id=os.environ["SHOPIFY_CLIENT_ID"],
        client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
    )

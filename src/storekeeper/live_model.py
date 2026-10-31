"""Real DeepSeek model configuration, independent of Shopify credentials."""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DeepSeekChat(ChatOpenAI):
    """Use tool calling for the project's existing Pydantic output schemas."""

    def with_structured_output(self, schema=None, **kwargs):
        kwargs.setdefault("method", "function_calling")
        return super().with_structured_output(schema, **kwargs)


def create_live_model(callbacks=None):
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model_name = os.getenv("DEEPSEEK_MODEL", "").strip()
    if not api_key or not model_name:
        raise RuntimeError("Fill DEEPSEEK_API_KEY and DEEPSEEK_MODEL in the project .env first.")
    return DeepSeekChat(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        model=model_name,
        temperature=0,
        max_tokens=2048,
        timeout=45,
        max_retries=0,
        extra_body={"thinking": {"type": "disabled"}},
        callbacks=callbacks,
    )

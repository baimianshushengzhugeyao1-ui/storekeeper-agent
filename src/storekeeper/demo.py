"""Offline preview of the real graph/API, using explicit fixtures instead of services.

This is a deterministic workflow demonstration, not a model-quality evaluation.
It never creates a Shopify client or reads model credentials.
"""

import argparse
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith import tracing_context

from storekeeper.api.app import FRONTEND_DIST_PATH, create_app
from storekeeper.classify import ClassifiedTask, TicketClassification
from storekeeper.graph.build import build_ticket_graph

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEMO_NOTICE = (
    '<aside style="background:#fff3cd;color:#684f00;padding:12px 18px;'
    'font:14px/1.5 system-ui;position:sticky;top:0;z-index:1000">'
    '<strong>OFFLINE DEMO · 测试数据</strong> — 固定解析器与测试订单；'
    '不调用大模型或 Shopify。支持“帮我取消订单”，补充 #1036（未发货）'
    '或 #1037（已发货）。批准仅改变演示工作流，不产生真实操作。'
    '</aside>'
)


class DemoClassifier:
    """Recognize only the documented cancellation examples; no LLM involved."""

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        customer_text = str(messages[-1].content)
        is_cancellation = "取消" in customer_text or "cancel" in customer_text.lower()
        references = re.findall(r"#[0-9]{1,10}(?![0-9])", customer_text)
        return TicketClassification(tasks=[ClassifiedTask(
            intent="cancel_order" if is_cancellation else "other",
            order_reference=references[0] if len(references) == 1 else None,
            new_shipping_address=None,
            confidence=1.0,
        )])


class DemoReplyModel:
    """Describe fixture outcomes with an explicit offline disclaimer."""

    def invoke(self, messages):
        result_text = str(messages[-1].content)
        if '"outcome": "executed"' in result_text:
            result = "The fixture cancellation was accepted after approval."
        elif '"outcome": "denied_by_policy"' in result_text:
            result = "The fixture order is fulfilled, so the policy gate denied cancellation."
        elif '"outcome": "rejected_by_human"' in result_text:
            result = "The operator rejected the fixture cancellation."
        elif '"outcome": "failed"' in result_text:
            result = "The fixture order was not found. Try #1036 or #1037 in a new ticket."
        else:
            result = "This offline preview supports cancellation requests only."
        return AIMessage(content=f"OFFLINE DEMO: {result} No real order or payment was changed.")


class DemoShopifyClient:
    """Return two fixed orders and local write receipts; never open a connection."""

    is_simulated = True

    def __init__(self):
        processed_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.orders = [
            {
                "id": f"gid://shopify/Order/{order_number}",
                "name": f"#{order_number}",
                "processedAt": processed_at,
                "displayFulfillmentStatus": fulfillment_status,
                "shippingAddress": None,
                "totalPriceSet": {"shopMoney": {"amount": "125.50", "currencyCode": "USD"}},
            }
            for order_number, fulfillment_status in [(1036, "UNFULFILLED"), (1037, "FULFILLED")]
        ]
        self.write_calls = []

    def graphql(self, query, variables=None):
        variables = variables or {}
        if "orders(" in query:
            requested_reference = variables["searchQuery"].removeprefix("name:")
            return {"orders": {"nodes": [order for order in self.orders if order["name"] == requested_reference]}}
        if "orderCancel(" in query:
            self.write_calls.append(dict(variables))
            return {"orderCancel": {
                "job": {"id": f"demo-job-{len(self.write_calls)}", "done": True},
                "orderCancelUserErrors": [],
            }}
        raise RuntimeError("This offline preview does not support that Shopify operation.")


def create_demo_app(demo_directory: Path | None = None):
    """Keep demo checkpoints separate from any real development-store state."""
    demo_directory = demo_directory or REPOSITORY_ROOT / "var" / "offline_demo"
    demo_directory.mkdir(parents=True, exist_ok=True)
    checkpoint_connection = sqlite3.connect(demo_directory / "checkpoints.sqlite", check_same_thread=False)
    fixture_client = DemoShopifyClient()
    reply_model = DemoReplyModel()
    graph = build_ticket_graph(
        checkpointer=SqliteSaver(checkpoint_connection),
        classifier_model=DemoClassifier(),
        draft_model=reply_model,
        answer_model=reply_model,
        shopify_client=fixture_client,
    )
    application = create_app(
        ticket_graph=graph,
        ticket_database_path=demo_directory / "tickets.sqlite",
        # Add the demo banner before mounting the existing compiled frontend.
        frontend_dist_path=demo_directory / "no_frontend_here",
    )
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def demo_lifespan(app):
        try:
            async with original_lifespan(app):
                yield
        finally:
            checkpoint_connection.close()

    application.router.lifespan_context = demo_lifespan
    application.state.demo_shopify_client = fixture_client

    @application.middleware("http")
    async def disable_demo_tracing(request, call_next):
        # Do not let inherited tracing credentials export fixture runs.
        # Context variables propagate into FastAPI's synchronous worker calls.
        with tracing_context(enabled=False):
            return await call_next(request)

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def offline_index():
        index_path = FRONTEND_DIST_PATH / "index.html"
        if not index_path.is_file():
            return HTMLResponse(DEMO_NOTICE + "<p>Build the frontend first: cd frontend; npm run build</p>", status_code=503)
        return HTMLResponse(index_path.read_text(encoding="utf-8").replace(
            "<body>", f'<body data-offline-demo="true">{DEMO_NOTICE}'
        ))

    if FRONTEND_DIST_PATH.is_dir():
        application.mount("/", StaticFiles(directory=FRONTEND_DIST_PATH), name="demo-assets")
    return application


def main():
    parser = argparse.ArgumentParser(description="Offline Storekeeper preview; no external service calls.")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    print("OFFLINE DEMO: fixture data and deterministic parsing; no model or Shopify calls.")
    uvicorn.run(create_demo_app(), host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
    main()

"""Public synthetic fixtures and explicit scripted replay; never a real LLM."""
import json
from datetime import datetime, timedelta

from langchain_core.messages import AIMessage

from storekeeper.demo import DemoShopifyClient
from storekeeper.order_resolution import CustomerOrder, FixtureOrderDirectory, SHANGHAI

DEMO_NOW = datetime(2026, 11, 4, 10, 0, tzinfo=SHANGHAI)


def make_fixture_directory():
    orders = [
        CustomerOrder("#2036", "demo-customer-A", DEMO_NOW.replace(day=3, hour=13, minute=30),
                      "79.90", "USD", "无线耳机"),
        CustomerOrder("#2037", "demo-customer-A", DEMO_NOW.replace(day=3, hour=16, minute=20),
                      "39.90", "USD", "无线鼠标"),
        CustomerOrder("#2038", "demo-customer-A", DEMO_NOW.replace(hour=9),
                      "89.00", "USD", "机械键盘"),
        CustomerOrder("#2039", "demo-customer-A", DEMO_NOW.replace(day=2, hour=14),
                      "45.00", "USD", "已发货测试订单", fulfilled=True),
        CustomerOrder("#9036", "demo-customer-B", DEMO_NOW.replace(day=3, hour=14),
                      "59.00", "USD", "其他客户订单 不得返回"),
    ]
    return FixtureOrderDirectory(orders, "demo-customer-A", DEMO_NOW)


class OrderAgentFixtureClient(DemoShopifyClient):
    def __init__(self, directory):
        super().__init__()
        # The action adapter also sees ONLY this fixture customer's orders.
        self.orders = [{"id": f"gid://shopify/Order/{o.reference[1:]}", "name": o.reference,
                        "processedAt": o.placed_at.isoformat(),
                        "displayFulfillmentStatus": "FULFILLED" if o.fulfilled else "UNFULFILLED",
                        "shippingAddress": None,
                        "totalPriceSet": {"shopMoney": {"amount": o.amount, "currencyCode": o.currency}}}
                       for o in directory._orders if o.customer_id == directory.customer_id]


class ScriptedOrderModel:
    """Deterministic UI/checkpoint rehearsal, NOT model performance evidence."""
    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages):
        question = next(m.content for m in messages if m.type == "human")
        previous = [m for m in messages if m.type == "tool"]
        if not previous:
            if "昨天" in question or "yesterday" in question.lower():
                day = DEMO_NOW - timedelta(days=1)
                morning = "上午" in question
                start = day.replace(hour=9 if morning else 13 if "13" in question else 12, minute=0)
                end = day.replace(hour=10 if morning else 14 if "14" in question else 18, minute=0)
                name, args = "search_orders_by_time", {"start_time": start.isoformat(), "end_time": end.isoformat()}
            else:
                name, args = "request_order_reference", {"reason": "没有可用时间信息，请提供订单号。"}
        else:
            last = json.loads(previous[-1].content)
            if last.get("customer_selected_reference"):
                name, args = "select_order", {"order_reference": last["customer_selected_reference"]}
            elif last.get("match_count") == 1:
                name, args = "select_order", {"order_reference": last["orders"][0]["order_reference"]}
            else:
                name, args = "request_order_reference", {"reason": "请确认目标订单。"}
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"replay-{len(previous)+1}"}])

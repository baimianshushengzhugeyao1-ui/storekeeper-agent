"""Real model order discovery plus fixture actions, or explicit scripted replay."""
import argparse
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith import tracing_context
from openai import APIConnectionError, APIStatusError

from storekeeper.api.app import FRONTEND_DIST_PATH, create_app
from storekeeper.demo import DemoClassifier, DemoReplyModel
from storekeeper.graph.build import build_ticket_graph
from storekeeper.live_model import PROJECT_ROOT, create_live_model
from storekeeper.order_agent_fixtures import (
    DEMO_NOW, OrderAgentFixtureClient, ScriptedOrderModel, make_fixture_directory,
)


def create_order_agent_preview(directory: Path | None = None, *, replay=False, model=None):
    directory = directory or PROJECT_ROOT / "var" / ("order_agent_replay" if replay else "order_agent_live")
    directory.mkdir(parents=True, exist_ok=True)
    order_directory = make_fixture_directory()
    client = OrderAgentFixtureClient(order_directory)
    connection = sqlite3.connect(directory / "checkpoints.sqlite", check_same_thread=False)
    try:
        model = model if model is not None else ScriptedOrderModel() if replay else create_live_model()
        classifier = DemoClassifier() if replay else model
        drafter = DemoReplyModel() if replay else model
        graph = build_ticket_graph(checkpointer=SqliteSaver(connection), classifier_model=classifier,
            draft_model=drafter, answer_model=drafter, shopify_client=client,
            order_agent_model=model, order_directory=order_directory)
        app = create_app(ticket_graph=graph, ticket_database_path=directory / "tickets.sqlite",
                         frontend_dist_path=directory / "no-static")
    except BaseException:
        connection.close()
        raise
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application):
                yield
        finally:
            connection.close()

    app.router.lifespan_context = lifespan
    app.state.fixture_shopify_client = client

    @app.middleware("http")
    async def disable_tracing(request, call_next):
        with tracing_context(enabled=False):
            return await call_next(request)

    @app.exception_handler(APIStatusError)
    async def model_error(request, error):
        text = "DeepSeek余额不足，请充值后新建工单重试。" if error.status_code == 402 else "模型接口返回错误，请检查服务后新建工单重试。"
        return JSONResponse(status_code=502, content={"detail": text})

    @app.exception_handler(APIConnectionError)
    async def connection_error(request, error):
        return JSONResponse(status_code=503, content={"detail": "模型暂时无法连接，请稍后新建工单重试。"})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        path = FRONTEND_DIST_PATH / "index.html"
        mode = "离线脚本演练 不是大模型运行" if replay else "真实DeepSeek 工具调用会计费"
        notice = (f'<aside class="order-agent-notice"><strong>{mode}</strong> · 模拟订单与模拟取消。'
                  f'固定演示时钟 {DEMO_NOW.isoformat()}。当前测试客户A，无生产登录权限系统。'
                  '试试：帮我取消昨天下午的订单。会查到 #2036 耳机和 #2037 鼠标，需选择并另行审批。'
                  '</aside>')
        if not path.exists():
            return HTMLResponse(notice + "<p>请先构建frontend。</p>", status_code=503)
        mode_attribute = "replay" if replay else "live"
        return HTMLResponse(path.read_text(encoding="utf-8").replace("<body>",
            f'<body data-order-agent-preview="{mode_attribute}">' + notice))

    if FRONTEND_DIST_PATH.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST_PATH), name="order-agent-assets")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--replay", action="store_true", help="Scripted rehearsal; no model or Shopify network calls.")
    args = parser.parse_args()
    uvicorn.run(create_order_agent_preview(replay=args.replay), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

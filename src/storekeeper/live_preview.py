"""Real DeepSeek calls with fixture orders only; never contacts Shopify."""

import argparse
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith import tracing_context

from storekeeper.api.app import FRONTEND_DIST_PATH, create_app
from storekeeper.demo import DemoShopifyClient
from storekeeper.graph.build import build_ticket_graph
from storekeeper.live_model import PROJECT_ROOT, create_live_model

NOTICE = ('<aside style="background:#fff3cd;padding:12px;font:14px/1.5 sans-serif">'
          '<strong>真实 DeepSeek · 测试订单</strong> — 每次模型调用可能计费；'
          '#1036 未发货，#1037 已发货。审批只产生模拟结果，不操作 Shopify。</aside>')


def create_live_preview(directory: Path | None = None, model=None):
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = PROJECT_ROOT / 'var/embedding_cache/all-MiniLM-L6-v2'
    model = model if model is not None else create_live_model()
    directory = directory or PROJECT_ROOT / 'var' / 'live_preview'
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / 'checkpoints.sqlite', check_same_thread=False)
    fixture_client = DemoShopifyClient()
    graph = build_ticket_graph(checkpointer=SqliteSaver(connection), classifier_model=model,
                               draft_model=model, answer_model=model, shopify_client=fixture_client)
    application = create_app(ticket_graph=graph, ticket_database_path=directory / 'tickets.sqlite',
                             frontend_dist_path=directory / 'not_static')
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with original_lifespan(app):
                yield
        finally:
            connection.close()

    application.router.lifespan_context = lifespan
    application.state.fixture_shopify_client = fixture_client

    @application.middleware('http')
    async def no_external_tracing(request, call_next):
        with tracing_context(enabled=False):
            return await call_next(request)

    @application.get('/', response_class=HTMLResponse, include_in_schema=False)
    def index():
        index_path = FRONTEND_DIST_PATH / 'index.html'
        if not index_path.exists():
            return HTMLResponse(NOTICE + '<p>Build frontend first.</p>', status_code=503)
        return HTMLResponse(index_path.read_text(encoding='utf-8').replace('<body>', '<body>' + NOTICE))

    if FRONTEND_DIST_PATH.is_dir():
        application.mount('/', StaticFiles(directory=FRONTEND_DIST_PATH), name='live-preview-assets')
    return application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--rag', choices=['baseline', 'enhanced'], help='Override POLICY_RETRIEVAL_MODE for this preview.')
    arguments = parser.parse_args()
    if arguments.rag:
        os.environ['POLICY_RETRIEVAL_MODE'] = arguments.rag
    uvicorn.run(create_live_preview(), host='127.0.0.1', port=arguments.port)


if __name__ == '__main__':
    main()

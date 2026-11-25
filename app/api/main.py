from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.routes import router
from app.api.ws import ws_router
from app.config import Settings, get_settings
from app.content import build_junior_math_graph, build_junior_math_questions
from app.llm.client import MockLLM, OpenAICompatibleLLM
from app.maintenance import maintenance_loop
from app.orchestrator import AgentOrchestrator
from app.rate_limit import SlidingWindowLimiter
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter
from app.store_postgres import PostgresStore
from app.ws_manager import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def build_llm(settings: Settings) -> Any:
    """构造 LLM。MOCK_LLM=false 却拿不到 key 时**直接失败**。

    之前这里会打一条 warning 然后退回 MockLLM：生产环境会静默返回罐头话术，
    日志里除了启动那一行完全看不出来，属于最危险的一类配置错误。
    """
    if settings.mock_llm:
        return MockLLM()
    if not settings.llm_api_key:
        raise RuntimeError(
            "MOCK_LLM=false 但 LLM_API_KEY 为空。请配置 LLM_API_KEY / DASHSCOPE_API_KEY，"
            "或显式设置 MOCK_LLM=true 使用离线 Mock 模型。"
        )
    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        retry_backoff=settings.llm_retry_backoff_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    if settings.database_url:
        store = await PostgresStore.connect(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            apply_schema=settings.auto_apply_schema,
        )
        logger.info("persistence: postgres")
    else:
        store = SqliteStoreAdapter(
            SqliteStore(
                settings.sqlite_path,
                apply_schema=settings.auto_apply_schema,
            )
        )
        logger.info("persistence: sqlite (%s)", settings.sqlite_path)

    graph = build_junior_math_graph()
    seed_questions = build_junior_math_questions()
    changed = await store.seed_questions(seed_questions)
    if changed:
        logger.info(
            "content ready: %d knowledge nodes, %d/%d questions written",
            len(graph.nodes),
            changed,
            len(seed_questions),
        )
    else:
        logger.info(
            "content ready: %d knowledge nodes, %d questions unchanged",
            len(graph.nodes),
            len(seed_questions),
        )

    llm = build_llm(settings)
    app.state.llm_mode = "mock" if isinstance(llm, MockLLM) else settings.llm_model
    app.state.rate_limiter = SlidingWindowLimiter()
    app.state.orchestrator = AgentOrchestrator(
        llm=llm,
        settings=settings,
        store=store,
        graph=graph,
    )
    watcher = asyncio.create_task(app.state.orchestrator.engagement.start_idle_watcher())
    maintenance = asyncio.create_task(
        maintenance_loop(store, settings, manager)
    )
    logger.info("orchestrator ready (llm=%s)", "mock" if isinstance(llm, MockLLM) else settings.llm_model)
    try:
        yield
    finally:
        watcher.cancel()
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        with suppress(asyncio.CancelledError):
            await maintenance
        if hasattr(llm, "aclose"):
            await llm.aclose()
        await store.close()
        logger.info("orchestrator stopped")


app = FastAPI(title="multi-agent-lite", version="0.1.0", lifespan=lifespan)
_cors_origins = list(get_settings().cors_origins)
if "*" in _cors_origins:
    raise RuntimeError("CORS_ORIGINS must list explicit origins, not '*'")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Platform-Token"],
)
app.include_router(auth_router)
app.include_router(router)
app.include_router(ws_router)
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.ws import ws_router
from app.config import Settings, get_settings
from app.llm.client import MockLLM, OpenAICompatibleLLM
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter
from app.store_postgres import PostgresStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def build_llm(settings: Settings) -> Any:
    if settings.mock_llm or not settings.llm_api_key:
        if not settings.mock_llm:
            logger.warning("MOCK_LLM=false but LLM_API_KEY is empty; falling back to MockLLM")
        return MockLLM()
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
        )
        logger.info("persistence: postgres")
    else:
        store = SqliteStoreAdapter(SqliteStore(settings.sqlite_path))
        logger.info("persistence: sqlite (%s)", settings.sqlite_path)

    llm = build_llm(settings)
    app.state.orchestrator = AgentOrchestrator(
        llm=llm,
        settings=settings,
        store=store,
    )
    watcher = asyncio.create_task(app.state.orchestrator.engagement.start_idle_watcher())
    logger.info("orchestrator ready (llm=%s)", "mock" if isinstance(llm, MockLLM) else settings.llm_model)
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        if hasattr(llm, "aclose"):
            await llm.aclose()
        await store.close()
        logger.info("orchestrator stopped")


app = FastAPI(title="multi-agent-lite", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(ws_router)
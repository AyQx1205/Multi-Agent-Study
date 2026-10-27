from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.ws import ws_router
from app.config import get_settings
from app.llm.client import MockLLM, OpenAICompatibleLLM
from app.orchestrator import AgentOrchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def build_llm() -> Any:
    settings = get_settings()
    if settings.mock_llm or not settings.llm_api_key:
        if not settings.mock_llm:
            logger.warning("MOCK_LLM=false but LLM_API_KEY is empty; falling back to MockLLM")
        return MockLLM()
    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.orchestrator = AgentOrchestrator(llm=build_llm())
    watcher = asyncio.create_task(app.state.orchestrator.engagement.start_idle_watcher())
    logger.info("orchestrator ready")
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
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
from __future__ import annotations

import os

# 单元测试不连真实数据库：强制走内存 SQLite，避免读到 .env 里的 PostgreSQL。
# PostgreSQL 集成测试单独用 TEST_DATABASE_URL（见 tests/test_store_postgres.py）。
os.environ["DATABASE_URL"] = ""
os.environ["SQLITE_PATH"] = ":memory:"

from datetime import datetime

import pytest

from app.core.events import Event, EventBus
from app.orchestrator import AgentOrchestrator


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def orch() -> AgentOrchestrator:
    return AgentOrchestrator()


@pytest.fixture
def make_orch():
    """需要注入时钟或 LLM 时用。"""
    created: list[AgentOrchestrator] = []

    def factory(**kwargs) -> AgentOrchestrator:
        orchestrator = AgentOrchestrator(**kwargs)
        created.append(orchestrator)
        return orchestrator

    return factory


@pytest.fixture
def capture(orch):
    """收集编排器总线上的指定类型事件。"""
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    def subscribe(*event_types) -> list[Event]:
        for event_type in event_types:
            orch.event_bus.subscribe(event_type, handler)
        return received

    return subscribe


@pytest.fixture
def fixed_clock():
    value = datetime(2026, 1, 1, 10, 0, 0)
    return lambda: value
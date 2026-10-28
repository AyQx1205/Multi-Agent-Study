"""PostgreSQL 集成测试（需要真实库 + asyncpg）。

运行方式：
    $env:TEST_DATABASE_URL = "postgresql://multi_agent:multi_agent_dev@127.0.0.1:5432/multi_agent"
    pytest tests/test_store_postgres.py -q

没有设置 TEST_DATABASE_URL 或没装 asyncpg 时自动跳过，不影响 SQLite 的 71 条单元测试。
"""

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest

from app.core.events import Event, EventType
from app.core.learner_model import LearnerModel
from app.store_postgres import PostgresStore

DSN = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN or importlib.util.find_spec("asyncpg") is None,
    reason="set TEST_DATABASE_URL and install asyncpg to run postgres tests",
)


async def test_postgres_roundtrip_and_optimistic_lock() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    learner_id = f"pg-{uuid.uuid4().hex[:8]}"
    try:
        model = LearnerModel(learner_id)
        model.update_mastery("arithmetic", True)
        await store.save_model(model)

        loaded = await store.load_model(learner_id)
        assert loaded is not None
        assert loaded.get_state("arithmetic").attempts == 1

        state = model.get_state("arithmetic")
        version = await store.get_version(learner_id, "arithmetic")
        assert version == 1
        assert await store.compare_and_swap(learner_id, state, version) is True
        assert await store.compare_and_swap(learner_id, state, version) is False
        assert await store.get_version(learner_id, "arithmetic") == 2
    finally:
        await store.close()


async def test_postgres_event_append_is_idempotent() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    event = Event(
        type=EventType.ENCOURAGEMENT,
        source="test",
        learner_id=f"pg-{uuid.uuid4().hex[:8]}",
        data={"a": 1},
    )
    try:
        await store.append_event(event)
        await store.append_event(event)
        async with store.pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM events WHERE id=$1", event.id
            )
        assert count == 1
    finally:
        await store.close()
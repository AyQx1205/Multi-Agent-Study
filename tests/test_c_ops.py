"""C 级运维项的回归测试。

覆盖：内存淘汰（含锁不被淘汰）、过期数据清理、WS 连接上限/心跳/空闲回收、
维护任务、指标暴露。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.content import build_junior_math_graph
from app.core.cache import BoundedCache, LockCache
from app.core.content import Question, QuestionType
from app.core.events import Event, EventType
from app.maintenance import run_once
from app.metrics import metrics
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter
from app.ws_manager import ConnectionManager


def _temp_db() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-c-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


# ── C20：有界缓存 ────────────────────────────────────────────────────────


def test_bounded_cache_evicts_least_recently_used() -> None:
    cache: BoundedCache = BoundedCache(2, name="test")
    cache["a"] = 1
    cache["b"] = 2
    cache["a"]  # 访问 a，让它变成最近使用
    cache["c"] = 3

    assert set(cache.keys()) == {"a", "c"}
    assert cache.evictions == 1
    assert cache.stats()["size"] == 2


async def test_lock_cache_never_evicts_a_held_lock() -> None:
    cache: LockCache = LockCache(1, name="test-locks")
    held = cache.setdefault("a", asyncio.Lock())
    await held.acquire()
    try:
        # 超容量插入新锁：不能把正在持有的 a 淘汰掉，否则同一学习者会拿到两把锁
        cache.setdefault("b", asyncio.Lock())
        assert "a" in cache
        assert cache.setdefault("a", asyncio.Lock()) is held
    finally:
        held.release()


async def test_orchestrator_learner_cache_is_bounded() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        await adapter.seed_questions(
            [
                Question(
                    id="q-c-1",
                    knowledge_id="linear_eq",
                    grade=7,
                    type=QuestionType.FILL,
                    stem="2x=4，x=?",
                    answer="2",
                    accepted=["x=2"],
                    explanation="两边除以 2。",
                )
            ]
        )
        settings = Settings(learner_cache_size=2, agent_state_cache_size=2)
        orch = AgentOrchestrator(
            store=adapter, graph=build_junior_math_graph(), settings=settings
        )
        for index in range(4):
            await orch.submit_question(
                f"learner-{index}", "q-c-1", "2", idempotency_key=f"c-{index}"
            )

        assert len(orch.learner_models) <= 2
        assert len(orch.tutor._attempts) <= 2
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


# ── C21：过期数据清理 ────────────────────────────────────────────────────


async def test_purge_expired_removes_stale_rows_and_keeps_valid_ones() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        org, user = await adapter.create_organization_with_admin(
            "清理学校", "purge-school", "admin@purge.local", "hash", "管理员"
        )
        # 一个有效会话 + 一个过期会话
        await adapter.create_session("live-hash", user.id, now, now + timedelta(hours=1))
        await adapter.create_session(
            "dead-hash", user.id, now - timedelta(days=2), now - timedelta(days=1)
        )
        # 一个未用 ticket + 一个用过的 ticket
        await adapter.create_ws_ticket(
            "unused-ticket", user.id, now, now + timedelta(seconds=30)
        )
        await adapter.create_ws_ticket(
            "used-ticket", user.id, now, now + timedelta(seconds=30)
        )
        await adapter.consume_ws_ticket("used-ticket", now)
        # 老限流记录
        await adapter.record_login_failure(
            "login-ip:old",
            now - timedelta(hours=5),
            window_seconds=60,
            max_failures=5,
            lock_seconds=60,
        )

        assert await adapter.has_active_session(user.id, now) is True

        counts = await adapter.purge_expired(
            now, now - timedelta(hours=1)
        )
        assert counts["auth_sessions"] == 1
        assert counts["ws_tickets"] == 1
        assert counts["login_throttle"] == 1

        # 有效会话必须留着
        assert await adapter.has_active_session(user.id, now) is True
        assert await adapter.get_session("live-hash") is not None
        assert await adapter.get_session("dead-hash") is None
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


def test_purge_history_deletes_only_old_rows() -> None:
    path = _temp_db()
    store = SqliteStore(path)
    try:
        old = Event(
            type=EventType.STUDENT_MESSAGE,
            source="t",
            learner_id="s1",
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        new = Event(type=EventType.STUDENT_MESSAGE, source="t", learner_id="s1")
        store.append_event(old)
        store.append_event(new)

        counts = store.purge_history(datetime(2025, 6, 1, tzinfo=timezone.utc))
        assert counts["events"] == 1
        remaining = store.get_events("s1")
        assert [event.id for event in remaining] == [new.id]
    finally:
        store.close()
        path.unlink(missing_ok=True)


# ── C22：WebSocket 连接上限 / 心跳 / 空闲回收 ────────────────────────────


class FakeWS:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: list[tuple[int, str]] = []
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


async def test_ws_connection_cap_closes_extra_sockets() -> None:
    manager = ConnectionManager()
    first, second = FakeWS(), FakeWS()

    assert await manager.connect("s1", first, max_connections=1) is True
    assert await manager.connect("s1", second, max_connections=1) is False

    assert first.accepted is True
    assert second.closed and second.closed[0][0] == 1008
    assert manager.count("s1") == 1
    assert manager.count() == 1


async def test_ws_heartbeat_and_idle_sweep() -> None:
    clock = {"now": 0.0}
    manager = ConnectionManager(clock=lambda: clock["now"])
    ws = FakeWS()
    await manager.connect("s1", ws)

    assert await manager.heartbeat() == 1
    assert ws.sent[-1]["event_type"] == "heartbeat"

    clock["now"] = 10.0
    assert await manager.sweep_idle(300) == 0

    clock["now"] = 1000.0
    assert await manager.sweep_idle(300) == 1
    assert manager.count() == 0
    assert ws.closed and ws.closed[-1][1] == "idle timeout"


async def test_ws_touch_keeps_connection_alive() -> None:
    clock = {"now": 0.0}
    manager = ConnectionManager(clock=lambda: clock["now"])
    ws = FakeWS()
    await manager.connect("s1", ws)

    clock["now"] = 250.0
    manager.touch("s1", ws)
    clock["now"] = 400.0
    # 上次活动在 250，距现在 150 秒 < 300，不该被回收
    assert await manager.sweep_idle(300) == 0
    assert manager.count("s1") == 1


# ── C21/C22：维护任务 ────────────────────────────────────────────────────


async def test_maintenance_run_once_purges_and_reports() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        now = datetime.now(timezone.utc)
        org, user = await adapter.create_organization_with_admin(
            "维护学校", "maint-school", "maint@x.local", "hash", "管理员"
        )
        await adapter.create_session(
            "dead-hash", user.id, now - timedelta(days=2), now - timedelta(days=1)
        )
        manager = ConnectionManager()
        results = await run_once(adapter, Settings(), manager)

        assert results["auth_sessions"] >= 1
        assert results["heartbeat_sent"] == 0
        assert await adapter.get_session("dead-hash") is None
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


# ── C23：指标暴露 ────────────────────────────────────────────────────────


def test_metrics_endpoint_exposes_runtime_counters(client, student) -> None:
    before = metrics.value("events_published_total")
    student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "metrics-1"},
        json={"question_id": "q-rational_numbers-2", "answer": "7"},
    )

    text = client.get("/metrics").text
    assert "# TYPE events_published_total counter" in text
    assert "submissions_total" in text
    assert "ws_connections" in text
    # 计数器确实增长过（直接读注册表，避免依赖具体标签）
    assert metrics.value("events_published_total") > before
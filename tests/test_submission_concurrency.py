"""并发提交的回归测试。

两个曾经真实存在的缺陷：

1. `_submission_cid` 在拿 learner 锁之前就被写入，并发提交互相覆盖标记后，
   后者的事件会绕过事务直接写库——SQLite 适配器锁不可重入，直接死锁。
2. 事件总线的深度计数器是实例级（全局）的，几条事件链同时跑就能把计数
   顶到上限，导致正常事件被丢弃。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.content import build_junior_math_graph
from app.core.content import Question, QuestionType
from app.core.events import Event, EventBus, EventType
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter


def _temp_db() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-concurrency-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


async def _make_orch() -> tuple[AgentOrchestrator, SqliteStoreAdapter, Path]:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    await adapter.seed_questions(
        [
            Question(
                id="q-concurrency-1",
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
    orch = AgentOrchestrator(store=adapter, graph=build_junior_math_graph())
    return orch, adapter, path


async def test_concurrent_same_learner_submissions_do_not_deadlock() -> None:
    orch, adapter, path = await _make_orch()
    try:
        async def one(key: str):
            return await orch.submit_question(
                "student-concurrent",
                "q-concurrency-1",
                "2",
                idempotency_key=key,
            )

        # 旧实现下这里会永久挂起：后者的事件需要写库，而适配器锁被它自己的
        # 提交事务持有。timeout 让失败表现为 TimeoutError，而不是卡死 CI。
        outcomes = await asyncio.wait_for(
            asyncio.gather(one("k1"), one("k2")), timeout=15
        )

        assert [o.replayed for o in outcomes] == [False, False]
        attempts = await adapter.list_attempts("student-concurrent")
        assert len(attempts) == 2

        # 同一道题的第二次作答只算练习，不再计入掌握度。
        assert [o.counts_toward_mastery for o in outcomes].count(True) == 1
        model = await adapter.load_model("student-concurrent")
        assert model is not None
        assert model.get_state("linear_eq").attempts == 2
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


async def test_depth_limit_is_per_chain_not_global() -> None:
    bus = EventBus()

    async def bounce(event: Event) -> None:
        current = int(event.data.get("d", 0))
        if current < 3:
            await bus.publish(
                Event(
                    type=EventType.STUDENT_SUBMISSION,
                    source="test",
                    learner_id=event.learner_id,
                    data={"d": current + 1},
                    correlation_id=event.correlation_id,
                )
            )

    bus.subscribe(EventType.STUDENT_SUBMISSION, bounce)

    # 10 条并发链 × 每条 4 层 = 40 次嵌套；旧实现会被 20 的全局上限截断。
    await asyncio.gather(
        *(
            bus.publish(
                Event(
                    type=EventType.STUDENT_SUBMISSION,
                    source="test",
                    learner_id=f"s{index}",
                    correlation_id=f"c{index}",
                )
            )
            for index in range(10)
        )
    )

    for index in range(10):
        chain = bus.get_history(learner_id=f"s{index}", limit=100)
        assert len(chain) == 4, f"learner s{index} lost events: {len(chain)}"


async def test_depth_limit_still_stops_runaway_chain() -> None:
    bus = EventBus()
    counter = {"n": 0}

    async def self_loop(event: Event) -> None:
        counter["n"] += 1
        await bus.publish(event)

    bus.subscribe(EventType.ENCOURAGEMENT, self_loop)
    await bus.publish(Event(type=EventType.ENCOURAGEMENT, source="t", learner_id="s1"))

    # 单条自环链仍然必须被截断，不能无限递归。
    assert counter["n"] <= 21

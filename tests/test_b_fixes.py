"""B 级修复的回归测试。

覆盖：提示阶梯重置、会话计时重置、提交不再二次查题、事件链不被历史窗口截断。
（B13 内容占位题见 test_content.py，B14 的 EF 上限见 test_sm2.py，
B18 的题库幂等写入见 test_store.py。）
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.content import build_junior_math_graph
from app.core.content import Question, QuestionType
from app.core.events import EventType
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter


def _temp_db() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-b-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


async def _orch_with_question() -> tuple[AgentOrchestrator, SqliteStoreAdapter, Path]:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    await adapter.seed_questions(
        [
            Question(
                id="q-b-1",
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


# ── B15：答对后提示阶梯要重置 ────────────────────────────────────────────


async def test_hint_ladder_resets_after_correct_answer(orch, submit) -> None:
    events = []
    for _ in range(4):
        events += await submit("s1", "arithmetic", False)
    levels = [e.data["hint_level"] for e in events if e.type is EventType.HINT_RESPONSE]
    assert levels == [1, 1, 2], "连续答错应逐级升级"

    # 答对之后阶梯清零，下一次卡住应该重新从第一级开始
    await submit("s1", "arithmetic", True)
    events = []
    for _ in range(2):
        events += await submit("s1", "arithmetic", False)
    levels = [e.data["hint_level"] for e in events if e.type is EventType.HINT_RESPONSE]
    assert levels == [1], f"答对后应从第一级重新开始，实际 {levels}"


# ── B16：长时间中断后要开新会话，不能误判疲劳 ────────────────────────────


async def test_new_session_resets_fatigue_timer(make_orch, submit_for) -> None:
    clock_now = {"t": datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)}

    def clock() -> datetime:
        return clock_now["t"]

    orch = make_orch(clock=clock)
    submit = submit_for(orch)

    await submit("s1", "arithmetic", False)
    eng = orch.engagement._get("s1")
    first_start = eng.session_start

    # 隔了 20 小时再来一题：这是新会话，不应该被判成「已学 45 分钟」
    clock_now["t"] += timedelta(hours=20)
    events = await submit("s1", "arithmetic", False)

    assert eng.session_start > first_start
    assert eng.session_duration_minutes < 1
    fatigue = [
        e
        for e in events
        if e.type is EventType.ENCOURAGEMENT and e.data.get("type") == "fatigue_break"
    ]
    assert not fatigue, "新会话不该触发疲劳提醒"


# ── B17：提交不再二次查题 ────────────────────────────────────────────────


def test_submit_does_not_refetch_question(client, student, monkeypatch) -> None:
    orch = client.app.state.orchestrator

    async def boom(question_id):  # pragma: no cover - 被调用即失败
        raise AssertionError("submit 不应在事务之后再查一次题目")

    monkeypatch.setattr(orch, "get_question", boom)
    response = student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "b17-1"},
        json={"question_id": "q-rational_numbers-2", "answer": "7"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 答案仍要正确返回（来自事务里那份题目，而不是再查一次）
    assert body["correct_answer"] == "7"
    assert body["explanation"]


# ── B19：事件链不受全局历史窗口截断 ──────────────────────────────────────


async def test_submission_events_are_not_truncated_by_history_limit(monkeypatch) -> None:
    from app.core import events as events_module

    # 把全局环形缓冲压到 2 条：如果实现退回去用 get_history，响应就会被截断
    monkeypatch.setattr(events_module, "HISTORY_LIMIT", 2)
    orch, adapter, path = await _orch_with_question()
    try:
        outcome = await orch.submit_question(
            "s1", "q-b-1", "2", idempotency_key="b19-1"
        )
        assert len(outcome.events) >= 4
        assert len({e.correlation_id for e in outcome.events}) == 1
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)

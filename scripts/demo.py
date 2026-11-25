"""端到端演示：走真实提交链路（题库判分 + 幂等 + 事务）。

    python scripts/demo.py            # 全部场景
    python scripts/demo.py idempotent # 单个场景

注意：这里刻意不再使用「客户端直接声称答对/答错」的旁路，
演示的就是 HTTP `/submit` 实际走的那条链路。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.content import build_junior_math_graph, build_junior_math_questions
from app.core.events import Event
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter

LEARNER = "s1"


def show(title: str, events: list[Event]) -> None:
    print(f"\n=== {title} ===")
    for event in events:
        detail = event.data
        if event.type.value == "tutor.teaching_response":
            detail = {
                "response": event.data.get("response", "")[:60],
                "style": event.data.get("teaching_style"),
            }
        elif len(str(detail)) > 120:
            detail = {k: detail[k] for k in list(detail)[:4]}
        print(f" {event.type.value:<34} <- {event.source:<16} {detail}")


async def make_orchestrator() -> tuple[AgentOrchestrator, SqliteStoreAdapter]:
    store = SqliteStoreAdapter(SqliteStore(":memory:"))
    await store.seed_questions(build_junior_math_questions())
    orch = AgentOrchestrator(store=store, graph=build_junior_math_graph())
    return orch, store


async def scenario_correct() -> None:
    orch, store = await make_orchestrator()
    try:
        outcome = await orch.submit_question(
            LEARNER,
            "q-rational_numbers-2",
            "7",
            idempotency_key="demo-correct-1",
        )
        print(
            f"   判分：is_correct={outcome.attempt.is_correct} "
            f"reveal={outcome.reveal_answer} "
            f"counts_toward_mastery={outcome.counts_toward_mastery}"
        )
        show("场景 1：答对一次（评估 -> 教学）", outcome.events)
    finally:
        await store.close()


async def scenario_wrong_twice() -> None:
    orch, store = await make_orchestrator()
    try:
        collected: list[Event] = []
        for index in (1, 2):
            outcome = await orch.submit_question(
                LEARNER,
                "q-rational_numbers-2",
                "999",
                idempotency_key=f"demo-wrong-{index}",
            )
            print(
                f"   第 {index} 次答错：reveal={outcome.reveal_answer} "
                f"counts_toward_mastery={outcome.counts_toward_mastery}"
            )
            collected += outcome.events
        show("场景 2：连错两次（第一次不给答案，第二次才揭示）", collected)
    finally:
        await store.close()


async def scenario_idempotent() -> None:
    orch, store = await make_orchestrator()
    try:
        first = await orch.submit_question(
            LEARNER, "q-rational_numbers-2", "7", idempotency_key="demo-idem"
        )
        second = await orch.submit_question(
            LEARNER, "q-rational_numbers-2", "7", idempotency_key="demo-idem"
        )
        print(
            f"   第一次 attempt={first.attempt.id[:8]} replayed={first.replayed} "
            f"events={len(first.events)}"
        )
        print(
            f"   重放   attempt={second.attempt.id[:8]} replayed={second.replayed} "
            f"events={len(second.events)}"
        )
        print(f"   数据库里的 attempt 条数={len(await store.list_attempts(LEARNER))}")
    finally:
        await store.close()


async def scenario_stuck() -> None:
    orch, store = await make_orchestrator()
    try:
        collected: list[Event] = []
        for index in range(3):
            outcome = await orch.submit_question(
                LEARNER,
                "q-quadratic_eq-1",
                "Z",
                idempotency_key=f"demo-stuck-{index}",
            )
            collected += outcome.events
        show("场景 3：连错三次（分级提示 + 挫败干预）", collected)
    finally:
        await store.close()


async def scenario_progress() -> None:
    orch, store = await make_orchestrator()
    try:
        for index, answer in enumerate(("7", "2"), start=1):
            outcome = await orch.submit_question(
                LEARNER,
                f"q-rational_numbers-{index + 1}",
                answer,
                idempotency_key=f"demo-progress-{index}",
            )
        show("场景 4：两道题都答对（掌握前置 -> 推荐下一个知识点）", outcome.events)
        print("   进度：", orch.get_learner_progress(LEARNER)["progress"])
    finally:
        await store.close()


async def scenario_repeat() -> None:
    orch, store = await make_orchestrator()
    try:
        outcome = None
        for index in range(2):
            outcome = await orch.submit_question(
                LEARNER,
                "q-rational_numbers-2",
                "7",
                idempotency_key=f"demo-repeat-{index}",
            )
        assert outcome is not None
        progress = orch.get_learner_progress(LEARNER)["progress"]
        print("\n=== 场景 5：同一道题用不同幂等键答对两次 ===")
        print(f"   第二次 counts_toward_mastery={outcome.counts_toward_mastery}")
        print(
            f"   作答次数={progress['total_attempts']} "
            f"平均掌握度={progress['avg_mastery']:.4f}（重复作答不再拉高掌握度）"
        )
    finally:
        await store.close()


SCENARIOS = {
    "correct": scenario_correct,
    "wrong": scenario_wrong_twice,
    "idempotent": scenario_idempotent,
    "stuck": scenario_stuck,
    "progress": scenario_progress,
    "repeat": scenario_repeat,
}


async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        for scenario in SCENARIOS.values():
            await scenario()
        return
    scenario = SCENARIOS.get(which)
    if scenario is None:
        print(f"未知场景：{which}，可选：{', '.join(SCENARIOS)}")
        raise SystemExit(2)
    await scenario()


if __name__ == "__main__":
    asyncio.run(main())
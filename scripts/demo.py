from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.events import Event
from app.orchestrator import AgentOrchestrator

def show(title: str, events: list[Event]) -> None:
    print(f"\n=== {title} ===")
    for event in events:
        detail = event.data
        if event.type.value == "tutor.teaching_response":
            detail = {"response": event.data.get("response", "")[:60],"style": event.data.get("teaching_style")}
        elif len(str(detail)) > 120:
            detail = {k: detail[k] for k in list(detail)[:4]}
        print(f" {event.type.value:<34} <- {event.source:<16} {detail}")

async def scenario_correct() -> None:
    orch = AgentOrchestrator()
    events = await orch.submit_answer("s1","arithmetic",True)
    show("场景 1:答对一次(评估 -> 教学)",events)

async def scenario_stuck() -> None:
    orch = AgentOrchestrator()
    collected: list[Event] = []
    for _ in range(3):
        collected += await orch.submit_answer("s1", "quadratic_eq", False)
    show("场景 2:连错三次(分级提示 + 挫败干预)", collected)

async def scenario_progress() -> None:
    orch = AgentOrchestrator()
    await orch.submit_answer("s1", "arithmetic", True)
    events = await orch.submit_answer("s1", "arithmetic", True)
    show("场景 3:掌握前置知识(推荐下一个知识点)", events)
    print("   进度：", orch.get_learner_progress("s1")["progress"])

async def scenario_bored() -> None:
    orch = AgentOrchestrator()
    collected: list[Event] = []
    for _ in range(5):
        collected += await orch.submit_answer("s1", "arithmetic", True)
    show("场景 4:连对五次(无聊检测 + 提速)", collected[-6:])

async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "correct"
    if which == "all":
        await scenario_correct()
        await scenario_stuck()
        await scenario_progress()
        await scenario_bored()
    elif which == "stuck":
        await scenario_stuck()
    elif which == "progress":
        await scenario_progress()
    elif which == "bored":
        await scenario_bored()
    else:
        await scenario_correct()

if __name__ == "__main__":
    asyncio.run(main())

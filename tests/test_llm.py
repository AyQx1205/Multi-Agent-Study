from __future__ import annotations

from app.core.events import EventType
from app.llm.client import MockLLM, guard_response
from app.orchestrator import AgentOrchestrator


def test_guard_blocks_answer_leak() -> None:
    ok, reason = guard_response("所以 x = 5，答案是 5")
    assert ok is False
    assert reason.startswith("leak") or reason == "multi_question"


def test_guard_blocks_multiple_questions() -> None:
    assert guard_response("会了吗？还有哪里不懂？")[0] is False


def test_guard_allows_single_socratic_question() -> None:
    assert guard_response("你觉得哪个条件最关键？")[0] is True


def test_guard_blocks_empty_and_long() -> None:
    assert guard_response("   ")[0] is False
    assert guard_response("很" * 300)[0] is False


async def test_mock_llm_records_calls() -> None:
    llm = MockLLM()
    text = await llm.complete("system", "user")
    assert text and llm.calls == [("system", "user")]


async def test_failure_falls_back_to_template() -> None:
    orch = AgentOrchestrator(llm=MockLLM(fail_times=1))
    events = await orch.send_message("s1", "我不会", "arithmetic")
    responses = [e for e in events if e.type is EventType.TEACHING_RESPONSE]
    assert responses and "？" in responses[-1].data["response"]


class _LeakyLLM:
    async def complete(self, system: str, user: str, **kwargs) -> str:
        return "答案是 5，所以 x = 5。"


async def test_leaky_output_is_replaced() -> None:
    orch = AgentOrchestrator(llm=_LeakyLLM())
    events = await orch.send_message("s1", "直接告诉我答案", "arithmetic")
    response = [e for e in events if e.type is EventType.TEACHING_RESPONSE][-1].data["response"]
    assert "答案是" not in response
    assert "？" in response
from __future__ import annotations

from app.core.events import Event, EventType


async def test_one_submission_one_correlation_id(orch) -> None:
    events = await orch.submit_answer("s1", "arithmetic", True)
    assert len(events) >= 3
    assert len({e.correlation_id for e in events}) == 1


async def test_flow_reaches_teaching_response(orch) -> None:
    events = await orch.submit_answer("s1", "arithmetic", True)
    responses = [e for e in events if e.type is EventType.TEACHING_RESPONSE]
    assert responses and responses[-1].data["response"]


async def test_progress_reflects_attempts(orch) -> None:
    await orch.submit_answer("s1", "arithmetic", True)
    await orch.submit_answer("s1", "fractions", False)
    progress = orch.get_learner_progress("s1")
    assert progress["progress"]["total_attempts"] == 2
    assert progress["progress"]["total_correct"] == 1


async def test_unknown_learner_progress(orch) -> None:
    assert orch.get_learner_progress("nobody")["status"] == "no_data"


async def test_question_flow_returns_assessment_context(orch) -> None:
    events = await orch.ask_question("s1", "arithmetic", "这一步为什么？")
    complete = [e for e in events if e.type is EventType.ASSESSMENT_COMPLETE]
    assert complete and complete[0].data["context"] == "student_question"


async def test_event_history_tracks_chain(orch) -> None:
    await orch.submit_answer("s1", "arithmetic", True)
    history: list[Event] = orch.event_bus.get_history(learner_id="s1", limit=100)
    assert any(e.type is EventType.MASTERY_UPDATED for e in history)
    assert any(e.type is EventType.REVIEW_SCHEDULED for e in history)
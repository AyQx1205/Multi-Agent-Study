"""5 个 Agent 的行为：订阅、产出、边界。"""

from __future__ import annotations

from datetime import timedelta

from app.agents.engagement import LearningState
from app.agents.hint import HintAgent
from app.core.events import Event, EventType


def types(events: list[Event]) -> list[str]:
    return [e.type.value for e in events]


async def test_assessment_emits_chain(orch) -> None:
    events = await orch.submit_answer("s1", "arithmetic", True)
    names = types(events)
    assert "assessment.mastery_updated" in names
    assert "assessment.complete" in names
    complete = next(e for e in events if e.type is EventType.ASSESSMENT_COMPLETE)
    assert complete.data["is_correct"] is True
    assert complete.data["overall_progress"]["total_attempts"] == 1


async def test_question_does_not_change_mastery(orch) -> None:
    await orch.ask_question("s1", "arithmetic", "为什么负负得正？")
    state = orch.learner_models["s1"].get_state("arithmetic")
    assert state.attempts == 0


async def test_weakness_after_three_wrong(orch) -> None:
    events: list[Event] = []
    for _ in range(3):
        events += await orch.submit_answer("s1", "quadratic_eq", False)
    weakness = [e for e in events if e.type is EventType.WEAKNESS_DETECTED]
    assert weakness and weakness[0].data["knowledge_id"] == "quadratic_eq"


async def test_tutor_stays_quiet_on_first_wrong(orch) -> None:
    events = await orch.submit_answer("s1", "arithmetic", False)
    assert "tutor.hint_needed" not in types(events)


async def test_tutor_requests_hint_on_second_wrong(orch) -> None:
    await orch.submit_answer("s1", "arithmetic", False)
    events = await orch.submit_answer("s1", "arithmetic", False)
    assert "tutor.hint_needed" in types(events)
    assert "hint.response" in types(events)


async def test_correct_answer_resets_streak(orch) -> None:
    await orch.submit_answer("s1", "arithmetic", False)
    await orch.submit_answer("s1", "arithmetic", True)
    events = await orch.submit_answer("s1", "arithmetic", False)
    assert "tutor.hint_needed" not in types(events)


async def test_tutor_forwards_hint_text(orch) -> None:
    await orch.submit_answer("s1", "arithmetic", False)
    events = await orch.submit_answer("s1", "arithmetic", False)
    forwarded = [
        e
        for e in events
        if e.type is EventType.TEACHING_RESPONSE and e.data.get("teaching_style") == "hint"
    ]
    assert forwarded and forwarded[0].data["response"].startswith("关于")


async def test_hint_level_escalation(bus) -> None:
    HintAgent("HintAgent", bus, {})
    for _ in range(5):
        await bus.publish(
            Event(
                type=EventType.HINT_NEEDED,
                source="TutorAgent",
                learner_id="s1",
                data={"knowledge_id": "k", "mastery": 0.4, "attempts": 2},
            )
        )
    levels = [
        e.data["hint_level"]
        for e in bus.get_history(learner_id="s1", limit=20)
        if e.type is EventType.HINT_RESPONSE
    ]
    assert levels == [1, 1, 2, 2, 3]


def test_hint_early_stop_for_very_low_mastery() -> None:
    assert HintAgent.decide_level(0, 0.1, 3) == 3
    assert HintAgent.decide_level(0, 0.4, 1) == 1


async def test_curriculum_recommends_next_topic(orch) -> None:
    await orch.submit_answer("s1", "arithmetic", True)
    events = await orch.submit_answer("s1", "arithmetic", True)
    topics = [e for e in events if e.type is EventType.NEXT_TOPIC]
    assert topics and topics[-1].data["knowledge_id"] == "negative_numbers"


async def test_curriculum_schedules_review(orch) -> None:
    events = await orch.submit_answer("s1", "arithmetic", True)
    scheduled = [e for e in events if e.type is EventType.REVIEW_SCHEDULED]
    assert scheduled and scheduled[0].data["weekly_schedule"]


async def test_curriculum_same_day_throttle(orch) -> None:
    for _ in range(3):
        await orch.submit_answer("s1", "arithmetic", True)
    item = orch.curriculum._reviews["s1"]["arithmetic"]
    assert item.total_reviews == 1


async def test_curriculum_remedial_path(orch) -> None:
    events: list[Event] = []
    for _ in range(3):
        events += await orch.submit_answer("s1", "quadratic_eq", False)
    paths = [e for e in events if e.type is EventType.PATH_UPDATED]
    assert paths and paths[0].data["remedial_path"][0] == "arithmetic"


async def test_engagement_frustration(orch) -> None:
    events: list[Event] = []
    for _ in range(3):
        events += await orch.submit_answer("s1", "arithmetic", False)
    alerts = [
        e.data["alert_type"]
        for e in events
        if e.type is EventType.ENGAGEMENT_ALERT
    ]
    assert "frustration" in alerts


async def test_engagement_boredom(orch) -> None:
    events: list[Event] = []
    for _ in range(5):
        events += await orch.submit_answer("s1", "arithmetic", True)
    alerts = [e.data["alert_type"] for e in events if e.type is EventType.ENGAGEMENT_ALERT]
    assert "boredom" in alerts


async def test_engagement_fatigue(make_orch, fixed_clock) -> None:
    orch = make_orch(clock=fixed_clock)
    await orch.submit_answer("s1", "arithmetic", False)
    orch.engagement._get("s1").session_start = fixed_clock() - timedelta(minutes=46)
    events = await orch.submit_answer("s1", "arithmetic", False)
    encouragement = [e for e in events if e.type is EventType.ENCOURAGEMENT]
    assert encouragement and encouragement[0].data["type"] == "fatigue_break"


async def test_engagement_struggling_has_no_alert(orch) -> None:
    events = await orch.submit_answer("s1", "arithmetic", False)
    assert "engagement.alert" not in types(events)
    assert orch.engagement._get("s1").state is LearningState.STRUGGLING


async def test_idle_watcher_emits_check(make_orch, fixed_clock) -> None:
    orch = make_orch(clock=fixed_clock)
    await orch.submit_answer("s1", "arithmetic", True)
    eng = orch.engagement._get("s1")
    eng.last_activity = fixed_clock() - timedelta(minutes=6)

    await orch.engagement.check_idle()

    events = orch.event_bus.get_history(learner_id="s1", limit=50)
    idle = [e for e in events if e.type is EventType.ENCOURAGEMENT and e.data.get("type") == "idle_check"]
    assert idle and eng.state is LearningState.IDLE
from __future__ import annotations

from app.core.events import Event, EventType
from app.orchestrator import AgentOrchestrator

PUBLISHES: dict[str, set[EventType]] = {
    "AssessmentAgent": {
        EventType.MASTERY_UPDATED,
        EventType.WEAKNESS_DETECTED,
        EventType.ASSESSMENT_COMPLETE,
    },
    "TutorAgent": {
        EventType.TEACHING_RESPONSE,
        EventType.HINT_NEEDED,
        EventType.DIFFICULTY_ADJUSTED,
    },
    "HintAgent": {EventType.HINT_RESPONSE},
    "CurriculumAgent": {
        EventType.PATH_UPDATED,
        EventType.NEXT_TOPIC,
        EventType.REVIEW_SCHEDULED,
    },
    "EngagementAgent": {
        EventType.ENGAGEMENT_ALERT,
        EventType.ENCOURAGEMENT,
        EventType.PACE_ADJUSTMENT,
    },
}


def test_event_type_count_and_uniqueness() -> None:
    values = [event.value for event in EventType]
    assert len(values) == 16
    assert len(set(values)) == 16


CASCADE = {
    EventType.MASTERY_UPDATED,
    EventType.WEAKNESS_DETECTED,
    EventType.ASSESSMENT_COMPLETE,
    EventType.HINT_NEEDED,
    EventType.HINT_RESPONSE,
    EventType.ENGAGEMENT_ALERT,
    EventType.PACE_ADJUSTMENT,
}

# 只给 UI 消费、Agent 之间不订阅的「终态事件」
TERMINAL = {
    EventType.TEACHING_RESPONSE,
    EventType.DIFFICULTY_ADJUSTED,
    EventType.PATH_UPDATED,
    EventType.NEXT_TOPIC,
    EventType.REVIEW_SCHEDULED,
    EventType.ENCOURAGEMENT,
}


def test_all_internal_events_are_subscribed() -> None:
    orch = AgentOrchestrator()
    subscribed: set[EventType] = set()
    for agent in (
        orch.assessment,
        orch.tutor,
        orch.curriculum,
        orch.hint,
        orch.engagement,
    ):
        subscribed |= set(agent.subscribed_events)

    student_inputs = {
        EventType.STUDENT_SUBMISSION,
        EventType.STUDENT_QUESTION,
        EventType.STUDENT_MESSAGE,
    }
    internal = set(EventType) - student_inputs
    assert internal == CASCADE | TERMINAL
    assert subscribed == CASCADE | student_inputs
    assert not (subscribed & TERMINAL)


def test_no_agent_subscribes_to_own_output() -> None:
    orch = AgentOrchestrator()
    for agent in (
        orch.assessment,
        orch.tutor,
        orch.curriculum,
        orch.hint,
        orch.engagement,
    ):
        assert not (set(agent.subscribed_events) & PUBLISHES[agent.name]), agent.name


def test_event_roundtrip() -> None:
    event = Event(
        type=EventType.STUDENT_SUBMISSION,
        source="api",
        learner_id="s1",
        data={"knowledge_id": "k"},
        correlation_id="c1",
    )
    restored = Event.model_validate(event.model_dump())
    assert restored.type is EventType.STUDENT_SUBMISSION
    assert restored.correlation_id == "c1"
    assert restored.data == {"knowledge_id": "k"}
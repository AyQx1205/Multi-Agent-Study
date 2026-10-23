"""事件总线：分发、异常隔离、历史、深度上限。"""

from __future__ import annotations

from app.core.events import Event, EventBus, EventType


async def test_publish_delivers_to_all_subscribers(bus: EventBus) -> None:
    got: list[Event] = []

    async def handler(event: Event) -> None:
        got.append(event)

    bus.subscribe(EventType.STUDENT_SUBMISSION, handler)
    bus.subscribe(EventType.STUDENT_SUBMISSION, handler)

    await bus.publish(Event(type=EventType.STUDENT_SUBMISSION, source="t", learner_id="s1"))

    assert len(got) == 2


async def test_publish_without_subscriber_is_noop(bus: EventBus) -> None:
    await bus.publish(Event(type=EventType.STUDENT_QUESTION, source="t", learner_id="s1"))
    assert len(bus.get_history(learner_id="s1")) == 1


async def test_history_filters(bus: EventBus) -> None:
    for index in range(5):
        await bus.publish(
            Event(
                type=EventType.STUDENT_SUBMISSION,
                source="t",
                learner_id=f"s{index % 2}",
                correlation_id=f"c{index}",
            )
        )

    assert len(bus.get_history()) == 5
    assert len(bus.get_history(learner_id="s0")) == 3
    assert len(bus.get_history(correlation_id="c1")) == 1
    assert len(bus.get_history(limit=2)) == 2


async def test_handler_exception_is_isolated(bus: EventBus) -> None:
    survived: list[Event] = []

    async def boom(event: Event) -> None:
        raise RuntimeError("boom")

    async def ok(event: Event) -> None:
        survived.append(event)

    bus.subscribe(EventType.STUDENT_MESSAGE, boom)
    bus.subscribe(EventType.STUDENT_MESSAGE, ok)

    await bus.publish(Event(type=EventType.STUDENT_MESSAGE, source="t", learner_id="s1"))

    assert len(survived) == 1


async def test_nested_publish_and_depth_limit() -> None:
    bus = EventBus()
    counter = {"n": 0}

    async def self_loop(event: Event) -> None:
        counter["n"] += 1
        await bus.publish(event)

    bus.subscribe(EventType.ENCOURAGEMENT, self_loop)
    await bus.publish(Event(type=EventType.ENCOURAGEMENT, source="t", learner_id="s1"))

    assert counter["n"] <= 21
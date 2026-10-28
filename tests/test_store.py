from __future__ import annotations

from datetime import datetime, timedelta

from app.core.content import Attempt, Question, QuestionType
from app.core.events import Event, EventType
from app.core.learner_model import LearnerModel
from app.core.spaced_repetition import ReviewItem
from app.store import SqliteStore


def make_store(tmp_path) -> SqliteStore:
    return SqliteStore(tmp_path / "test.db")


def test_save_and_load_model(tmp_path) -> None:
    store = make_store(tmp_path)
    model = LearnerModel("s1")
    model.update_mastery("arithmetic", True)
    store.save_model(model)

    loaded = store.load_model("s1")
    assert loaded is not None
    assert loaded.get_state("arithmetic").mastery == model.get_state("arithmetic").mastery
    assert loaded.get_state("arithmetic").attempts == 1
    store.close()


def test_load_unknown_returns_none(tmp_path) -> None:
    store = make_store(tmp_path)
    assert store.load_model("nobody") is None
    store.close()


def test_optimistic_lock(tmp_path) -> None:
    store = make_store(tmp_path)
    model = LearnerModel("s1")
    model.update_mastery("arithmetic", True)
    store.save_model(model)

    state = model.get_state("arithmetic")
    assert store.get_version("s1", "arithmetic") == 1
    assert store.compare_and_swap("s1", state, 1) is True
    assert store.compare_and_swap("s1", state, 1) is False
    assert store.get_version("s1", "arithmetic") == 2
    store.close()


def test_event_append_is_idempotent(tmp_path) -> None:
    store = make_store(tmp_path)
    event = Event(type=EventType.ENCOURAGEMENT, source="t", learner_id="s1", data={"a": 1})
    store.append_event(event)
    store.append_event(event)
    assert store.count_events() == 1
    store.close()


def test_events_survive_reopen(tmp_path) -> None:
    path = tmp_path / "reopen.db"
    first = SqliteStore(path)
    event = Event(
        type=EventType.TEACHING_RESPONSE,
        source="t",
        learner_id="s1",
        data={"response": "先看条件？"},
        correlation_id="c1",
    )
    first.append_event(event)
    first.close()

    second = SqliteStore(path)
    restored = second.get_events("s1")
    assert len(restored) == 1
    assert restored[0].id == event.id
    assert restored[0].data["response"] == "先看条件？"
    assert restored[0].correlation_id == "c1"
    second.close()


def test_reviews_survive_reopen(tmp_path) -> None:
    path = tmp_path / "reviews.db"
    now = datetime.now()
    item = ReviewItem(
        knowledge_id="arithmetic",
        easiness_factor=2.3,
        interval_days=6,
        repetition=2,
        next_review=now + timedelta(days=6),
        last_review=now,
        total_reviews=2,
    )

    first = SqliteStore(path)
    first.save_model(LearnerModel("s1"))
    first.save_reviews("s1", {"arithmetic": item})
    first.close()

    second = SqliteStore(path)
    restored = second.load_reviews("s1")["arithmetic"]
    assert restored.knowledge_id == "arithmetic"
    assert restored.interval_days == 6
    assert restored.repetition == 2
    assert restored.total_reviews == 2
    assert restored.next_review == item.next_review
    second.close()


def test_question_bank_roundtrip(tmp_path) -> None:
    store = make_store(tmp_path)
    question = Question(
        id="q-demo-1",
        knowledge_id="linear_eq",
        grade=7,
        type=QuestionType.CHOICE,
        stem="2x=4，x=?",
        options=["1", "2", "3", "4"],
        answer="B",
        explanation="两边除以 2。",
        difficulty=1,
    )
    assert store.seed_questions([question]) == 1
    # 幂等
    assert store.seed_questions([question]) == 1
    assert store.get_question(question.id) == question
    assert store.list_questions(knowledge_id="linear_eq", grade=7)[0].id == question.id
    assert store.list_questions(knowledge_id="missing") == []
    store.close()


def test_attempts_roundtrip(tmp_path) -> None:
    store = make_store(tmp_path)
    attempt = Attempt(
        learner_id="s1",
        question_id="q-demo-1",
        knowledge_id="linear_eq",
        answer="B",
        is_correct=True,
        time_spent_seconds=8.5,
    )
    store.save_attempt(attempt)
    restored = store.list_attempts("s1")
    assert len(restored) == 1
    assert restored[0].question_id == "q-demo-1"
    assert restored[0].is_correct is True
    assert restored[0].time_spent_seconds == 8.5
    assert store.list_attempts("nobody") == []
    store.close()
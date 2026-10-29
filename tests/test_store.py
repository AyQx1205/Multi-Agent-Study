from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.content import Attempt, Question, QuestionType
from app.core.events import Event, EventType
from app.core.learner_model import LearnerModel
from app.core.spaced_repetition import ReviewItem
from app.store import SqliteStore


@pytest.fixture
def store_path():
    """不用 pytest 的 tmp_path：部分 Windows 沙箱对 pytest-of-* 目录有 ACL 限制。"""
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-store-", suffix=".db")
    os.close(fd)
    path = Path(raw_path)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_save_and_load_model(store_path) -> None:
    store = SqliteStore(store_path)
    model = LearnerModel("s1")
    model.update_mastery("arithmetic", True)
    store.save_model(model)

    loaded = store.load_model("s1")
    assert loaded is not None
    assert loaded.get_state("arithmetic").mastery == model.get_state("arithmetic").mastery
    assert loaded.get_state("arithmetic").attempts == 1
    store.close()


def test_load_unknown_returns_none(store_path) -> None:
    store = SqliteStore(store_path)
    assert store.load_model("nobody") is None
    store.close()


def test_optimistic_lock(store_path) -> None:
    store = SqliteStore(store_path)
    model = LearnerModel("s1")
    model.update_mastery("arithmetic", True)
    store.save_model(model)

    state = model.get_state("arithmetic")
    assert store.get_version("s1", "arithmetic") == 1
    assert store.compare_and_swap("s1", state, 1) is True
    assert store.compare_and_swap("s1", state, 1) is False
    assert store.get_version("s1", "arithmetic") == 2
    store.close()


def test_event_append_is_idempotent(store_path) -> None:
    store = SqliteStore(store_path)
    event = Event(type=EventType.ENCOURAGEMENT, source="t", learner_id="s1", data={"a": 1})
    store.append_event(event)
    store.append_event(event)
    assert store.count_events() == 1
    store.close()


def test_events_survive_reopen(store_path) -> None:
    path = store_path
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


def test_reviews_survive_reopen(store_path) -> None:
    path = store_path
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


def test_question_bank_roundtrip(store_path) -> None:
    store = SqliteStore(store_path)
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


def test_attempts_roundtrip(store_path) -> None:
    store = SqliteStore(store_path)
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


def test_attempt_idempotency_key_is_unique_per_learner(store_path) -> None:
    store = SqliteStore(store_path)
    first = Attempt(
        learner_id="s1",
        question_id="q-demo-1",
        knowledge_id="linear_eq",
        answer="B",
        is_correct=True,
        idempotency_key="key-1",
        request_fingerprint="fp-1",
    )
    duplicate = Attempt(
        learner_id="s1",
        question_id="q-demo-1",
        knowledge_id="linear_eq",
        answer="B",
        is_correct=True,
        idempotency_key="key-1",
        request_fingerprint="fp-1",
    )
    other_learner = Attempt(
        learner_id="s2",
        question_id="q-demo-1",
        knowledge_id="linear_eq",
        answer="B",
        is_correct=True,
        idempotency_key="key-1",
        request_fingerprint="fp-1",
    )
    assert store.save_attempt(first) is True
    assert store.save_attempt(duplicate) is False
    assert store.save_attempt(other_learner) is True
    restored = store.get_attempt_by_idempotency("s1", "key-1")
    assert restored is not None
    assert restored.id == first.id
    assert len(store.list_attempts("s1")) == 1
    store.close()
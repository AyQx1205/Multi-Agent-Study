from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from app.content import build_junior_math_graph
from app.core.content import Attempt, Question, QuestionType
from app.core.events import Event, EventType
from app.core.learner_model import LearnerModel
from app.core.spaced_repetition import ReviewItem
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter


def _temp_db() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-submit-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


async def test_submit_transaction_rolls_back_all_side_effects() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        question = Question(
            id="q-txn-1",
            knowledge_id="linear_eq",
            grade=7,
            type=QuestionType.FILL,
            stem="2x=4，x=?",
            answer="2",
            accepted=["x=2"],
            explanation="两边除以 2。",
        )
        await adapter.seed_questions([question])
        orch = AgentOrchestrator(
            store=adapter,
            graph=build_junior_math_graph(),
        )

        async def fail(event: Event) -> None:
            raise RuntimeError("agent failure after model mutation")

        orch.event_bus.subscribe(EventType.STUDENT_SUBMISSION, fail)
        with pytest.raises(RuntimeError):
            await orch.submit_question(
                "student-txn",
                question.id,
                "2",
                idempotency_key="txn-key-1",
            )

        assert await adapter.list_attempts("student-txn") == []
        assert await adapter.load_model("student-txn") is None
        assert await adapter.get_events("student-txn") == []
        assert await adapter.get_attempt_by_idempotency(
            "student-txn", "txn-key-1"
        ) is None
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


async def test_commit_failure_rolls_back_attempt(monkeypatch) -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        async def build(
            model: LearnerModel, reviews: dict[str, ReviewItem]
        ) -> tuple[Attempt, list[Event]]:
            return (
                Attempt(
                    learner_id=model.learner_id,
                    question_id="q-txn-rollback",
                    knowledge_id="linear_eq",
                    answer="2",
                    is_correct=True,
                    idempotency_key="txn-key-rollback",
                    request_fingerprint="fp-rollback",
                ),
                [],
            )

        def explode(model: LearnerModel) -> None:
            raise RuntimeError("simulated write failure after attempt insert")

        monkeypatch.setattr(adapter._inner, "_save_model_no_commit", explode)
        with pytest.raises(RuntimeError, match="simulated write failure"):
            await adapter.submit_transaction(
                "student-txn-rollback", "txn-key-rollback", "fp-rollback", build
            )

        assert await adapter.list_attempts("student-txn-rollback") == []
        assert await adapter.load_model("student-txn-rollback") is None
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


async def test_event_for_another_learner_does_not_deadlock_sqlite() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        question = Question(
            id="q-txn-bg",
            knowledge_id="linear_eq",
            grade=7,
            type=QuestionType.FILL,
            stem="2x=4，x=?",
            answer="2",
            accepted=["x=2"],
            explanation="两边除以 2。",
        )
        await adapter.seed_questions([question])
        orch = AgentOrchestrator(store=adapter, graph=build_junior_math_graph())

        async def also_emit_for_other(event: Event) -> None:
            await orch.event_bus.publish(
                Event(
                    type=EventType.ENCOURAGEMENT,
                    source="background",
                    learner_id="other-learner",
                    correlation_id="bg-c",
                    data={"message": "hello"},
                )
            )

        orch.event_bus.subscribe(EventType.ASSESSMENT_COMPLETE, also_emit_for_other)
        await asyncio.wait_for(
            orch.submit_question(
                "student-txn-bg",
                question.id,
                "2",
                idempotency_key="txn-key-bg",
            ),
            timeout=5,
        )
        other_events = await adapter.get_events("other-learner")
        assert len(other_events) == 1
        assert other_events[0].data["message"] == "hello"
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


async def test_store_submission_commits_attempt_model_review_and_event() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        async def build(
            model: LearnerModel, reviews: dict[str, ReviewItem]
        ) -> tuple[Attempt, list[Event]]:
            state = model.update_mastery("linear_eq", True)
            reviews["linear_eq"] = ReviewItem(
                knowledge_id="linear_eq",
                next_review=state.last_attempt,
            )
            attempt = Attempt(
                learner_id=model.learner_id,
                question_id="q-txn-2",
                knowledge_id="linear_eq",
                answer="2",
                is_correct=True,
                idempotency_key="txn-key-2",
                request_fingerprint="fp-2",
            )
            event = Event(
                type=EventType.ASSESSMENT_COMPLETE,
                source="test",
                learner_id=model.learner_id,
                correlation_id="c-txn-2",
                data={"knowledge_id": "linear_eq"},
            )
            return attempt, [event]

        attempt, events, replayed = await adapter.submit_transaction(
            "student-txn-2", "txn-key-2", "fp-2", build
        )
        assert replayed is False
        assert len(events) == 1
        assert await adapter.list_attempts("student-txn-2") == [attempt]
        loaded = await adapter.load_model("student-txn-2")
        assert loaded is not None
        assert loaded.get_state("linear_eq").attempts == 1
        assert (await adapter.load_reviews("student-txn-2"))["linear_eq"].next_review
        assert len(await adapter.get_events("student-txn-2")) == 1

        async def should_not_run(
            model: LearnerModel, reviews: dict[str, ReviewItem]
        ) -> tuple[Attempt, list[Event]]:
            raise AssertionError("build must not run for idempotent replay")

        replay_attempt, replay_events, replayed = await adapter.submit_transaction(
            "student-txn-2", "txn-key-2", "fp-2", should_not_run
        )
        assert replayed is True
        assert replay_attempt.id == attempt.id
        assert replay_events == []
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)
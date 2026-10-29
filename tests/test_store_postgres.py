"""PostgreSQL 集成测试（需要真实库 + asyncpg）。

运行方式：
    $env:TEST_DATABASE_URL = "postgresql://multi_agent:multi_agent_dev@127.0.0.1:5432/multi_agent"
    pytest tests/test_store_postgres.py -q

没有设置 TEST_DATABASE_URL 或没装 asyncpg 时自动跳过，不影响 SQLite 单元测试。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.accounts import Role
from app.core.content import Attempt, Question, QuestionType
from app.core.errors import IdempotencyConflictError
from app.core.events import Event, EventType
from app.core.learner_model import LearnerModel
from app.core.spaced_repetition import ReviewItem
from app.store_postgres import PostgresStore

DSN = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN or importlib.util.find_spec("asyncpg") is None,
    reason="set TEST_DATABASE_URL and install asyncpg to run postgres tests",
)


async def test_postgres_roundtrip_and_optimistic_lock() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    learner_id = f"pg-{uuid.uuid4().hex[:8]}"
    try:
        model = LearnerModel(learner_id)
        model.update_mastery("arithmetic", True)
        await store.save_model(model)

        loaded = await store.load_model(learner_id)
        assert loaded is not None
        assert loaded.get_state("arithmetic").attempts == 1

        state = model.get_state("arithmetic")
        version = await store.get_version(learner_id, "arithmetic")
        assert version == 1
        assert await store.compare_and_swap(learner_id, state, version) is True
        assert await store.compare_and_swap(learner_id, state, version) is False
        assert await store.get_version(learner_id, "arithmetic") == 2
    finally:
        await store.close()


async def test_postgres_event_append_is_idempotent() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    event = Event(
        type=EventType.ENCOURAGEMENT,
        source="test",
        learner_id=f"pg-{uuid.uuid4().hex[:8]}",
        data={"a": 1},
    )
    try:
        await store.append_event(event)
        await store.append_event(event)
        async with store.pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM events WHERE id=$1", event.id
            )
        assert count == 1
    finally:
        await store.close()


async def test_postgres_reviews_roundtrip() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    learner_id = f"pg-{uuid.uuid4().hex[:8]}"
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
    try:
        await store.save_model(LearnerModel(learner_id))
        await store.save_reviews(learner_id, {"arithmetic": item})
        restored = (await store.load_reviews(learner_id))["arithmetic"]
        assert restored.interval_days == 6
        assert restored.repetition == 2
        assert restored.total_reviews == 2
        assert restored.next_review == item.next_review
    finally:
        await store.close()


async def test_postgres_auth_tenancy_and_idempotency() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    suffix = uuid.uuid4().hex[:8]
    token_hash = f"token-{suffix}"
    now = datetime.now(timezone.utc)
    try:
        org = await store.create_organization(f"PG学校{suffix}", f"pg-school-{suffix}")
        admin = await store.create_user(
            org.id, f"admin-{suffix}@test.local", "hash", "管理员", Role.ADMIN
        )
        teacher = await store.create_user(
            org.id, f"teacher-{suffix}@test.local", "hash", "老师", Role.TEACHER
        )
        student = await store.create_user(
            org.id, f"student-{suffix}@test.local", "hash", "学生", Role.STUDENT
        )
        classroom = await store.create_class(
            org.id, "一班", f"C-{suffix}", teacher.id
        )
        await store.add_enrollment(classroom.id, student.id)

        assert await store.user_in_org(student.id, org.id) is True
        assert await store.is_teacher_of_student(teacher.id, student.id) is True
        assert await store.is_teacher_of_student(admin.id, student.id) is False
        assert [c.id for c in await store.list_classes_for_user(student)] == [
            classroom.id
        ]

        await store.create_session(token_hash, student.id, now, now + timedelta(hours=1))
        session = await store.get_session(token_hash)
        assert session is not None and session.user_id == student.id
        await store.revoke_session(token_hash)
        revoked = await store.get_session(token_hash)
        assert revoked is not None and revoked.revoked_at is not None

        attempt = Attempt(
            learner_id=student.id,
            question_id=f"q-pg-{suffix}",
            knowledge_id="linear_eq",
            answer="x=2",
            is_correct=True,
            idempotency_key=f"idem-{suffix}",
            request_fingerprint="fingerprint",
        )
        assert await store.save_attempt(attempt) is True
        assert await store.save_attempt(attempt) is False
        replay = await store.get_attempt_by_idempotency(
            student.id, f"idem-{suffix}"
        )
        assert replay is not None and replay.id == attempt.id
    finally:
        async with store.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM attempts WHERE learner_id=$1", student.id
            )
            await conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash=$1", token_hash
            )
            await conn.execute(
                "DELETE FROM enrollments WHERE class_id=$1", classroom.id
            )
            await conn.execute("DELETE FROM classes WHERE id=$1", classroom.id)
            await conn.execute("DELETE FROM users WHERE org_id=$1", org.id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", org.id)
        await store.close()


async def test_postgres_submit_transaction_replays_same_key() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    learner_id = f"pg-idem-{uuid.uuid4().hex[:8]}"
    key = f"key-{uuid.uuid4().hex[:8]}"

    async def build(model: LearnerModel, reviews: dict) -> tuple[Attempt, list[Event]]:
        model.update_mastery("arithmetic", True)
        attempt = Attempt(
            learner_id=learner_id,
            question_id="q-idem",
            knowledge_id="arithmetic",
            answer="1",
            is_correct=True,
            idempotency_key=key,
            request_fingerprint="fp-1",
        )
        event = Event(
            type=EventType.ASSESSMENT_COMPLETE,
            source="test",
            learner_id=learner_id,
            correlation_id=f"c-{uuid.uuid4().hex[:6]}",
            data={},
        )
        return attempt, [event]

    try:
        first, second = await asyncio.gather(
            store.submit_transaction(learner_id, key, "fp-1", build),
            store.submit_transaction(learner_id, key, "fp-1", build),
        )
        assert sorted([first[2], second[2]]) == [False, True]
        assert first[0].id == second[0].id
        assert len(await store.list_attempts(learner_id)) == 1
        loaded = await store.load_model(learner_id)
        assert loaded is not None
        assert loaded.get_state("arithmetic").attempts == 1

        with pytest.raises(IdempotencyConflictError):
            await store.submit_transaction(learner_id, key, "fp-2", build)
        assert len(await store.list_attempts(learner_id)) == 1
    finally:
        async with store.pool.acquire() as conn:
            await conn.execute("DELETE FROM events WHERE learner_id=$1", learner_id)
            await conn.execute("DELETE FROM attempts WHERE learner_id=$1", learner_id)
            await conn.execute("DELETE FROM learners WHERE learner_id=$1", learner_id)
        await store.close()


async def test_postgres_submit_transaction_serializes_same_learner() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    learner_id = f"pg-txn-{uuid.uuid4().hex[:8]}"
    try:
        async def build(model: LearnerModel, reviews: dict) -> tuple[Attempt, list[Event]]:
            state = model.update_mastery("arithmetic", True)
            # 放大竞态窗口：没有 learner 锁时，另一个任务会读到旧状态。
            await asyncio.sleep(0.05)
            attempt = Attempt(
                learner_id=learner_id,
                question_id=f"q-{uuid.uuid4().hex[:6]}",
                knowledge_id="arithmetic",
                answer="1",
                is_correct=True,
            )
            event = Event(
                type=EventType.ASSESSMENT_COMPLETE,
                source="test",
                learner_id=learner_id,
                correlation_id=f"c-{uuid.uuid4().hex[:6]}",
                data={"knowledge_id": "arithmetic", "attempts": state.attempts},
            )
            return attempt, [event]

        first, second = await asyncio.gather(
            store.submit_transaction(learner_id, None, None, build),
            store.submit_transaction(learner_id, None, None, build),
        )
        assert first[2] is False
        assert second[2] is False

        loaded = await store.load_model(learner_id)
        assert loaded is not None
        assert loaded.get_state("arithmetic").attempts == 2
        assert len(await store.list_attempts(learner_id)) == 2
        async with store.pool.acquire() as conn:
            event_count = await conn.fetchval(
                "SELECT COUNT(*) FROM events WHERE learner_id=$1", learner_id
            )
        assert event_count == 2
    finally:
        async with store.pool.acquire() as conn:
            await conn.execute("DELETE FROM events WHERE learner_id=$1", learner_id)
            await conn.execute("DELETE FROM attempts WHERE learner_id=$1", learner_id)
            await conn.execute("DELETE FROM learners WHERE learner_id=$1", learner_id)
        await store.close()


async def test_postgres_question_and_attempt_roundtrip() -> None:
    assert DSN
    store = await PostgresStore.connect(DSN)
    suffix = uuid.uuid4().hex[:8]
    question = Question(
        id=f"q-pg-{suffix}",
        knowledge_id="linear_eq",
        grade=7,
        type=QuestionType.FILL,
        stem="2x=4，x=?",
        answer="2",
        accepted=["x=2"],
        explanation="两边除以 2。",
    )
    attempt = Attempt(
        learner_id=f"pg-{suffix}",
        question_id=question.id,
        knowledge_id=question.knowledge_id,
        answer="x=2",
        is_correct=True,
        time_spent_seconds=3.5,
    )
    try:
        await store.seed_questions([question])
        await store.seed_questions([question])
        loaded = await store.get_question(question.id)
        assert loaded is not None
        assert loaded.answer == "2"
        assert loaded.accepted == ["x=2"]
        assert (await store.list_questions(knowledge_id="linear_eq"))[0].id

        await store.save_attempt(attempt)
        attempts = await store.list_attempts(attempt.learner_id)
        assert len(attempts) == 1
        assert attempts[0].is_correct is True
        assert attempts[0].time_spent_seconds == 3.5
    finally:
        await store.close()
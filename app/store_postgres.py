"""PostgreSQL 持久化实现（asyncpg）。

和 `app/store.py` 的 SQLite 版保持同一套语义：
- learners / knowledge_states / review_items / events 四张表；
- knowledge_states.version 做乐观锁；
- events 以 id 为主键，重复写入自动忽略（至少一次投递，幂等消费）。

启动时可以通过 `PostgresStore.connect()` 自动建表；生产环境建议把
`SCHEMA_SQL` 交给 Alembic / yoyo 之类的迁移工具执行，而不是每次启动跑 DDL。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.core.accounts import Classroom, Organization, Role, SessionRecord, User
from app.core.content import Attempt, Question, QuestionType
from app.core.errors import IdempotencyConflictError
from app.core.events import Event, EventType
from app.core.learner_model import KnowledgeState, LearnerModel
from app.core.spaced_repetition import ReviewItem

try:
    import asyncpg
except ImportError:  # pragma: no cover - 没装驱动时仍允许 SQLite 模式导入本模块
    asyncpg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS learners (
    learner_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata   JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS knowledge_states (
    learner_id    TEXT NOT NULL REFERENCES learners(learner_id) ON DELETE CASCADE,
    knowledge_id  TEXT NOT NULL,
    mastery       DOUBLE PRECISION NOT NULL,
    alpha         DOUBLE PRECISION NOT NULL DEFAULT 1,
    beta          DOUBLE PRECISION NOT NULL DEFAULT 9,
    attempts      INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    streak        INTEGER NOT NULL DEFAULT 0,
    last_attempt  TIMESTAMPTZ,
    version       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (learner_id, knowledge_id)
);

CREATE TABLE IF NOT EXISTS review_items (
    learner_id      TEXT NOT NULL REFERENCES learners(learner_id) ON DELETE CASCADE,
    knowledge_id    TEXT NOT NULL,
    easiness_factor DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    interval_days   DOUBLE PRECISION NOT NULL DEFAULT 0,
    repetition      INTEGER NOT NULL DEFAULT 0,
    next_review     TIMESTAMPTZ NOT NULL,
    last_review     TIMESTAMPTZ,
    total_reviews   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (learner_id, knowledge_id)
);

CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    source         TEXT NOT NULL,
    learner_id     TEXT NOT NULL,
    correlation_id TEXT,
    timestamp      TIMESTAMPTZ NOT NULL,
    data           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_events_learner ON events(learner_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_corr ON events(correlation_id);

CREATE TABLE IF NOT EXISTS questions (
    id           TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    grade        INTEGER NOT NULL,
    type         TEXT NOT NULL,
    stem         TEXT NOT NULL,
    options      JSONB NOT NULL DEFAULT '[]'::jsonb,
    answer       TEXT NOT NULL,
    accepted     JSONB NOT NULL DEFAULT '[]'::jsonb,
    explanation  TEXT NOT NULL DEFAULT '',
    difficulty   INTEGER NOT NULL DEFAULT 2,
    source       TEXT NOT NULL DEFAULT 'seed',
    version      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_questions_knowledge ON questions(knowledge_id, grade);

CREATE TABLE IF NOT EXISTS attempts (
    id                 TEXT PRIMARY KEY,
    learner_id         TEXT NOT NULL,
    question_id        TEXT NOT NULL,
    knowledge_id       TEXT NOT NULL,
    answer             TEXT NOT NULL,
    is_correct         BOOLEAN NOT NULL,
    time_spent_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL,
    idempotency_key    TEXT,
    request_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_learner ON attempts(learner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_idem
    ON attempts(learner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS organizations (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL,
    UNIQUE (org_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS classes (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL,
    name       TEXT NOT NULL,
    code       TEXT NOT NULL,
    teacher_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (org_id, code)
);

CREATE TABLE IF NOT EXISTS enrollments (
    class_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (class_id, user_id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
"""


def _utc(value: datetime | None) -> datetime | None:
    """asyncpg 对 TIMESTAMPTZ 建议传 aware datetime；naive 按 UTC 处理。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def advisory_key(learner_id: str) -> int:
    """稳定的 64-bit signed key，供 PostgreSQL advisory lock 使用。"""
    digest = hashlib.sha256(learner_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PostgresStore:
    name = "postgres"

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    async def connect(
        cls,
        dsn: str,
        min_size: int = 1,
        max_size: int = 10,
        *,
        apply_schema: bool = True,
    ) -> "PostgresStore":
        if asyncpg is None:
            raise RuntimeError(
                "asyncpg is not installed. Run: pip install -r requirements.txt"
            )
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
        )
        store = cls(pool)
        if apply_schema:
            await store.apply_schema()
        logger.info("postgres connected (pool=%s..%s)", min_size, max_size)
        return store

    async def apply_schema(self) -> None:
        await self.pool.execute(SCHEMA_SQL)
        # 兼容已有 attempts 表（2026-10 之前的库没有幂等列）。
        await self.pool.execute(
            "ALTER TABLE attempts ADD COLUMN IF NOT EXISTS idempotency_key TEXT"
        )
        await self.pool.execute(
            "ALTER TABLE attempts ADD COLUMN IF NOT EXISTS request_fingerprint TEXT"
        )
        await self.pool.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_idem
            ON attempts(learner_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )

    async def close(self) -> None:
        await self.pool.close()

    async def healthcheck(self) -> bool:
        async with self.pool.acquire() as conn:
            # 只 ping 不够：迁移没跑完时数据库可达但不能接流量。
            return bool(
                await conn.fetchval(
                    "SELECT to_regclass('public.attempts') IS NOT NULL"
                )
            )

    # ── 学习者 ──
    async def save_model(self, model: LearnerModel) -> None:
        rows = [
            (
                model.learner_id,
                state.knowledge_id,
                state.mastery,
                state.alpha,
                state.beta,
                state.attempts,
                state.correct_count,
                state.streak,
                _utc(state.last_attempt),
            )
            for state in model.knowledge_states.values()
        ]
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO learners(learner_id, metadata)
                    VALUES($1, $2::jsonb)
                    ON CONFLICT (learner_id) DO UPDATE
                    SET metadata = excluded.metadata
                    """,
                    model.learner_id,
                    json.dumps(model.metadata, ensure_ascii=False, default=str),
                )
                if rows:
                    await conn.executemany(
                        """
                        INSERT INTO knowledge_states
                            (learner_id, knowledge_id, mastery, alpha, beta, attempts,
                             correct_count, streak, last_attempt, version)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,1)
                        ON CONFLICT (learner_id, knowledge_id) DO UPDATE SET
                            mastery=excluded.mastery,
                            alpha=excluded.alpha,
                            beta=excluded.beta,
                            attempts=excluded.attempts,
                            correct_count=excluded.correct_count,
                            streak=excluded.streak,
                            last_attempt=excluded.last_attempt,
                            version=knowledge_states.version+1
                        """,
                        rows,
                    )

    async def load_model(self, learner_id: str) -> LearnerModel | None:
        async with self.pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM learners WHERE learner_id=$1", learner_id
            )
            if not exists:
                return None
            rows = await conn.fetch(
                "SELECT * FROM knowledge_states WHERE learner_id=$1", learner_id
            )

        model = LearnerModel(learner_id)
        for row in rows:
            model.knowledge_states[row["knowledge_id"]] = KnowledgeState(
                knowledge_id=row["knowledge_id"],
                mastery=row["mastery"],
                alpha=row["alpha"],
                beta=row["beta"],
                attempts=row["attempts"],
                correct_count=row["correct_count"],
                streak=row["streak"],
                last_attempt=row["last_attempt"],
            )
        return model

    async def _load_model_conn(
        self, conn: Any, learner_id: str
    ) -> LearnerModel | None:
        exists = await conn.fetchval(
            "SELECT 1 FROM learners WHERE learner_id=$1", learner_id
        )
        if not exists:
            return None
        rows = await conn.fetch(
            "SELECT * FROM knowledge_states WHERE learner_id=$1", learner_id
        )
        model = LearnerModel(learner_id)
        for row in rows:
            model.knowledge_states[row["knowledge_id"]] = KnowledgeState(
                knowledge_id=row["knowledge_id"],
                mastery=row["mastery"],
                alpha=row["alpha"],
                beta=row["beta"],
                attempts=row["attempts"],
                correct_count=row["correct_count"],
                streak=row["streak"],
                last_attempt=row["last_attempt"],
            )
        return model

    async def _load_reviews_conn(
        self, conn: Any, learner_id: str
    ) -> dict[str, ReviewItem]:
        rows = await conn.fetch(
            "SELECT * FROM review_items WHERE learner_id=$1", learner_id
        )
        result: dict[str, ReviewItem] = {}
        for row in rows:
            next_review = row["next_review"]
            last_review = row["last_review"]
            if next_review is not None and next_review.tzinfo is not None:
                next_review = next_review.replace(tzinfo=None)
            if last_review is not None and last_review.tzinfo is not None:
                last_review = last_review.replace(tzinfo=None)
            result[row["knowledge_id"]] = ReviewItem(
                knowledge_id=row["knowledge_id"],
                easiness_factor=row["easiness_factor"],
                interval_days=row["interval_days"],
                repetition=row["repetition"],
                next_review=next_review,
                last_review=last_review,
                total_reviews=row["total_reviews"],
            )
        return result

    async def _save_model_conn(self, conn: Any, model: LearnerModel) -> None:
        rows = [
            (
                model.learner_id,
                state.knowledge_id,
                state.mastery,
                state.alpha,
                state.beta,
                state.attempts,
                state.correct_count,
                state.streak,
                _utc(state.last_attempt),
            )
            for state in model.knowledge_states.values()
        ]
        await conn.execute(
            """
            INSERT INTO learners(learner_id, metadata)
            VALUES($1, $2::jsonb)
            ON CONFLICT (learner_id) DO UPDATE
            SET metadata = excluded.metadata
            """,
            model.learner_id,
            json.dumps(model.metadata, ensure_ascii=False, default=str),
        )
        if rows:
            await conn.executemany(
                """
                INSERT INTO knowledge_states
                    (learner_id, knowledge_id, mastery, alpha, beta, attempts,
                     correct_count, streak, last_attempt, version)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,1)
                ON CONFLICT (learner_id, knowledge_id) DO UPDATE SET
                    mastery=excluded.mastery,
                    alpha=excluded.alpha,
                    beta=excluded.beta,
                    attempts=excluded.attempts,
                    correct_count=excluded.correct_count,
                    streak=excluded.streak,
                    last_attempt=excluded.last_attempt,
                    version=knowledge_states.version+1
                """,
                rows,
            )

    async def _save_reviews_conn(
        self, conn: Any, learner_id: str, items: dict[str, ReviewItem]
    ) -> None:
        await conn.execute("DELETE FROM review_items WHERE learner_id=$1", learner_id)
        if items:
            await conn.executemany(
                """
                INSERT INTO review_items
                    (learner_id, knowledge_id, easiness_factor, interval_days,
                     repetition, next_review, last_review, total_reviews)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                """,
                [
                    (
                        learner_id,
                        item.knowledge_id,
                        item.easiness_factor,
                        item.interval_days,
                        item.repetition,
                        _utc(item.next_review),
                        _utc(item.last_review),
                        item.total_reviews,
                    )
                    for item in items.values()
                ],
            )

    async def _append_events_conn(self, conn: Any, events: list[Event]) -> None:
        if not events:
            return
        await conn.executemany(
            """
            INSERT INTO events
                (id, type, source, learner_id, correlation_id, timestamp, data)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                (
                    event.id,
                    event.type.value,
                    event.source,
                    event.learner_id,
                    event.correlation_id,
                    _utc(event.timestamp),
                    json.dumps(event.data, ensure_ascii=False, default=str),
                )
                for event in events
            ],
        )

    async def submit_transaction(
        self,
        learner_id: str,
        idempotency_key: str | None,
        request_fingerprint: str | None,
        build: Callable[
            [LearnerModel, dict[str, ReviewItem]],
            Awaitable[tuple[Attempt, list[Event]]],
        ],
    ) -> tuple[Attempt, list[Event], bool]:
        """单事务完成幂等检查、attempt、mastery、review、events。

        `pg_advisory_xact_lock` 以 learner_id 为粒度串行化同一学生的提交；
        锁随事务自动释放，不需要额外连接。
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)", advisory_key(learner_id)
                )

                if idempotency_key:
                    row = await conn.fetchrow(
                        "SELECT * FROM attempts WHERE learner_id=$1 AND idempotency_key=$2",
                        learner_id,
                        idempotency_key,
                    )
                    if row is not None:
                        existing = self._attempt_from_row(row)
                        if existing.request_fingerprint != request_fingerprint:
                            raise IdempotencyConflictError(idempotency_key)
                        return existing, [], True

                model = await self._load_model_conn(conn, learner_id)
                if model is None:
                    model = LearnerModel(learner_id)
                reviews = await self._load_reviews_conn(conn, learner_id)
                attempt, events = await build(model, reviews)

                status = await conn.execute(
                    """
                    INSERT INTO attempts
                        (id, learner_id, question_id, knowledge_id, answer,
                         is_correct, time_spent_seconds, created_at,
                         idempotency_key, request_fingerprint)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT DO NOTHING
                    """,
                    attempt.id,
                    attempt.learner_id,
                    attempt.question_id,
                    attempt.knowledge_id,
                    attempt.answer,
                    attempt.is_correct,
                    attempt.time_spent_seconds,
                    _utc(attempt.created_at),
                    attempt.idempotency_key,
                    attempt.request_fingerprint,
                )
                if status == "INSERT 0 0":
                    if idempotency_key:
                        row = await conn.fetchrow(
                            "SELECT * FROM attempts WHERE learner_id=$1 AND idempotency_key=$2",
                            learner_id,
                            idempotency_key,
                        )
                        if row is not None:
                            existing = self._attempt_from_row(row)
                            if existing.request_fingerprint == request_fingerprint:
                                return existing, [], True
                    raise IdempotencyConflictError(idempotency_key or attempt.id)

                await self._save_model_conn(conn, model)
                await self._save_reviews_conn(conn, learner_id, reviews)
                await self._append_events_conn(conn, events)
                return attempt, events, False

    # ── 组织 / 用户 / 班级 / 会话 ──
    @staticmethod
    def _organization_from_row(row: Any) -> Organization:
        return Organization(
            id=row["id"], name=row["name"], slug=row["slug"], created_at=row["created_at"]
        )

    @staticmethod
    def _user_from_row(row: Any) -> User:
        return User(
            id=row["id"],
            org_id=row["org_id"],
            email=row["email"],
            display_name=row["display_name"],
            role=Role(row["role"]),
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _class_from_row(row: Any) -> Classroom:
        return Classroom(
            id=row["id"],
            org_id=row["org_id"],
            name=row["name"],
            code=row["code"],
            teacher_id=row["teacher_id"],
            created_at=row["created_at"],
        )

    async def count_users(self) -> int:
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM users") or 0)

    async def create_organization(self, name: str, slug: str) -> Organization:
        org = Organization(
            id=str(uuid.uuid4()), name=name, slug=slug, created_at=datetime.now(timezone.utc)
        )
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO organizations(id, name, slug, created_at) VALUES($1,$2,$3,$4)",
                    org.id,
                    org.name,
                    org.slug,
                    org.created_at,
                )
        except Exception as exc:
            if asyncpg is not None and isinstance(exc, asyncpg.UniqueViolationError):
                raise ValueError("organization slug already exists") from exc
            raise
        return org

    async def get_organization(self, org_id: str) -> Organization | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
        return self._organization_from_row(row) if row else None

    async def get_organization_by_slug(self, slug: str) -> Organization | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM organizations WHERE slug=$1", slug)
        return self._organization_from_row(row) if row else None

    async def create_user(
        self,
        org_id: str,
        email: str,
        password_hash: str,
        display_name: str,
        role: Role,
    ) -> User:
        user = User(
            id=str(uuid.uuid4()),
            org_id=org_id,
            email=email.lower().strip(),
            display_name=display_name,
            role=role,
            created_at=datetime.now(timezone.utc),
        )
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO users
                        (id, org_id, email, password_hash, display_name, role,
                         is_active, created_at)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                    """,
                    user.id,
                    user.org_id,
                    user.email,
                    password_hash,
                    user.display_name,
                    user.role.value,
                    user.is_active,
                    user.created_at,
                )
        except Exception as exc:
            if asyncpg is not None and isinstance(exc, asyncpg.UniqueViolationError):
                raise ValueError("user already exists in this organization") from exc
            raise
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
        return self._user_from_row(row) if row else None

    async def get_user_by_email(self, org_id: str, email: str) -> User | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE org_id=$1 AND email=$2",
                org_id,
                email.lower().strip(),
            )
        return self._user_from_row(row) if row else None

    async def get_user_credentials(self, org_id: str, email: str) -> tuple[User, str] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE org_id=$1 AND email=$2",
                org_id,
                email.lower().strip(),
            )
        if row is None:
            return None
        return self._user_from_row(row), row["password_hash"]

    async def list_users(self, org_id: str) -> list[User]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM users WHERE org_id=$1 ORDER BY created_at, id", org_id
            )
        return [self._user_from_row(row) for row in rows]

    async def create_class(
        self, org_id: str, name: str, code: str, teacher_id: str | None = None
    ) -> Classroom:
        classroom = Classroom(
            id=str(uuid.uuid4()),
            org_id=org_id,
            name=name,
            code=code.strip().upper(),
            teacher_id=teacher_id,
            created_at=datetime.now(timezone.utc),
        )
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO classes(id, org_id, name, code, teacher_id, created_at)
                    VALUES($1,$2,$3,$4,$5,$6)
                    """,
                    classroom.id,
                    classroom.org_id,
                    classroom.name,
                    classroom.code,
                    classroom.teacher_id,
                    classroom.created_at,
                )
        except Exception as exc:
            if asyncpg is not None and isinstance(exc, asyncpg.UniqueViolationError):
                raise ValueError("class code already exists in this organization") from exc
            raise
        return classroom

    async def get_class(self, class_id: str) -> Classroom | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM classes WHERE id=$1", class_id)
        return self._class_from_row(row) if row else None

    async def get_class_by_code(self, org_id: str, code: str) -> Classroom | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM classes WHERE org_id=$1 AND code=$2",
                org_id,
                code.strip().upper(),
            )
        return self._class_from_row(row) if row else None

    async def list_classes_for_user(self, user: User) -> list[Classroom]:
        async with self.pool.acquire() as conn:
            if user.role is Role.ADMIN:
                rows = await conn.fetch(
                    "SELECT * FROM classes WHERE org_id=$1 ORDER BY created_at, id",
                    user.org_id,
                )
            elif user.role is Role.TEACHER:
                rows = await conn.fetch(
                    "SELECT * FROM classes WHERE org_id=$1 AND teacher_id=$2 "
                    "ORDER BY created_at, id",
                    user.org_id,
                    user.id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT c.* FROM classes c
                    JOIN enrollments e ON e.class_id=c.id
                    WHERE c.org_id=$1 AND e.user_id=$2
                    ORDER BY c.created_at, c.id
                    """,
                    user.org_id,
                    user.id,
                )
        return [self._class_from_row(row) for row in rows]

    async def add_enrollment(self, class_id: str, user_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO enrollments(class_id, user_id, created_at)
                VALUES($1,$2,$3)
                ON CONFLICT (class_id, user_id) DO NOTHING
                """,
                class_id,
                user_id,
                datetime.now(timezone.utc),
            )

    async def list_class_students(self, class_id: str) -> list[User]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.* FROM users u
                JOIN enrollments e ON e.user_id=u.id
                WHERE e.class_id=$1
                ORDER BY u.display_name, u.id
                """,
                class_id,
            )
        return [self._user_from_row(row) for row in rows]

    async def user_in_org(self, user_id: str, org_id: str) -> bool:
        async with self.pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM users WHERE id=$1 AND org_id=$2", user_id, org_id
                )
            )

    async def is_teacher_of_student(self, teacher_id: str, student_id: str) -> bool:
        async with self.pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    """
                    SELECT 1 FROM classes c
                    JOIN enrollments e ON e.class_id=c.id
                    WHERE c.teacher_id=$1 AND e.user_id=$2
                    """,
                    teacher_id,
                    student_id,
                )
            )

    # ── 会话 ──
    async def create_session(
        self, token_hash: str, user_id: str, created_at: datetime, expires_at: datetime
    ) -> SessionRecord:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
                VALUES($1,$2,$3,$4)
                """,
                token_hash,
                user_id,
                _utc(created_at),
                _utc(expires_at),
            )
        return SessionRecord(
            token_hash=token_hash,
            user_id=user_id,
            created_at=created_at,
            expires_at=expires_at,
        )

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM auth_sessions WHERE token_hash=$1", token_hash
            )
        if row is None:
            return None
        return SessionRecord(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )

    async def revoke_session(self, token_hash: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE auth_sessions SET revoked_at=$1 WHERE token_hash=$2",
                datetime.now(timezone.utc),
                token_hash,
            )

    async def save_reviews(self, learner_id: str, items: dict[str, ReviewItem]) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM review_items WHERE learner_id=$1", learner_id
                )
                if items:
                    await conn.executemany(
                        """
                        INSERT INTO review_items
                            (learner_id, knowledge_id, easiness_factor, interval_days,
                             repetition, next_review, last_review, total_reviews)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                        """,
                        [
                            (
                                learner_id,
                                item.knowledge_id,
                                item.easiness_factor,
                                item.interval_days,
                                item.repetition,
                                _utc(item.next_review),
                                _utc(item.last_review),
                                item.total_reviews,
                            )
                            for item in items.values()
                        ],
                    )

    async def load_reviews(self, learner_id: str) -> dict[str, ReviewItem]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM review_items WHERE learner_id=$1", learner_id
            )
        result: dict[str, ReviewItem] = {}
        for row in rows:
            next_review = row["next_review"]
            last_review = row["last_review"]
            # Curriculum 的时钟是 naive datetime；去掉 tzinfo 保持同一套墙钟时间。
            if next_review is not None and next_review.tzinfo is not None:
                next_review = next_review.replace(tzinfo=None)
            if last_review is not None and last_review.tzinfo is not None:
                last_review = last_review.replace(tzinfo=None)
            result[row["knowledge_id"]] = ReviewItem(
                knowledge_id=row["knowledge_id"],
                easiness_factor=row["easiness_factor"],
                interval_days=row["interval_days"],
                repetition=row["repetition"],
                next_review=next_review,
                last_review=last_review,
                total_reviews=row["total_reviews"],
            )
        return result

    async def get_version(self, learner_id: str, knowledge_id: str) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT version FROM knowledge_states
                WHERE learner_id=$1 AND knowledge_id=$2
                """,
                learner_id,
                knowledge_id,
            )
        return int(value or 0)

    async def compare_and_swap(
        self, learner_id: str, state: KnowledgeState, expected_version: int
    ) -> bool:
        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE knowledge_states
                SET mastery=$1, alpha=$2, beta=$3, attempts=$4, correct_count=$5,
                    streak=$6, last_attempt=$7, version=version+1
                WHERE learner_id=$8 AND knowledge_id=$9 AND version=$10
                """,
                state.mastery,
                state.alpha,
                state.beta,
                state.attempts,
                state.correct_count,
                state.streak,
                _utc(state.last_attempt),
                learner_id,
                state.knowledge_id,
                expected_version,
            )
        return status == "UPDATE 1"

    # ── 题库与答题记录 ──
    async def seed_questions(self, questions: list[Question]) -> int:
        rows = [
            (
                question.id,
                question.knowledge_id,
                question.grade,
                question.type.value,
                question.stem,
                json.dumps(question.options, ensure_ascii=False),
                question.answer,
                json.dumps(question.accepted, ensure_ascii=False),
                question.explanation,
                question.difficulty,
                question.source,
                question.version,
            )
            for question in questions
        ]
        if not rows:
            return 0
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO questions
                        (id, knowledge_id, grade, type, stem, options, answer,
                         accepted, explanation, difficulty, source, version)
                    VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8::jsonb,$9,$10,$11,$12)
                    ON CONFLICT (id) DO UPDATE SET
                        knowledge_id=excluded.knowledge_id,
                        grade=excluded.grade,
                        type=excluded.type,
                        stem=excluded.stem,
                        options=excluded.options,
                        answer=excluded.answer,
                        accepted=excluded.accepted,
                        explanation=excluded.explanation,
                        difficulty=excluded.difficulty,
                        source=excluded.source,
                        version=excluded.version
                    """,
                    rows,
                )
        return len(rows)

    @staticmethod
    def _question_from_row(row: Any) -> Question:
        options = row["options"]
        accepted = row["accepted"]
        if isinstance(options, str):
            options = json.loads(options)
        if isinstance(accepted, str):
            accepted = json.loads(accepted)
        return Question(
            id=row["id"],
            knowledge_id=row["knowledge_id"],
            grade=row["grade"],
            type=QuestionType(row["type"]),
            stem=row["stem"],
            options=list(options or []),
            answer=row["answer"],
            accepted=list(accepted or []),
            explanation=row["explanation"],
            difficulty=row["difficulty"],
            source=row["source"],
            version=row["version"],
        )

    async def get_question(self, question_id: str) -> Question | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM questions WHERE id=$1", question_id
            )
        return self._question_from_row(row) if row else None

    async def list_questions(
        self,
        knowledge_id: str | None = None,
        grade: int | None = None,
        limit: int = 100,
    ) -> list[Question]:
        clauses: list[str] = []
        params: list[Any] = []
        if knowledge_id:
            params.append(knowledge_id)
            clauses.append(f"knowledge_id=${len(params)}")
        if grade is not None:
            params.append(grade)
            clauses.append(f"grade=${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM questions {where} "
                f"ORDER BY grade, difficulty, id LIMIT ${len(params)}",
                *params,
            )
        return [self._question_from_row(row) for row in rows]

    async def save_attempt(self, attempt: Attempt) -> bool:
        """写入成功返回 True；幂等键冲突返回 False。"""
        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                INSERT INTO attempts
                    (id, learner_id, question_id, knowledge_id, answer,
                     is_correct, time_spent_seconds, created_at,
                     idempotency_key, request_fingerprint)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT DO NOTHING
                """,
                attempt.id,
                attempt.learner_id,
                attempt.question_id,
                attempt.knowledge_id,
                attempt.answer,
                attempt.is_correct,
                attempt.time_spent_seconds,
                _utc(attempt.created_at),
                attempt.idempotency_key,
                attempt.request_fingerprint,
            )
        return status == "INSERT 0 1"

    @staticmethod
    def _attempt_from_row(row: Any) -> Attempt:
        return Attempt(
            id=row["id"],
            learner_id=row["learner_id"],
            question_id=row["question_id"],
            knowledge_id=row["knowledge_id"],
            answer=row["answer"],
            is_correct=bool(row["is_correct"]),
            time_spent_seconds=row["time_spent_seconds"],
            created_at=row["created_at"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
        )

    async def get_attempt_by_idempotency(
        self, learner_id: str, idempotency_key: str
    ) -> Attempt | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM attempts WHERE learner_id=$1 AND idempotency_key=$2",
                learner_id,
                idempotency_key,
            )
        return self._attempt_from_row(row) if row else None

    async def list_attempts(self, learner_id: str, limit: int = 50) -> list[Attempt]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM attempts
                WHERE learner_id=$1
                ORDER BY created_at DESC, id DESC
                LIMIT $2
                """,
                learner_id,
                max(1, min(limit, 500)),
            )
        return [self._attempt_from_row(row) for row in rows]

    # ── 事件 ──
    async def append_event(self, event: Event) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO events
                    (id, type, source, learner_id, correlation_id, timestamp, data)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                event.id,
                event.type.value,
                event.source,
                event.learner_id,
                event.correlation_id,
                _utc(event.timestamp),
                json.dumps(event.data, ensure_ascii=False, default=str),
            )

    async def count_events(self) -> int:
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM events") or 0)

    async def get_events(self, learner_id: str, limit: int = 50) -> list[Event]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM events
                WHERE learner_id=$1
                ORDER BY timestamp DESC, id DESC
                LIMIT $2
                """,
                learner_id,
                max(1, limit),
            )
        events: list[Event] = []
        for row in reversed(rows):
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            events.append(
                Event(
                    id=row["id"],
                    type=EventType(row["type"]),
                    source=row["source"],
                    learner_id=row["learner_id"],
                    correlation_id=row["correlation_id"],
                    timestamp=row["timestamp"],
                    data=data,
                )
            )
        return events
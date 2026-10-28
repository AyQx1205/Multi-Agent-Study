"""PostgreSQL 持久化实现（asyncpg）。

和 `app/store.py` 的 SQLite 版保持同一套语义：
- learners / knowledge_states / review_items / events 四张表；
- knowledge_states.version 做乐观锁；
- events 以 id 为主键，重复写入自动忽略（至少一次投递，幂等消费）。

启动时可以通过 `PostgresStore.connect()` 自动建表；生产环境建议把
`SCHEMA_SQL` 交给 Alembic / yoyo 之类的迁移工具执行，而不是每次启动跑 DDL。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.events import Event, EventType
from app.core.learner_model import KnowledgeState, LearnerModel

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
"""


def _utc(value: datetime | None) -> datetime | None:
    """asyncpg 对 TIMESTAMPTZ 建议传 aware datetime；naive 按 UTC 处理。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


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

    async def close(self) -> None:
        await self.pool.close()

    async def healthcheck(self) -> bool:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1

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
                            last_attempt=excluded.last_attempt
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
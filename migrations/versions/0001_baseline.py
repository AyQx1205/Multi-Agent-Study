"""baseline: learners / knowledge / events / questions / attempts

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _syntax() -> tuple[str, str, str]:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        return "JSONB", "TIMESTAMPTZ", "BOOLEAN"
    return "TEXT", "TEXT", "INTEGER"


def upgrade() -> None:
    json_type, ts_type, bool_type = _syntax()
    empty_json = "'{}'::jsonb" if json_type == "JSONB" else "'{}'"
    empty_array = "'[]'::jsonb" if json_type == "JSONB" else "'[]'"

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS learners (
            learner_id TEXT PRIMARY KEY,
            created_at {ts_type} NOT NULL,
            metadata   {json_type} NOT NULL DEFAULT {empty_json}
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS knowledge_states (
            learner_id    TEXT NOT NULL,
            knowledge_id  TEXT NOT NULL,
            mastery       DOUBLE PRECISION NOT NULL,
            alpha         DOUBLE PRECISION NOT NULL DEFAULT 1,
            beta          DOUBLE PRECISION NOT NULL DEFAULT 9,
            attempts      INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            streak        INTEGER NOT NULL DEFAULT 0,
            last_attempt  {ts_type},
            version       INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (learner_id, knowledge_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS review_items (
            learner_id      TEXT NOT NULL,
            knowledge_id    TEXT NOT NULL,
            easiness_factor DOUBLE PRECISION NOT NULL DEFAULT 2.5,
            interval_days   DOUBLE PRECISION NOT NULL DEFAULT 0,
            repetition      INTEGER NOT NULL DEFAULT 0,
            next_review     {ts_type} NOT NULL,
            last_review     {ts_type},
            total_reviews   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (learner_id, knowledge_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS events (
            id             TEXT PRIMARY KEY,
            type           TEXT NOT NULL,
            source         TEXT NOT NULL,
            learner_id     TEXT NOT NULL,
            correlation_id TEXT,
            timestamp      {ts_type} NOT NULL,
            data           {json_type} NOT NULL DEFAULT {empty_json}
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_learner ON events(learner_id, timestamp)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_corr ON events(correlation_id)")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS questions (
            id           TEXT PRIMARY KEY,
            knowledge_id TEXT NOT NULL,
            grade        INTEGER NOT NULL,
            type         TEXT NOT NULL,
            stem         TEXT NOT NULL,
            options      {json_type} NOT NULL DEFAULT {empty_array},
            answer       TEXT NOT NULL,
            accepted     {json_type} NOT NULL DEFAULT {empty_array},
            explanation  TEXT NOT NULL DEFAULT '',
            difficulty   INTEGER NOT NULL DEFAULT 2,
            source       TEXT NOT NULL DEFAULT 'seed',
            version      INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_knowledge "
        "ON questions(knowledge_id, grade)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS attempts (
            id                 TEXT PRIMARY KEY,
            learner_id         TEXT NOT NULL,
            question_id        TEXT NOT NULL,
            knowledge_id       TEXT NOT NULL,
            answer             TEXT NOT NULL,
            is_correct         {bool_type} NOT NULL,
            time_spent_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at         {ts_type} NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempts_learner "
        "ON attempts(learner_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id)"
    )


def downgrade() -> None:
    # baseline 可能是已有生产库的存量结构，降级不删除业务表，避免误删数据。
    pass
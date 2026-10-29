"""auth, tenancy and submission idempotency

Revision ID: 0002_auth_tenancy
Revises: 0001_baseline
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth_tenancy"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _syntax() -> tuple[str, str, str]:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        return "TIMESTAMPTZ", "BOOLEAN", "TRUE"
    return "TEXT", "INTEGER", "1"


def upgrade() -> None:
    ts_type, bool_type, true_literal = _syntax()

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS organizations (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            slug       TEXT NOT NULL UNIQUE,
            created_at {ts_type} NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            org_id        TEXT NOT NULL,
            email         TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            role          TEXT NOT NULL,
            is_active     {bool_type} NOT NULL DEFAULT {true_literal},
            created_at    {ts_type} NOT NULL,
            UNIQUE (org_id, email)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS classes (
            id         TEXT PRIMARY KEY,
            org_id     TEXT NOT NULL,
            name       TEXT NOT NULL,
            code       TEXT NOT NULL,
            teacher_id TEXT,
            created_at {ts_type} NOT NULL,
            UNIQUE (org_id, code)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS enrollments (
            class_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            created_at {ts_type} NOT NULL,
            PRIMARY KEY (class_id, user_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            created_at {ts_type} NOT NULL,
            expires_at {ts_type} NOT NULL,
            revoked_at {ts_type}
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id)"
    )

    # attempts 的幂等列：兼容旧库，重复执行时跳过。
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("attempts")}
    if "idempotency_key" not in columns:
        op.add_column("attempts", sa.Column("idempotency_key", sa.Text(), nullable=True))
    if "request_fingerprint" not in columns:
        op.add_column(
            "attempts", sa.Column("request_fingerprint", sa.Text(), nullable=True)
        )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_idem
        ON attempts(learner_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_attempts_idem")
    op.execute("DROP TABLE IF EXISTS auth_sessions")
    op.execute("DROP TABLE IF EXISTS enrollments")
    op.execute("DROP TABLE IF EXISTS classes")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS organizations")
    # 幂等列保留，避免 SQLite 旧版本不支持 DROP COLUMN；数据可继续使用。
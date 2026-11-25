"""security hardening: ws tickets, login throttle, audit log

Revision ID: 0003_security_hardening
Revises: 0002_auth_tenancy
Create Date: 2026-10-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003_security_hardening"
down_revision: Union[str, None] = "0002_auth_tenancy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _types() -> tuple[str, str, str]:
    if op.get_bind().dialect.name == "postgresql":
        return "TIMESTAMPTZ", "JSONB", "'{}'::jsonb"
    return "TEXT", "TEXT", "'{}'"


def upgrade() -> None:
    ts_type, json_type, json_default = _types()

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS ws_tickets (
            ticket_hash TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            created_at  {ts_type} NOT NULL,
            expires_at  {ts_type} NOT NULL,
            used_at     {ts_type}
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ws_tickets_user ON ws_tickets(user_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS login_throttle (
            throttle_key TEXT PRIMARY KEY,
            failures     INTEGER NOT NULL DEFAULT 0,
            window_start {ts_type} NOT NULL,
            locked_until {ts_type}
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id            TEXT PRIMARY KEY,
            org_id        TEXT,
            actor_user_id TEXT,
            action        TEXT NOT NULL,
            target_type   TEXT,
            target_id     TEXT,
            ip            TEXT,
            user_agent    TEXT,
            metadata      {json_type} NOT NULL DEFAULT {json_default},
            created_at    {ts_type} NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_org_created "
        "ON audit_logs(org_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_org_created")
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS login_throttle")
    op.execute("DROP INDEX IF EXISTS idx_ws_tickets_user")
    op.execute("DROP TABLE IF EXISTS ws_tickets")
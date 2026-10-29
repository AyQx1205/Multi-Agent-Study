"""Alembic 迁移冒烟测试：空库升级 + 旧 attempts 表升级。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("alembic")

ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(db_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = ""
    env["SQLITE_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _temp_db() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-migration-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


def test_upgrade_fresh_database() -> None:
    db = _temp_db()
    try:
        _run_alembic(db)
        with closing(sqlite3.connect(db)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert {"organizations", "users", "classes", "enrollments", "auth_sessions"} <= tables
        assert {"idempotency_key", "request_fingerprint"} <= columns
        assert version == ("0002_auth_tenancy",)
    finally:
        db.unlink(missing_ok=True)


def test_migrated_database_starts_without_auto_ddl(monkeypatch) -> None:
    from app.api.main import app

    db = _temp_db()
    try:
        _run_alembic(db)
        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("SQLITE_PATH", str(db))
        monkeypatch.setenv("AUTO_APPLY_SCHEMA", "false")

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            response = client.post(
                "/auth/bootstrap",
                json={
                    "org_name": "迁移验收学校",
                    "org_slug": "migrated-school",
                    "admin_email": "admin@migrated.local",
                    "password": "migrated-pass-123",
                },
            )
            assert response.status_code == 201, response.text
    finally:
        db.unlink(missing_ok=True)


def test_upgrade_legacy_attempts_table() -> None:
    db = _temp_db()
    try:
        with closing(sqlite3.connect(db)) as conn:
            conn.execute(
                """
                CREATE TABLE attempts (
                    id TEXT PRIMARY KEY,
                    learner_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    knowledge_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    is_correct INTEGER NOT NULL,
                    time_spent_seconds REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

        _run_alembic(db)
        with closing(sqlite3.connect(db)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
        assert {"idempotency_key", "request_fingerprint"} <= columns
    finally:
        db.unlink(missing_ok=True)
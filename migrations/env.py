"""Alembic 环境：SQLite 走同步驱动，PostgreSQL 走 asyncpg。"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # 让 alembic 也能读取项目根目录的 .env
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 不依赖 app.models 的 metadata；迁移里的 DDL 是显式 SQL。
target_metadata = None


def _database_url() -> str:
    """优先 DATABASE_URL；否则和 app/config.py 一样回退 SQLite。"""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        sqlite_path = os.getenv("SQLITE_PATH", "knowledge.db")
        if sqlite_path == ":memory:":
            return "sqlite://"
        return f"sqlite:///{Path(sqlite_path).resolve().as_posix()}"
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    config.set_main_option("sqlalchemy.url", url)

    if url.startswith("postgresql+asyncpg://"):
        async def _run_async() -> None:
            engine = create_async_engine(url, poolclass=pool.NullPool)
            async with engine.connect() as connection:
                await connection.run_sync(_do_run_migrations)
            await engine.dispose()

        asyncio.run(_run_async())
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
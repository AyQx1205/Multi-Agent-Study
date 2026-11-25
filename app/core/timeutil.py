"""统一时间口径。

规则：进程内所有时间戳一律是 **aware UTC**。

历史数据（早期 SQLite 行里没有偏移量的 ISO 字符串、测试注入的 naive 时钟）
按 UTC 解释，交给 `ensure_utc` 归一化，避免出现「同一事件两个时间」或
SQLite / PostgreSQL 两种后端返回不同 `isoformat()` 的问题。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """当前时间（aware UTC）。替代 `datetime.now()`。"""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """把 datetime 归一化成 aware UTC；naive 视为 UTC。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_iso_utc(value: str | None) -> datetime | None:
    """解析存储里的 ISO 字符串，结果一定是 aware UTC。"""
    if not value:
        return None
    return ensure_utc(datetime.fromisoformat(value))
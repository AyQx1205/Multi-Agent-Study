"""时间口径回归测试。

历史缺陷：SQLite 里写的是 naive 本地时间，PostgreSQL 侧 `_utc()` 把 naive
直接标成 UTC，于是同一个事件「内存里一个时间、库里另一个时间」，两个后端
返回的 `isoformat()` 也不一致。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.content import Attempt
from app.core.events import Event, EventType
from app.core.spaced_repetition import ReviewItem, SpacedRepetition
from app.core.timeutil import ensure_utc, utcnow
from app.store import SqliteStore


def _temp_db() -> Path:
    """不用 pytest 的 tmp_path：它在受限环境里可能建不出目录。"""
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-tz-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


def test_utcnow_is_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_ensure_utc_treats_naive_as_utc() -> None:
    naive = datetime(2026, 1, 1, 10, 0, 0)
    assert ensure_utc(naive) == datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_models_normalize_on_construction() -> None:
    naive = datetime(2026, 1, 1, 10, 0, 0)
    event = Event(
        type=EventType.STUDENT_MESSAGE, source="t", learner_id="s1", timestamp=naive
    )
    attempt = Attempt(
        learner_id="s1",
        question_id="q",
        knowledge_id="k",
        answer="1",
        is_correct=True,
        created_at=naive,
    )
    review = ReviewItem(knowledge_id="k", next_review=naive, last_review=naive)
    assert event.timestamp.tzinfo is timezone.utc
    assert attempt.created_at.tzinfo is timezone.utc
    assert review.next_review.tzinfo is timezone.utc


def test_sqlite_event_roundtrip_keeps_the_same_instant() -> None:
    path = _temp_db()
    store = SqliteStore(path)
    try:
        event = Event(type=EventType.STUDENT_SUBMISSION, source="api", learner_id="s1")
        store.append_event(event)
        loaded = store.get_events("s1")[0]
        # 同一个事件：内存里的时间戳和从库里读出来的必须完全相等。
        assert loaded.timestamp == event.timestamp
        assert loaded.timestamp.tzinfo is not None
    finally:
        store.close()
        path.unlink(missing_ok=True)


def test_legacy_naive_row_is_read_as_utc() -> None:
    """早期数据是没带偏移量的字符串，读出来要按 UTC 解释而不是崩溃。"""
    path = _temp_db()
    store = SqliteStore(path)
    try:
        store.append_event(
            Event(type=EventType.STUDENT_SUBMISSION, source="api", learner_id="s1")
        )
    finally:
        store.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE events SET timestamp=? WHERE learner_id='s1'",
            (datetime(2026, 1, 1, 10, 0, 0).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    store = SqliteStore(path)
    try:
        loaded = store.get_events("s1")[0]
        assert loaded.timestamp == datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    finally:
        store.close()
        path.unlink(missing_ok=True)


def test_review_schedule_compares_aware_and_naive_without_type_error() -> None:
    """naive 时钟（测试注入 / 旧调用方）与库里读出的 aware 时间不能混着炸。"""
    item = ReviewItem(
        knowledge_id="k",
        next_review=datetime(2026, 1, 1, 9, 0, 0),
        last_review=datetime(2026, 1, 1, 8, 0, 0),
    )
    sr = SpacedRepetition()
    naive_now = datetime(2026, 1, 1, 10, 0, 0)
    assert item.is_due(naive_now) is True
    assert sr.get_due_items([item], now=naive_now) == [item]
    assert sr.get_study_schedule([item], days_ahead=3, now=naive_now)
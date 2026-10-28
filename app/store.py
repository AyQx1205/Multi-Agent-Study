"""SQLite 持久化 —— 最小可用的落库 + 乐观锁。

对应教程：04-进阶/14-持久化与并发一致性.md

设计要点：
- 只提供 load/save/CAS/append_event，不含任何业务判断；
- knowledge_states 带 version，compare_and_swap 用 version 做冲突检测；
- events 是 append-only，主键去重。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.core.events import Event, EventType
from app.core.learner_model import KnowledgeState, LearnerModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS learners (
    learner_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS knowledge_states (
    learner_id    TEXT NOT NULL,
    knowledge_id  TEXT NOT NULL,
    mastery       REAL NOT NULL,
    alpha         REAL NOT NULL DEFAULT 1,
    beta          REAL NOT NULL DEFAULT 9,
    attempts      INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    streak        INTEGER NOT NULL DEFAULT 0,
    last_attempt  TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (learner_id, knowledge_id)
);

CREATE TABLE IF NOT EXISTS review_items (
    learner_id      TEXT NOT NULL,
    knowledge_id    TEXT NOT NULL,
    easiness_factor REAL NOT NULL DEFAULT 2.5,
    interval_days   REAL NOT NULL DEFAULT 0,
    repetition      INTEGER NOT NULL DEFAULT 0,
    next_review     TEXT NOT NULL,
    last_review     TEXT,
    total_reviews   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (learner_id, knowledge_id)
);

CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    source         TEXT NOT NULL,
    learner_id     TEXT NOT NULL,
    correlation_id TEXT,
    timestamp      TEXT NOT NULL,
    data           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_learner ON events(learner_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_corr ON events(correlation_id);
"""


class SqliteStore:
    def __init__(self, path: str | Path = "knowledge.db") -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── 学习者 ──
    def save_model(self, model: LearnerModel) -> None:
        now = datetime.now().isoformat()
        self.conn.execute(
            "INSERT OR IGNORE INTO learners(learner_id, created_at, metadata) VALUES (?,?,?)",
            (model.learner_id, now, json.dumps(model.metadata)),
        )
        for state in model.knowledge_states.values():
            self.conn.execute(
                """
                INSERT INTO knowledge_states
                    (learner_id, knowledge_id, mastery, alpha, beta, attempts,
                     correct_count, streak, last_attempt, version)
                VALUES (?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(learner_id, knowledge_id) DO UPDATE SET
                    mastery=excluded.mastery, alpha=excluded.alpha, beta=excluded.beta,
                    attempts=excluded.attempts, correct_count=excluded.correct_count,
                    streak=excluded.streak, last_attempt=excluded.last_attempt
                """,
                (
                    model.learner_id,
                    state.knowledge_id,
                    state.mastery,
                    state.alpha,
                    state.beta,
                    state.attempts,
                    state.correct_count,
                    state.streak,
                    state.last_attempt.isoformat() if state.last_attempt else None,
                ),
            )
        self.conn.commit()

    def load_model(self, learner_id: str) -> LearnerModel | None:
        row = self.conn.execute(
            "SELECT learner_id FROM learners WHERE learner_id=?", (learner_id,)
        ).fetchone()
        if row is None:
            return None

        model = LearnerModel(learner_id)
        for state_row in self.conn.execute(
            "SELECT * FROM knowledge_states WHERE learner_id=?", (learner_id,)
        ):
            last_attempt = state_row["last_attempt"]
            model.knowledge_states[state_row["knowledge_id"]] = KnowledgeState(
                knowledge_id=state_row["knowledge_id"],
                mastery=state_row["mastery"],
                alpha=state_row["alpha"],
                beta=state_row["beta"],
                attempts=state_row["attempts"],
                correct_count=state_row["correct_count"],
                streak=state_row["streak"],
                last_attempt=datetime.fromisoformat(last_attempt) if last_attempt else None,
            )
        return model

    def get_version(self, learner_id: str, knowledge_id: str) -> int:
        row = self.conn.execute(
            "SELECT version FROM knowledge_states WHERE learner_id=? AND knowledge_id=?",
            (learner_id, knowledge_id),
        ).fetchone()
        return int(row["version"]) if row else 0

    def compare_and_swap(
        self, learner_id: str, state: KnowledgeState, expected_version: int
    ) -> bool:
        """版本匹配才写入；返回是否成功。"""
        cursor = self.conn.execute(
            """
            UPDATE knowledge_states
            SET mastery=?, alpha=?, beta=?, attempts=?, correct_count=?, streak=?,
                last_attempt=?, version=version+1
            WHERE learner_id=? AND knowledge_id=? AND version=?
            """,
            (
                state.mastery,
                state.alpha,
                state.beta,
                state.attempts,
                state.correct_count,
                state.streak,
                state.last_attempt.isoformat() if state.last_attempt else None,
                learner_id,
                state.knowledge_id,
                expected_version,
            ),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    # ── 事件 ──
    def append_event(self, event: Event) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO events(id, type, source, learner_id, correlation_id, timestamp, data)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                event.id,
                event.type.value,
                event.source,
                event.learner_id,
                event.correlation_id,
                event.timestamp.isoformat(),
                json.dumps(event.data, ensure_ascii=False, default=str),
            ),
        )
        self.conn.commit()

    def count_events(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])

    def get_events(self, learner_id: str, limit: int = 50) -> list[Event]:
        """按时间正序返回某个学习者的最近事件（重启后仍可查）。"""
        rows = self.conn.execute(
            """
            SELECT * FROM events
            WHERE learner_id=?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (learner_id, max(1, limit)),
        ).fetchall()
        events: list[Event] = []
        for row in reversed(rows):
            events.append(
                Event(
                    id=row["id"],
                    type=EventType(row["type"]),
                    source=row["source"],
                    learner_id=row["learner_id"],
                    correlation_id=row["correlation_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    data=json.loads(row["data"]),
                )
            )
        return events
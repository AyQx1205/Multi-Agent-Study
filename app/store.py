"""SQLite 持久化 —— 最小可用的落库 + 乐观锁。

对应教程：04-进阶/14-持久化与并发一致性.md

设计要点：
- 提供 load/save/CAS/append_event 以及 SM-2 复习排期的读写；
- knowledge_states 带 version，compare_and_swap 用 version 做冲突检测；
- events 是 append-only，主键去重。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.core.content import Attempt, Question, QuestionType
from app.core.events import Event, EventType
from app.core.learner_model import KnowledgeState, LearnerModel
from app.core.spaced_repetition import ReviewItem

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

CREATE TABLE IF NOT EXISTS questions (
    id           TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    grade        INTEGER NOT NULL,
    type         TEXT NOT NULL,
    stem         TEXT NOT NULL,
    options      TEXT NOT NULL DEFAULT '[]',
    answer       TEXT NOT NULL,
    accepted     TEXT NOT NULL DEFAULT '[]',
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
    is_correct         INTEGER NOT NULL,
    time_spent_seconds REAL NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_learner ON attempts(learner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
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

    # ── 复习排期（SM-2） ──
    def save_reviews(self, learner_id: str, items: dict[str, ReviewItem]) -> None:
        """整组覆盖：排期是派生状态，删除后重写比逐条 diff 简单可靠。"""
        self.conn.execute("DELETE FROM review_items WHERE learner_id=?", (learner_id,))
        for item in items.values():
            self.conn.execute(
                """
                INSERT INTO review_items
                    (learner_id, knowledge_id, easiness_factor, interval_days,
                     repetition, next_review, last_review, total_reviews)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    learner_id,
                    item.knowledge_id,
                    item.easiness_factor,
                    item.interval_days,
                    item.repetition,
                    item.next_review.isoformat(),
                    item.last_review.isoformat() if item.last_review else None,
                    item.total_reviews,
                ),
            )
        self.conn.commit()

    def load_reviews(self, learner_id: str) -> dict[str, ReviewItem]:
        rows = self.conn.execute(
            "SELECT * FROM review_items WHERE learner_id=?", (learner_id,)
        ).fetchall()
        result: dict[str, ReviewItem] = {}
        for row in rows:
            last_review = row["last_review"]
            result[row["knowledge_id"]] = ReviewItem(
                knowledge_id=row["knowledge_id"],
                easiness_factor=row["easiness_factor"],
                interval_days=row["interval_days"],
                repetition=row["repetition"],
                next_review=datetime.fromisoformat(row["next_review"]),
                last_review=datetime.fromisoformat(last_review) if last_review else None,
                total_reviews=row["total_reviews"],
            )
        return result

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

    # ── 题库与答题记录 ──
    def seed_questions(self, questions: list[Question]) -> int:
        """幂等导入种子题库；内容更新时覆盖同 ID 的题。"""
        for question in questions:
            self.conn.execute(
                """
                INSERT INTO questions
                    (id, knowledge_id, grade, type, stem, options, answer,
                     accepted, explanation, difficulty, source, version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
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
                ),
            )
        self.conn.commit()
        return len(questions)

    def _question_from_row(self, row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"],
            knowledge_id=row["knowledge_id"],
            grade=row["grade"],
            type=QuestionType(row["type"]),
            stem=row["stem"],
            options=json.loads(row["options"]),
            answer=row["answer"],
            accepted=json.loads(row["accepted"]),
            explanation=row["explanation"],
            difficulty=row["difficulty"],
            source=row["source"],
            version=row["version"],
        )

    def get_question(self, question_id: str) -> Question | None:
        row = self.conn.execute(
            "SELECT * FROM questions WHERE id=?", (question_id,)
        ).fetchone()
        return self._question_from_row(row) if row else None

    def list_questions(
        self,
        knowledge_id: str | None = None,
        grade: int | None = None,
        limit: int = 100,
    ) -> list[Question]:
        clauses: list[str] = []
        params: list[object] = []
        if knowledge_id:
            clauses.append("knowledge_id=?")
            params.append(knowledge_id)
        if grade is not None:
            clauses.append("grade=?")
            params.append(grade)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self.conn.execute(
            f"SELECT * FROM questions {where} ORDER BY grade, difficulty, id LIMIT ?",
            params,
        ).fetchall()
        return [self._question_from_row(row) for row in rows]

    def save_attempt(self, attempt: Attempt) -> None:
        self.conn.execute(
            """
            INSERT INTO attempts
                (id, learner_id, question_id, knowledge_id, answer,
                 is_correct, time_spent_seconds, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                attempt.id,
                attempt.learner_id,
                attempt.question_id,
                attempt.knowledge_id,
                attempt.answer,
                int(attempt.is_correct),
                attempt.time_spent_seconds,
                attempt.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def list_attempts(self, learner_id: str, limit: int = 50) -> list[Attempt]:
        rows = self.conn.execute(
            """
            SELECT * FROM attempts
            WHERE learner_id=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (learner_id, max(1, min(limit, 500))),
        ).fetchall()
        return [
            Attempt(
                id=row["id"],
                learner_id=row["learner_id"],
                question_id=row["question_id"],
                knowledge_id=row["knowledge_id"],
                answer=row["answer"],
                is_correct=bool(row["is_correct"]),
                time_spent_seconds=row["time_spent_seconds"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

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
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
import uuid
from datetime import datetime
from pathlib import Path

from app.core.accounts import Classroom, Organization, Role, SessionRecord, User
from app.core.content import Attempt, Question, QuestionType
from app.core.errors import IdempotencyConflictError
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
    created_at         TEXT NOT NULL,
    idempotency_key    TEXT,
    request_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_learner ON attempts(learner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_idem
    ON attempts(learner_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS organizations (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    UNIQUE (org_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS classes (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL,
    name       TEXT NOT NULL,
    code       TEXT NOT NULL,
    teacher_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (org_id, code)
);

CREATE TABLE IF NOT EXISTS enrollments (
    class_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (class_id, user_id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
"""


class SqliteStore:
    def __init__(
        self, path: str | Path = "knowledge.db", *, apply_schema: bool = True
    ) -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if apply_schema:
            self.conn.executescript(SCHEMA)
            self._ensure_columns()
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()

    def _ensure_columns(self) -> None:
        """兼容已有 attempts 表：旧库没有幂等列。"""
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(attempts)").fetchall()
        }
        if "idempotency_key" not in columns:
            self.conn.execute("ALTER TABLE attempts ADD COLUMN idempotency_key TEXT")
        if "request_fingerprint" not in columns:
            self.conn.execute("ALTER TABLE attempts ADD COLUMN request_fingerprint TEXT")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_idem
            ON attempts(learner_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )

    def close(self) -> None:
        self.conn.close()

    def healthcheck(self) -> bool:
        try:
            row = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempts'"
            ).fetchone()
            return bool(row and row[0] == 1)
        except Exception:
            return False

    # ── 组织 / 用户 / 班级 / 会话 ──
    @staticmethod
    def _organization_from_row(row: sqlite3.Row) -> Organization:
        return Organization(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            org_id=row["org_id"],
            email=row["email"],
            display_name=row["display_name"],
            role=Role(row["role"]),
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _class_from_row(row: sqlite3.Row) -> Classroom:
        return Classroom(
            id=row["id"],
            org_id=row["org_id"],
            name=row["name"],
            code=row["code"],
            teacher_id=row["teacher_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def count_users(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def create_organization(self, name: str, slug: str) -> Organization:
        org = Organization(
            id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            created_at=datetime.now(),
        )
        try:
            self.conn.execute(
                "INSERT INTO organizations(id, name, slug, created_at) VALUES (?,?,?,?)",
                (org.id, org.name, org.slug, org.created_at.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("organization slug already exists") from exc
        self.conn.commit()
        return org

    def get_organization(self, org_id: str) -> Organization | None:
        row = self.conn.execute(
            "SELECT * FROM organizations WHERE id=?", (org_id,)
        ).fetchone()
        return self._organization_from_row(row) if row else None

    def get_organization_by_slug(self, slug: str) -> Organization | None:
        row = self.conn.execute(
            "SELECT * FROM organizations WHERE slug=?", (slug,)
        ).fetchone()
        return self._organization_from_row(row) if row else None

    def create_user(
        self,
        org_id: str,
        email: str,
        password_hash: str,
        display_name: str,
        role: Role,
    ) -> User:
        user = User(
            id=str(uuid.uuid4()),
            org_id=org_id,
            email=email.lower().strip(),
            display_name=display_name,
            role=role,
            created_at=datetime.now(),
        )
        try:
            self.conn.execute(
                """
                INSERT INTO users
                    (id, org_id, email, password_hash, display_name, role, is_active, created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    user.id,
                    user.org_id,
                    user.email,
                    password_hash,
                    user.display_name,
                    user.role.value,
                    int(user.is_active),
                    user.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("user already exists in this organization") from exc
        self.conn.commit()
        return user

    def get_user_by_id(self, user_id: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_email(self, org_id: str, email: str) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE org_id=? AND email=?",
            (org_id, email.lower().strip()),
        ).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_credentials(self, org_id: str, email: str) -> tuple[User, str] | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE org_id=? AND email=?",
            (org_id, email.lower().strip()),
        ).fetchone()
        if row is None:
            return None
        return self._user_from_row(row), row["password_hash"]

    def list_users(self, org_id: str) -> list[User]:
        rows = self.conn.execute(
            "SELECT * FROM users WHERE org_id=? ORDER BY created_at, id", (org_id,)
        ).fetchall()
        return [self._user_from_row(row) for row in rows]

    def create_class(
        self, org_id: str, name: str, code: str, teacher_id: str | None = None
    ) -> Classroom:
        classroom = Classroom(
            id=str(uuid.uuid4()),
            org_id=org_id,
            name=name,
            code=code.strip().upper(),
            teacher_id=teacher_id,
            created_at=datetime.now(),
        )
        try:
            self.conn.execute(
                """
                INSERT INTO classes(id, org_id, name, code, teacher_id, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    classroom.id,
                    classroom.org_id,
                    classroom.name,
                    classroom.code,
                    classroom.teacher_id,
                    classroom.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("class code already exists in this organization") from exc
        self.conn.commit()
        return classroom

    def get_class(self, class_id: str) -> Classroom | None:
        row = self.conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        return self._class_from_row(row) if row else None

    def get_class_by_code(self, org_id: str, code: str) -> Classroom | None:
        row = self.conn.execute(
            "SELECT * FROM classes WHERE org_id=? AND code=?",
            (org_id, code.strip().upper()),
        ).fetchone()
        return self._class_from_row(row) if row else None

    def list_classes_for_user(self, user: User) -> list[Classroom]:
        if user.role is Role.ADMIN:
            rows = self.conn.execute(
                "SELECT * FROM classes WHERE org_id=? ORDER BY created_at, id",
                (user.org_id,),
            ).fetchall()
        elif user.role is Role.TEACHER:
            rows = self.conn.execute(
                "SELECT * FROM classes WHERE org_id=? AND teacher_id=? "
                "ORDER BY created_at, id",
                (user.org_id, user.id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT c.* FROM classes c
                JOIN enrollments e ON e.class_id=c.id
                WHERE c.org_id=? AND e.user_id=?
                ORDER BY c.created_at, c.id
                """,
                (user.org_id, user.id),
            ).fetchall()
        return [self._class_from_row(row) for row in rows]

    def add_enrollment(self, class_id: str, user_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO enrollments(class_id, user_id, created_at) VALUES (?,?,?)",
            (class_id, user_id, datetime.now().isoformat()),
        )
        self.conn.commit()

    def list_class_students(self, class_id: str) -> list[User]:
        rows = self.conn.execute(
            """
            SELECT u.* FROM users u
            JOIN enrollments e ON e.user_id=u.id
            WHERE e.class_id=?
            ORDER BY u.display_name, u.id
            """,
            (class_id,),
        ).fetchall()
        return [self._user_from_row(row) for row in rows]

    def user_in_org(self, user_id: str, org_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM users WHERE id=? AND org_id=?", (user_id, org_id)
            ).fetchone()
            is not None
        )

    def is_teacher_of_student(self, teacher_id: str, student_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM classes c
            JOIN enrollments e ON e.class_id=c.id
            WHERE c.teacher_id=? AND e.user_id=?
            """,
            (teacher_id, student_id),
        ).fetchone()
        return row is not None

    # ── 会话 ──
    def create_session(
        self, token_hash: str, user_id: str, created_at: datetime, expires_at: datetime
    ) -> SessionRecord:
        self.conn.execute(
            """
            INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
            VALUES (?,?,?,?)
            """,
            (token_hash, user_id, created_at.isoformat(), expires_at.isoformat()),
        )
        self.conn.commit()
        return SessionRecord(
            token_hash=token_hash,
            user_id=user_id,
            created_at=created_at,
            expires_at=expires_at,
        )

    def get_session(self, token_hash: str) -> SessionRecord | None:
        row = self.conn.execute(
            "SELECT * FROM auth_sessions WHERE token_hash=?", (token_hash,)
        ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked_at=(
                datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None
            ),
        )

    def revoke_session(self, token_hash: str) -> None:
        self.conn.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
            (datetime.now().isoformat(), token_hash),
        )
        self.conn.commit()

    # ── 学习者 ──
    def _save_model_no_commit(self, model: LearnerModel) -> None:
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
                    streak=excluded.streak, last_attempt=excluded.last_attempt,
                    version=knowledge_states.version+1
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

    def save_model(self, model: LearnerModel) -> None:
        with self.conn:
            self._save_model_no_commit(model)

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
    def _save_reviews_no_commit(
        self, learner_id: str, items: dict[str, ReviewItem]
    ) -> None:
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

    def save_reviews(self, learner_id: str, items: dict[str, ReviewItem]) -> None:
        with self.conn:
            self._save_reviews_no_commit(learner_id, items)

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

    def save_attempt(self, attempt: Attempt) -> bool:
        """写入成功返回 True；幂等键冲突返回 False（由上层读取已有记录）。"""
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO attempts
                (id, learner_id, question_id, knowledge_id, answer,
                 is_correct, time_spent_seconds, created_at,
                 idempotency_key, request_fingerprint)
            VALUES (?,?,?,?,?,?,?,?,?,?)
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
                attempt.idempotency_key,
                attempt.request_fingerprint,
            ),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> Attempt:
        return Attempt(
            id=row["id"],
            learner_id=row["learner_id"],
            question_id=row["question_id"],
            knowledge_id=row["knowledge_id"],
            answer=row["answer"],
            is_correct=bool(row["is_correct"]),
            time_spent_seconds=row["time_spent_seconds"],
            created_at=datetime.fromisoformat(row["created_at"]),
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
        )

    def get_attempt_by_idempotency(
        self, learner_id: str, idempotency_key: str
    ) -> Attempt | None:
        row = self.conn.execute(
            "SELECT * FROM attempts WHERE learner_id=? AND idempotency_key=?",
            (learner_id, idempotency_key),
        ).fetchone()
        return self._attempt_from_row(row) if row else None

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
        return [self._attempt_from_row(row) for row in rows]

    # ── 一次答题的原子提交 ──
    def begin_submission(
        self,
        learner_id: str,
        idempotency_key: str | None,
        request_fingerprint: str | None,
    ) -> tuple[Attempt | None, LearnerModel | None, dict[str, ReviewItem]]:
        """开启写事务并读取当前学习者状态。

        返回 (existing, model, reviews)：
        - existing 非空表示幂等重放，事务已回滚；
        - 否则事务保持打开，调用方完成计算后必须调用 finish/rollback。
        """
        if self.conn.in_transaction:
            self.conn.rollback()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if idempotency_key:
                existing = self.get_attempt_by_idempotency(
                    learner_id, idempotency_key
                )
                if existing is not None:
                    if existing.request_fingerprint != request_fingerprint:
                        raise IdempotencyConflictError(idempotency_key)
                    self.conn.rollback()
                    return existing, None, {}
            model = self.load_model(learner_id) or LearnerModel(learner_id)
            reviews = self.load_reviews(learner_id)
            return None, model, reviews
        except BaseException:
            self.conn.rollback()
            raise

    def finish_submission(
        self,
        attempt: Attempt,
        model: LearnerModel,
        reviews: dict[str, ReviewItem],
        events: list[Event],
    ) -> bool:
        """原子写入 attempt + mastery + review + events；冲突返回 False。"""
        try:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO attempts
                    (id, learner_id, question_id, knowledge_id, answer,
                     is_correct, time_spent_seconds, created_at,
                     idempotency_key, request_fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?)
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
                    attempt.idempotency_key,
                    attempt.request_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                self.conn.rollback()
                return False
            self._save_model_no_commit(model)
            self._save_reviews_no_commit(model.learner_id, reviews)
            self._append_events_no_commit(events)
            self.conn.commit()
            return True
        except BaseException:
            self.conn.rollback()
            raise

    def rollback_submission(self) -> None:
        if self.conn.in_transaction:
            self.conn.rollback()

    def _append_events_no_commit(self, events: list[Event]) -> None:
        for event in events:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO events
                    (id, type, source, learner_id, correlation_id, timestamp, data)
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

    # ── 事件 ──
    def append_event(self, event: Event) -> None:
        with self.conn:
            self._append_events_no_commit([event])

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
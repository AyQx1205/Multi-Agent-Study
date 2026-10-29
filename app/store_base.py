"""持久化抽象层。

- Postgres 走原生异步驱动（asyncpg）；
- SQLite 仍是同步实现，用 `SqliteStoreAdapter` 包成同一套异步接口，
  这样 orchestrator 不需要关心底层数据库；
- 测试里的 `tests/test_store.py` 继续直接使用同步的 `SqliteStore`。

对应教程：04-进阶/14-持久化与并发一致性.md
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from app.core.accounts import Classroom, Organization, Role, SessionRecord, User
from app.core.content import Attempt, Question
from app.core.errors import IdempotencyConflictError
from app.core.events import Event
from app.core.learner_model import KnowledgeState, LearnerModel
from app.core.spaced_repetition import ReviewItem
from app.store import SqliteStore


@runtime_checkable
class Store(Protocol):
    """两种后端共用的最小接口（全部异步）。"""

    name: str

    async def healthcheck(self) -> bool: ...

    async def load_model(self, learner_id: str) -> LearnerModel | None: ...

    async def save_model(self, model: LearnerModel) -> None: ...

    async def load_reviews(self, learner_id: str) -> dict[str, ReviewItem]: ...

    async def save_reviews(self, learner_id: str, items: dict[str, ReviewItem]) -> None: ...

    async def get_version(self, learner_id: str, knowledge_id: str) -> int: ...

    async def compare_and_swap(
        self, learner_id: str, state: KnowledgeState, expected_version: int
    ) -> bool: ...

    async def append_event(self, event: Event) -> None: ...

    async def count_events(self) -> int: ...

    async def get_events(self, learner_id: str, limit: int = 50) -> list[Event]: ...

    async def seed_questions(self, questions: list[Question]) -> int: ...

    async def get_question(self, question_id: str) -> Question | None: ...

    async def list_questions(
        self,
        knowledge_id: str | None = None,
        grade: int | None = None,
        limit: int = 100,
    ) -> list[Question]: ...

    async def save_attempt(self, attempt: Attempt) -> bool: ...

    async def submit_transaction(
        self,
        learner_id: str,
        idempotency_key: str | None,
        request_fingerprint: str | None,
        build: Callable[
            [LearnerModel, dict[str, ReviewItem]],
            Awaitable[tuple[Attempt, list[Event]]],
        ],
    ) -> tuple[Attempt, list[Event], bool]: ...

    async def get_attempt_by_idempotency(
        self, learner_id: str, idempotency_key: str
    ) -> Attempt | None: ...

    async def list_attempts(self, learner_id: str, limit: int = 50) -> list[Attempt]: ...

    async def count_users(self) -> int: ...

    async def create_organization(self, name: str, slug: str) -> Organization: ...

    async def get_organization(self, org_id: str) -> Organization | None: ...

    async def get_organization_by_slug(self, slug: str) -> Organization | None: ...

    async def create_user(
        self, org_id: str, email: str, password_hash: str, display_name: str, role: Role
    ) -> User: ...

    async def get_user_by_id(self, user_id: str) -> User | None: ...

    async def get_user_by_email(self, org_id: str, email: str) -> User | None: ...

    async def get_user_credentials(
        self, org_id: str, email: str
    ) -> tuple[User, str] | None: ...

    async def list_users(self, org_id: str) -> list[User]: ...

    async def create_class(
        self, org_id: str, name: str, code: str, teacher_id: str | None = None
    ) -> Classroom: ...

    async def get_class(self, class_id: str) -> Classroom | None: ...

    async def get_class_by_code(self, org_id: str, code: str) -> Classroom | None: ...

    async def list_classes_for_user(self, user: User) -> list[Classroom]: ...

    async def add_enrollment(self, class_id: str, user_id: str) -> None: ...

    async def list_class_students(self, class_id: str) -> list[User]: ...

    async def user_in_org(self, user_id: str, org_id: str) -> bool: ...

    async def is_teacher_of_student(self, teacher_id: str, student_id: str) -> bool: ...

    async def create_session(
        self, token_hash: str, user_id: str, created_at: Any, expires_at: Any
    ) -> SessionRecord: ...

    async def get_session(self, token_hash: str) -> SessionRecord | None: ...

    async def revoke_session(self, token_hash: str) -> None: ...

    async def close(self) -> None: ...


class SqliteStoreAdapter:
    """把同步 SqliteStore 适配成 Store；用锁串行化同一个连接的访问。"""

    name = "sqlite"

    def __init__(self, inner: SqliteStore) -> None:
        self._inner = inner
        self._lock = asyncio.Lock()

    async def _call(self, func: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(func, *args)

    async def healthcheck(self) -> bool:
        return await self._call(self._inner.healthcheck)

    async def load_model(self, learner_id: str) -> LearnerModel | None:
        return await self._call(self._inner.load_model, learner_id)

    async def save_model(self, model: LearnerModel) -> None:
        await self._call(self._inner.save_model, model)

    async def load_reviews(self, learner_id: str) -> dict[str, ReviewItem]:
        return await self._call(self._inner.load_reviews, learner_id)

    async def save_reviews(self, learner_id: str, items: dict[str, ReviewItem]) -> None:
        await self._call(self._inner.save_reviews, learner_id, items)

    async def get_version(self, learner_id: str, knowledge_id: str) -> int:
        return await self._call(self._inner.get_version, learner_id, knowledge_id)

    async def compare_and_swap(
        self, learner_id: str, state: KnowledgeState, expected_version: int
    ) -> bool:
        return await self._call(
            self._inner.compare_and_swap, learner_id, state, expected_version
        )

    async def append_event(self, event: Event) -> None:
        await self._call(self._inner.append_event, event)

    async def count_events(self) -> int:
        return await self._call(self._inner.count_events)

    async def get_events(self, learner_id: str, limit: int = 50) -> list[Event]:
        return await self._call(self._inner.get_events, learner_id, limit)

    async def seed_questions(self, questions: list[Question]) -> int:
        return await self._call(self._inner.seed_questions, questions)

    async def get_question(self, question_id: str) -> Question | None:
        return await self._call(self._inner.get_question, question_id)

    async def list_questions(
        self,
        knowledge_id: str | None = None,
        grade: int | None = None,
        limit: int = 100,
    ) -> list[Question]:
        return await self._call(
            self._inner.list_questions, knowledge_id, grade, limit
        )

    async def save_attempt(self, attempt: Attempt) -> bool:
        return await self._call(self._inner.save_attempt, attempt)

    async def submit_transaction(
        self,
        learner_id: str,
        idempotency_key: str | None,
        request_fingerprint: str | None,
        build: Callable[
            [LearnerModel, dict[str, ReviewItem]],
            Awaitable[tuple[Attempt, list[Event]]],
        ],
    ) -> tuple[Attempt, list[Event], bool]:
        """在同一个 SQLite 写事务里完成幂等检查和所有落库。

        这里会一直持有 adapter 的锁，回调里不能再调用本 adapter 的其他方法；
        当前提交链路只操作内存中的 LearnerModel，因此是安全的。
        """
        async with self._lock:
            existing, model, reviews = await asyncio.to_thread(
                self._inner.begin_submission,
                learner_id,
                idempotency_key,
                request_fingerprint,
            )
            if existing is not None:
                return existing, [], True
            assert model is not None

            try:
                attempt, events = await build(model, reviews)
                created = await asyncio.to_thread(
                    self._inner.finish_submission,
                    attempt,
                    model,
                    reviews,
                    events,
                )
                if created:
                    return attempt, events, False

                current = await asyncio.to_thread(
                    self._inner.get_attempt_by_idempotency,
                    learner_id,
                    idempotency_key,
                )
                if (
                    current is not None
                    and current.request_fingerprint == request_fingerprint
                ):
                    return current, [], True
                raise IdempotencyConflictError(idempotency_key or attempt.id)
            except BaseException:
                await asyncio.to_thread(self._inner.rollback_submission)
                raise

    async def get_attempt_by_idempotency(
        self, learner_id: str, idempotency_key: str
    ) -> Attempt | None:
        return await self._call(
            self._inner.get_attempt_by_idempotency, learner_id, idempotency_key
        )

    async def list_attempts(self, learner_id: str, limit: int = 50) -> list[Attempt]:
        return await self._call(self._inner.list_attempts, learner_id, limit)

    async def count_users(self) -> int:
        return await self._call(self._inner.count_users)

    async def create_organization(self, name: str, slug: str) -> Organization:
        return await self._call(self._inner.create_organization, name, slug)

    async def get_organization(self, org_id: str) -> Organization | None:
        return await self._call(self._inner.get_organization, org_id)

    async def get_organization_by_slug(self, slug: str) -> Organization | None:
        return await self._call(self._inner.get_organization_by_slug, slug)

    async def create_user(
        self, org_id: str, email: str, password_hash: str, display_name: str, role: Role
    ) -> User:
        return await self._call(
            self._inner.create_user,
            org_id,
            email,
            password_hash,
            display_name,
            role,
        )

    async def get_user_by_id(self, user_id: str) -> User | None:
        return await self._call(self._inner.get_user_by_id, user_id)

    async def get_user_by_email(self, org_id: str, email: str) -> User | None:
        return await self._call(self._inner.get_user_by_email, org_id, email)

    async def get_user_credentials(
        self, org_id: str, email: str
    ) -> tuple[User, str] | None:
        return await self._call(self._inner.get_user_credentials, org_id, email)

    async def list_users(self, org_id: str) -> list[User]:
        return await self._call(self._inner.list_users, org_id)

    async def create_class(
        self, org_id: str, name: str, code: str, teacher_id: str | None = None
    ) -> Classroom:
        return await self._call(
            self._inner.create_class, org_id, name, code, teacher_id
        )

    async def get_class(self, class_id: str) -> Classroom | None:
        return await self._call(self._inner.get_class, class_id)

    async def get_class_by_code(self, org_id: str, code: str) -> Classroom | None:
        return await self._call(self._inner.get_class_by_code, org_id, code)

    async def list_classes_for_user(self, user: User) -> list[Classroom]:
        return await self._call(self._inner.list_classes_for_user, user)

    async def add_enrollment(self, class_id: str, user_id: str) -> None:
        await self._call(self._inner.add_enrollment, class_id, user_id)

    async def list_class_students(self, class_id: str) -> list[User]:
        return await self._call(self._inner.list_class_students, class_id)

    async def user_in_org(self, user_id: str, org_id: str) -> bool:
        return await self._call(self._inner.user_in_org, user_id, org_id)

    async def is_teacher_of_student(self, teacher_id: str, student_id: str) -> bool:
        return await self._call(
            self._inner.is_teacher_of_student, teacher_id, student_id
        )

    async def create_session(
        self, token_hash: str, user_id: str, created_at: Any, expires_at: Any
    ) -> SessionRecord:
        return await self._call(
            self._inner.create_session, token_hash, user_id, created_at, expires_at
        )

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        return await self._call(self._inner.get_session, token_hash)

    async def revoke_session(self, token_hash: str) -> None:
        await self._call(self._inner.revoke_session, token_hash)

    async def close(self) -> None:
        await self._call(self._inner.close)
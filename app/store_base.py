"""持久化抽象层。

- Postgres 走原生异步驱动（asyncpg）；
- SQLite 仍是同步实现，用 `SqliteStoreAdapter` 包成同一套异步接口，
  这样 orchestrator 不需要关心底层数据库；
- 测试里的 `tests/test_store.py` 继续直接使用同步的 `SqliteStore`。

对应教程：04-进阶/14-持久化与并发一致性.md
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from app.core.events import Event
from app.core.learner_model import KnowledgeState, LearnerModel
from app.store import SqliteStore


@runtime_checkable
class Store(Protocol):
    """两种后端共用的最小接口（全部异步）。"""

    name: str

    async def load_model(self, learner_id: str) -> LearnerModel | None: ...

    async def save_model(self, model: LearnerModel) -> None: ...

    async def get_version(self, learner_id: str, knowledge_id: str) -> int: ...

    async def compare_and_swap(
        self, learner_id: str, state: KnowledgeState, expected_version: int
    ) -> bool: ...

    async def append_event(self, event: Event) -> None: ...

    async def count_events(self) -> int: ...

    async def get_events(self, learner_id: str, limit: int = 50) -> list[Event]: ...

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

    async def load_model(self, learner_id: str) -> LearnerModel | None:
        return await self._call(self._inner.load_model, learner_id)

    async def save_model(self, model: LearnerModel) -> None:
        await self._call(self._inner.save_model, model)

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

    async def close(self) -> None:
        await self._call(self._inner.close)
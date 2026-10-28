from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from app.agents import AssessmentAgent, CurriculumAgent,EngagementAgent,HintAgent,TutorAgent
from app.config import Settings, get_settings
from app.core.events import Event, EventType,EventBus
from app.core.learner_model import LearnerModel
from app.llm.client import LLMClient
from app.store_base import Store

logger = logging.getLogger(__name__)

class AgentOrchestrator:
    def __init__(
        self,
        llm: LLMClient | None = None,
        clock: Callable[[],datetime] | None = None,
        settings: Settings | None = None,
        store: Store | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store
        self._locks: dict[str, asyncio.Lock] = {}
        self.event_bus = EventBus()
        self.learner_models: dict[str,LearnerModel] = {}
        if store is not None:
            # 所有事件都落库；append_event 以 event.id 去重
            for event_type in EventType:
                self.event_bus.subscribe(event_type, self._persist_event)
        self.assessment = AssessmentAgent(
            "AssessmentAgent",
            self.event_bus,
            self.learner_models,
            weakness_threshold=self.settings.weakness_threshold,
        )
        self.tutor = TutorAgent(
            "TutorAgent",
            self.event_bus,
            self.learner_models,
            llm=llm,
            hint_after_attempts=self.settings.hint_after_attempts,
            llm_timeout=self.settings.llm_timeout_seconds,
            llm_for_answers=self.settings.tutor_llm_for_answers,
        )
        self.curriculum = CurriculumAgent(
            "CurriculumAgent",
            self.event_bus,
            self.learner_models,
            clock=clock,
            mastery_threshold=self.settings.mastery_threshold,
        )
        self.hint = HintAgent("HintAgent", self.event_bus, self.learner_models)
        self.engagement = EngagementAgent("EngagementAgent", self.event_bus, self.learner_models, clock=clock)

    # ── 持久化：加载 / 保存 / 事件落库 ──
    def _lock(self, learner_id: str) -> asyncio.Lock:
        """同一 learner 的写操作串行化（单进程内；多 worker 仍需 DB 乐观锁）。"""
        return self._locks.setdefault(learner_id, asyncio.Lock())

    async def _persist_event(self, event: Event) -> None:
        if self.store is None:
            return
        try:
            await self.store.append_event(event)
        except Exception:
            # 落库失败不能阻断教学流程；记录后继续
            logger.exception("persist event failed: %s", event.id)

    async def _ensure_loaded(self, learner_id: str) -> None:
        if self.store is None or learner_id in self.learner_models:
            return
        model = await self.store.load_model(learner_id)
        if model is not None:
            self.learner_models[learner_id] = model
            logger.info("loaded learner %s from %s", learner_id, self.store.name)

    async def _save(self, learner_id: str) -> None:
        if self.store is None:
            return
        model = self.learner_models.get(learner_id)
        if model is not None:
            await self.store.save_model(model)

    async def ensure_learner(self, learner_id: str) -> None:
        """API 查询进度前先确保已从数据库加载。"""
        async with self._lock(learner_id):
            await self._ensure_loaded(learner_id)

    async def submit_answer(
            self,
            learner_id: str,
            knowledge_id: str,
            is_correct: bool,
            time_spent: float = 0,
            correlation_id: str | None = None
    ) -> list[Event]:
        async with self._lock(learner_id):
            await self._ensure_loaded(learner_id)
            cid = correlation_id or f"c-{uuid.uuid4().hex[:8]}"
            await self.event_bus.publish(
                Event(
                    type=EventType.STUDENT_SUBMISSION,
                    source="api",
                    learner_id=learner_id,
                    data={
                        "knowledge_id": knowledge_id,
                        "is_correct": is_correct,
                        "time_spent_seconds": time_spent
                    },
                    correlation_id=cid
                )
            )
            await self._save(learner_id)
            return self.event_bus.get_history(correlation_id=cid,limit=200)

    async def ask_question(self,learner_id: str,knowledge_id: str,question: str) -> list[Event]:
        async with self._lock(learner_id):
            await self._ensure_loaded(learner_id)
            cid = f"c-{uuid.uuid4().hex[:8]}"
            await self.event_bus.publish(
                Event(
                    type=EventType.STUDENT_QUESTION,
                    source="api",
                    learner_id=learner_id,
                    data={
                        "knowledge_id": knowledge_id,
                        "question": question
                    },
                    correlation_id=cid
                )
            )
            return self.event_bus.get_history(correlation_id=cid,limit=200)

    async def send_message(self,learner_id: str,message: str,knowledge_id: str = "general") -> list[Event]:
        async with self._lock(learner_id):
            await self._ensure_loaded(learner_id)
            cid = f"c-{uuid.uuid4().hex[:8]}"
            await self.event_bus.publish(
                Event(
                    type=EventType.STUDENT_MESSAGE,
                    source="api",
                    learner_id=learner_id,
                    data={
                        "message": message,
                        "knowledge_id": knowledge_id
                    },
                    correlation_id=cid
                )
            )
            return self.event_bus.get_history(correlation_id=cid,limit=200)

    def get_learner_progress(self,learner_id: str) -> dict[str,Any]:
        model = self.learner_models.get(learner_id)
        if model is None:
            return {"learner_id": learner_id,"status": "no_data"}
        return {
            "learner_id": learner_id,
            "progress":model.get_overall_progress(),
            "weak_points":[
                {"id":s.knowledge_id, "mastery": round(s.mastery,4)} for s in model.get_weak_points()
            ],
            "strong_points":[
                {"id":s.knowledge_id, "mastery": round(s.mastery,4)} for s in model.get_strong_points()
            ],
        }


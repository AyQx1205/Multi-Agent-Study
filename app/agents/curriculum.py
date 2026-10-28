from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from app.agents.base import BaseAgent
from app.core.events import Event, EventType
from app.core.knowledge_graph import KnowledgeGraph, build_sample_math_graph
from app.core.spaced_repetition import ReviewItem, SpacedRepetition

logger = logging.getLogger(__name__)

MASTERY_THRESHOLD = 0.6


class CurriculumAgent(BaseAgent):
    def __init__(
        self,
        *args: Any,
        graph: KnowledgeGraph | None = None,
        sr: SpacedRepetition | None = None,
        clock: Callable[[], datetime] | None = None,
        mastery_threshold: float = MASTERY_THRESHOLD,
        **kwargs: Any,
    ) -> None:
        self.knowledge_graph = graph or build_sample_math_graph()
        self.sr = sr or SpacedRepetition()
        self._clock = clock or datetime.now
        self._mastery_threshold = mastery_threshold
        self._reviews: dict[str, dict[str, ReviewItem]] = {}
        self._recommended: dict[str, str] = {}
        super().__init__(*args, **kwargs)

    # ── 复习状态的持久化入口（由 orchestrator 调用） ──
    def export_reviews(self, learner_id: str) -> dict[str, ReviewItem]:
        return dict(self._reviews.get(learner_id, {}))

    def import_reviews(self, learner_id: str, items: dict[str, ReviewItem]) -> None:
        self._reviews[learner_id] = dict(items)

    @property
    def subscribed_events(self) -> list[EventType]:
        return [
            EventType.MASTERY_UPDATED,
            EventType.WEAKNESS_DETECTED,
            EventType.PACE_ADJUSTMENT,
        ]

    async def handle_event(self, event: Event) -> None:
        if event.type is EventType.MASTERY_UPDATED:
            await self._handle_mastery(event)
        elif event.type is EventType.WEAKNESS_DETECTED:
            await self._handle_weakness(event)
        elif event.type is EventType.PACE_ADJUSTMENT:
            logger.info("[Curriculum] pace %s for %s", event.data.get("action"), event.learner_id)

    # ── mastery 更新：复习排期 + 推荐 ──
    async def _handle_mastery(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id", "")
        mastery = float(event.data.get("mastery", 0.0))

        self._apply_review(learner_id, knowledge_id, self._mastery_to_quality(mastery))

        if mastery >= self._mastery_threshold:
            await self._recommend_next(learner_id, event.correlation_id)

        await self._send_schedule(learner_id, event.correlation_id)

    @staticmethod
    def _mastery_to_quality(mastery: float) -> int:
        if mastery >= 0.9:
            return 5
        if mastery >= 0.75:
            return 4
        if mastery >= 0.6:
            return 3
        if mastery >= 0.4:
            return 2
        if mastery >= 0.2:
            return 1
        return 0

    def _apply_review(self, learner_id: str, knowledge_id: str, quality: int) -> None:
        """同一天只排一次，避免一天 5 题把间隔推到 3 个月。"""
        now = self._clock()
        items = self._reviews.setdefault(learner_id, {})
        item = items.get(knowledge_id)
        if item is None:
            item = ReviewItem(knowledge_id=knowledge_id, next_review=now)
            items[knowledge_id] = item

        same_day = item.last_review is not None and item.last_review.date() == now.date()
        if not same_day:
            self.sr.review(item, quality, now=now)

    def _mastered_ids(self, learner_id: str) -> set[str]:
        model = self.get_learner_model(learner_id)
        return {
            state.knowledge_id
            for state in model.knowledge_states.values()
            if state.mastery >= self._mastery_threshold
        }

    async def _recommend_next(self, learner_id: str, correlation_id: str | None) -> None:
        ready = self.knowledge_graph.get_ready_nodes(self._mastered_ids(learner_id))
        if not ready:
            return
        next_id = ready[0]
        if self._recommended.get(learner_id) == next_id:
            return
        self._recommended[learner_id] = next_id
        node = self.knowledge_graph.nodes[next_id]
        await self.emit(
            EventType.NEXT_TOPIC,
            learner_id,
            {
                "knowledge_id": next_id,
                "name": node.name,
                "difficulty": node.difficulty,
                "reason": "前置知识已掌握，推荐学习新内容",
                "alternatives": ready[1:4],
            },
            correlation_id=correlation_id,
        )

    async def _send_schedule(self, learner_id: str, correlation_id: str | None) -> None:
        now = self._clock()
        items = list(self._reviews.get(learner_id, {}).values())
        due = self.sr.get_due_items(items, now=now)
        schedule = self.sr.get_study_schedule(items, days_ahead=7, now=now)

        if not due and not schedule:
            return

        await self.emit(
            EventType.REVIEW_SCHEDULED,
            learner_id,
            {
                "due_now": [
                    {"knowledge_id": item.knowledge_id, "overdue_days": round(item.overdue_days(now), 1)}
                    for item in due[:5]
                ],
                "weekly_schedule": schedule,
            },
            correlation_id=correlation_id,
        )

    async def _handle_weakness(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id", "")
        path = self.knowledge_graph.get_learning_path(knowledge_id, self._mastered_ids(learner_id))
        if not path:
            return
        await self.emit(
            EventType.PATH_UPDATED,
            learner_id,
            {
                "reason": "weakness_detected",
                "weak_knowledge_id": knowledge_id,
                "remedial_path": path,
                "message": f"检测到「{knowledge_id}」薄弱，建议先复习前置知识",
            },
            correlation_id=event.correlation_id,
        )
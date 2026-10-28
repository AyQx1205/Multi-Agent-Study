from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any,Awaitable,Callable

from pydantic import BaseModel,Field

logger = logging.getLogger(__name__)

MAX_DEPTH = 20
HISTORY_LIMIT = 1000

class EventType(str, Enum):
    # 学生输入
    STUDENT_SUBMISSION = "student.submission"
    STUDENT_QUESTION = "student.question"
    STUDENT_MESSAGE = "student.message"

    # 评估
    MASTERY_UPDATED = "assessment.mastery_updated"
    WEAKNESS_DETECTED = "assessment.weakness_detected"
    ASSESSMENT_COMPLETE = "assessment.complete"

    # 教学
    TEACHING_RESPONSE = "tutor.teaching_response"
    HINT_NEEDED = "tutor.hint_needed"
    DIFFICULTY_ADJUSTED = "tutor.difficulty_adjusted"

    # 课程
    PATH_UPDATED = "curriculum.path_updated"
    NEXT_TOPIC = "curriculum.next_topic"
    REVIEW_SCHEDULED = "curriculum.review_scheduled"

    # 提示
    HINT_RESPONSE = "hint.response"

    # 互动
    ENGAGEMENT_ALERT = "engagement.alert"
    ENCOURAGEMENT = "engagement.encouragement"
    PACE_ADJUSTMENT = "engagement.pace_adjustment"

class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: EventType = Field(description="事件类型")
    source: str = Field(description="事件来源")
    learner_id: str = Field(description="学习者ID")
    timestamp: datetime = Field(default_factory=datetime.now,description="事件时间戳")
    data: dict[str, Any] = Field(default_factory=dict,description="事件数据")
    correlation_id: str | None = Field(None,description="事件关联ID")

EventHandler = Callable[[Event], Awaitable[None]]

class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = {}
        self._history: list[Event] = []
        self._lock = asyncio.Lock()
        self._depth = 0

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)
        logger.debug("subscribed %s -> %s", event_type.value, getattr(handler, "__name__", handler))

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        if self._depth >= MAX_DEPTH:
            logger.error(
                "event depth limit reached (%s); drop %s from %s",
                MAX_DEPTH,
                event.type.value,
                event.source,
            )
            return

        async with self._lock:
            self._history.append(event)
            if len(self._history) > HISTORY_LIMIT:
                del self._history[:len(self._history) - HISTORY_LIMIT]

        handlers = list(self._subscribers.get(event.type, []))
        if not handlers:
            logger.debug("no handler for %s", event.type.value)
            return

        self._depth += 1
        try:
            await asyncio.gather(*(self._safe_call(h,event) for h in handlers))
        finally:
            self._depth -= 1

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "handler error: %s -> %s", event.type.value, getattr(handler, "__name__", handler)
            )

    def get_history(
            self,
            learner_id: str | None = None,
            event_type: EventType | None = None,
            correlation_id: str | None = None,
            limit: int = 50,
        ) -> list[Event]:
            events = self._history
            if learner_id:
                events = [e for e in events if e.learner_id == learner_id]
            if event_type:
                events = [e for e in events if e.type == event_type]
            if correlation_id:
                events = [e for e in events if e.correlation_id == correlation_id]
            if limit <= 0:
                return []
            return events[-limit:]

    def clear_history(self) -> None:
        self._history.clear()

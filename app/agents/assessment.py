from __future__ import annotations

import logging

from app.agents.base import BaseAgent
from app.core.events import Event, EventType

logger = logging.getLogger(__name__)

WEAKNESS_THRESHOLD = 0.3
WEAKNESS_MIN_ATTEMPTS = 3


class AssessmentAgent(BaseAgent):
    @property
    def subscribed_events(self) -> list[EventType]:
        return [EventType.STUDENT_SUBMISSION, EventType.STUDENT_QUESTION]

    async def handle_event(self, event: Event) -> None:
        if event.type is EventType.STUDENT_SUBMISSION:
            await self._handle_submission(event)
        elif event.type is EventType.STUDENT_QUESTION:
            await self._handle_question(event)
        else: 
            logger.warning("unhandled event type: %s",event.type)

    async def _handle_submission(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id")
        if not knowledge_id:
            logger.warning("submission missing knowledge_id; learner=%s", learner_id)
            return

        if "is_correct" not in event.data:
            logger.warning(
                "submission missing is_correct, defaulting to False; learner=%s kp=%s",
                learner_id,
                knowledge_id,
            )
        is_correct = bool(event.data.get("is_correct", False))
        cid = event.correlation_id

        model = self.get_learner_model(learner_id)
        state = model.update_mastery(knowledge_id, is_correct)

        await self.emit(
            EventType.MASTERY_UPDATED,
            learner_id,
            {
                "knowledge_id": knowledge_id,
                "mastery": round(state.mastery, 4),
                "level": state.level.value,
                "attempts": state.attempts,
                "streak": state.streak,
                "confidence": round(state.confidence, 4),
                "is_correct": is_correct,
            },
            correlation_id=cid,
        )

        if state.mastery < WEAKNESS_THRESHOLD and state.attempts >= WEAKNESS_MIN_ATTEMPTS:
            await self.emit(
                EventType.WEAKNESS_DETECTED,
                learner_id,
                {
                    "knowledge_id": knowledge_id,
                    "mastery": round(state.mastery, 4),
                    "attempts": state.attempts,
                    "suggestion": "需要回到前置知识点复习",
                },
                correlation_id=cid,
            )

        await self.emit(
            EventType.ASSESSMENT_COMPLETE,
            learner_id,
            {
                "knowledge_id": knowledge_id,
                "is_correct": is_correct,
                "mastery": round(state.mastery, 4),
                "level": state.level.value,
                "overall_progress": model.get_overall_progress(),
            },
            correlation_id=cid,
        )

    async def _handle_question(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id", "general")
        model = self.get_learner_model(learner_id)
        state = model.get_state(knowledge_id)

        await self.emit(
            EventType.ASSESSMENT_COMPLETE,
            learner_id,
            {
                "knowledge_id": knowledge_id,
                "question": event.data.get("question", ""),
                "is_correct": None,
                "mastery": round(state.mastery, 4),
                "level": state.level.value,
                "context": "student_question",
                "overall_progress": model.get_overall_progress(),
            },
            correlation_id=event.correlation_id,
        )
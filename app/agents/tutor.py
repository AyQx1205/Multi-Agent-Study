from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.base import BaseAgent
from app.core.events import Event, EventType
from app.llm.client import LLMClient, guard_response

logger = logging.getLogger(__name__)

HINT_AFTER_ATTEMPTS = 2

SOCRATIC_PROMPTS: dict[str, str] = {
    "beginner": (
        "你是一位耐心的数学老师，学生刚开始学习这个知识点。用最简单的语言和例子帮助理解，"
        "先问学生对基础概念是否了解，不要直接给答案。"
    ),
    "developing": (
        "你是一位苏格拉底式的数学老师。先问学生已经知道什么，再引导他发现关键步骤；"
        "学生卡住时给关键提示，而不是答案。"
    ),
    "proficient": (
        "你是一位挑战型数学老师。追问「为什么」「还有别的方法吗」，引导他发现知识点之间的联系。"
    ),
    "mastered": (
        "你是一位高级导师。引导学生总结方法论、做跨知识点综合题，鼓励他用费曼学习法讲给别人听。"
    ),
}

class TutorAgent(BaseAgent):
    def __init__(self, *args: Any, llm: LLMClient | None = None, **kwargs: Any) -> None:
        self._llm = llm
        self._attempts: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    @property
    def subscribed_events(self) -> list[EventType]:
        return [
            EventType.ASSESSMENT_COMPLETE,
            EventType.STUDENT_MESSAGE,
            EventType.HINT_RESPONSE,
            EventType.ENGAGEMENT_ALERT,
        ]

    async def handle_event(self, event: Event) -> None:
        if event.type is EventType.ASSESSMENT_COMPLETE:
            await self._handle_assessment(event)
        elif event.type is EventType.STUDENT_MESSAGE:
            await self._handle_message(event)
        elif event.type is EventType.HINT_RESPONSE:
            await self._handle_hint_response(event)
        elif event.type is EventType.ENGAGEMENT_ALERT:
            await self._handle_engagement_alert(event)

    async def _handle_assessment(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id")
        if not knowledge_id:
            logger.warning("submission missing knowledge_id; learner=%s", learner_id)
            return
        is_correct = event.data.get("is_correct")
        level = event.data.get("level", "beginner")
        mastery = float(event.data.get("mastery", 0.0))
        key = f"{learner_id}:{knowledge_id}"

        if is_correct is False:
            attempts = self._attempts.get(key, 0) + 1
            self._attempts[key] = attempts
            if attempts >= HINT_AFTER_ATTEMPTS:
                await self.emit(
                    EventType.HINT_NEEDED,
                    learner_id,
                    {
                        "knowledge_id": knowledge_id,
                        "mastery": mastery,
                        "attempts": attempts,
                        "level": level,
                    },
                    correlation_id=event.correlation_id,
                )
                return
        elif is_correct is True:
            self._attempts.pop(key, None)

        response = await self._generate(
            knowledge_id=knowledge_id,
            level=level,
            mastery=mastery,
            is_correct=is_correct,
            question=event.data.get("question", ""),
        )
        await self.emit(
            EventType.TEACHING_RESPONSE,
            learner_id,
            {
                "knowledge_id": knowledge_id,
                "response": response,
                "teaching_style": "socratic",
                "difficulty_level": level,
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_message(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id", "general")
        state = self.get_learner_model(learner_id).get_state(knowledge_id)
        response = await self._generate(
            knowledge_id=knowledge_id,
            level=state.level.value,
            mastery=state.mastery,
            is_correct=None,
            question=event.data.get("message", ""),
        )
        await self.emit(
            EventType.TEACHING_RESPONSE,
            learner_id,
            {
                "knowledge_id": knowledge_id,
                "response": response,
                "teaching_style": "socratic",
                "difficulty_level": state.level.value,
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_hint_response(self, event: Event) -> None:
        await self.emit(
            EventType.TEACHING_RESPONSE,
            event.learner_id,
            {
                "knowledge_id": event.data.get("knowledge_id", ""),
                "response": event.data.get("hint_text", ""),
                "teaching_style": "hint",
                "hint_level": event.data.get("hint_level", 1),
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_engagement_alert(self, event: Event) -> None:
        alert_type = event.data.get("alert_type", "")
        if alert_type == "frustration":
            await self.emit(
                EventType.DIFFICULTY_ADJUSTED,
                event.learner_id,
                {
                    "action": "decrease",
                    "reason": "检测到挫败感",
                    "message": "我注意到你可能遇到了困难，我们换个角度，从更简单的地方开始。"
                },
                correlation_id=event.correlation_id,
            )
        elif alert_type == "boredom":
            await self.emit(
                EventType.DIFFICULTY_ADJUSTED,
                event.learner_id,
                {
                    "action": "increase",
                    "reason": "检测到学生可能感觉无聊",
                    "message": "看起来这对你来说太简单了！让我给你一个更有挑战性的问题。"
                },
                correlation_id=event.correlation_id,
            )

    async def _generate(
            self,
            knowledge_id: str,
            level: str,
            mastery: float,
            is_correct: bool | None,
            question: str,
    ) -> str:
        fallback = self._template_response(knowledge_id, level, mastery, is_correct, question)
        if self._llm is None:
            return fallback

        system = (
            SOCRATIC_PROMPTS.get(level, SOCRATIC_PROMPTS["beginner"])
            + "\n硬约束:绝对不要给出最终答案或完整步骤;一次只问一个问题;不超过 120 字。"
        )
        user = (
            f"知识点：{knowledge_id}\n掌握度:{mastery:.2f}({level})\n"
            f"本次答对：{is_correct}\n学生说:{question or '（无）'}"
        )
        try:
            text = await asyncio.wait_for(
                self._llm.generate(system, user),
                timeout=8.0,
            )
        except Exception:
            logger.warning("llm call failed, fallback to template", exc_info=True)
            return fallback

        ok, reason = guard_response(text)
        if not ok:
            logger.warning("llm output blocked: %s", reason)
            return fallback
        return text

    def _template_response(
        self,
        knowledge_id: str,
        level: str,
        mastery: float,
        is_correct: bool | None,
        question: str,
    ) -> str:
        if is_correct is True:
            return (
                f"很好！你在「{knowledge_id}」上的表现不错，当前掌握度 {mastery:.0%}。"
                f"让我问一个更深入的问题：你能用自己的话解释这个概念吗？"
            )
        if is_correct is False:
            return (
                f"没关系，我们一起来分析「{knowledge_id}」。先不看答案："
                f"你觉得这道题卡在哪一步？能不能先用最简单的数字代进去试试？"
            )
        return (
            f"关于「{knowledge_id}」：在我回答之前，先说说你已经了解了哪些内容？"
            f"我们一起看看你的理解对不对。"
        )
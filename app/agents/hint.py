from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.core.events import Event, EventType

logger = logging.getLogger(__name__)

METACOGNITIVE = 1
SCAFFOLDING = 2
TARGETED = 3

LEVEL_NAMES = {METACOGNITIVE: "metacognitive", SCAFFOLDING: "scaffolding", TARGETED: "targeted"}

HINT_TEMPLATES: dict[int, dict[str, Any]] = {
    METACOGNITIVE: {
        "description": "元认知暗示 - 引导学生反思自己的思考过程",
        "templates": [
            "题目里的条件你都用上了吗？",
            "这道题和你做过的哪道题比较像？",
            "如果答案已经出来了，倒推过程应该是什么样？",
            "先把已知条件画个图或列个表，也许能看出规律。",
        ],
    },
    SCAFFOLDING: {
        "description": "脚手架引导 - 给出关键步骤但不给答案",
        "templates": [
            "关键是先找到对应的公式。你还记得它长什么样吗？",
            "第一步先整理条件，第二步再代入。你试试能不能接下去。",
            "答案和一个关键数值有关，从这个方向想想看。",
        ],
    },
    TARGETED: {
        "description": "直接提示 - 给出具体解法（仅在多次尝试后使用）",
        "templates": [
            "我把思路拆成三步写给你，但请合上答案自己重做一遍，然后做 2 道同类题巩固。",
        ],
    },
}

class HintAgent(BaseAgent):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._hint_history: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    @property
    def subscribed_events(self) -> list[EventType]:
        return [EventType.HINT_NEEDED]

    async def handle_event(self, event: Event) -> None:
        if event.type is EventType.HINT_NEEDED:
            await self._provide_hint(event)

    @staticmethod
    def decide_level(hint_count: int, mastery: float, attempts: int) -> int:
        if mastery < 0.15 and attempts >= 3:
            return TARGETED
        if hint_count <= 1:
            return METACOGNITIVE
        if hint_count <= 3:
            return SCAFFOLDING
        return TARGETED

    async def _provide_hint(self, event: Event) -> None:
        learner_id = event.learner_id
        knowledge_id = event.data.get("knowledge_id", "")
        mastery = float(event.data.get("mastery", 0.0))
        attempts = int(event.data.get("attempts", 1))
        key = f"{learner_id}:{knowledge_id}"

        level = self.decide_level(self._hint_history.get(key, 0), mastery, attempts)
        self._hint_history[key] = self._hint_history.get(key, 0) + 1

        templates = HINT_TEMPLATES[level]["templates"]
        body = templates[(self._hint_history[key] - 1) % len(templates)]

        await self.emit(
            EventType.HINT_RESPONSE,
            learner_id,
            {
                "knowledge_id": knowledge_id,
                "hint_level": level,
                "hint_level_name": LEVEL_NAMES[level],
                "hint_text": f"关于「{knowledge_id}」的提示：{body}",
                "hint_count": self._hint_history[key],
                "description": HINT_TEMPLATES[level]["description"],
            },
            correlation_id=event.correlation_id,
        )
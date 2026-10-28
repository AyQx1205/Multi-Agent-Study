"""题库与答题记录的数据模型。

内容与业务分离：题目本身是静态数据，掌握度/复习仍然由 Agent 维护。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    CHOICE = "choice"
    FILL = "fill"


class Question(BaseModel):
    id: str = Field(description="题目ID")
    knowledge_id: str = Field(description="所属知识点")
    grade: int = Field(ge=1, le=9, description="年级")
    type: QuestionType = Field(default=QuestionType.FILL)
    stem: str = Field(description="题干")
    options: list[str] = Field(default_factory=list, description="选择题选项")
    answer: str = Field(description="标准答案；选择题为 A/B/C/D")
    accepted: list[str] = Field(default_factory=list, description="可接受的等价答案")
    explanation: str = Field(default="", description="解析")
    difficulty: int = Field(default=2, ge=1, le=3)
    source: str = Field(default="seed")
    version: int = Field(default=1)

    def public_dict(self) -> dict[str, Any]:
        """给前端的题目，不含答案和解析。"""
        return {
            "id": self.id,
            "knowledge_id": self.knowledge_id,
            "grade": self.grade,
            "type": self.type.value,
            "stem": self.stem,
            "options": list(self.options),
            "difficulty": self.difficulty,
        }


class Attempt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    learner_id: str
    question_id: str
    knowledge_id: str
    answer: str
    is_correct: bool
    time_spent_seconds: float = 0.0
    created_at: datetime = Field(default_factory=datetime.now)
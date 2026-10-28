from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class MasteryLevel(str, Enum):
    NOT_STARTED = "not_started"
    BEGINNER = "beginner"
    DEVELOPING = "developing"
    PROFICIENT = "proficient"
    MASTERED = "mastered"

class KnowledgeState(BaseModel):
    knowledge_id: str = Field(description="知识ID")
    mastery:float = 0.1
    alpha:float = 1.0
    beta:float = 9.0
    attempts:int = 0
    correct_count:int = 0
    streak:int = 0
    last_attempt:datetime | None = None

    @property
    def level(self) -> MasteryLevel:
        if self.attempts == 0:
            return MasteryLevel.NOT_STARTED
        elif self.mastery < 0.3:
            return MasteryLevel.BEGINNER
        elif self.mastery < 0.6:
            return MasteryLevel.DEVELOPING
        elif self.mastery < 0.85:
            return MasteryLevel.PROFICIENT
        else:
            return MasteryLevel.MASTERED

    @property
    def confidence(self) -> float:
        return min(1.0, (self.alpha + self.beta) / 20.0)

class BKTParams(BaseModel):
    p_init:float = 0.1
    p_transit:float = 0.15
    p_guess:float = 0.2
    p_slip:float = 0.1

class LearnerModel:
    def __init__(self, learner_id: str, bkt_params: BKTParams | None = None) -> None:
        self.learner_id = learner_id
        self.bkt = bkt_params or BKTParams()
        self.knowledge_states: dict[str, KnowledgeState] = {}
        self.session_start:datetime = datetime.now()
        self.total_interactions:int = 0
        self.metadata: dict[str, Any] = {}

    def get_state(self, knowledge_id: str) -> KnowledgeState:
        if knowledge_id not in self.knowledge_states:
            self.knowledge_states[knowledge_id] = KnowledgeState(knowledge_id=knowledge_id,mastery=self.bkt.p_init)
        return self.knowledge_states[knowledge_id]

    def peek_state(self, knowledge_id: str) -> KnowledgeState | None:
        """只读查询：不会为提问/闲聊创建“幻觉知识点”。"""
        return self.knowledge_states.get(knowledge_id)

    def update_mastery(self, knowledge_id: str, is_correct: bool) -> KnowledgeState:
        state = self.get_state(knowledge_id)
        p_l = state.mastery
        p = self.bkt

        if is_correct:
            p_obs_given_l = 1 - p.p_slip
            p_obs_given_not_l = p.p_guess
            state.correct_count += 1
            state.streak += 1
            state.alpha += 1
        else:
            p_obs_given_l = p.p_slip
            p_obs_given_not_l = 1 - p.p_guess
            state.streak = 0
            state.beta += 1

        p_obs = p_l * p_obs_given_l + (1 - p_l) * p_obs_given_not_l
        posterior = (p_l * p_obs_given_l) / p_obs if p_obs > 0 else p_l
        updated = posterior + (1 - posterior) * p.p_transit

        state.mastery = max(0.0, min(1.0, updated))
        state.attempts += 1
        state.last_attempt = datetime.now()
        self.total_interactions += 1
        return state

    def get_weak_points(self,threshold:float = 0.4,limit:int = 10) -> list[KnowledgeState]:
        weak = [s for s in self.knowledge_states.values() if s.mastery < threshold and s.attempts > 0]
        return sorted(weak,key=lambda s: s.mastery)[:limit]

    def get_strong_points(self,threshold:float = 0.85) -> list[KnowledgeState]:
        return [s for s in self.knowledge_states.values() if s.mastery >= threshold]

    def get_overall_progress(self) -> dict[str, Any]:
        states = list(self.knowledge_states.values())
        if not states:
            return {
                "total_knowledge_points": 0,
                "avg_mastery": 0.0,
                "total_attempts": 0,
                "total_correct": 0,
                "accuracy": 0.0,
                "level_distribution": {},
            }

        distribution: dict[str, int] = {}
        for s in states:
            distribution[s.level.value] = distribution.get(s.level.value, 0) + 1

        attempts = sum(s.attempts for s in states)
        correct = sum(s.correct_count for s in states)
        return {
            "total_knowledge_points": len(states),
            "avg_mastery": round(sum(s.mastery for s in states) / len(states),4),
            "total_attempts": attempts,
            "total_correct": correct,
            "accuracy": round(correct / attempts,4) if attempts > 0 else 0.0,
            "level_distribution": distribution,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "knowledge_states": {kid:s.model_dump(mode="json") for kid,s in self.knowledge_states.items()},
            "total_interactions": self.total_interactions,
            "session_start": self.session_start.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LearnerModel":
        model = cls(payload["learner_id"])
        for kid,raw in payload.get("knowledge_states",{}).items():
            model.knowledge_states[kid] = KnowledgeState(**raw)
        model.total_interactions = payload.get("total_interactions",0)
        model.metadata = payload.get("metadata",{})
        started = payload.get("session_start")
        if started:
            model.session_start = datetime.fromisoformat(started)
        return model
        

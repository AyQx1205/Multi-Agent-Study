from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from app.agents import AssessmentAgent, CurriculumAgent,EngagementAgent,HintAgent,TutorAgent
from app.core.events import Event, EventType,EventBus
from app.core.learner_model import LearnerModel
from app.llm.client import LLMClient

class AgentOrchestrator:
    def __init__(self, llm: LLMClient | None = None,clock: Callable[[],datetime] | None = None) -> None:
        self.event_bus = EventBus()
        self.learner_models: dict[str,LearnerModel] = {}
        self.assessment = AssessmentAgent("AssessmentAgent",self.event_bus,self.learner_models)
        self.tutor = TutorAgent("TutorAgent",self.event_bus,self.learner_models,llm=llm)
        self.curriculum = CurriculumAgent("CurriculumAgent",self.event_bus,self.learner_models,clock=clock)
        self.hint = HintAgent("HintAgent",self.event_bus,self.learner_models)
        self.engagement = EngagementAgent("EngagementAgent",self.event_bus,self.learner_models,clock=clock)

    async def submit_answer(
            self,
            learner_id: str,
            knowledge_id: str,
            is_correct: bool,
            time_spent: float = 0,
            correlation_id: str | None = None
    ) -> list[Event]:
        cid = correlation_id or f"{uuid.uuid4().hex[:8]}"
        await self.event_bus.publish(
            Event(
                type=EventType.STUDENT_SUBMISSION,
                source="api",
                learner_id=learner_id,
                data={
                    "knowledge_id": knowledge_id,
                    "is_correct": is_correct,
                    "time_spent": time_spent
                },
                correlation_id=cid
            )
        )
        return self.event_bus.get_history(correlation_id=cid,limit=200)

    async def ask_question(self,learn_id: str,knowledge_id: str,question: str) -> list[Event]:
        cid = f"{uuid.uuid4().hex[:8]}"
        await self.event_bus.publish(
            Event(
                type=EventType.STUDENT_QUESTION,
                source="api",
                learner_id=learn_id,
                data={
                    "knowledge_id": knowledge_id,
                    "question": question
                },
                correlation_id=cid
            )
        )
        return self.event_bus.get_history(correlation_id=cid,limit=200)

    async def send_message(self,learner_id: str,message: str,knowledge_id: str = "general") -> list[Event]:
        cid = f"{uuid.uuid4().hex[:8]}"
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


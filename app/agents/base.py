from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.core.events import Event, EventType,EventBus
from app.core.learner_model import LearnerModel

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    def __init__(self, name: str,event_bus: EventBus,learner_models: dict[str,LearnerModel]) -> None:
        self.name = name
        self.event_bus = event_bus
        self.learner_models = learner_models
        self._register_handlers()
        logger.debug("[%s] Initialized",self.name)

    def _register_handlers(self) -> None:
        for event_type in self.subscribe_events:
            self.event_bus.subscribe(event_type,self.handle_event)

    @property
    @abstractmethod
    def subscribed_events(self) -> list[EventType]: ...

    @abstractmethod
    async def handle_event(self,event: Event) -> None: ...

    async def emit(self,event_type: EventType,learner_id: str,data: dict[str,Any],correlation_id: str | None = None) -> None:
        await self.event_bus.publish(
            Event(
                type=event_type,source=self.name,learner_id=learner_id,data=data,correlation_id=correlation_id
                )
            )

    def get_learner_model(self,learner_id: str) -> LearnerModel:
        if learner_id not in self.learner_models:
            self.learner_models[learner_id] = LearnerModel(learner_id)
        return self.learner_models[learner_id]
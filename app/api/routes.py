from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["education"])


class SubmitAnswerRequest(BaseModel):
    learner_id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    is_correct: bool
    time_spent_seconds: float = Field(default=0, ge=0, le=3600)


class AskQuestionRequest(BaseModel):
    learner_id: str = Field(min_length=1)
    knowledge_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=500)


class SendMessageRequest(BaseModel):
    learner_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=500)
    knowledge_id: str = "general"


def _serialize(events: list[Any], limit: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "type": e.type.value,
            "source": e.source,
            "data": e.data,
            "timestamp": e.timestamp.isoformat(),
            "correlation_id": e.correlation_id,
        }
        for e in events[-limit:]
    ]


@router.get("/health")
async def health_check(request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    agents = [orch.assessment, orch.tutor, orch.curriculum, orch.hint, orch.engagement]
    return {
        "status": "ok",
        "service": "multi-agent-lite",
        "agents": len(agents),
        "storage": orch.store.name if orch.store else "memory",
    }


@router.post("/submit")
async def submit_answer(req: SubmitAnswerRequest, request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    events = await orch.submit_answer(
        req.learner_id, req.knowledge_id, req.is_correct, req.time_spent_seconds
    )
    return {
        "status": "processed",
        "events_triggered": len(events),
        "events": _serialize(events),
        "correlation_id": events[-1].correlation_id if events else None,
    }


@router.post("/question")
async def ask_question(req: AskQuestionRequest, request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    events = await orch.ask_question(req.learner_id, req.knowledge_id, req.question)
    return {
        "status": "processed",
        "events_triggered": len(events),
        "events": _serialize(events),
        "correlation_id": events[-1].correlation_id if events else None,
    }


@router.post("/message")
async def send_message(req: SendMessageRequest, request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    events = await orch.send_message(req.learner_id, req.message, req.knowledge_id)
    return {
        "status": "processed",
        "events_triggered": len(events),
        "events": _serialize(events),
        "correlation_id": events[-1].correlation_id if events else None,
    }


@router.get("/progress/{learner_id}")
async def get_progress(learner_id: str, request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    await orch.ensure_learner(learner_id)
    return orch.get_learner_progress(learner_id)


@router.get("/events/{learner_id}")
async def get_events(learner_id: str, request: Request, limit: int = 50) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    limit = max(1, min(limit, 200))
    if orch.store is not None:
        events = await orch.store.get_events(learner_id, limit)
    else:
        events = orch.event_bus.get_history(learner_id=learner_id, limit=limit)
    return {"learner_id": learner_id, "events": _serialize(events, limit=len(events))}


@router.get("/knowledge-graph")
async def get_knowledge_graph(request: Request) -> dict[str, Any]:
    graph = request.app.state.orchestrator.curriculum.knowledge_graph
    return {
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "difficulty": n.difficulty,
                "prerequisites": n.prerequisites,
                "tags": n.tags,
            }
            for n in graph.nodes.values()
        ],
        "learning_order": graph.topological_sort(),
    }
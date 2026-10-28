from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.content import content_summary
from app.orchestrator import QuestionNotFoundError

router = APIRouter(tags=["education"])


class SubmitAnswerRequest(BaseModel):
    learner_id: str = Field(min_length=1, max_length=80)
    question_id: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=200)
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
        "content": content_summary(),
    }


@router.get("/questions")
async def list_questions(
    request: Request,
    knowledge_id: str | None = None,
    grade: int | None = Query(default=None, ge=1, le=9),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    questions = await orch.get_questions(knowledge_id, grade, limit)
    return {
        "count": len(questions),
        "items": [question.public_dict() for question in questions],
    }


@router.get("/questions/{question_id}")
async def get_question(question_id: str, request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    question = await orch.get_question(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    return question.public_dict()


@router.post("/submit")
async def submit_answer(req: SubmitAnswerRequest, request: Request) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    try:
        events, attempt = await orch.submit_question(
            req.learner_id,
            req.question_id,
            req.answer,
            req.time_spent_seconds,
        )
    except QuestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="question not found") from exc

    question = await orch.get_question(req.question_id)
    return {
        "status": "processed",
        "attempt_id": attempt.id,
        "question_id": attempt.question_id,
        "knowledge_id": attempt.knowledge_id,
        "is_correct": attempt.is_correct,
        "correct_answer": question.answer if question else None,
        "explanation": question.explanation if question else "",
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


@router.get("/attempts/{learner_id}")
async def get_attempts(
    learner_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    attempts = await orch.get_attempts(learner_id, limit)
    return {
        "learner_id": learner_id,
        "count": len(attempts),
        "items": [
            {
                "id": attempt.id,
                "question_id": attempt.question_id,
                "knowledge_id": attempt.knowledge_id,
                "answer": attempt.answer,
                "is_correct": attempt.is_correct,
                "time_spent_seconds": attempt.time_spent_seconds,
                "created_at": attempt.created_at.isoformat(),
            }
            for attempt in attempts
        ],
    }


@router.get("/knowledge-graph")
async def get_knowledge_graph(request: Request) -> dict[str, Any]:
    graph = request.app.state.orchestrator.curriculum.knowledge_graph
    return {
        "subject": "math",
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "grade": n.grade,
                "difficulty": n.difficulty,
                "prerequisites": n.prerequisites,
                "tags": n.tags,
            }
            for n in graph.nodes.values()
        ],
        "learning_order": graph.topological_sort(),
    }
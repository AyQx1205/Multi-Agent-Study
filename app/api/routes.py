from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import ensure_learner_access, get_principal, require_roles
from app.config import get_settings
from app.content import content_summary
from app.core.accounts import Principal, Role
from app.core.errors import IdempotencyConflictError, QuestionNotFoundError
from app.metrics import metrics

router = APIRouter(tags=["education"])


def _enforce_rate_limit(request: Request, bucket: str, user_id: str, limit: int) -> None:
    """按用户限流；超限回 429 + Retry-After。

    限流器挂在 `app.state` 上（每个应用实例一个），多实例时各算各的。
    """
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    wait = limiter.hit(f"{bucket}:{user_id}", limit)
    if wait is not None:
        metrics.inc("rate_limited_total", bucket=bucket, channel="http")
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(int(wait) + 1)},
        )


class SubmitAnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=200)
    time_spent_seconds: float = Field(default=0, ge=0, le=3600)


class AskQuestionRequest(BaseModel):
    knowledge_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=500)


class SendMessageRequest(BaseModel):
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
        "llm": getattr(request.app.state, "llm_mode", "unknown"),
        "content": content_summary(),
    }


@router.get("/live")
async def liveness() -> dict[str, str]:
    """进程存活探针：不查数据库，避免数据库抖动导致容器被误杀。"""
    return {"status": "ok"}


@router.get("/metrics")
async def metrics_endpoint() -> PlainTextResponse:
    """Prometheus 文本格式指标；内网抓取，勿暴露到公网。"""
    return PlainTextResponse(metrics.render())


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, Any]:
    """就绪探针：数据库可用才接流量。"""
    orch = request.app.state.orchestrator
    if orch.store is None:
        response.status_code = 503
        return {"status": "not_ready", "reason": "store_not_configured"}
    try:
        healthy = await asyncio.wait_for(orch.store.healthcheck(), timeout=2.0)
    except Exception:
        healthy = False
    if not healthy:
        response.status_code = 503
        return {"status": "not_ready", "reason": "database_unavailable"}
    return {"status": "ready", "storage": orch.store.name}


@router.get("/questions")
async def list_questions(
    request: Request,
    knowledge_id: str | None = None,
    grade: int | None = Query(default=None, ge=1, le=9),
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    questions = await orch.get_questions(knowledge_id, grade, limit)
    return {
        "count": len(questions),
        "items": [question.public_dict() for question in questions],
    }


@router.get("/questions/{question_id}")
async def get_question(
    question_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    question = await orch.get_question(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    return question.public_dict()


@router.post("/submit")
async def submit_answer(
    req: SubmitAnswerRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="客户端生成的唯一键；同键重复提交只处理一次。",
    ),
    principal: Principal = Depends(require_roles(Role.STUDENT)),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    _enforce_rate_limit(
        request,
        "submit",
        principal.user_id,
        get_settings().submit_rate_per_minute,
    )
    try:
        outcome = await orch.submit_question(
            principal.user_id,
            req.question_id,
            req.answer,
            req.time_spent_seconds,
            idempotency_key=idempotency_key,
        )
    except QuestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="question not found") from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different question or answer",
        ) from exc

    attempt = outcome.attempt
    events = outcome.events
    return {
        "status": "replayed" if outcome.replayed else "processed",
        "replayed": outcome.replayed,
        "attempt_id": attempt.id,
        "question_id": attempt.question_id,
        "knowledge_id": attempt.knowledge_id,
        "is_correct": attempt.is_correct,
        "correct_answer": outcome.revealed_answer,
        "explanation": outcome.revealed_explanation,
        "answer_revealed": outcome.reveal_answer,
        "attempts_for_question": outcome.attempts_for_question,
        "counts_toward_mastery": outcome.counts_toward_mastery,
        "events_triggered": len(events),
        "events": _serialize(events),
        "correlation_id": events[-1].correlation_id if events else None,
    }


@router.post("/question")
async def ask_question(
    req: AskQuestionRequest,
    request: Request,
    principal: Principal = Depends(require_roles(Role.STUDENT)),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    _enforce_rate_limit(
        request,
        "question",
        principal.user_id,
        get_settings().message_rate_per_minute,
    )
    events = await orch.ask_question(principal.user_id, req.knowledge_id, req.question)
    return {
        "status": "processed",
        "events_triggered": len(events),
        "events": _serialize(events),
        "correlation_id": events[-1].correlation_id if events else None,
    }


@router.post("/message")
async def send_message(
    req: SendMessageRequest,
    request: Request,
    principal: Principal = Depends(require_roles(Role.STUDENT)),
) -> dict[str, Any]:
    orch = request.app.state.orchestrator
    _enforce_rate_limit(
        request,
        "message",
        principal.user_id,
        get_settings().message_rate_per_minute,
    )
    events = await orch.send_message(principal.user_id, req.message, req.knowledge_id)
    return {
        "status": "processed",
        "events_triggered": len(events),
        "events": _serialize(events),
        "correlation_id": events[-1].correlation_id if events else None,
    }


@router.get("/progress/{learner_id}")
async def get_progress(
    learner_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    await ensure_learner_access(request, principal, learner_id)
    orch = request.app.state.orchestrator
    await orch.ensure_learner(learner_id)
    return orch.get_learner_progress(learner_id)


@router.get("/events/{learner_id}")
async def get_events(
    learner_id: str,
    request: Request,
    limit: int = 50,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    await ensure_learner_access(request, principal, learner_id)
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
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    await ensure_learner_access(request, principal, learner_id)
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
async def get_knowledge_graph(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
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

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.accounts import Role, User
from app.core.errors import IdempotencyConflictError, QuestionNotFoundError
from app.metrics import metrics
from app.security import hash_session_token
from app.ws_manager import manager

logger = logging.getLogger(__name__)
ws_router = APIRouter()

MAX_ID_CHARS = 80
MAX_TEXT_CHARS = 500
MAX_IDEMPOTENCY_CHARS = 128


async def _authorize_student(
    websocket: WebSocket, user: User | None, learner_id: str
) -> bool:
    if user is None or not user.is_active or user.role is not Role.STUDENT:
        await websocket.close(code=1008, reason="student account required")
        return False
    if user.id != learner_id:
        await websocket.close(code=1008, reason="cannot connect as another learner")
        return False
    return True


async def _authenticate(websocket: WebSocket, learner_id: str) -> User | None:
    """优先用一次性 ?ticket=...；旧 ?token= 仅在显式开启时兼容。

    返回认证到的用户；失败时已经关闭连接并返回 None。
    """
    store = websocket.app.state.orchestrator.store
    now = datetime.now(UTC)
    ticket = websocket.query_params.get("ticket", "")
    if ticket:
        user_id = await store.consume_ws_ticket(hash_session_token(ticket), now)
        if user_id is None:
            await websocket.close(code=1008, reason="invalid or expired ticket")
            return None
        user = await store.get_user_by_id(user_id)
        return user if await _authorize_student(websocket, user, learner_id) else None

    if not get_settings().ws_allow_query_token:
        await websocket.close(code=1008, reason="missing ticket")
        return None

    # 兼容旧客户端：仅在 WS_ALLOW_QUERY_TOKEN=true 时接受长期 token。
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=1008, reason="missing ticket")
        return None
    session = await store.get_session(hash_session_token(token))
    if session is None or session.revoked_at is not None:
        await websocket.close(code=1008, reason="invalid token")
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        await websocket.close(code=1008, reason="token expired")
        return None
    user = await store.get_user_by_id(session.user_id)
    return user if await _authorize_student(websocket, user, learner_id) else None


def _check_rate(websocket: WebSocket, user_id: str, bucket: str, limit: int) -> bool:
    limiter = getattr(websocket.app.state, "rate_limiter", None)
    if limiter is None:
        return True
    allowed = limiter.hit(f"{bucket}:{user_id}", limit) is None
    if not allowed:
        metrics.inc("rate_limited_total", bucket=bucket, channel="ws")
    return allowed


@ws_router.websocket("/ws/{learner_id}")
async def websocket_endpoint(websocket: WebSocket, learner_id: str) -> None:
    user = await _authenticate(websocket, learner_id)
    if user is None:
        return

    settings = get_settings()
    connected = await manager.connect(
        learner_id,
        websocket,
        max_connections=settings.ws_max_connections_per_learner,
    )
    if not connected:
        return
    max_frame = max(512, settings.ws_max_message_chars)
    store = websocket.app.state.orchestrator.store
    try:
        while True:
            raw = await websocket.receive_text()
            manager.touch(learner_id, websocket)
            if len(raw) > max_frame:
                await manager.send(
                    learner_id,
                    {"error": "message_too_large", "limit": max_frame},
                )
                continue

            # 每条消息前复查会话：登出、被禁用、会话过期之后，
            # 已经建立的连接不能继续提交（否则等于一个永久有效的旁路）。
            if not await store.has_active_session(user.id, datetime.now(UTC)):
                await manager.send(learner_id, {"error": "session_expired"})
                await websocket.close(code=1008, reason="session expired")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send(learner_id, {"error": "invalid json"})
                continue
            action = data.get("action", "") if isinstance(data, dict) else ""
            if not action:
                await manager.send(learner_id, {"error": "invalid payload"})
                continue
            orch = websocket.app.state.orchestrator

            if action == "submit":
                question_id = str(data.get("question_id") or "")
                answer = str(data.get("answer") or "")
                idempotency_key = str(data.get("idempotency_key") or "")
                if not question_id or not answer:
                    await manager.send(
                        learner_id, {"error": "question_id and answer required"}
                    )
                    continue
                if len(question_id) > MAX_ID_CHARS or len(answer) > MAX_TEXT_CHARS:
                    await manager.send(
                        learner_id,
                        {
                            "error": "field too long",
                            "limits": {"question_id": MAX_ID_CHARS, "answer": MAX_TEXT_CHARS},
                        },
                    )
                    continue
                if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_CHARS:
                    await manager.send(
                        learner_id,
                        {"error": "idempotency_key required (max 128 chars)"},
                    )
                    continue
                if not _check_rate(
                    websocket, user.id, "ws-submit", settings.submit_rate_per_minute
                ):
                    await manager.send(learner_id, {"error": "rate_limited"})
                    continue
                try:
                    time_spent = float(data.get("time_spent_seconds", 0) or 0)
                except (TypeError, ValueError):
                    time_spent = 0.0
                try:
                    outcome = await orch.submit_question(
                        learner_id,
                        question_id,
                        answer,
                        time_spent,
                        idempotency_key=idempotency_key,
                    )
                except QuestionNotFoundError:
                    await manager.send(learner_id, {"error": "question not found"})
                    continue
                except IdempotencyConflictError:
                    await manager.send(
                        learner_id,
                        {
                            "error": "idempotency_key_conflict",
                            "detail": "key already used with different payload",
                        },
                    )
                    continue
                events = outcome.events
                attempt = outcome.attempt
                await manager.send(
                    learner_id,
                    {
                        "event_type": "submission.result",
                        "replayed": outcome.replayed,
                        "question_id": attempt.question_id,
                        "knowledge_id": attempt.knowledge_id,
                        "is_correct": attempt.is_correct,
                        "correct_answer": outcome.revealed_answer,
                        "explanation": outcome.revealed_explanation,
                        "answer_revealed": outcome.reveal_answer,
                        "attempts_for_question": outcome.attempts_for_question,
                        "counts_toward_mastery": outcome.counts_toward_mastery,
                    },
                )
            elif action == "question":
                knowledge_id = str(data.get("knowledge_id") or "")
                question = str(data.get("question") or "")
                if not knowledge_id or not question:
                    await manager.send(
                        learner_id, {"error": "knowledge_id and question required"}
                    )
                    continue
                if len(knowledge_id) > MAX_ID_CHARS or len(question) > MAX_TEXT_CHARS:
                    await manager.send(
                        learner_id,
                        {
                            "error": "field too long",
                            "limits": {"knowledge_id": MAX_ID_CHARS, "question": MAX_TEXT_CHARS},
                        },
                    )
                    continue
                if not _check_rate(
                    websocket, user.id, "ws-question", settings.message_rate_per_minute
                ):
                    await manager.send(learner_id, {"error": "rate_limited"})
                    continue
                events = await orch.ask_question(learner_id, knowledge_id, question)
            elif action == "message":
                message = str(data.get("message") or "")
                knowledge_id = str(data.get("knowledge_id") or "general")
                if not message:
                    await manager.send(learner_id, {"error": "message required"})
                    continue
                if len(message) > MAX_TEXT_CHARS or len(knowledge_id) > MAX_ID_CHARS:
                    await manager.send(
                        learner_id,
                        {
                            "error": "field too long",
                            "limits": {"message": MAX_TEXT_CHARS, "knowledge_id": MAX_ID_CHARS},
                        },
                    )
                    continue
                if not _check_rate(
                    websocket, user.id, "ws-message", settings.message_rate_per_minute
                ):
                    await manager.send(learner_id, {"error": "rate_limited"})
                    continue
                events = await orch.send_message(learner_id, message, knowledge_id)
            elif action == "ping":
                await manager.send(learner_id, {"event_type": "pong"})
                continue
            else:
                await manager.send(learner_id, {"error": f"unknown action: {action}"})
                continue

            for event in events:
                await manager.send(
                    learner_id,
                    {
                        "event_type": event.type.value,
                        "source": event.source,
                        "data": event.data,
                        "timestamp": event.timestamp.isoformat(),
                        "correlation_id": event.correlation_id,
                    },
                )
    except WebSocketDisconnect:
        manager.disconnect(learner_id, websocket)
    except Exception:
        logger.exception("ws error for %s", learner_id)
        manager.disconnect(learner_id, websocket)

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.accounts import Role
from app.orchestrator import IdempotencyConflictError, QuestionNotFoundError
from app.security import hash_session_token

logger = logging.getLogger(__name__)
ws_router = APIRouter()


class ConnectionManager:
    """一个学生可以有多端连接，所以用 set。"""

    def __init__(self) -> None:
        self.active: dict[str, set[WebSocket]] = {}

    async def connect(self, learner_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.active.setdefault(learner_id, set()).add(ws)

    def disconnect(self, learner_id: str, ws: WebSocket) -> None:
        sockets = self.active.get(learner_id)
        if sockets:
            sockets.discard(ws)
            if not sockets:
                self.active.pop(learner_id, None)

    async def send(self, learner_id: str, payload: dict[str, Any]) -> None:
        for ws in list(self.active.get(learner_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                logger.warning("ws send failed, dropping one connection", exc_info=True)
                self.disconnect(learner_id, ws)


manager = ConnectionManager()


async def _authenticate(websocket: WebSocket, learner_id: str) -> bool:
    """WebSocket 用 ?token=... 认证；只允许学生连接自己的 learner_id。"""
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=1008, reason="missing token")
        return False

    store = websocket.app.state.orchestrator.store
    session = await store.get_session(hash_session_token(token))
    if session is None or session.revoked_at is not None:
        await websocket.close(code=1008, reason="invalid token")
        return False
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        await websocket.close(code=1008, reason="token expired")
        return False

    user = await store.get_user_by_id(session.user_id)
    if user is None or not user.is_active or user.role is not Role.STUDENT:
        await websocket.close(code=1008, reason="student account required")
        return False
    if user.id != learner_id:
        await websocket.close(code=1008, reason="cannot connect as another learner")
        return False
    return True


@ws_router.websocket("/ws/{learner_id}")
async def websocket_endpoint(websocket: WebSocket, learner_id: str) -> None:
    if not await _authenticate(websocket, learner_id):
        return

    await manager.connect(learner_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
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
                if not idempotency_key or len(idempotency_key) > 128:
                    await manager.send(
                        learner_id,
                        {"error": "idempotency_key required (max 128 chars)"},
                    )
                    continue
                try:
                    time_spent = float(data.get("time_spent_seconds", 0) or 0)
                except (TypeError, ValueError):
                    time_spent = 0.0
                try:
                    events, attempt, replayed = await orch.submit_question(
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
                question = await orch.get_question(question_id)
                await manager.send(
                    learner_id,
                    {
                        "event_type": "submission.result",
                        "replayed": replayed,
                        "question_id": attempt.question_id,
                        "knowledge_id": attempt.knowledge_id,
                        "is_correct": attempt.is_correct,
                        "correct_answer": question.answer if question else None,
                        "explanation": question.explanation if question else "",
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
                events = await orch.ask_question(learner_id, knowledge_id, question)
            elif action == "message":
                message = str(data.get("message") or "")
                if not message:
                    await manager.send(learner_id, {"error": "message required"})
                    continue
                events = await orch.send_message(
                    learner_id, message, str(data.get("knowledge_id") or "general")
                )
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
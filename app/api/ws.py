from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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


@ws_router.websocket("/ws/{learner_id}")
async def websocket_endpoint(websocket: WebSocket, learner_id: str) -> None:
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
                knowledge_id = str(data.get("knowledge_id") or "")
                if not knowledge_id:
                    await manager.send(learner_id, {"error": "knowledge_id required"})
                    continue
                try:
                    time_spent = float(data.get("time_spent_seconds", 0) or 0)
                except (TypeError, ValueError):
                    time_spent = 0.0
                events = await orch.submit_answer(
                    learner_id,
                    knowledge_id,
                    bool(data.get("is_correct", False)),
                    time_spent,
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
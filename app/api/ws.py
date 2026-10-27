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
            data = json.loads(await websocket.receive_text())
            action = data.get("action", "")
            orch = websocket.app.state.orchestrator

            if action == "submit":
                events = await orch.submit_answer(
                    learner_id,
                    data.get("knowledge_id", ""),
                    bool(data.get("is_correct", False)),
                    float(data.get("time_spent_seconds", 0) or 0),
                )
            elif action == "question":
                events = await orch.ask_question(
                    learner_id, data.get("knowledge_id", ""), data.get("question", "")
                )
            elif action == "message":
                events = await orch.send_message(
                    learner_id, data.get("message", ""), data.get("knowledge_id", "general")
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
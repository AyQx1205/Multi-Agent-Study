"""WebSocket 连接注册表。

单独放一个模块，是为了让 `auth.py`（登出时要踢掉该用户的连接）也能引用，
而不必反过来依赖 `app.api.ws` 这个路由模块。

除了「谁在线」，这里还负责三件事：
- 连接数上限（一个学生开几十条连接会把服务端文件描述符吃光）；
- 心跳（长连接会被代理静默掐断，服务端要主动发帧探测）；
- 空闲清理（挂死的连接要回收，否则永远占着内存）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import WebSocket

from app.metrics import metrics

logger = logging.getLogger(__name__)


class ConnectionManager:
    """一个学生可以有多端连接，所以每个学习者对应一组连接。"""

    def __init__(self, clock=time.monotonic) -> None:
        # learner_id -> {websocket: 最近一次活动时间(monotonic)}
        self.active: dict[str, dict[WebSocket, float]] = {}
        self._clock = clock
        self._connections = 0
        self._refresh_gauge()

    # ── 指标 ──
    def _refresh_gauge(self) -> None:
        metrics.set_gauge("ws_connections", self._connections)
        metrics.set_gauge("ws_learners", len(self.active))

    def count(self, learner_id: str | None = None) -> int:
        if learner_id is None:
            return self._connections
        return len(self.active.get(learner_id, {}))

    # ── 连接生命周期 ──
    async def connect(
        self, learner_id: str, ws: WebSocket, *, max_connections: int = 5
    ) -> bool:
        await ws.accept()
        sockets = self.active.setdefault(learner_id, {})
        if len(sockets) >= max(1, max_connections):
            await ws.close(code=1008, reason="too many connections")
            if not sockets:
                self.active.pop(learner_id, None)
            metrics.inc("ws_rejected_total", reason="too_many_connections")
            self._refresh_gauge()
            return False
        sockets[ws] = self._clock()
        self._connections += 1
        self._refresh_gauge()
        return True

    def touch(self, learner_id: str, ws: WebSocket) -> None:
        sockets = self.active.get(learner_id)
        if sockets and ws in sockets:
            sockets[ws] = self._clock()

    def disconnect(self, learner_id: str, ws: WebSocket) -> None:
        sockets = self.active.get(learner_id)
        if not sockets:
            return
        if ws in sockets:
            sockets.pop(ws, None)
            self._connections = max(0, self._connections - 1)
        if not sockets:
            self.active.pop(learner_id, None)
        self._refresh_gauge()

    async def send(self, learner_id: str, payload: dict[str, Any]) -> None:
        for ws in list(self.active.get(learner_id, {})):
            try:
                await ws.send_json(payload)
            except Exception:
                logger.warning("ws send failed, dropping one connection", exc_info=True)
                self.disconnect(learner_id, ws)

    async def close_learner(
        self, learner_id: str, *, code: int = 1008, reason: str = "session ended"
    ) -> int:
        """主动关闭某个学习者的全部连接；返回关闭了几条。

        用于登出 / 账号被禁用：撤销会话之后，已经建立的 WebSocket 不会自己失效，
        不关掉它就等于留了一个永久有效的旁路。
        """
        sockets = list(self.active.get(learner_id, {}).keys())
        for ws in sockets:
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                logger.warning("ws close failed for %s", learner_id, exc_info=True)
            self.disconnect(learner_id, ws)
        return len(sockets)

    # ── 心跳与空闲回收 ──
    async def heartbeat(self) -> int:
        """向所有连接发一个心跳帧；发失败的连接就地回收。"""
        sent = 0
        for learner_id in list(self.active.keys()):
            for ws in list(self.active.get(learner_id, {}).keys()):
                try:
                    await ws.send_json({"event_type": "heartbeat"})
                    sent += 1
                except Exception:
                    self.disconnect(learner_id, ws)
        metrics.inc("ws_heartbeats_total", value=sent)
        return sent

    async def sweep_idle(self, max_idle_seconds: float) -> int:
        """关闭长时间没有任何消息的连接，返回关闭条数。"""
        now = self._clock()
        closed = 0
        for learner_id in list(self.active.keys()):
            for ws, last_seen in list(self.active.get(learner_id, {}).items()):
                if now - last_seen <= max_idle_seconds:
                    continue
                try:
                    await ws.close(code=1008, reason="idle timeout")
                except Exception:
                    logger.debug("ws idle close failed", exc_info=True)
                self.disconnect(learner_id, ws)
                closed += 1
        if closed:
            metrics.inc("ws_idle_closed_total", value=closed)
        return closed


manager = ConnectionManager()
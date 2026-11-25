"""WebSocket 连接注册表。

单独放一个模块，是为了让 `auth.py`（登出时要踢掉该用户的连接）也能引用，
而不必反过来依赖 `app.api.ws` 这个路由模块。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


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

    async def close_learner(
        self, learner_id: str, *, code: int = 1008, reason: str = "session ended"
    ) -> int:
        """主动关闭某个学习者的全部连接；返回关闭了几条。

        用于登出 / 账号被禁用：撤销会话之后，已经建立的 WebSocket 不会自己失效，
        不关掉它就等于留了一个永久有效的旁路。
        """
        sockets = list(self.active.get(learner_id, set()))
        self.active.pop(learner_id, None)
        for ws in sockets:
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                logger.warning("ws close failed for %s", learner_id, exc_info=True)
        return len(sockets)


manager = ConnectionManager()
"""安全审计日志：记录认证与管理操作，失败不阻断主流程。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from app.core.accounts import AuditLogEntry

logger = logging.getLogger(__name__)


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


async def record_audit(
    store: Any,
    action: str,
    *,
    org_id: str | None = None,
    actor_user_id: str | None = None,
    request: Request | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if store is None:
        return
    user_agent = None
    if request is not None:
        raw_agent = request.headers.get("user-agent") or ""
        user_agent = raw_agent[:300] or None
    entry = AuditLogEntry(
        id=f"a-{uuid.uuid4().hex}",
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=client_ip(request),
        user_agent=user_agent,
        metadata=metadata or {},
        created_at=datetime.now(UTC),
    )
    try:
        await store.append_audit_log(entry)
    except Exception:
        # 审计失败要告警，但不能把登录/管理请求变成不可用。
        logger.exception("audit append failed: %s", action)

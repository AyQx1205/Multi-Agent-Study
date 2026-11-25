"""后台维护：WebSocket 心跳/空闲清理 + 数据库过期数据清理。

挂在 lifespan 里跑一个后台任务；每轮都吞异常，维护失败不能把服务带崩。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.config import Settings
from app.core.timeutil import utcnow
from app.metrics import metrics
from app.ws_manager import ConnectionManager

logger = logging.getLogger(__name__)


async def run_once(
    store: object, settings: Settings, manager: ConnectionManager
) -> dict[str, int]:
    """执行一轮维护，返回各类清理条数（便于测试与日志）。"""
    results: dict[str, int] = {}

    sent = await manager.heartbeat()
    results["heartbeat_sent"] = sent
    closed = await manager.sweep_idle(settings.ws_idle_timeout_seconds)
    results["ws_idle_closed"] = closed

    now = utcnow()
    throttle_before = now - timedelta(
        seconds=max(settings.login_window_seconds, settings.login_lock_seconds)
    )
    purged = await store.purge_expired(now, throttle_before)  # type: ignore[attr-defined]
    for kind, count in purged.items():
        results[kind] = count
        if count:
            metrics.inc("purged_rows_total", value=count, kind=kind)

    if settings.events_retention_days > 0 or settings.attempts_retention_days > 0:
        # 两个留存期取较长的那个作为统一切点，避免删错。
        days = max(settings.events_retention_days, settings.attempts_retention_days)
        history = await store.purge_history(now - timedelta(days=days))  # type: ignore[attr-defined]
        for kind, count in history.items():
            results[f"history_{kind}"] = count
            if count:
                metrics.inc("purged_rows_total", value=count, kind=f"history_{kind}")

    metrics.inc("maintenance_runs_total")
    return results


async def maintenance_loop(
    store: object,
    settings: Settings,
    manager: ConnectionManager,
    *,
    interval_seconds: int | None = None,
) -> None:
    interval = max(5, interval_seconds or settings.maintenance_interval_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            results = await run_once(store, settings, manager)
            if any(value for value in results.values()):
                logger.info("maintenance: %s", results)
        except Exception:
            logger.exception("maintenance tick failed")

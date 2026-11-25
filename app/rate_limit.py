"""进程内滑动窗口限流。

`/submit`、`/question`、`/message` 都能触发 LLM 调用，之前只有登录有限流：
一个学生循环调 `/message` 就能把配额打满。

注意：这是**进程内**实现，多实例部署时每个实例各算各的。要跨实例一致，
需要换成 Redis 之类的共享计数器（与当前的内存态 LearnerModel 是同一类限制）。
"""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowLimiter:
    """按 key 记录最近 window 秒内的命中时间。"""

    def __init__(self, max_keys: int = 10000) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._max_keys = max(1, max_keys)

    def hit(
        self,
        key: str,
        limit: int,
        window_seconds: float = 60.0,
        *,
        now: float | None = None,
    ) -> float | None:
        """记一次尝试。

        放行返回 None；超限返回「还要等多少秒」，调用方据此回 429 / Retry-After。
        `limit <= 0` 表示不限流。
        """
        if limit <= 0:
            return None
        moment = time.monotonic() if now is None else now
        bucket = self._hits.get(key)
        if bucket is None:
            if len(self._hits) >= self._max_keys:
                # 兜底：直接清空，避免 key 无界增长（限流器是保护装置，不是账本）。
                self._hits.clear()
            bucket = deque()
            self._hits[key] = bucket

        cutoff = moment - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            return max(0.001, window_seconds - (moment - bucket[0]))

        bucket.append(moment)
        return None

    def clear(self) -> None:
        self._hits.clear()

    def active_keys(self) -> int:
        return len(self._hits)

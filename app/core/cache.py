"""有界缓存：给「按学习者累积」的内存状态加淘汰，防止长期运行必漏内存。

为什么不用 TTL：学习状态没有「过期」语义，用 LRU 更合适——最近活跃的
学习者留在内存里，冷数据被淘汰后从数据库重新加载（store 已经是真相源）。

锁缓存另外处理：**绝不淘汰正在被持有的锁**，否则同一学习者会拿到两把
不同的锁，互斥直接失效。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from app.metrics import metrics


class BoundedCache(OrderedDict):
    """按访问顺序淘汰的 dict（LRU），接口与 dict 兼容。"""

    def __init__(self, maxsize: int = 1000, *, name: str = "cache") -> None:
        super().__init__()
        self.maxsize = max(1, int(maxsize))
        self.name = name
        self.evictions = 0

    # ── 访问 / 写入都刷新 LRU 顺序 ──
    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        self._evict()

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return self[key]
        return default

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return self[key]
        self[key] = default
        return default

    def _evict(self) -> None:
        if len(self) > self.maxsize:
            self.popitem(last=False)
            self._count_eviction()

    def _count_eviction(self) -> None:
        self.evictions += 1
        metrics.inc("cache_evictions_total", cache=self.name)

    def stats(self) -> dict[str, Any]:
        return {"size": len(self), "maxsize": self.maxsize, "evictions": self.evictions}


class LockCache(BoundedCache):
    """存放 asyncio.Lock 的有界缓存：只淘汰**未被持有**的锁。

    事件循环是单线程的：`_lock()` 返回后调用方会立刻 `async with`，中间没有
    让出点；所以任何「即将被使用」的锁要么已被持有（locked() 为真，不淘汰），
    要么马上就要被这个任务锁上。淘汰未持有的锁是安全的。

    如果所有候选都被持有（或只剩刚插入的那个），就允许暂时超出上限：
    宁可变大，也不能让同一学习者拿到两把锁、把互斥破坏掉。
    """

    def _evict(self) -> None:
        if len(self) <= self.maxsize:
            return
        newest = next(reversed(self), None)
        for key in list(self.keys()):
            if len(self) <= self.maxsize:
                return
            if key == newest:
                continue
            lock = OrderedDict.__getitem__(self, key)
            if getattr(lock, "locked", lambda: False)():
                continue
            self.pop(key, None)
            self._count_eviction()

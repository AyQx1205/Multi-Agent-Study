"""极简进程内指标（Prometheus 文本格式）。

没有引入 prometheus_client：这里只需要计数器 + 少量 gauge，
多一个依赖不划算。多实例部署时由 Prometheus 各自抓取再聚合即可。

用法：
    from app.metrics import metrics
    metrics.inc("events_dropped_total", reason="depth_limit")
    metrics.set_gauge("ws_connections", 3)
"""

from __future__ import annotations

import threading
from collections import defaultdict

_TEXT_ESCAPES = {"\\": "\\\\", "\n": "\\n", '"': '\\"'}


def _escape(value: object) -> str:
    return "".join(_TEXT_ESCAPES.get(ch, ch) for ch in str(value))


def _labels(labels: dict[str, object] | None) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(labels.items()))
    return "{" + inner + "}"


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], float] = defaultdict(float)
        self._gauges: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(name: str, labels: dict[str, object] | None) -> tuple[str, str]:
        return name, _labels(labels)

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    def set_gauge(self, name: str, value: float, **labels: object) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def add_gauge(self, name: str, delta: float, **labels: object) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = self._gauges.get(key, 0.0) + delta

    def value(self, name: str, **labels: object) -> float:
        """不传标签时返回该名字下所有标签的合计（便于测试与看板）。"""
        with self._lock:
            if labels:
                key = self._key(name, labels)
                if key in self._counters:
                    return self._counters[key]
                return self._gauges.get(key, 0.0)
            return sum(
                value for (key_name, _), value in self._counters.items() if key_name == name
            ) + sum(
                value for (key_name, _), value in self._gauges.items() if key_name == name
            )

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()

    def render(self) -> str:
        """渲染成 Prometheus 文本暴露格式。"""
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)

        for name in sorted({key[0] for key in counters}):
            lines.append(f"# TYPE {name} counter")
            for (key_name, label_text), value in sorted(counters.items()):
                if key_name == name:
                    lines.append(f"{name}{label_text} {value:g}")

        for name in sorted({key[0] for key in gauges}):
            lines.append(f"# TYPE {name} gauge")
            for (key_name, label_text), value in sorted(gauges.items()):
                if key_name == name:
                    lines.append(f"{name}{label_text} {value:g}")

        return "\n".join(lines) + "\n"


metrics = Metrics()

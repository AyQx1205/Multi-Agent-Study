from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

try:  # httpx 只在真实模型模式下需要；缺了也不影响 Mock 与测试
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

BANNED_PATTERNS = [
    r"答案是",
    r"答案为",
    r"所以\s*x\s*=",
    r"顶点(坐标)?是",
    r"最终结果",
    r"x\s*=\s*-?\d",
]
MAX_REPLY_CHARS = 200


class LLMClient(Protocol):
    async def complete(
        self, system: str, user: str, *, max_tokens: int = 300, temperature: float = 0.4
    ) -> str: ...


class MockLLM:
    """确定性假模型：零成本、可注入延迟与失败，单测默认用它。"""

    def __init__(self, delay: float = 0.0, fail_times: int = 0) -> None:
        self.delay = delay
        self.fail_times = fail_times
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 300, temperature: float = 0.4
    ) -> str:
        self.calls.append((system, user))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("mock llm failure")
        return "先别急着算。你觉得题目里哪个条件最关键？"


class OpenAICompatibleLLM:
    """兼容 /chat/completions 的服务（OpenAI、DeepSeek、本地 vLLM 等）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 300, temperature: float = 0.4
    ) -> str:
        if httpx is None:
            raise RuntimeError("httpx is required for OpenAICompatibleLLM")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"

        if self._client is not None:
            response = await self._client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def guard_response(text: str, max_chars: int = MAX_REPLY_CHARS) -> tuple[bool, str]:
    """出口护栏：长度、泄题、多问。返回 (是否通过, 原因)。"""
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if len(text) > max_chars:
        return False, "too_long"
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text):
            return False, f"leak:{pattern}"
    if text.count("？") + text.count("?") > 1:
        return False, "multi_question"
    return True, "ok"
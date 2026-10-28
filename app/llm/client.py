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
    """兼容 /chat/completions 的服务（OpenAI、DeepSeek、百炼、本地 vLLM 等）。"""

    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 8.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> Any:
        if httpx is None:
            raise RuntimeError("httpx is required for OpenAICompatibleLLM")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        """lifespan 关闭时释放连接池；外部注入的 client 由调用方负责。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 300, temperature: float = 0.4
    ) -> str:
        client = await self._get_client()
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

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.retry_backoff * (2 ** attempt))
                continue

            if response.status_code in self.RETRYABLE_STATUS and attempt < self.max_retries:
                logger.warning(
                    "llm retryable status=%s attempt=%s", response.status_code, attempt + 1
                )
                await asyncio.sleep(self.retry_backoff * (2 ** attempt))
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                logger.warning(
                    "llm http error status=%s body=%s",
                    response.status_code,
                    response.text[:200],
                )
                raise

            data = response.json()
            choice = (data.get("choices") or [{}])[0] or {}
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                # 部分兼容服务返回多段 content
                content = "".join(
                    str(part.get("text", "")) if isinstance(part, dict) else str(part)
                    for part in content
                )
            usage = data.get("usage") or {}
            logger.info(
                "llm ok model=%s prompt_tokens=%s completion_tokens=%s",
                self.model,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )
            return (content or "").strip()

        raise RuntimeError(
            f"llm request failed after {self.max_retries + 1} attempts"
        ) from last_error


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
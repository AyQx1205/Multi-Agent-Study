"""用一条最小请求验证 DashScope / OpenAI 兼容接口是否配置正确。

    python scripts/check_llm.py

不会打印 API Key；失败时保留原始异常，方便判断是 401、模型名不对还是超时。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.main import build_llm
from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    print(f"base_url = {settings.llm_base_url}")
    print(f"model    = {settings.llm_model}")
    print(f"mock     = {settings.mock_llm}")

    if not settings.mock_llm and not settings.llm_api_key:
        print("LLM_API_KEY 为空：先把百炼控制台的 Key 填到 .env")
        return

    llm = build_llm(settings)
    try:
        reply = await llm.complete(
            "你是一位苏格拉底式数学老师。只问一个问题，不要给答案。",
            "学生问：为什么负负得正？",
            max_tokens=600,
            temperature=0.3,
        )
        print(f"reply    = {reply}")
    finally:
        if hasattr(llm, "aclose"):
            await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
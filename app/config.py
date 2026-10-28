from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # 可选：装了 python-dotenv（uvicorn[standard] 会带）就自动读 .env
    from dotenv import load_dotenv

    # 明确指向项目根目录，避免从别的目录启动 uvicorn 时读不到 .env
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover - 没装也不影响 Mock 模式
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # LLM
    mock_llm: bool = True
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4.1-flash"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 0.5
    tutor_llm_for_answers: bool = False

    # 持久化：DATABASE_URL 非空走 PostgreSQL，否则回退 SQLite
    database_url: str = ""
    sqlite_path: str = "knowledge.db"
    db_pool_min: int = 1
    db_pool_max: int = 10

    # 教学策略
    mastery_threshold: float = 0.6
    weakness_threshold: float = 0.3
    hint_after_attempts: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mock_llm=_env_bool("MOCK_LLM", True),
            llm_base_url=os.getenv(
                "LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            # 兼容阿里云常见的 DASHSCOPE_API_KEY 变量名
            llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4.1-flash"),
            llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 20.0),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", 2),
            llm_retry_backoff_seconds=_env_float("LLM_RETRY_BACKOFF_SECONDS", 0.5),
            tutor_llm_for_answers=_env_bool("TUTOR_LLM_FOR_ANSWERS", False),
            database_url=os.getenv("DATABASE_URL", "").strip(),
            sqlite_path=os.getenv("SQLITE_PATH", "knowledge.db"),
            db_pool_min=_env_int("DB_POOL_MIN", 1),
            db_pool_max=_env_int("DB_POOL_MAX", 10),
            mastery_threshold=_env_float("MASTERY_THRESHOLD", 0.6),
            weakness_threshold=_env_float("WEAKNESS_THRESHOLD", 0.3),
            hint_after_attempts=_env_int("HINT_AFTER_ATTEMPTS", 2),
        )


def get_settings() -> Settings:
    return Settings.from_env()
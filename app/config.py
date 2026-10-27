from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    mock_llm: bool = True
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 8.0
    mastery_threshold: float = 0.6
    weakness_threshold: float = 0.3
    hint_after_attempts: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mock_llm=_env_bool("MOCK_LLM", True),
            llm_base_url=os.getenv("LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", cls.llm_model),
            llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 8.0),
            mastery_threshold=_env_float("MASTERY_THRESHOLD", 0.6),
            weakness_threshold=_env_float("WEAKNESS_THRESHOLD", 0.3),
            hint_after_attempts=int(os.getenv("HINT_AFTER_ATTEMPTS", "2")),
        )


def get_settings() -> Settings:
    return Settings.from_env()
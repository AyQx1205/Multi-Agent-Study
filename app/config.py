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


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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
    # 生产环境设为 false：只允许 Alembic 改表；开发/测试保留自动建表方便启动
    auto_apply_schema: bool = True

    # 认证：登录会话有效期；生产环境建议放入 HttpOnly Cookie 或接 OIDC
    session_ttl_hours: int = 24
    # 平台运维令牌：用于创建新学校（多租户开通），未配置时该接口关闭
    platform_admin_token: str = ""

    # 安全：CORS 白名单、登录限流、WebSocket 一次性 ticket
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    )
    login_max_failures: int = 5
    login_ip_max_failures: int = 30
    login_window_seconds: int = 900
    login_lock_seconds: int = 900
    ws_ticket_ttl_seconds: int = 30
    # 仅用于兼容旧客户端；生产保持 false，WebSocket 必须使用 ticket。
    ws_allow_query_token: bool = False
    # 单帧最大字符数；防止一个超大 payload 直接进内存/进 prompt。
    ws_max_message_chars: int = 4096

    # 交互限流（按用户、每分钟）。目前是进程内实现，多实例时需要换成共享存储。
    submit_rate_per_minute: int = 30
    message_rate_per_minute: int = 10

    # 教学策略
    mastery_threshold: float = 0.6
    weakness_threshold: float = 0.3
    hint_after_attempts: int = 2
    # 答错后要答几次才揭示标准答案：防止「乱答一次换答案」再重交刷掌握度。
    answer_reveal_after_attempts: int = 2
    # 距上次活动超过这么久，就视为新一次学习会话（重置会话时长/疲劳判断）。
    engagement_session_gap_minutes: int = 30

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
            auto_apply_schema=_env_bool("AUTO_APPLY_SCHEMA", True),
            session_ttl_hours=_env_int("SESSION_TTL_HOURS", 24),
            platform_admin_token=os.getenv("PLATFORM_ADMIN_TOKEN", "").strip(),
            cors_origins=_env_csv(
                "CORS_ORIGINS",
                (
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                    "http://localhost:8000",
                    "http://127.0.0.1:8000",
                ),
            ),
            login_max_failures=_env_int("LOGIN_MAX_FAILURES", 5),
            login_ip_max_failures=_env_int("LOGIN_IP_MAX_FAILURES", 30),
            login_window_seconds=_env_int("LOGIN_WINDOW_SECONDS", 900),
            login_lock_seconds=_env_int("LOGIN_LOCK_SECONDS", 900),
            ws_ticket_ttl_seconds=_env_int("WS_TICKET_TTL_SECONDS", 30),
            ws_allow_query_token=_env_bool("WS_ALLOW_QUERY_TOKEN", False),
            ws_max_message_chars=_env_int("WS_MAX_MESSAGE_CHARS", 4096),
            submit_rate_per_minute=_env_int("SUBMIT_RATE_PER_MINUTE", 30),
            message_rate_per_minute=_env_int("MESSAGE_RATE_PER_MINUTE", 10),
            mastery_threshold=_env_float("MASTERY_THRESHOLD", 0.6),
            weakness_threshold=_env_float("WEAKNESS_THRESHOLD", 0.3),
            hint_after_attempts=_env_int("HINT_AFTER_ATTEMPTS", 2),
            answer_reveal_after_attempts=_env_int("ANSWER_REVEAL_AFTER_ATTEMPTS", 2),
            engagement_session_gap_minutes=_env_int("ENGAGEMENT_SESSION_GAP_MINUTES", 30),
        )


def get_settings() -> Settings:
    return Settings.from_env()
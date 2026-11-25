"""跨层共享的领域异常。"""

from __future__ import annotations


class IdempotencyConflictError(ValueError):
    """同一个幂等键被用于不同的题目/答案。"""


class BootstrapAlreadyCompletedError(RuntimeError):
    """系统已经有用户，`/auth/bootstrap` 只能成功一次。"""


class QuestionNotFoundError(LookupError):
    """题目不存在；API 层转成 404。

    放在 core.errors 而不是 orchestrator：路由和 WebSocket 不该为了一个
    异常类型反过来依赖编排器的实现模块。
    """

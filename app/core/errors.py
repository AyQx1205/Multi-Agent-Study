"""跨层共享的领域异常。"""

from __future__ import annotations


class IdempotencyConflictError(ValueError):
    """同一个幂等键被用于不同的题目/答案。"""
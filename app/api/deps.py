"""FastAPI 认证依赖：Bearer token -> Principal，以及租户/班级权限判断。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.accounts import Principal, Role, User
from app.security import hash_session_token

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorized()

    store = getattr(request.app.state, "orchestrator", None)
    store = store.store if store is not None else None
    if store is None:
        raise _unauthorized("storage unavailable")

    session = await store.get_session(hash_session_token(credentials.credentials))
    if session is None or session.revoked_at is not None:
        raise _unauthorized("invalid or revoked token")

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise _unauthorized("token expired")

    user = await store.get_user_by_id(session.user_id)
    if user is None or not user.is_active:
        raise _unauthorized("user disabled")
    return user


async def get_principal(user: User = Depends(get_current_user)) -> Principal:
    return Principal(
        user_id=user.id,
        org_id=user.org_id,
        role=user.role,
        email=user.email,
        display_name=user.display_name,
    )


def require_roles(*roles: Role) -> Callable:
    allowed = set(roles)

    async def _dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient permissions",
            )
        return principal

    return _dependency


async def ensure_learner_access(
    request: Request, principal: Principal, learner_id: str
) -> None:
    """学生只能看自己；老师只能看自己班学生；管理员限本组织。"""
    if principal.user_id == learner_id:
        return

    store = request.app.state.orchestrator.store
    if principal.is_admin:
        if await store.user_in_org(learner_id, principal.org_id):
            return
    elif principal.is_teacher and await store.is_teacher_of_student(
        principal.user_id, learner_id
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="learner is outside your organization/classes",
    )
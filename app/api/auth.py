"""登录、组织/班级管理。

首个管理员通过 `/auth/bootstrap` 创建，只允许在系统没有任何用户时调用；
之后由管理员创建教师/学生，教师创建班级并加入学生。
"""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.api.deps import (
    bearer_scheme,
    get_principal,
    require_roles,
)
from app.audit import client_ip, record_audit
from app.config import get_settings
from app.core.accounts import Principal, Role, User
from app.core.errors import BootstrapAlreadyCompletedError
from app.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)
from app.ws_manager import manager

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class BootstrapRequest(BaseModel):
    org_name: str = Field(min_length=2, max_length=100)
    org_slug: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9-]+$")
    admin_email: str = Field(max_length=200, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="管理员", min_length=1, max_length=80)


class LoginRequest(BaseModel):
    org_slug: str = Field(min_length=2, max_length=40)
    email: str = Field(max_length=200, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class CreateUserRequest(BaseModel):
    email: str = Field(max_length=200, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    role: Role = Role.STUDENT


class CreateClassRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9-]+$")
    teacher_id: str | None = None


class EnrollRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=80)


def _public_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "org_id": user.org_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }


async def _issue_session(
    request: Request, user: User
) -> tuple[str, datetime]:
    settings = get_settings()
    token = new_session_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.session_ttl_hours)
    await request.app.state.orchestrator.store.create_session(
        hash_session_token(token), user.id, now, expires_at
    )
    return token, expires_at


def _session_payload(user: User, token: str, expires_at: datetime) -> dict[str, Any]:
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "user": _public_user(user),
    }


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap(req: BootstrapRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.orchestrator.store
    # 检查「系统还没有用户」和写入必须原子：分两步的话，两个并发请求会各自
    # 建出一套组织 + 管理员（之前在 count_users() 与 create_* 之间就有这个窗口）。
    try:
        org, admin = await store.create_organization_with_admin(
            req.org_name,
            req.org_slug,
            req.admin_email,
            hash_password(req.password),
            req.display_name,
            require_no_users=True,
        )
    except BootstrapAlreadyCompletedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="bootstrap already completed",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    token, expires_at = await _issue_session(request, admin)
    await record_audit(
        store,
        "bootstrap",
        org_id=org.id,
        actor_user_id=admin.id,
        request=request,
        target_type="organization",
        target_id=org.id,
    )
    return {"organization": {"id": org.id, "name": org.name, "slug": org.slug}} | _session_payload(
        admin, token, expires_at
    )


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    req: BootstrapRequest,
    request: Request,
    platform_token: str = Header(default="", alias="X-Platform-Token"),
) -> dict[str, Any]:
    """平台运维开通新学校；需配置 PLATFORM_ADMIN_TOKEN。"""
    expected = get_settings().platform_admin_token
    if not expected or not hmac.compare_digest(platform_token, expected):
        raise HTTPException(status_code=403, detail="platform onboarding disabled")

    store = request.app.state.orchestrator.store
    try:
        # 同样走单事务：组织与管理员要么都建好，要么都不建，避免留下孤儿组织。
        org, admin = await store.create_organization_with_admin(
            req.org_name,
            req.org_slug,
            req.admin_email,
            hash_password(req.password),
            req.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_audit(
        store,
        "organization.onboard",
        org_id=org.id,
        request=request,
        target_type="organization",
        target_id=org.id,
        metadata={"admin_user_id": admin.id},
    )
    return {
        "organization": {"id": org.id, "name": org.name, "slug": org.slug},
        "admin": _public_user(admin),
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request) -> dict[str, Any]:
    settings = get_settings()
    store = request.app.state.orchestrator.store
    now = datetime.now(UTC)
    email = req.email.lower().strip()
    ip = client_ip(request) or "unknown"
    # 只锁定「账号 + 来源 IP」组合，避免攻击者用少量失败请求锁死全公司账号。
    throttle_key = f"login:{req.org_slug.lower()}:{email}:{ip}"
    ip_key = f"login-ip:{ip}"

    org = await store.get_organization_by_slug(req.org_slug)
    org_id = org.id if org is not None else None

    # 前置锁定只看「账号 + IP」。来源 IP 的总量阈值不参与前置判断：
    # 学校/公司共用出口 IP 时，别人的失败不应该把正常用户挡在门外
    # （否则一个 NAT 后面的人互相锁死）。IP 阈值只在失败路径上生效。
    pair_locked = await store.login_locked_until(throttle_key, now)
    if pair_locked is not None:
        retry_after = max(1, int((pair_locked - now).total_seconds()))
        await record_audit(
            store,
            "login.blocked",
            org_id=org_id,
            request=request,
            metadata={"reason": "account_locked", "retry_after": retry_after},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    async def reject(
        reason: str, status_code: int, detail: str, user_id: str | None = None
    ) -> NoReturn:
        # 来源 IP 的总量阈值只在**会失败**的请求上生效：有效的账号密码
        # 永远放行，所以学校 NAT 后面不会被别人的失败连带锁死。
        ip_locked = await store.login_locked_until(ip_key, now)
        if ip_locked is not None:
            retry_after = max(1, int((ip_locked - now).total_seconds()))
            # 审计必须带 org_id，否则管理员在 /auth/audit 里看不到「有人被锁」。
            await record_audit(
                store,
                "login.blocked",
                org_id=org_id,
                actor_user_id=user_id,
                request=request,
                metadata={"reason": "ip_locked", "trigger": reason},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many failed login attempts",
                headers={"Retry-After": str(retry_after)},
            )

        # 两条线并行累积：账号+IP（针对账号爆破）与来源 IP 总量（针对撞库）。
        await store.record_login_failure(
            throttle_key,
            now,
            window_seconds=settings.login_window_seconds,
            max_failures=settings.login_max_failures,
            lock_seconds=settings.login_lock_seconds,
        )
        await store.record_login_failure(
            ip_key,
            now,
            window_seconds=settings.login_window_seconds,
            max_failures=settings.login_ip_max_failures,
            lock_seconds=settings.login_lock_seconds,
        )
        await record_audit(
            store,
            "login.failure",
            org_id=org_id,
            actor_user_id=user_id,
            request=request,
            metadata={"reason": reason},
        )
        raise HTTPException(status_code=status_code, detail=detail)

    if org is None:
        await reject("unknown_organization", 401, "invalid credentials")

    credentials = await store.get_user_credentials(org.id, email)
    if credentials is None:
        await reject("unknown_user", 401, "invalid credentials")
    user, password_hash = credentials
    if not verify_password(req.password, password_hash):
        await reject("bad_password", 401, "invalid credentials", user.id)
    if not user.is_active:
        await reject("disabled", 403, "user disabled", user.id)

    # 成功只清「账号+IP」计数。来源 IP 的总量计数保留：否则攻击者只要有
    # 一个自己的账号，就能反复登录把自己的撞库尝试清零。
    await store.reset_login_failures(throttle_key)
    token, expires_at = await _issue_session(request, user)
    await record_audit(
        store,
        "login.success",
        org_id=user.org_id,
        actor_user_id=user.id,
        request=request,
    )
    return _session_payload(user, token, expires_at)


@router.post("/ws-ticket", status_code=status.HTTP_201_CREATED)
async def issue_ws_ticket(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    """浏览器 WebSocket 无法设置 Authorization 头，改用短时一次性 ticket。"""
    if not principal.is_student:
        raise HTTPException(status_code=403, detail="student account required")
    settings = get_settings()
    store = request.app.state.orchestrator.store
    ticket = new_session_token()
    now = datetime.now(UTC)
    ttl = max(5, min(settings.ws_ticket_ttl_seconds, 300))
    expires_at = now + timedelta(seconds=ttl)
    await store.create_ws_ticket(
        hash_session_token(ticket), principal.user_id, now, expires_at
    )
    await record_audit(
        store,
        "ws.ticket.issue",
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        request=request,
        target_type="user",
        target_id=principal.user_id,
    )
    return {"ticket": ticket, "expires_at": expires_at.isoformat()}


@router.post("/logout")
async def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str]:
    if credentials is not None and credentials.credentials:
        store = request.app.state.orchestrator.store
        token_hash = hash_session_token(credentials.credentials)
        session = await store.get_session(token_hash)
        user = await store.get_user_by_id(session.user_id) if session else None
        await store.revoke_session(token_hash)
        if user is not None:
            # 撤销会话之后，已经建立的 WebSocket 并不会自己失效：
            # 必须主动踢掉，否则等于留了一条永久有效的旁路。
            closed = await manager.close_learner(user.id, reason="logged out")
            if closed:
                logger.info("closed %d websocket(s) for %s on logout", closed, user.id)
            await record_audit(
                store,
                "logout",
                org_id=user.org_id,
                actor_user_id=user.id,
                request=request,
                target_type="user",
                target_id=user.id,
                metadata={"websockets_closed": closed},
            )
    return {"status": "logged_out"}


@router.get("/me")
async def me(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {
        "id": principal.user_id,
        "org_id": principal.org_id,
        "email": principal.email,
        "display_name": principal.display_name,
        "role": principal.role.value,
    }


@router.get("/users")
async def list_users(
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    users = await request.app.state.orchestrator.store.list_users(principal.org_id)
    return {"count": len(users), "items": [_public_user(user) for user in users]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    req: CreateUserRequest,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    if req.role is Role.ADMIN:
        raise HTTPException(
            status_code=400,
            detail="use a separate admin bootstrap flow to create another admin",
        )
    try:
        user = await request.app.state.orchestrator.store.create_user(
            principal.org_id,
            req.email,
            hash_password(req.password),
            req.display_name,
            req.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_audit(
        request.app.state.orchestrator.store,
        "user.create",
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        request=request,
        target_type="user",
        target_id=user.id,
        metadata={"role": user.role.value},
    )
    return _public_user(user)


@router.get("/classes")
async def list_classes(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    store = request.app.state.orchestrator.store
    user = await store.get_user_by_id(principal.user_id)
    classes = await store.list_classes_for_user(user)
    return {
        "count": len(classes),
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "code": c.code,
                "teacher_id": c.teacher_id,
                "created_at": c.created_at.isoformat(),
            }
            for c in classes
        ],
    }


@router.post("/classes", status_code=status.HTTP_201_CREATED)
async def create_class(
    req: CreateClassRequest,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict[str, Any]:
    store = request.app.state.orchestrator.store
    teacher_id = req.teacher_id or (principal.user_id if principal.is_teacher else None)
    if teacher_id is not None:
        teacher = await store.get_user_by_id(teacher_id)
        if (
            teacher is None
            or teacher.org_id != principal.org_id
            or teacher.role is not Role.TEACHER
        ):
            raise HTTPException(status_code=400, detail="invalid teacher in this organization")
        if principal.is_teacher and teacher_id != principal.user_id:
            raise HTTPException(status_code=403, detail="teachers can only own their classes")

    try:
        classroom = await store.create_class(
            principal.org_id, req.name, req.code, teacher_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_audit(
        store,
        "class.create",
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        request=request,
        target_type="class",
        target_id=classroom.id,
        metadata={"teacher_id": classroom.teacher_id},
    )
    return {
        "id": classroom.id,
        "name": classroom.name,
        "code": classroom.code,
        "teacher_id": classroom.teacher_id,
        "created_at": classroom.created_at.isoformat(),
    }


@router.post("/classes/{class_id}/enroll")
async def enroll_student(
    class_id: str,
    req: EnrollRequest,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict[str, str]:
    store = request.app.state.orchestrator.store
    classroom = await store.get_class(class_id)
    if classroom is None or classroom.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="class not found")
    if principal.is_teacher and classroom.teacher_id != principal.user_id:
        raise HTTPException(status_code=403, detail="not your class")

    student = await store.get_user_by_id(req.user_id)
    if (
        student is None
        or student.org_id != principal.org_id
        or student.role is not Role.STUDENT
    ):
        raise HTTPException(status_code=400, detail="student not found in this organization")
    await store.add_enrollment(class_id, student.id)
    await record_audit(
        store,
        "class.enroll",
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        request=request,
        target_type="class",
        target_id=class_id,
        metadata={"student_id": student.id},
    )
    return {"status": "enrolled", "class_id": class_id, "user_id": student.id}


@router.get("/classes/{class_id}/students")
async def class_students(
    class_id: str,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.TEACHER)),
) -> dict[str, Any]:
    store = request.app.state.orchestrator.store
    classroom = await store.get_class(class_id)
    if classroom is None or classroom.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="class not found")
    if principal.is_teacher and classroom.teacher_id != principal.user_id:
        raise HTTPException(status_code=403, detail="not your class")
    students = await store.list_class_students(class_id)
    return {"count": len(students), "items": [_public_user(s) for s in students]}


@router.get("/audit")
async def list_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    entries = await request.app.state.orchestrator.store.list_audit_logs(
        principal.org_id, limit
    )
    return {
        "count": len(entries),
        "items": [
            {
                "id": entry.id,
                "org_id": entry.org_id,
                "actor_user_id": entry.actor_user_id,
                "action": entry.action,
                "target_type": entry.target_type,
                "target_id": entry.target_id,
                "ip": entry.ip,
                "user_agent": entry.user_agent,
                "metadata": entry.metadata,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in entries
        ],
    }

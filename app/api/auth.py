"""登录、组织/班级管理。

首个管理员通过 `/auth/bootstrap` 创建，只允许在系统没有任何用户时调用；
之后由管理员创建教师/学生，教师创建班级并加入学生。
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.api.deps import (
    bearer_scheme,
    get_principal,
    require_roles,
)
from app.config import get_settings
from app.core.accounts import Principal, Role, User
from app.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

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
    now = datetime.now(timezone.utc)
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
    if await store.count_users() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="bootstrap already completed",
        )

    try:
        org = await store.create_organization(req.org_name, req.org_slug)
        admin = await store.create_user(
            org.id,
            req.admin_email,
            hash_password(req.password),
            req.display_name,
            Role.ADMIN,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    token, expires_at = await _issue_session(request, admin)
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
        org = await store.create_organization(req.org_name, req.org_slug)
        admin = await store.create_user(
            org.id,
            req.admin_email,
            hash_password(req.password),
            req.display_name,
            Role.ADMIN,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "organization": {"id": org.id, "name": org.name, "slug": org.slug},
        "admin": _public_user(admin),
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.orchestrator.store
    org = await store.get_organization_by_slug(req.org_slug)
    if org is None:
        raise HTTPException(status_code=401, detail="invalid credentials")

    credentials = await store.get_user_credentials(org.id, req.email)
    if credentials is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    user, password_hash = credentials
    if not verify_password(req.password, password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="user disabled")

    token, expires_at = await _issue_session(request, user)
    return _session_payload(user, token, expires_at)


@router.post("/logout")
async def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str]:
    if credentials is not None and credentials.credentials:
        await request.app.state.orchestrator.store.revoke_session(
            hash_session_token(credentials.credentials)
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
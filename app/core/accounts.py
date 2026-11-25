"""组织、用户、班级与会话模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"


class Organization(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime


class User(BaseModel):
    id: str
    org_id: str
    email: str
    display_name: str
    role: Role
    is_active: bool = True
    created_at: datetime


class Classroom(BaseModel):
    id: str
    org_id: str
    name: str
    code: str
    teacher_id: str | None = None
    created_at: datetime


class SessionRecord(BaseModel):
    token_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


class AuditLogEntry(BaseModel):
    id: str
    org_id: str | None = None
    actor_user_id: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Principal(BaseModel):
    """认证后的调用者；不包含密码。"""

    user_id: str
    org_id: str
    role: Role
    email: str
    display_name: str = Field(default="")

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_teacher(self) -> bool:
        return self.role is Role.TEACHER

    @property
    def is_student(self) -> bool:
        return self.role is Role.STUDENT

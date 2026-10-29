from __future__ import annotations

import os

# 单元测试不连真实数据库：强制走内存 SQLite，避免读到 .env 里的 PostgreSQL。
# PostgreSQL 集成测试单独用 TEST_DATABASE_URL（见 tests/test_store_postgres.py）。
os.environ["DATABASE_URL"] = ""
os.environ["SQLITE_PATH"] = ":memory:"

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.events import Event, EventBus
from app.orchestrator import AgentOrchestrator


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin(client):
    response = client.post(
        "/auth/bootstrap",
        json={
            "org_name": "测试学校",
            "org_slug": "test-school",
            "admin_email": "admin@test.local",
            "password": "admin-pass-123",
            "display_name": "测试管理员",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "client": client,
        "token": body["access_token"],
        "user": body["user"],
        "org": body["organization"],
        "headers": auth_headers(body["access_token"]),
    }


def _create_and_login(client, admin_token, email: str, display_name: str, role: str):
    created = client.post(
        "/auth/users",
        headers=auth_headers(admin_token),
        json={
            "email": email,
            "password": "student-pass-123",
            "display_name": display_name,
            "role": role,
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()
    login = client.post(
        "/auth/login",
        json={
            "org_slug": "test-school",
            "email": email,
            "password": "student-pass-123",
        },
    )
    assert login.status_code == 200, login.text
    body = login.json()
    return {
        "client": client,
        "id": user["id"],
        "token": body["access_token"],
        "user": user,
        "headers": auth_headers(body["access_token"]),
    }


@pytest.fixture
def student(admin):
    return _create_and_login(
        admin["client"], admin["token"], "student@test.local", "测试学生", "student"
    )


@pytest.fixture
def teacher(admin):
    return _create_and_login(
        admin["client"], admin["token"], "teacher@test.local", "测试老师", "teacher"
    )


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def orch() -> AgentOrchestrator:
    return AgentOrchestrator()


@pytest.fixture
def make_orch():
    """需要注入时钟或 LLM 时用。"""
    created: list[AgentOrchestrator] = []

    def factory(**kwargs) -> AgentOrchestrator:
        orchestrator = AgentOrchestrator(**kwargs)
        created.append(orchestrator)
        return orchestrator

    return factory


@pytest.fixture
def capture(orch):
    """收集编排器总线上的指定类型事件。"""
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    def subscribe(*event_types) -> list[Event]:
        for event_type in event_types:
            orch.event_bus.subscribe(event_type, handler)
        return received

    return subscribe


@pytest.fixture
def fixed_clock():
    value = datetime(2026, 1, 1, 10, 0, 0)
    return lambda: value
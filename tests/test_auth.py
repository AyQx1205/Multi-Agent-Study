"""登录、会话、组织/班级隔离。"""

from __future__ import annotations

import asyncio

from app.core.accounts import Role
from app.security import hash_password
from tests.conftest import auth_headers


def _create_student(admin, email: str, name: str) -> dict:
    created = admin["client"].post(
        "/auth/users",
        headers=admin["headers"],
        json={
            "email": email,
            "password": "student-pass-123",
            "display_name": name,
            "role": "student",
        },
    )
    assert created.status_code == 201, created.text
    login = admin["client"].post(
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
        "id": created.json()["id"],
        "token": body["access_token"],
        "headers": auth_headers(body["access_token"]),
    }


def test_bootstrap_only_once(admin) -> None:
    response = admin["client"].post(
        "/auth/bootstrap",
        json={
            "org_name": "另一所学校",
            "org_slug": "another-school",
            "admin_email": "other-admin@test.local",
            "password": "admin-pass-123",
        },
    )
    assert response.status_code == 409


def test_login_and_me(admin) -> None:
    response = admin["client"].post(
        "/auth/login",
        json={
            "org_slug": "test-school",
            "email": "admin@test.local",
            "password": "admin-pass-123",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "admin"

    me = admin["client"].get("/auth/me", headers=auth_headers(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "admin@test.local"


def test_wrong_password_is_rejected(admin) -> None:
    response = admin["client"].post(
        "/auth/login",
        json={
            "org_slug": "test-school",
            "email": "admin@test.local",
            "password": "wrong-password",
        },
    )
    assert response.status_code == 401


def test_logout_revokes_token(admin) -> None:
    token = admin["token"]
    assert admin["client"].get("/auth/me", headers=auth_headers(token)).status_code == 200
    assert admin["client"].post("/auth/logout", headers=auth_headers(token)).status_code == 200
    assert admin["client"].get("/auth/me", headers=auth_headers(token)).status_code == 401


def test_student_cannot_read_another_student(admin, student) -> None:
    other = _create_student(admin, "other@test.local", "另一个学生")
    response = student["client"].get(
        f"/progress/{other['id']}", headers=student["headers"]
    )
    assert response.status_code == 403


def test_teacher_can_read_only_enrolled_students(admin, teacher, student) -> None:
    other = _create_student(admin, "not-in-class@test.local", "未入班学生")
    class_response = teacher["client"].post(
        "/auth/classes",
        headers=teacher["headers"],
        json={"name": "七年级一班", "code": "G7-1"},
    )
    assert class_response.status_code == 201, class_response.text
    class_id = class_response.json()["id"]

    enrolled = teacher["client"].post(
        f"/auth/classes/{class_id}/enroll",
        headers=teacher["headers"],
        json={"user_id": student["id"]},
    )
    assert enrolled.status_code == 200, enrolled.text

    assert teacher["client"].get(
        f"/progress/{student['id']}", headers=teacher["headers"]
    ).status_code == 200
    assert teacher["client"].get(
        f"/progress/{other['id']}", headers=teacher["headers"]
    ).status_code == 403

    students = teacher["client"].get(
        f"/auth/classes/{class_id}/students", headers=teacher["headers"]
    ).json()
    assert [item["id"] for item in students["items"]] == [student["id"]]


def test_teacher_cannot_submit_answers(teacher) -> None:
    response = teacher["client"].post(
        "/submit",
        headers={**teacher["headers"], "Idempotency-Key": "teacher-submit"},
        json={"question_id": "q-rational_numbers-2", "answer": "7"},
    )
    assert response.status_code == 403


def test_admin_cannot_read_cross_org_user(admin) -> None:
    store = admin["client"].app.state.orchestrator.store
    org = asyncio.run(store.create_organization("隔离学校", "isolated-school"))
    other = asyncio.run(
        store.create_user(
            org.id,
            "isolated@test.local",
            hash_password("student-pass-123"),
            "外校学生",
            Role.STUDENT,
        )
    )
    response = admin["client"].get(
        f"/progress/{other.id}", headers=admin["headers"]
    )
    assert response.status_code == 403


def test_admin_can_read_same_org_student(admin, student) -> None:
    response = admin["client"].get(
        f"/progress/{student['id']}", headers=admin["headers"]
    )
    assert response.status_code == 200


def test_platform_can_onboard_another_school(admin, monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", "platform-secret")
    response = admin["client"].post(
        "/auth/organizations",
        headers={"X-Platform-Token": "platform-secret"},
        json={
            "org_name": "第二所学校",
            "org_slug": "second-school",
            "admin_email": "admin@second.local",
            "password": "second-admin-123",
            "display_name": "第二管理员",
        },
    )
    assert response.status_code == 201, response.text
    second_admin_id = response.json()["admin"]["id"]

    # 第一所学校的 admin 不能读第二所学校的管理员/学生数据。
    denied = admin["client"].get(
        f"/progress/{second_admin_id}", headers=admin["headers"]
    )
    assert denied.status_code == 403

    login = admin["client"].post(
        "/auth/login",
        json={
            "org_slug": "second-school",
            "email": "admin@second.local",
            "password": "second-admin-123",
        },
    )
    assert login.status_code == 200
    second_token = login.json()["access_token"]
    allowed = admin["client"].get(
        f"/progress/{second_admin_id}",
        headers=auth_headers(second_token),
    )
    assert allowed.status_code == 200
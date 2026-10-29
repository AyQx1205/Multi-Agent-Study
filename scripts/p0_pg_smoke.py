"""P0-1 两实例 PostgreSQL 冒烟：并发提交、跨实例重放、冲突。"""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

A = "http://127.0.0.1:8010"
B = "http://127.0.0.1:8011"
SUFFIX = uuid.uuid4().hex[:8]
ORG_SLUG = f"p0-smoke-{SUFFIX}"
ADMIN_EMAIL = f"admin-{SUFFIX}@p0.local"
STUDENT_EMAIL = f"student-{SUFFIX}@p0.local"
PASSWORD = "p0-smoke-pass-123"


def request(
    base: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    idem: str | None = None,
    **kwargs,
) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idem:
        headers["Idempotency-Key"] = idem
    with httpx.Client(timeout=30) as client:
        return client.request(method, f"{base}{path}", headers=headers, **kwargs)


def main() -> None:
    org = request(
        A,
        "POST",
        "/auth/bootstrap",
        json={
            "org_name": f"P0 Smoke {SUFFIX}",
            "org_slug": ORG_SLUG,
            "admin_email": ADMIN_EMAIL,
            "password": PASSWORD,
            "display_name": "P0 Admin",
        },
    )
    org.raise_for_status()
    admin_token = org.json()["access_token"]

    created = request(
        A,
        "POST",
        "/auth/users",
        token=admin_token,
        json={
            "email": STUDENT_EMAIL,
            "password": PASSWORD,
            "display_name": "P0 Student",
            "role": "student",
        },
    )
    created.raise_for_status()
    student_id = created.json()["id"]

    login = request(
        B,
        "POST",
        "/auth/login",
        json={"org_slug": ORG_SLUG, "email": STUDENT_EMAIL, "password": PASSWORD},
    )
    login.raise_for_status()
    student_token = login.json()["access_token"]

    def submit(base: str, key: str, answer: str = "7") -> httpx.Response:
        return request(
            base,
            "POST",
            "/submit",
            token=student_token,
            idem=key,
            json={
                "question_id": "q-rational_numbers-2",
                "answer": answer,
                "time_spent_seconds": 5,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, A, f"p0-a-{SUFFIX}")
        second = pool.submit(submit, B, f"p0-b-{SUFFIX}")
        responses = [first.result(), second.result()]

    for response in responses:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["replayed"] is False
        assert body["is_correct"] is True

    attempts = request(
        B, "GET", f"/attempts/{student_id}", token=student_token
    )
    attempts.raise_for_status()
    attempt_body = attempts.json()
    progress = request(
        B, "GET", f"/progress/{student_id}", token=student_token
    )
    progress.raise_for_status()
    progress_body = progress.json()

    replay = submit(
        B,
        f"p0-a-{SUFFIX}",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["events_triggered"] == 0

    conflict = submit(
        A,
        f"p0-a-{SUFFIX}",
        answer="999",
    )
    assert conflict.status_code == 409, conflict.text

    after = request(
        A, "GET", f"/attempts/{student_id}", token=student_token
    ).json()

    print(
        json.dumps(
            {
                "org_slug": ORG_SLUG,
                "student_id": student_id,
                "concurrent_statuses": [r.status_code for r in responses],
                "concurrent_attempt_ids": [r.json()["attempt_id"] for r in responses],
                "attempt_count": attempt_body["count"],
                "after_replay_count": after["count"],
                "progress": progress_body.get("progress", {}),
                "replay": replay.json(),
                "conflict_status": conflict.status_code,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
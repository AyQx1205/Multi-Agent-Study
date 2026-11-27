"""端到端冒烟：对着**已经启动**的服务跑一遍真实用户链路。

为什么需要它：`/ready` 只证明进程连得上库，证明不了「表结构能不能支撑一次写入」。
曾经就有一个只在生产口径下出现的缺陷（迁移建的 `learners` 表少了 `created_at`
默认值），`/ready` 返回 200，第一次答题才炸。这个脚本会把那条路径跑一遍。

用法：

    python scripts/smoke_e2e.py                      # 默认 http://127.0.0.1:8000
    python scripts/smoke_e2e.py http://127.0.0.1:8077

前提：目标库必须是**空库**（脚本会调用 /auth/bootstrap）。非空时返回 409，
脚本会提示改用已有账号，并以非零码退出。

只用标准库，方便在没装依赖的环境（比如 CI 的容器冒烟步骤）里直接跑。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8000"

ORG_SLUG = "smoke-school"
ADMIN_EMAIL = "admin@smoke.local"
STUDENT_EMAIL = "student@smoke.local"
PASSWORD = "smoke-pass-123"


def call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers=merged, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:300]}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE).rstrip("/")

    status, ready = call(base, "/ready")
    print(f"/ready                 -> {status} {ready}")
    if status != 200:
        fail("服务未就绪")

    status, boot = call(
        base,
        "/auth/bootstrap",
        method="POST",
        body={
            "org_name": "冒烟测试学校",
            "org_slug": ORG_SLUG,
            "admin_email": ADMIN_EMAIL,
            "password": PASSWORD,
        },
    )
    if status == 409:
        fail("目标库不是空的（/auth/bootstrap 返回 409）；冒烟需要干净的库")
    if status != 201:
        fail(f"/auth/bootstrap -> {status} {boot}")
    admin_headers = {"Authorization": f"Bearer {boot['access_token']}"}
    print(f"/auth/bootstrap        -> {status}")

    status, created = call(
        base,
        "/auth/users",
        method="POST",
        headers=admin_headers,
        body={
            "email": STUDENT_EMAIL,
            "password": PASSWORD,
            "display_name": "冒烟学生",
            "role": "student",
        },
    )
    if status != 201:
        fail(f"/auth/users -> {status} {created}")
    print(f"/auth/users            -> {status}")

    status, login = call(
        base,
        "/auth/login",
        method="POST",
        body={"org_slug": ORG_SLUG, "email": STUDENT_EMAIL, "password": PASSWORD},
    )
    if status != 200:
        fail(f"/auth/login -> {status} {login}")
    student_headers = {"Authorization": f"Bearer {login['access_token']}"}
    learner_id = login["user"]["id"]
    print(f"/auth/login            -> {status}")

    status, bundle = call(base, "/questions?limit=1", headers=student_headers)
    if status != 200 or not bundle.get("items"):
        fail(f"/questions -> {status} {bundle}")
    question = bundle["items"][0]
    print(f"/questions             -> {status} ({question['id']})")

    # 故意答错：事件链会带上 assessment / curriculum 的完整分支，
    # 同时验证「答错时答案不立即揭示」这条路。
    submit_headers = {**student_headers, "Idempotency-Key": "smoke-key-1"}
    status, submitted = call(
        base,
        "/submit",
        method="POST",
        headers=submit_headers,
        body={"question_id": question["id"], "answer": "__definitely_wrong__"},
    )
    if status != 200:
        fail(f"/submit -> {status} {submitted}")
    print(
        f"/submit                -> {status} status={submitted['status']} "
        f"events={submitted['events_triggered']} "
        f"types={[event['type'] for event in submitted['events']]}"
    )
    if submitted["events_triggered"] < 3:
        fail(f"事件链太短：{submitted['events_triggered']}")

    status, replay = call(
        base,
        "/submit",
        method="POST",
        headers=submit_headers,
        body={"question_id": question["id"], "answer": "__definitely_wrong__"},
    )
    if status != 200 or not replay.get("replayed"):
        fail(f"同键重放没有命中幂等：{status} {replay}")
    print(f"/submit (replay)       -> {status} replayed={replay['replayed']}")

    status, progress = call(base, f"/progress/{learner_id}", headers=student_headers)
    if status != 200:
        fail(f"/progress -> {status} {progress}")
    print(
        f"/progress/{{learner}}    -> {status} "
        f"attempts={progress['progress']['total_attempts']}"
    )

    print("\nSMOKE OK")

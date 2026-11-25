"""A 级加固的回归测试。

覆盖：bootstrap 竞态、登录限流的两处缺陷、登出切断 WebSocket、
交互限流与 WS 输入上限、幂等指纹归一化、LLM 不进锁/不进事务、Mock 静默降级。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from app.agents.tutor import TutorAgent  # noqa: F401  (类型提示用)
from app.api.main import build_llm
from app.config import Settings
from app.content import build_junior_math_graph
from app.core.content import Question, QuestionType
from app.core.errors import BootstrapAlreadyCompletedError
from app.orchestrator import AgentOrchestrator
from app.store import SqliteStore
from app.store_base import SqliteStoreAdapter


def _temp_db() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="multiagent-a-", suffix=".db")
    os.close(fd)
    return Path(raw_path)


def _ws_ticket(client, token: str) -> str:
    response = client.post(
        "/auth/ws-ticket", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201, response.text
    return response.json()["ticket"]


# ── A6：bootstrap 竞态 ────────────────────────────────────────────────────


async def test_bootstrap_is_atomic_under_concurrency() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        async def attempt(slug: str):
            return await adapter.create_organization_with_admin(
                f"学校{slug}",
                slug,
                f"admin@{slug}.local",
                "hash",
                "管理员",
                require_no_users=True,
            )

        results = await asyncio.gather(
            attempt("a"), attempt("b"), return_exceptions=True
        )
        ok = [r for r in results if not isinstance(r, BaseException)]
        failed = [r for r in results if isinstance(r, BaseException)]
        assert len(ok) == 1
        assert len(failed) == 1
        assert isinstance(failed[0], BootstrapAlreadyCompletedError)
        # 不能留下第二套组织/管理员。
        assert await adapter.count_users() == 1
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


# ── A7：登录限流 ─────────────────────────────────────────────────────────


def test_valid_login_is_not_blocked_by_ip_throttle(admin, monkeypatch) -> None:
    """同一出口 IP 上别人的失败，不能把正常用户挡在门外。"""
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "20")
    monkeypatch.setenv("LOGIN_IP_MAX_FAILURES", "1")
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "60")
    client = admin["client"]

    wrong = {
        "org_slug": "test-school",
        "email": "someone-else@test.local",
        "password": "nope",
    }
    assert client.post("/auth/login", json=wrong).status_code == 401
    # IP 已被别人的失败锁住：再来一次错误请求应该 429
    assert client.post("/auth/login", json=wrong).status_code == 429

    # 但有效凭据必须照常放行
    ok = client.post(
        "/auth/login",
        json={
            "org_slug": "test-school",
            "email": "admin@test.local",
            "password": "admin-pass-123",
        },
    )
    assert ok.status_code == 200


def test_login_blocked_audit_is_visible_to_admin(admin, monkeypatch) -> None:
    """被锁事件必须带 org_id，否则管理员在 /auth/audit 里永远看不到。"""
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "20")
    monkeypatch.setenv("LOGIN_IP_MAX_FAILURES", "1")
    monkeypatch.setenv("LOGIN_LOCK_SECONDS", "60")
    client = admin["client"]

    wrong = {
        "org_slug": "test-school",
        "email": "victim@test.local",
        "password": "nope",
    }
    client.post("/auth/login", json=wrong)
    blocked = client.post("/auth/login", json=wrong)
    assert blocked.status_code == 429

    entries = client.get("/auth/audit", headers=admin["headers"]).json()["items"]
    blocked_entries = [e for e in entries if e["action"] == "login.blocked"]
    assert blocked_entries
    assert all(e["org_id"] == admin["org"]["id"] for e in blocked_entries)


# ── A8：登出 / 会话失效要切断 WebSocket ──────────────────────────────────


def test_has_active_session_tracks_revocation(student) -> None:
    from datetime import datetime

    from app.security import hash_session_token

    client = student["client"]
    store = client.app.state.orchestrator.store
    now = datetime.now(UTC)
    token_hash = hash_session_token(student["token"])

    async def scenario() -> tuple[bool, bool]:
        before = await store.has_active_session(student["id"], now)
        await store.revoke_session(token_hash)
        after = await store.has_active_session(student["id"], now)
        return before, after

    # 应用的异步存储跑在 TestClient 的 portal 线程里，必须通过它调用。
    before, after = client.portal.call(scenario)
    assert before is True
    assert after is False


def test_logout_closes_open_websocket(client, student) -> None:
    ticket = _ws_ticket(student["client"], student["token"])
    with client.websocket_connect(f"/ws/{student['id']}?ticket={ticket}") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["event_type"] == "pong"

        assert (
            student["client"].post(
                "/auth/logout", headers=student["headers"]
            ).status_code
            == 200
        )

        # 登出后连接必须失效：要么被服务端关掉，要么下一次操作被拒。
        ended = False
        try:
            ws.send_json({"action": "ping"})
            payload = ws.receive_json()
            ended = payload.get("error") == "session_expired"
        except WebSocketDisconnect:
            ended = True
        assert ended


# ── A9：交互限流与 WS 输入上限 ───────────────────────────────────────────


def test_submit_is_rate_limited(student, monkeypatch) -> None:
    monkeypatch.setenv("SUBMIT_RATE_PER_MINUTE", "1")
    payload = {"question_id": "q-rational_numbers-2", "answer": "7"}

    first = student["client"].post(
        "/submit", headers={**student["headers"], "Idempotency-Key": "rl-1"}, json=payload
    )
    assert first.status_code == 200

    second = student["client"].post(
        "/submit", headers={**student["headers"], "Idempotency-Key": "rl-2"}, json=payload
    )
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


def test_websocket_rejects_oversized_frame(client, student) -> None:
    ticket = _ws_ticket(student["client"], student["token"])
    with client.websocket_connect(f"/ws/{student['id']}?ticket={ticket}") as ws:
        ws.send_text("x" * 8000)
        payload = ws.receive_json()
        assert payload["error"] == "message_too_large"


def test_websocket_rejects_overlong_fields(client, student) -> None:
    ticket = _ws_ticket(student["client"], student["token"])
    with client.websocket_connect(f"/ws/{student['id']}?ticket={ticket}") as ws:
        ws.send_json(
            {
                "action": "question",
                "knowledge_id": "k" * 200,
                "question": "为什么？",
            }
        )
        payload = ws.receive_json()
        assert payload["error"] == "field too long"


def test_websocket_submit_is_rate_limited(client, student, monkeypatch) -> None:
    monkeypatch.setenv("SUBMIT_RATE_PER_MINUTE", "1")
    ticket = _ws_ticket(student["client"], student["token"])
    with client.websocket_connect(f"/ws/{student['id']}?ticket={ticket}") as ws:
        ws.send_json(
            {
                "action": "submit",
                "question_id": "q-rational_numbers-2",
                "answer": "7",
                "idempotency_key": "ws-rl-1",
            }
        )
        assert ws.receive_json()["event_type"] == "submission.result"

        ws.send_json(
            {
                "action": "submit",
                "question_id": "q-rational_numbers-3",
                "answer": "2",
                "idempotency_key": "ws-rl-2",
            }
        )
        # 第一次提交的事件帧还在队列里，往后读直到拿到限流错误。
        for _ in range(80):
            payload = ws.receive_json()
            if payload.get("error") == "rate_limited":
                break
        else:  # pragma: no cover - 防御性
            raise AssertionError("expected rate_limited frame")


# ── A10：幂等指纹与判分归一化一致 ────────────────────────────────────────


def test_idempotency_fingerprint_uses_normalized_answer(student) -> None:
    headers = {**student["headers"], "Idempotency-Key": "norm-1"}
    first = student["client"].post(
        "/submit",
        headers=headers,
        json={"question_id": "q-linear_eq-1", "answer": "x=4"},
    )
    assert first.status_code == 200, first.text

    # 只多了一个空格：判分相同，幂等必须识别为重放而不是 409。
    second = student["client"].post(
        "/submit",
        headers=headers,
        json={"question_id": "q-linear_eq-1", "answer": "x = 4"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    assert second.json()["attempt_id"] == first.json()["attempt_id"]


# ── A11：LLM 不进学习者锁、不进数据库事务 ───────────────────────────────


class _LockProbeLLM:
    """在 LLM 被调用的瞬间，记录两把锁是否被持有、以及调用链。"""

    def __init__(self, orchestrator: AgentOrchestrator, adapter: SqliteStoreAdapter) -> None:
        self._orchestrator = orchestrator
        self._adapter = adapter
        self.samples: list[dict[str, object]] = []

    async def complete(self, system, user, *, max_tokens=300, temperature=0.4) -> str:
        import traceback

        frames = [frame.name for frame in traceback.extract_stack()[:-1]]
        self.samples.append(
            {
                "learner_locked": self._orchestrator._lock("s1").locked(),
                "store_locked": self._adapter._lock.locked(),
                "from_enrichment": "enrich_answer" in frames,
            }
        )
        return "先说说你已经知道的部分？"


async def test_llm_is_called_outside_learner_lock_and_transaction() -> None:
    path = _temp_db()
    adapter = SqliteStoreAdapter(SqliteStore(path))
    try:
        await adapter.seed_questions(
            [
                Question(
                    id="q-a11",
                    knowledge_id="linear_eq",
                    grade=7,
                    type=QuestionType.FILL,
                    stem="2x=4，x=?",
                    answer="2",
                    accepted=["x=2"],
                    explanation="两边除以 2。",
                )
            ]
        )
        settings = Settings(tutor_llm_for_answers=True)
        orch = AgentOrchestrator(
            store=adapter, graph=build_junior_math_graph(), settings=settings
        )
        probe = _LockProbeLLM(orch, adapter)
        orch.tutor._llm = probe  # 注入探针，替代真实模型

        outcome = await orch.submit_question("s1", "q-a11", "2", idempotency_key="a11-1")

        # 提交链路：模板优先，唯一的 LLM 调用来自事务提交后的 enrich_answer。
        submit_samples = list(probe.samples)
        assert submit_samples, "预期提交后会补发一条 LLM 讲解"
        assert all(not s["learner_locked"] for s in submit_samples)
        assert all(not s["store_locked"] for s in submit_samples)
        assert all(s["from_enrichment"] for s in submit_samples)

        # 提问链路：LLM 在锁外调用（此时可能有别的协程在写事件，不看 store 锁）。
        probe.samples.clear()
        await orch.ask_question("s1", "linear_eq", "这一步为什么？")
        assert probe.samples
        assert all(not s["learner_locked"] for s in probe.samples)

        # 讲解是在事务提交后补发的
        assert any(e.data.get("enriched") for e in outcome.events)
    finally:
        await adapter.close()
        path.unlink(missing_ok=True)


# ── A12：Mock 静默降级 ───────────────────────────────────────────────────


def test_build_llm_fails_fast_without_key() -> None:
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        build_llm(Settings(mock_llm=False, llm_api_key=""))


def test_health_reports_llm_mode(client) -> None:
    body = client.get("/health").json()
    assert body["llm"] == "mock"

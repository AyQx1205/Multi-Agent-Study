"""REST + WebSocket：认证、判分、幂等与协议契约。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings


def _ws_ticket(client: TestClient, token: str) -> str:
    response = client.post(
        "/auth/ws-ticket", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201, response.text
    return response.json()["ticket"]


def test_health_is_public(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["agents"] == 5
    assert body["content"]["knowledge_nodes"] == 60
    assert body["content"]["questions"] == 180


def test_demo_console_is_served(client: TestClient) -> None:
    response = client.get("/demo")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "multi-agent-lite" in body
    # 页面必须走一次性 ticket 建 WS，且自带幂等键逻辑
    assert "/auth/ws-ticket" in body
    assert "idempotency_key" in body


def test_liveness_and_readiness(client: TestClient) -> None:
    live = client.get("/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["storage"] == "sqlite"


def test_readiness_reports_503_when_database_is_down(
    client: TestClient, monkeypatch
) -> None:
    async def unavailable() -> bool:
        return False

    monkeypatch.setattr(
        client.app.state.orchestrator.store, "healthcheck", unavailable
    )
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_cors_allows_only_whitelisted_origins(client: TestClient) -> None:
    allowed_origin = get_settings().cors_origins[0]
    allowed = client.options(
        "/auth/login",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == allowed_origin

    denied = client.options(
        "/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_education_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/questions").status_code == 401
    assert client.get("/knowledge-graph").status_code == 401


def test_questions_hide_answers(admin) -> None:
    response = admin["client"].get(
        "/questions",
        params={"knowledge_id": "rational_numbers"},
        headers=admin["headers"],
    )
    body = response.json()
    assert response.status_code == 200
    assert body["count"] == 3
    for item in body["items"]:
        assert "answer" not in item
        assert "explanation" not in item
        assert item["knowledge_id"] == "rational_numbers"


def test_submit_is_server_graded(student) -> None:
    response = student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "wrong-answer-1"},
        json={
            "question_id": "q-rational_numbers-2",
            "answer": "999",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["is_correct"] is False
    # 第一次答错不揭示答案：否则「乱答一次换答案」再重交就能刷掌握度。
    assert body["answer_revealed"] is False
    assert body["correct_answer"] is None
    assert body["explanation"] == ""
    assert body["attempts_for_question"] == 1
    assert body["events_triggered"] >= 3


def test_answer_revealed_after_second_wrong_attempt(student) -> None:
    payload = {"question_id": "q-rational_numbers-2", "answer": "999"}
    first = student["client"].post(
        "/submit", headers={**student["headers"], "Idempotency-Key": "reveal-1"}, json=payload
    ).json()
    assert first["answer_revealed"] is False
    assert first["correct_answer"] is None

    second = student["client"].post(
        "/submit", headers={**student["headers"], "Idempotency-Key": "reveal-2"}, json=payload
    ).json()
    assert second["answer_revealed"] is True
    assert second["correct_answer"] == "7"
    assert second["attempts_for_question"] == 2


def test_repeat_submission_does_not_inflate_mastery(student) -> None:
    payload = {"question_id": "q-rational_numbers-2", "answer": "7"}
    first = student["client"].post(
        "/submit", headers={**student["headers"], "Idempotency-Key": "repeat-1"}, json=payload
    ).json()
    assert first["counts_toward_mastery"] is True
    mastery_after_first = student["client"].get(
        f"/progress/{student['id']}", headers=student["headers"]
    ).json()["progress"]["avg_mastery"]

    second = student["client"].post(
        "/submit", headers={**student["headers"], "Idempotency-Key": "repeat-2"}, json=payload
    ).json()
    assert second["counts_toward_mastery"] is False
    progress = student["client"].get(
        f"/progress/{student['id']}", headers=student["headers"]
    ).json()["progress"]
    assert progress["total_attempts"] == 2
    assert progress["avg_mastery"] == mastery_after_first


def test_submit_correct_answer_and_attempt(student) -> None:
    response = student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "correct-1"},
        json={
            "question_id": "q-rational_numbers-2",
            "answer": "7",
            "time_spent_seconds": 12,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["is_correct"] is True
    assert body["knowledge_id"] == "rational_numbers"
    assert body["replayed"] is False
    assert any(e["type"] == "assessment.complete" for e in body["events"])
    assert body["correlation_id"]

    attempts = student["client"].get(
        f"/attempts/{student['id']}", headers=student["headers"]
    ).json()
    assert attempts["count"] == 1
    assert attempts["items"][0]["is_correct"] is True
    assert attempts["items"][0]["time_spent_seconds"] == 12


def test_submit_requires_idempotency_key(student) -> None:
    response = student["client"].post(
        "/submit",
        headers=student["headers"],
        json={"question_id": "q-rational_numbers-2", "answer": "7"},
    )
    assert response.status_code == 422


def test_idempotent_replay_does_not_duplicate(student) -> None:
    payload = {
        "question_id": "q-rational_numbers-2",
        "answer": "7",
        "time_spent_seconds": 3,
    }
    headers = {**student["headers"], "Idempotency-Key": "same-key-1"}
    first = student["client"].post("/submit", headers=headers, json=payload)
    second = student["client"].post("/submit", headers=headers, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["attempt_id"] == first.json()["attempt_id"]
    assert second.json()["events_triggered"] == 0

    attempts = student["client"].get(
        f"/attempts/{student['id']}", headers=student["headers"]
    ).json()
    assert attempts["count"] == 1


def test_idempotency_key_cannot_be_reused_for_another_answer(student) -> None:
    headers = {**student["headers"], "Idempotency-Key": "same-key-conflict"}
    first = student["client"].post(
        "/submit",
        headers=headers,
        json={"question_id": "q-rational_numbers-2", "answer": "7"},
    )
    assert first.status_code == 200
    second = student["client"].post(
        "/submit",
        headers=headers,
        json={"question_id": "q-rational_numbers-2", "answer": "999"},
    )
    assert second.status_code == 409


def test_progress_after_submit(student) -> None:
    student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "progress-1"},
        json={"question_id": "q-rational_numbers-3", "answer": "0"},
    )
    body = student["client"].get(
        f"/progress/{student['id']}", headers=student["headers"]
    ).json()
    assert body["progress"]["total_attempts"] == 1
    assert body["progress"]["total_correct"] == 0


def test_submit_validation(student) -> None:
    response = student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "validation-1"},
        json={"question_id": "", "answer": ""},
    )
    assert response.status_code == 422


def test_unknown_question_returns_404(student) -> None:
    response = student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "unknown-1"},
        json={"question_id": "q-nope", "answer": "1"},
    )
    assert response.status_code == 404


def test_event_timestamp_is_identical_between_submit_and_events(student) -> None:
    """同一个事件在 /submit 响应里和在 /events 里必须是同一个时间戳。"""
    body = student["client"].post(
        "/submit",
        headers={**student["headers"], "Idempotency-Key": "ts-1"},
        json={"question_id": "q-rational_numbers-2", "answer": "7"},
    ).json()
    submitted = [e["timestamp"] for e in body["events"]]
    assert submitted

    events = student["client"].get(
        f"/events/{student['id']}", headers=student["headers"]
    ).json()["events"]
    stored = {e["timestamp"] for e in events}
    assert set(submitted) <= stored
    # 时区信息必须存在（aware UTC），否则前端/报表会各自猜。
    assert all(ts.endswith("+00:00") for ts in submitted)


def test_websocket_requires_ticket(client: TestClient, student) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/{student['id']}"):
            pass
    assert exc.value.code == 1008


def test_websocket_rejects_legacy_query_token(client: TestClient, student) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/{student['id']}?token={student['token']}"
        ):
            pass
    assert exc.value.code == 1008


def test_websocket_ticket_is_one_time(client: TestClient, student) -> None:
    ticket = _ws_ticket(student["client"], student["token"])
    with client.websocket_connect(f"/ws/{student['id']}?ticket={ticket}") as ws:
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["event_type"] == "pong"

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/{student['id']}?ticket={ticket}"):
            pass
    assert exc.value.code == 1008


def test_websocket_ticket_requires_student(client: TestClient, admin) -> None:
    response = admin["client"].post("/auth/ws-ticket", headers=admin["headers"])
    assert response.status_code == 403


def test_websocket_pushes_result_and_events(client: TestClient, student) -> None:
    ticket = _ws_ticket(student["client"], student["token"])
    with client.websocket_connect(
        f"/ws/{student['id']}?ticket={ticket}"
    ) as ws:
        ws.send_json(
            {
                "action": "submit",
                "idempotency_key": "ws-key-1",
                "question_id": "q-rational_numbers-2",
                "answer": "7",
            }
        )
        frame = ws.receive_json()
        assert frame["event_type"] == "submission.result"
        assert frame["is_correct"] is True
        assert frame["replayed"] is False

        event_frame = ws.receive_json()
        assert event_frame["event_type"]


def test_knowledge_graph_endpoint(admin) -> None:
    body = admin["client"].get(
        "/knowledge-graph", headers=admin["headers"]
    ).json()
    assert body["subject"] == "math"
    assert len(body["nodes"]) == 60
    assert body["learning_order"][0] == "rational_numbers"
    assert all("grade" in node for node in body["nodes"])

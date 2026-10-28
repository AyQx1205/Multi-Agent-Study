"""REST + WebSocket：协议层契约。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["agents"] == 5
    assert body["content"]["knowledge_nodes"] == 60
    assert body["content"]["questions"] == 180


def test_questions_hide_answers(client: TestClient) -> None:
    response = client.get("/questions", params={"knowledge_id": "rational_numbers"})
    body = response.json()
    assert response.status_code == 200
    assert body["count"] == 3
    for item in body["items"]:
        assert "answer" not in item
        assert "explanation" not in item
        assert item["knowledge_id"] == "rational_numbers"


def test_submit_is_server_graded(client: TestClient) -> None:
    # q-rational_numbers-2 的正确答案是 7；客户端传 999 必须判错。
    response = client.post(
        "/submit",
        json={
            "learner_id": "s1",
            "question_id": "q-rational_numbers-2",
            "answer": "999",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["is_correct"] is False
    assert body["correct_answer"] == "7"
    assert body["events_triggered"] >= 3


def test_submit_correct_answer_and_attempt(client: TestClient) -> None:
    response = client.post(
        "/submit",
        json={
            "learner_id": "s2",
            "question_id": "q-rational_numbers-2",
            "answer": "7",
            "time_spent_seconds": 12,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["is_correct"] is True
    assert body["knowledge_id"] == "rational_numbers"
    assert any(e["type"] == "assessment.complete" for e in body["events"])
    assert body["correlation_id"]

    attempts = client.get("/attempts/s2").json()
    assert attempts["count"] == 1
    assert attempts["items"][0]["is_correct"] is True
    assert attempts["items"][0]["time_spent_seconds"] == 12


def test_progress_after_submit(client: TestClient) -> None:
    client.post(
        "/submit",
        json={
            "learner_id": "s3",
            "question_id": "q-rational_numbers-3",
            "answer": "0",
        },
    )
    body = client.get("/progress/s3").json()
    assert body["progress"]["total_attempts"] == 1
    assert body["progress"]["total_correct"] == 0


def test_submit_validation(client: TestClient) -> None:
    response = client.post(
        "/submit",
        json={"learner_id": "", "question_id": "x", "answer": "y"},
    )
    assert response.status_code == 422


def test_unknown_question_returns_404(client: TestClient) -> None:
    response = client.post(
        "/submit",
        json={"learner_id": "s4", "question_id": "q-nope", "answer": "1"},
    )
    assert response.status_code == 404


def test_websocket_pushes_result_and_events(client: TestClient) -> None:
    with client.websocket_connect("/ws/s5") as ws:
        ws.send_json(
            {
                "action": "submit",
                "question_id": "q-rational_numbers-2",
                "answer": "7",
            }
        )
        frame = ws.receive_json()
        assert frame["event_type"] == "submission.result"
        assert frame["is_correct"] is True

        event_frame = ws.receive_json()
        assert event_frame["event_type"]


def test_knowledge_graph_endpoint(client: TestClient) -> None:
    body = client.get("/knowledge-graph").json()
    assert body["subject"] == "math"
    assert len(body["nodes"]) == 60
    assert body["learning_order"][0] == "rational_numbers"
    assert all("grade" in node for node in body["nodes"])
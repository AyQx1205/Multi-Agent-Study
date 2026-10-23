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


def test_submit_returns_event_chain(client: TestClient) -> None:
    response = client.post(
        "/submit",
        json={"learner_id": "s1", "knowledge_id": "arithmetic", "is_correct": True},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["events_triggered"] >= 3
    assert any(e["type"] == "assessment.complete" for e in body["events"])
    assert body["correlation_id"]


def test_progress_after_submit(client: TestClient) -> None:
    client.post(
        "/submit",
        json={"learner_id": "s2", "knowledge_id": "fractions", "is_correct": False},
    )
    body = client.get("/progress/s2").json()
    assert body["progress"]["total_attempts"] == 1


def test_submit_validation(client: TestClient) -> None:
    response = client.post("/submit", json={"learner_id": "", "knowledge_id": "x", "is_correct": True})
    assert response.status_code == 422


def test_websocket_pushes_events(client: TestClient) -> None:
    with client.websocket_connect("/ws/s3") as ws:
        ws.send_json({"action": "submit", "knowledge_id": "arithmetic", "is_correct": True})
        frame = ws.receive_json()
        assert "event_type" in frame
        assert frame["event_type"]


def test_knowledge_graph_endpoint(client: TestClient) -> None:
    body = client.get("/knowledge-graph").json()
    assert len(body["nodes"]) == 20
    assert body["learning_order"][0] == "arithmetic"
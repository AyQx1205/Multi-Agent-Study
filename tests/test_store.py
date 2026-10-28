from __future__ import annotations

from app.core.events import Event, EventType
from app.core.learner_model import LearnerModel
from app.store import SqliteStore


def make_store(tmp_path) -> SqliteStore:
    return SqliteStore(tmp_path / "test.db")


def test_save_and_load_model(tmp_path) -> None:
    store = make_store(tmp_path)
    model = LearnerModel("s1")
    model.update_mastery("arithmetic", True)
    store.save_model(model)

    loaded = store.load_model("s1")
    assert loaded is not None
    assert loaded.get_state("arithmetic").mastery == model.get_state("arithmetic").mastery
    assert loaded.get_state("arithmetic").attempts == 1
    store.close()


def test_load_unknown_returns_none(tmp_path) -> None:
    store = make_store(tmp_path)
    assert store.load_model("nobody") is None
    store.close()


def test_optimistic_lock(tmp_path) -> None:
    store = make_store(tmp_path)
    model = LearnerModel("s1")
    model.update_mastery("arithmetic", True)
    store.save_model(model)

    state = model.get_state("arithmetic")
    assert store.get_version("s1", "arithmetic") == 1
    assert store.compare_and_swap("s1", state, 1) is True
    assert store.compare_and_swap("s1", state, 1) is False
    assert store.get_version("s1", "arithmetic") == 2
    store.close()


def test_event_append_is_idempotent(tmp_path) -> None:
    store = make_store(tmp_path)
    event = Event(type=EventType.ENCOURAGEMENT, source="t", learner_id="s1", data={"a": 1})
    store.append_event(event)
    store.append_event(event)
    assert store.count_events() == 1
    store.close()


def test_events_survive_reopen(tmp_path) -> None:
    path = tmp_path / "reopen.db"
    first = SqliteStore(path)
    event = Event(
        type=EventType.TEACHING_RESPONSE,
        source="t",
        learner_id="s1",
        data={"response": "先看条件？"},
        correlation_id="c1",
    )
    first.append_event(event)
    first.close()

    second = SqliteStore(path)
    restored = second.get_events("s1")
    assert len(restored) == 1
    assert restored[0].id == event.id
    assert restored[0].data["response"] == "先看条件？"
    assert restored[0].correlation_id == "c1"
    second.close()
"""BKT：数值、等级、薄弱点、进度。"""

from __future__ import annotations

import pytest

from app.core.learner_model import BKTParams, LearnerModel, MasteryLevel


def test_correct_increases_mastery() -> None:
    model = LearnerModel("s1")
    initial = model.get_state("arithmetic").mastery
    state = model.update_mastery("arithmetic", True)
    assert state.mastery > initial


def test_first_correct_matches_hand_calculation() -> None:
    model = LearnerModel("s1")
    state = model.update_mastery("arithmetic", True)
    assert state.mastery == pytest.approx(0.4333, abs=1e-3)


def test_first_wrong_matches_hand_calculation() -> None:
    model = LearnerModel("s1")
    state = model.update_mastery("arithmetic", False)
    assert state.mastery == pytest.approx(0.1617, abs=1e-3)


def test_wrong_lowers_relative_to_correct() -> None:
    model = LearnerModel("s1")
    model.update_mastery("k", True)
    after_correct = model.get_state("k").mastery
    model.update_mastery("k", False)
    assert model.get_state("k").mastery < after_correct


def test_all_wrong_still_rises() -> None:
    """已知行为：p_transit 让「一直错」也会缓慢上涨（教程第 2 章）。"""
    model = LearnerModel("s1")
    first = model.update_mastery("k", False).mastery
    second = model.update_mastery("k", False).mastery
    assert second > first


def test_level_progression() -> None:
    model = LearnerModel("s1")
    assert model.get_state("k").level is MasteryLevel.NOT_STARTED
    for _ in range(20):
        model.update_mastery("k", True)
    assert model.get_state("k").level is MasteryLevel.MASTERED


def test_weak_points_excludes_unattempted() -> None:
    model = LearnerModel("s1")
    model.update_mastery("a", False)
    model.update_mastery("b", True)
    weak = [s.knowledge_id for s in model.get_weak_points()]
    assert weak == ["a"]


def test_overall_progress() -> None:
    model = LearnerModel("s1")
    model.update_mastery("a", True)
    model.update_mastery("b", False)
    progress = model.get_overall_progress()
    assert progress["total_knowledge_points"] == 2
    assert progress["total_attempts"] == 2
    assert progress["total_correct"] == 1


def test_custom_p_init() -> None:
    model = LearnerModel("s1", BKTParams(p_init=0.5))
    assert model.update_mastery("k", True).mastery == pytest.approx(0.8455, abs=1e-3)
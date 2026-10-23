"""SM-2：间隔、EF、重置、到期与排期。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.spaced_repetition import ReviewItem, SpacedRepetition

NOW = datetime(2026, 1, 1, 9, 0, 0)


def test_intervals_grow_1_6_then_multiply() -> None:
    sr = SpacedRepetition()
    item = ReviewItem(knowledge_id="k", next_review=NOW)

    sr.review(item, 5, now=NOW)
    assert item.interval_days == 1
    sr.review(item, 5, now=NOW + timedelta(days=1))
    assert item.interval_days == 6
    sr.review(item, 5, now=NOW + timedelta(days=7))
    assert item.interval_days == pytest.approx(15)


def test_quality_below_3_resets() -> None:
    sr = SpacedRepetition()
    item = ReviewItem(knowledge_id="k", next_review=NOW)
    for _ in range(3):
        sr.review(item, 5, now=NOW)
    sr.review(item, 1, now=NOW)
    assert item.repetition == 0
    assert item.interval_days == 1


def test_ef_bounds() -> None:
    sr = SpacedRepetition()
    easy = ReviewItem(knowledge_id="e", next_review=NOW)
    for _ in range(3):
        sr.review(easy, 5, now=NOW)
    assert easy.easiness_factor == pytest.approx(2.5)

    hard = ReviewItem(knowledge_id="h", next_review=NOW)
    for _ in range(6):
        sr.review(hard, 2, now=NOW)
    assert hard.easiness_factor == pytest.approx(1.3)


def test_due_items_sorted() -> None:
    sr = SpacedRepetition()
    items = [
        ReviewItem(knowledge_id="late", next_review=NOW - timedelta(days=5)),
        ReviewItem(knowledge_id="soon", next_review=NOW - timedelta(days=1)),
        ReviewItem(knowledge_id="future", next_review=NOW + timedelta(days=3)),
    ]
    assert [i.knowledge_id for i in sr.get_due_items(items, now=NOW)] == ["late", "soon"]


def test_study_schedule_buckets_by_day() -> None:
    sr = SpacedRepetition()
    items = [
        ReviewItem(knowledge_id="a", next_review=NOW + timedelta(days=1)),
        ReviewItem(knowledge_id="b", next_review=NOW + timedelta(days=1)),
        ReviewItem(knowledge_id="c", next_review=NOW + timedelta(days=2)),
    ]
    schedule = sr.get_study_schedule(items, days_ahead=7, now=NOW)
    assert schedule["2026-01-02"] == ["a", "b"]
    assert schedule["2026-01-03"] == ["c"]
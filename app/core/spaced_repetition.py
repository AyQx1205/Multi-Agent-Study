from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pydantic import BaseModel, Field, field_validator

from app.core.timeutil import ensure_utc, utcnow

logger = logging.getLogger(__name__)

MIN_EF = 1.3
MAX_EF = 2.5

class ReviewItem(BaseModel):
    knowledge_id: str = Field(description="复习知识点id")
    easiness_factor: float = Field(default=2.5, description="复习难度因子")
    interval_days: float = Field(default=0, description="复习间隔")
    repetition: int = Field(default=0, description="复习次数")
    next_review: datetime = Field(description="下次复习时间")
    last_review: datetime | None = Field(default=None, description="上次复习时间")
    total_reviews: int = Field(default=0, description="总复习次数")

    @field_validator("next_review", "last_review")
    @classmethod
    def _normalize_review_times(cls, value: datetime | None) -> datetime | None:
        """入口统一成 aware UTC，避免 naive/aware 混用导致比较直接抛 TypeError。"""
        return ensure_utc(value)

    def is_due(self, now: datetime | None = None) -> bool:
        return (ensure_utc(now) or utcnow()) >= (ensure_utc(self.next_review) or utcnow())

    def overdue_days(self, now: datetime | None = None) -> float:
        current = ensure_utc(now) or utcnow()
        due = ensure_utc(self.next_review) or current
        return max(0.0, (current - due).total_seconds() / 86400)

class SpacedRepetition:
    def review(self,item: ReviewItem,quality: int,now:datetime | None = None) -> ReviewItem:
        now = ensure_utc(now) or utcnow()
        quality = max(0, min(5,int(quality)))

        item.total_reviews += 1
        item.last_review = now

        delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
        item.easiness_factor = max(MIN_EF, min(MAX_EF, item.easiness_factor + delta))

        if quality < 3:
            item.repetition = 0
            item.interval_days = 1
        else:
            if item.repetition == 0:
                item.interval_days = 1
            elif item.repetition == 1:
                item.interval_days = 6
            else:
                item.interval_days = item.interval_days * item.easiness_factor
            item.repetition += 1

        item.next_review = now + timedelta(days=item.interval_days)
        return item

    def get_due_items(self,items: list[ReviewItem],now: datetime | None = None) -> list[ReviewItem]:
        current = ensure_utc(now) or utcnow()
        due = [item for item in items if (ensure_utc(item.next_review) or current) <= current]
        return sorted(due,key=lambda x: x.next_review)

    def get_study_schedule(self,items: list[ReviewItem],days_ahead: int = 7,now: datetime | None = None) -> dict[str,list[str]]:
        current = ensure_utc(now) or utcnow()
        schedule:dict[str,list[str]] = {}
        for offset in range(days_ahead):
            day = (current + timedelta(days=offset)).date()
            if offset == 0:
                bucket = [
                    item.knowledge_id
                    for item in items
                    if (ensure_utc(item.next_review) or current).date() <= day
                ]
            else:
                bucket = [
                    item.knowledge_id
                    for item in items
                    if (ensure_utc(item.next_review) or current).date() == day
                ]
            if bucket:
                schedule[day.isoformat()] = sorted(set(bucket))        
        return schedule    